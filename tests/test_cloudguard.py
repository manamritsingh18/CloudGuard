import asyncio
import base64
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import time

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "monitor"))
import config
import slo as slo_math
from storage import Store


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backend = load("backend_main", ROOT / "backend" / "main.py")
monitor = load("monitor_main", ROOT / "monitor" / "main.py")

LAB = config.Plane(id="lab", name="Local lab", description="", headline="gateway")
LAB_TARGETS = (
    config.Target(id="app-1", name="app-1", plane="lab", url="http://app1:8000/health", interval=5, timeout=2,
                  critical=False, expect_json={"status": ("healthy",), "instance": ("app-1",)}),
    config.Target(id="app-2", name="app-2", plane="lab", url="http://app2:8000/health", interval=5, timeout=2,
                  critical=False, expect_json={"status": ("healthy",), "instance": ("app-2",)}),
    config.Target(id="gateway", name="Gateway", plane="lab", url="http://nginx/api/app/health", interval=5, timeout=2,
                  expect_json={"status": ("healthy",), "instance": ("app-1", "app-2")}),
)


def sample(target="gateway", healthy=True, latency=10, timestamp=None, metrics=None):
    return {"target": target, "healthy": healthy, "latency_ms": latency,
            "timestamp": time.time() if timestamp is None else timestamp,
            "error": None if healthy else "Connection failed", "status_code": 200 if healthy else None,
            "metrics": metrics or {}}


def minimal(**overrides):
    raw = {"planes": [{"id": "prod", "name": "Production"}],
           "targets": [{"id": "api", "plane": "prod", "url": "https://example.test/health",
                        "interval_seconds": 30, "timeout_seconds": 5, "metrics": {"db_ms": "database.latency_ms"}}],
           "slos": [{"id": "api-up", "target": "api", "objective": "availability", "goal_percent": 99, "window_hours": 24}]}
    raw.update(overrides)
    return raw


def jwt(claims):
    encode = lambda part: base64.urlsafe_b64encode(json.dumps(part).encode()).decode().rstrip("=")
    return f"{encode({'alg': 'HS256'})}.{encode(claims)}.signature"


def test_app_identity_health_and_validation():
    with TestClient(backend.app) as client:
        assert client.get("/").json()["instance"] == backend.INSTANCE_ID
        assert client.get("/health").json()["status"] == "healthy"
        assert client.get("/echo", params={"message": "hello"}).json()["message"] == "hello"
        assert client.get("/echo").status_code == 422
        assert client.get("/echo", params={"message": "x" * 201}).status_code == 422


def test_shipped_targets_file_is_valid_and_public_view_hides_headers():
    loaded = config.load(env={}, max_window_hours=168)
    assert [plane.id for plane in loaded.planes] == ["production", "fallback", "lab"]
    assert {"app-1", "app-2", "gateway", "rw-api", "rw-api-deep", "supabase-auth"} <= {t.id for t in loaded.targets}
    assert loaded.target("app-1").interval == 5  # ${CHECK_INTERVAL_SECONDS:-5}
    assert loaded.target("supabase-auth").headers["apikey"].startswith("sb_publishable_")
    public = json.dumps(loaded.public())
    assert "headers" not in public and "sb_publishable_" not in public
    assert "tls_days_left" in loaded.target("rw-frontend").metric_names
    assert "tls_days_left" not in loaded.target("gateway").metric_names


def test_environment_substitution_uses_value_then_default():
    raw = minimal(targets=[{"id": "api", "plane": "prod", "url": "${API_URL:-https://default.test/health}",
                            "interval_seconds": "${EVERY:-30}", "timeout_seconds": 5}], slos=[])
    assert config.parse(raw, env={}).targets[0].url == "https://default.test/health"
    assert config.parse(raw, env={"API_URL": ""}).targets[0].url == "https://default.test/health"
    parsed = config.parse(raw, env={"API_URL": "https://other.test/h", "EVERY": "60"}).targets[0]
    assert (parsed.url, parsed.interval) == ("https://other.test/h", 60)
    with pytest.raises(config.ConfigError, match="NO_DEFAULT"):
        config.parse(minimal(targets=[{"id": "api", "plane": "prod", "url": "${NO_DEFAULT}",
                                       "interval_seconds": 30, "timeout_seconds": 5}]), env={})


def test_every_problem_is_reported_at_once():
    raw = minimal(
        targets=[{"id": "api", "plane": "prod", "url": "ftp://bad", "interval_seconds": 5, "timeout_seconds": 9},
                 {"id": "api", "plane": "missing", "url": "https://x.test", "interval_seconds": 5,
                  "timeout_seconds": 1, "method": "POST"}],
        slos=[{"id": "perfect", "target": "api", "objective": "availability", "goal_percent": 100, "window_hours": 24},
              {"id": "speed", "target": "api", "objective": "metric", "metric": "nope", "max": 1, "goal_percent": 90,
               "window_hours": 24},
              {"id": "long", "target": "api", "objective": "latency", "goal_percent": 90, "window_hours": 9999}])
    with pytest.raises(config.ConfigError) as error:
        config.parse(raw, env={}, max_window_hours=168)
    message = str(error.value)
    for expected in ["must be an http(s) URL", "must not exceed interval_seconds", "duplicate id 'api'",
                     "'missing' is not one of", "only GET and HEAD", "100% leaves no error budget",
                     "'nope' is not recorded", "window_hours: 9999", "threshold_ms: is required"]:
        assert expected in message, expected


@pytest.mark.parametrize("value", ["sb_secret_abc123", f"Bearer {jwt({'role': 'service_role'})}", jwt({"role": "service_role"})])
def test_secret_keys_are_refused(value):
    raw = minimal()
    raw["targets"][0]["headers"] = {"apikey": value}
    with pytest.raises(config.ConfigError, match="publishable key"):
        config.parse(raw, env={})
    raw = minimal()
    raw["targets"][0]["url"] = f"https://example.test/health?apikey={value.split()[-1]}"
    with pytest.raises(config.ConfigError, match="secret key"):
        config.parse(raw, env={})


def test_publishable_and_anon_keys_are_accepted():
    raw = minimal()
    raw["targets"][0]["headers"] = {"apikey": "sb_publishable_abc", "x-anon": jwt({"role": "anon"})}
    assert config.parse(raw, env={}).targets[0].headers["apikey"] == "sb_publishable_abc"


def test_empty_storage_has_no_invented_availability(tmp_path):
    result = Store(tmp_path / "data.db").summary(24, LAB_TARGETS, (LAB,))
    assert result["planes"][0]["status"] == "unknown"
    for target in result["targets"]:
        assert target["total_checks"] == 0
        assert target["availability_percent"] is None
        assert target["average_latency_ms"] is None


def test_metrics_and_transition_deduplication_persist(tmp_path):
    path = tmp_path / "data.db"
    store = Store(path)
    now = time.time()
    for i, healthy in enumerate([True, False, False, True]):
        store.record_batch([sample(healthy=healthy, latency=10 + i * 10, timestamp=now - 4 + i)])
    result = Store(path).summary(24, LAB_TARGETS, (LAB,), now=now)["targets"][2]
    assert result["total_checks"] == 4
    assert result["successful_checks"] == result["failed_checks"] == 2
    assert result["availability_percent"] == 50
    assert result["average_latency_ms"] == 25  # Successful samples 10 and 40 only.
    assert result["last_success"] == now - 1
    assert [event["kind"] for event in store.events(30)] == ["recovery", "incident"]


def test_plane_rollup_separates_degraded_outage_and_stale(tmp_path):
    store = Store(tmp_path / "data.db")
    now = time.time()
    store.record_batch([sample("gateway", timestamp=now), sample("app-1", timestamp=now),
                        sample("app-2", healthy=False, timestamp=now)])
    plane = store.summary(24, LAB_TARGETS, (LAB,), now=now)["planes"][0]
    assert (plane["status"], plane["failing"]) == ("degraded", ["app-2"])
    store.record_batch([sample("gateway", healthy=False, timestamp=now + 1)])
    assert store.summary(24, LAB_TARGETS, (LAB,), now=now + 1)["planes"][0]["status"] == "outage"
    result = store.summary(24, LAB_TARGETS, (LAB,), now=now + 20)
    assert result["planes"][0]["status"] == "unknown"
    assert all(target["status"] == "stale" for target in result["targets"])
    assert all(target["current_latency_ms"] is None for target in result["targets"])


def test_window_and_history_bucket_counts(tmp_path):
    store = Store(tmp_path / "data.db")
    now = time.time()
    store.record_batch([sample(timestamp=now - 7200), sample(timestamp=now - 20),
                        sample(healthy=False, timestamp=now - 10)])
    result = store.summary(1, LAB_TARGETS, (LAB,), now=now)["targets"][2]
    assert result["total_checks"] == 2
    assert result["availability_percent"] == 50
    history = store.history("gateway", 1, now=now)
    assert len(history) <= 120
    assert sum(point["checks"] for point in history) == 2
    assert sum(point["failures"] for point in history) == 1


def test_version_one_database_is_migrated_and_metrics_are_charted(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE samples (id INTEGER PRIMARY KEY, target TEXT NOT NULL, timestamp REAL NOT NULL,"
                   " healthy INTEGER NOT NULL, latency_ms REAL NOT NULL, error TEXT)")
        db.execute("INSERT INTO samples(target,timestamp,healthy,latency_ms) VALUES ('gateway', ?, 1, 5)", (time.time() - 30,))
    store = Store(path)
    now = time.time()
    store.record_batch([sample("rw-api-deep", timestamp=now - 20, metrics={"db_latency_ms": 4000}),
                        sample("rw-api-deep", healthy=False, timestamp=now - 10, metrics={"db_latency_ms": 6000})])
    assert store.summary(1, LAB_TARGETS, (LAB,), now=now)["targets"][2]["total_checks"] == 1  # old row kept
    points = store.history("rw-api-deep", 1, now=now, metric="db_latency_ms")
    assert sum(point["checks"] for point in points) == 2
    assert {point["value"] for point in points} <= {4000, 5000, 6000}
    with pytest.raises(ValueError):
        store.history("rw-api-deep", 1, metric="x'; DROP TABLE samples; --")


def test_retention_removes_old_samples_and_events(tmp_path):
    store = Store(tmp_path / "data.db")
    store.record_batch([sample(healthy=False, timestamp=time.time() - 8 * 86400)], retention_days=7)
    assert store.events(30) == []
    assert store.summary(168, LAB_TARGETS, (LAB,))["targets"][2]["total_checks"] == 0


def run_probe(target, handler):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await monitor.probe(client, target)
    return asyncio.run(run())


@pytest.mark.parametrize("mode,expected", [
    ("healthy", True), ("wrong_identity", False), ("invalid_json", False),
    ("timeout", False), ("http_error", False), ("wrong_shape", False), ("connection", False),
])
def test_probe_checks_identity_body_and_errors(mode, expected):
    def handler(request):
        if mode == "timeout":
            raise httpx.ReadTimeout("test", request=request)
        if mode == "connection":
            raise httpx.ConnectError("test", request=request)
        if mode == "http_error":
            return httpx.Response(503)
        if mode == "invalid_json":
            return httpx.Response(200, text="invalid")
        if mode == "wrong_shape":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"status": "healthy", "instance": "other" if mode == "wrong_identity" else "app-1"})

    result = run_probe(LAB_TARGETS[0], handler)
    assert result["healthy"] is expected
    assert (result["error"] is None) is expected
    assert result["latency_ms"] >= 0


@pytest.mark.parametrize("expect,response,error", [
    ({"expect_status": (401,), "body_contains": ("Secret API key required",)},
     httpx.Response(401, text='{"message":"Secret API key required"}'), None),
    ({"expect_status": (401,)}, httpx.Response(200, text="open"), "HTTP 200 (expected 401)"),
    ({"body_contains": ("sb_publishable_",)}, httpx.Response(200, text="window.X = {}"), "missing 'sb_publishable_'"),
    ({"body_excludes": ("sb_secret_",)}, httpx.Response(200, text="key: sb_secret_123"), "forbidden text 'sb_secret_'"),
    ({"expect_json": {"worker.online": (True,)}}, httpx.Response(200, json={"worker": {"online": 1}}),
     "worker.online is 1, expected true"),
    ({"expect_json": {"database.ok": (True,)}}, httpx.Response(200, json={"database": {}}),
     "database.ok is missing, expected true"),
])
def test_probe_expectations(expect, response, error):
    target = config.Target(id="t", name="t", plane="p", url="https://example.test", interval=30, timeout=5, **expect)
    result = run_probe(target, lambda request: response)
    if error is None:
        assert result["error"] is None and result["healthy"] is True
    else:
        assert error in (result["error"] or "") and result["healthy"] is False


def test_probe_keeps_metrics_when_an_expectation_fails():
    target = config.Target(id="deep", name="deep", plane="p", url="https://example.test", interval=30, timeout=5,
                           expect_json={"database.ok": (True,)},
                           metrics={"db_latency_ms": "database.latency_ms", "online": "worker.online", "text": "status"})
    payload = {"status": "degraded", "database": {"ok": False, "latency_ms": 7300}, "worker": {"online": True}}
    result = run_probe(target, lambda request: httpx.Response(200, json=payload))
    assert result["healthy"] is False
    assert result["metrics"] == {"db_latency_ms": 7300, "online": 1}  # text values are not metrics


def slo_for(objective="availability", goal=99, **fields):
    return config.Slo(id="s", name="s", target="gateway", objective=objective, goal_percent=goal, window_hours=24, **fields)


def evaluate(store, slo, now):
    window = store.sli_counts(slo, now - slo.window_hours * 3600)
    burn = store.sli_counts(slo, now - slo.burn_window_hours * 3600)
    return slo_math.evaluate(slo, LAB_TARGETS[2], window, burn, now)


def test_availability_slo_error_budget_and_burn_rate(tmp_path):
    store = Store(tmp_path / "slo.db")
    now = time.time()
    # 200 checks over the last ~3 hours, one failure two hours ago.
    store.record_batch([sample(healthy=i != 80, timestamp=now - 10800 + i * 54) for i in range(200)])
    result = evaluate(store, slo_for(goal=99, sla_percent=98), now)
    assert (result["valid_checks"], result["good_checks"], result["bad_checks"]) == (200, 199, 1)
    assert result["attainment_percent"] == pytest.approx(99.5)
    assert result["budget_checks"] == pytest.approx(2)          # 1% of 200 checks may fail
    assert result["budget_remaining_percent"] == pytest.approx(50)
    assert result["burn_rate"] == 0                              # the failure is outside the last hour
    assert result["allowed_bad_minutes"] == pytest.approx(14.4)  # 1% of 24 h
    assert result["coverage_percent"] == pytest.approx(100 * (199 * 54) / 86400)
    assert (result["status"], result["sla_met"]) == ("met", True)


def test_slo_breach_at_risk_and_no_data(tmp_path):
    store = Store(tmp_path / "slo.db")
    now = time.time()
    assert evaluate(store, slo_for(), now)["status"] == "no_data"
    store.record_batch([sample(healthy=i >= 3, timestamp=now - 300 + i) for i in range(100)])
    breached = evaluate(store, slo_for(goal=99, sla_percent=96), now)
    assert breached["status"] == "breached" and breached["budget_remaining_percent"] == pytest.approx(-200)
    assert breached["sla_met"] is True and breached["burn_rate"] == pytest.approx(3)
    # 3 bad of 100 within a 95% goal: 40% budget left but burning 0.6x the sustainable rate.
    assert evaluate(store, slo_for(goal=95), now)["status"] == "met"
    # Same data, 96.5% goal: 14% budget left → at risk.
    assert evaluate(store, slo_for(goal=96.5), now)["status"] == "at_risk"


def test_latency_and_metric_slos_count_only_valid_checks(tmp_path):
    store = Store(tmp_path / "slo.db")
    now = time.time()
    store.record_batch([sample(latency=100, timestamp=now - 50), sample(latency=900, timestamp=now - 40),
                        sample(healthy=False, latency=5000, timestamp=now - 30),
                        sample(timestamp=now - 20, metrics={"db_latency_ms": 1500}),
                        sample(timestamp=now - 10, metrics={"db_latency_ms": 4800})])
    latency = evaluate(store, slo_for("latency", goal=90, threshold_ms=800), now)
    assert (latency["valid_checks"], latency["good_checks"]) == (4, 3)  # the failed check is not a latency event
    metric = evaluate(store, slo_for("metric", goal=90, metric="db_latency_ms", max_value=2000), now)
    assert (metric["valid_checks"], metric["good_checks"]) == (2, 1)
    floor = evaluate(store, slo_for("metric", goal=90, metric="db_latency_ms", min_value=2000), now)
    assert floor["good_checks"] == 1


def test_monitor_api_validation_and_worker_health(tmp_path):
    # Avoid starting a live worker in unit tests; integration tests exercise it in Docker.
    monitor.app.state.store = Store(tmp_path / "api.db")
    monitor.app.state.last_recorded = {}
    monitor.app.state.first_due = {target.id: time.time() + 1 for target in monitor.CONFIG.targets}
    client = TestClient(monitor.app)
    assert client.get("/health").status_code == 200  # just started: nothing is due yet
    monitor.app.state.first_due = {target.id: time.time() - 3600 for target in monitor.CONFIG.targets}
    assert client.get("/health").status_code == 503
    monitor.app.state.last_recorded = {target.id: time.time() for target in monitor.CONFIG.targets}
    assert client.get("/health").status_code == 200
    summary = client.get("/summary").json()
    assert {plane["status"] for plane in summary["planes"]} == {"unknown"}
    assert "headers" not in client.get("/targets").text
    slos = client.get("/slos").json()["slos"]
    assert slos and all(item["status"] == "no_data" for item in slos)
    for route in ["/summary?hours=0", "/summary?hours=169", "/history?target=bad", "/incidents?limit=101",
                  "/history?target=gateway&metric=db_latency_ms", "/history?target=rw-api-deep&metric=nope"]:
        assert client.get(route).status_code == 422, route
    assert client.get("/history?target=gateway").json()["points"] == []
    assert client.get("/history?target=rw-api-deep&metric=db_latency_ms").json()["unit"] == "ms"
    assert client.get("/incidents").json()["events"] == []
    assert client.get("/audit?limit=101").status_code == 422
    assert client.get("/audit").json()["actions"] == []


def test_audit_persists_and_rejects_unknown_actions(tmp_path):
    path = tmp_path / "audit.db"
    store = Store(path)
    store.record_action("app-1", "stop", "requested")
    store.record_action("app-1", "stop", "succeeded")
    assert [action["outcome"] for action in Store(path).audit(10)] == ["succeeded", "requested"]
    with pytest.raises(ValueError):
        store.record_action("app-1", "delete", "succeeded")


def test_history_adapts_to_short_window_and_is_bounded(tmp_path):
    store = Store(tmp_path / "history.db")
    now = time.time()
    store.record_batch([sample(timestamp=now - offset) for offset in range(600)])
    points = store.history("gateway", 24, now=now)
    assert 2 < len(points) <= 120
    assert sum(point["checks"] for point in points) == 600
