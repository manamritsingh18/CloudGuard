"""Observe multiple real probe cycles and fail on any newly failed check."""
import json
import os
import time
import urllib.request

base = os.getenv("LIVE_BASE_URL", "http://nginx")


def summary():
    with urllib.request.urlopen(base + "/api/monitor/summary", timeout=8) as response:
        return json.load(response)


before = summary()
started = time.monotonic()
for _ in range(12):
    time.sleep(5)
    current = summary()
    assert current["overall_status"] == "healthy", current
    for old, new in zip(before["targets"], current["targets"]):
        assert new["failed_checks"] == old["failed_checks"], (old, new)
after = summary()
print(json.dumps({"elapsed_seconds": round(time.monotonic() - started, 1),
                  "new_checks": {new["target"]: new["total_checks"] - old["total_checks"]
                                 for old, new in zip(before["targets"], after["targets"])},
                  "new_failures": 0}, indent=2))
