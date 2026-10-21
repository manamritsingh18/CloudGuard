"""Read-only assertions against the running Compose network; no fabricated samples."""
import argparse
import json
import os
import time
import urllib.error
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument("--expect", choices=["healthy", "degraded", "outage"], default="healthy")
parser.add_argument("--requests", type=int, default=20)
args = parser.parse_args()
base = os.getenv("LIVE_BASE_URL", "http://nginx")


def get(path):
    with urllib.request.urlopen(base + path, timeout=8) as response:
        body = response.read()
        return json.loads(body) if "application/json" in response.headers.get("Content-Type", "") else body


deadline = time.monotonic() + 35
while True:
    summary = get("/api/monitor/summary")
    if summary["overall_status"] == args.expect:
        break
    if time.monotonic() >= deadline:
        raise AssertionError(f"Expected {args.expect}, received {summary}")
    time.sleep(1)

assert b"CloudGuard" in get("/")
assert b"Infrastructure overview" in get("/dashboard")
assert get("/healthz")["status"] == "healthy"
assert get("/api/monitor/health")["status"] == "healthy"
assert get("/styles.css") and get("/app.js")
assert get("/landing.css") and get("/landing.js") and get("/interactions.js")
assert isinstance(get("/api/monitor/history")["points"], list)
for route in ["/api/monitor/summary?hours=0", "/api/monitor/history?target=bad", "/api/app/echo"]:
    try:
        get(route)
    except urllib.error.HTTPError as exc:
        assert exc.code == 422
    else:
        raise AssertionError(f"Input validation missing: {route}")

identities = []
warmup_requests = 0
if args.expect != "outage":
    if args.expect == "healthy":
        # Independent probes can recover before NGINX's 5s passive failure
        # timeout expires. Wait for both upstreams to rejoin before measuring.
        rejoin_deadline = time.monotonic() + 15
        seen = set()
        while len(seen) < 2:
            seen.add(get("/api/app/")["instance"])
            warmup_requests += 1
            if time.monotonic() >= rejoin_deadline:
                raise AssertionError(f"Both healthy replicas did not rejoin NGINX: {seen}")
            if len(seen) < 2:
                time.sleep(0.25)
    for _ in range(args.requests):
        identities.append(get("/api/app/")["instance"])
    assert set(identities) <= {"app-1", "app-2"}
    if args.expect == "healthy":
        assert set(identities) == {"app-1", "app-2"}
    else:
        assert set(identities) == {"app-2"}, identities
        assert any(event["target"] == "app-1" and event["kind"] == "incident"
                   for event in get("/api/monitor/incidents")["events"])
else:
    try:
        get("/api/app/")
    except urllib.error.HTTPError as exc:
        assert exc.code == 502
    else:
        raise AssertionError("Both stopped replicas should make the application unavailable")

print(json.dumps({"expected_status": args.expect, "observed_status": summary["overall_status"],
                  "upstream_rejoin_warmup_requests": warmup_requests,
                  "successful_application_requests": len(identities),
                  "instance_distribution": {name: identities.count(name) for name in set(identities)},
                  "targets": summary["targets"], "recent_events": get("/api/monitor/incidents?limit=5")["events"]}, indent=2))
