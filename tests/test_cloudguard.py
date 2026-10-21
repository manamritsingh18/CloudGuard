import asyncio
import importlib.util
from pathlib import Path
import sys
import time

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "monitor"))
from storage import Store


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backend = load("backend_main", ROOT / "backend" / "main.py")
monitor = load("monitor_main", ROOT / "monitor" / "main.py")


def sample(target="gateway", healthy=True, latency=10, timestamp=None):
    return {"target": target, "healthy": healthy, "latency_ms": latency,
            "timestamp": time.time() if timestamp is None else timestamp,
            "error": None if healthy else "Connection failed"}


def test_app_identity_health_and_validation():
    with TestClient(backend.app) as client:
        assert client.get("/").json()["instance"] == backend.INSTANCE_ID
        assert client.get("/health").json()["status"] == "healthy"
        assert client.get("/echo", params={"message": "hello"}).json()["message"] == "hello"
        assert client.get("/echo").status_code == 422
        assert client.get("/echo", params={"message": "x" * 201}).status_code == 422


def test_empty_storage_has_no_invented_availability(tmp_path):
    result = Store(tmp_path / "data.db").summary(24, 15)
    assert result["overall_status"] == "unknown"
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
    result = Store(path).summary(24, 15, now=now)["targets"][2]
    assert result["total_checks"] == 4
    assert result["successful_checks"] == result["failed_checks"] == 2
    assert result["availability_percent"] == 50
    assert result["average_latency_ms"] == 25  # Successful samples 10 and 40 only.
    assert result["last_success"] == now - 1
    assert [event["kind"] for event in store.events(30)] == ["recovery", "incident"]


def test_stale_and_degraded_are_distinct(tmp_path):
    store = Store(tmp_path / "data.db")
    now = time.time()
    store.record_batch([sample("gateway", timestamp=now), sample("app-1", timestamp=now),
                        sample("app-2", healthy=False, timestamp=now)])
    assert store.summary(24, 15, now=now)["overall_status"] == "degraded"
    result = store.summary(24, 15, now=now + 20)
    assert result["overall_status"] == "unknown"
    assert all(target["status"] == "stale" for target in result["targets"])
    assert all(target["current_latency_ms"] is None for target in result["targets"])


def test_window_and_history_bucket_counts(tmp_path):
    store = Store(tmp_path / "data.db")
    now = time.time()
    store.record_batch([sample(timestamp=now - 7200), sample(timestamp=now - 20),
                        sample(healthy=False, timestamp=now - 10)])
    result = store.summary(1, 15, now=now)["targets"][2]
    assert result["total_checks"] == 2
    assert result["availability_percent"] == 50
    history = store.history("gateway", 1, now=now)
    assert len(history) <= 120
    assert sum(point["checks"] for point in history) == 2
    assert sum(point["failures"] for point in history) == 1


def test_retention_removes_old_samples_and_events(tmp_path):
    store = Store(tmp_path / "data.db")
    store.record_batch([sample(healthy=False, timestamp=time.time() - 8 * 86400)], retention_days=7)
    assert store.events(30) == []
    assert store.summary(168, 15)["targets"][2]["total_checks"] == 0


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

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await monitor.probe(client, "app-1", "http://test/health")
    result = asyncio.run(run())
    assert result["healthy"] is expected
    assert (result["error"] is None) is expected
    assert result["latency_ms"] >= 0


def test_monitor_api_validation_and_worker_health(tmp_path):
    # Avoid starting a live worker in unit tests; integration tests exercise it in Docker.
    monitor.app.state.store = Store(tmp_path / "api.db")
    monitor.app.state.last_cycle = None
    client = TestClient(monitor.app)
    assert client.get("/health").status_code == 503
    monitor.app.state.last_cycle = time.time()
    assert client.get("/health").status_code == 200
    assert client.get("/summary").json()["overall_status"] == "unknown"
    for route in ["/summary?hours=0", "/summary?hours=169", "/history?target=bad", "/incidents?limit=101"]:
        assert client.get(route).status_code == 422
    assert client.get("/history").json()["points"] == []
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
