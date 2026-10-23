"""Observe multiple real lab probe cycles and fail on any newly failed lab check.

Production targets depend on the internet and on other people's servers, so they are not part
of this local stability check.
"""
import json
import os
import time
import urllib.request

base = os.getenv("LIVE_BASE_URL", "http://nginx")


def summary():
    with urllib.request.urlopen(base + "/api/monitor/summary", timeout=8) as response:
        result = json.load(response)
    result["targets"] = [target for target in result["targets"] if target["plane"] == "lab"]
    result["lab_status"] = next(plane["status"] for plane in result["planes"] if plane["id"] == "lab")
    return result


before = summary()
started = time.monotonic()
for _ in range(12):
    time.sleep(5)
    current = summary()
    assert current["lab_status"] == "healthy", current
    for old, new in zip(before["targets"], current["targets"]):
        assert new["failed_checks"] == old["failed_checks"], (old, new)
after = summary()
print(json.dumps({"elapsed_seconds": round(time.monotonic() - started, 1),
                  "new_checks": {new["target"]: new["total_checks"] - old["total_checks"]
                                 for old, new in zip(before["targets"], after["targets"])},
                  "new_failures": 0}, indent=2))
