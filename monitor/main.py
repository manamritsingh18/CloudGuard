"""One monitoring worker probes every configured target on its own interval (see targets.yaml)."""
import asyncio
from contextlib import asynccontextmanager
import json
import logging
import os
import ssl
import time

import httpx
from fastapi import FastAPI, Query, HTTPException
import config
import slo as slo_math
from storage import Store, metric_unit

logger = logging.getLogger("cloudguard.monitor")
RETENTION = int(os.getenv("RETENTION_DAYS", "7"))
if not 1 <= RETENTION <= 365:
    raise ValueError("Invalid monitoring configuration: retention 1–365 days")
CONFIG = config.load(max_window_hours=RETENTION * 24)
TARGETS = {target.id: target for target in CONFIG.targets}
MAX_BODY_BYTES = 256 * 1024
USER_AGENT = "CloudGuard/2.0 (read-only uptime monitor)"
# Two new connections opened at the same instant to the RoadWatch server were measured taking
# about 1 s longer than one alone, so each target's first probe is offset to keep probes apart.
STAGGER_SECONDS = 1.5
MISSING = object()


def lookup(payload, path):
    value = payload
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return MISSING
        value = value[key]
    return value


def same(value, expected):
    # JSON true must not match 1: compare booleans by identity.
    if isinstance(value, bool) or isinstance(expected, bool):
        return value is expected
    return value == expected


def short(value):
    if value is MISSING:
        return "missing"
    return json.dumps(value)[:60]


def check(target, status_code, body, metrics):
    """None when the response meets every expectation, otherwise a short, safe reason."""
    if target.expect_status:
        if status_code not in target.expect_status:
            return f"HTTP {status_code} (expected {' or '.join(map(str, target.expect_status))})"
    elif not 200 <= status_code < 300:
        return f"HTTP {status_code}"
    text = body.decode("utf-8", errors="replace")
    for needle in target.body_contains:
        if needle not in text:
            return f"Response is missing {needle!r}"
    for needle in target.body_excludes:
        if needle in text:
            return f"Response contains forbidden text {needle!r}"
    if not (target.expect_json or target.metrics):
        return None
    try:
        payload = json.loads(text)
    except ValueError:
        return "Invalid JSON response"
    if not isinstance(payload, dict):
        return "Invalid health response"
    # Metrics are kept even when an expectation fails: a slow database is still worth recording.
    for name, path in target.metrics.items():
        value = lookup(payload, path)
        if isinstance(value, bool):
            value = int(value)
        if isinstance(value, (int, float)):
            metrics[name] = value
    for path, allowed in target.expect_json.items():
        value = lookup(payload, path)
        if not any(same(value, option) for option in allowed):
            return f"{path} is {short(value)}, expected {' or '.join(short(option) for option in allowed)}"
    return None


def tls_days_left(response):
    try:
        stream = response.extensions.get("network_stream")
        ssl_object = stream.get_extra_info("ssl_object") if stream is not None else None
        not_after = (ssl_object.getpeercert() or {}).get("notAfter") if ssl_object is not None else None
        return round((ssl.cert_time_to_seconds(not_after) - time.time()) / 86400, 1) if not_after else None
    except (AttributeError, ValueError):
        return None


async def read_limited(response):
    chunks, size = [], 0
    async for chunk in response.aiter_bytes():
        chunks.append(chunk)
        size += len(chunk)
        if size >= MAX_BODY_BYTES:
            break
    return b"".join(chunks)[:MAX_BODY_BYTES]


async def probe(client, target):
    started = time.perf_counter()
    healthy, error, status_code, metrics = False, None, None, {}
    try:
        async with asyncio.timeout(target.timeout):
            async with client.stream(target.method, target.url, headers=target.headers,
                                     timeout=target.timeout) as response:
                status_code = response.status_code
                days = tls_days_left(response)
                if days is not None:
                    metrics[config.TLS_METRIC] = days
                body = await read_limited(response)
        error = check(target, status_code, body, metrics)
        healthy = error is None
    except (httpx.TimeoutException, TimeoutError):
        error = "Health check timed out"
    except httpx.ConnectError as exc:
        error = "TLS certificate rejected" if "CERTIFICATE_VERIFY_FAILED" in str(exc) else "Connection failed"
    except httpx.RequestError:
        error = "Connection failed"
    return {"target": target.id, "timestamp": time.time(), "healthy": healthy,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3), "error": error,
            "status_code": status_code, "metrics": metrics}


async def run_target(app, client, target, delay):
    await asyncio.sleep(delay)
    while True:
        started = time.monotonic()
        try:
            sample = await probe(client, target)
            await asyncio.to_thread(app.state.store.record_batch, [sample])
            app.state.last_recorded[target.id] = time.time()
        except Exception:
            logger.exception("Monitoring cycle failed for %s", target.id)
        await asyncio.sleep(max(0.1, target.interval - (time.monotonic() - started)))


async def housekeeping(app):
    while True:
        await asyncio.sleep(60)
        try:
            await asyncio.to_thread(app.state.store.prune, RETENTION)
        except Exception:
            logger.exception("Retention cleanup failed")


@asynccontextmanager
async def lifespan(app):
    app.state.store = Store(os.getenv("DB_PATH", "/data/cloudguard.db"))
    app.state.last_recorded = {}
    delays = {target.id: (index * STAGGER_SECONDS) % target.interval for index, target in enumerate(CONFIG.targets)}
    app.state.first_due = {target_id: time.time() + delay for target_id, delay in delays.items()}
    # Fresh probe connections measure what a new visitor sees (DNS, TCP, TLS) and avoid racing
    # Uvicorn's 5s idle keep-alive timeout in the lab, whose default sampling period is also 5s.
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False, headers={"User-Agent": USER_AGENT},
                                 limits=httpx.Limits(max_keepalive_connections=0)) as client:
        tasks = [asyncio.create_task(run_target(app, client, target, delays[target.id])) for target in CONFIG.targets]
        tasks.append(asyncio.create_task(housekeeping(app)))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="CloudGuard Monitoring", version="2.0.0", lifespan=lifespan,
              docs_url=None, redoc_url=None)


@app.exception_handler(Exception)
async def unexpected_error(request, exc):
    from fastapi.responses import JSONResponse
    logger.error("Monitoring API request failed", exc_info=exc)
    return JSONResponse(status_code=503, content={"detail": "Monitoring temporarily unavailable"})


def known_target(target_id):
    if target_id not in TARGETS:
        raise HTTPException(422, f"Unknown target. Configured targets: {', '.join(TARGETS)}")
    return TARGETS[target_id]


@app.get("/health")
def health():
    """Healthy while every target has completed a probe within its own staleness window.

    A target that has not been probed yet counts from its first scheduled probe, so a slow first
    answer (a sleeping free-tier host) does not mark a freshly started worker unhealthy.
    """
    now = time.time()
    recorded, first_due = app.state.last_recorded, getattr(app.state, "first_due", {})
    late = [target.id for target in CONFIG.targets
            if now - recorded.get(target.id, first_due.get(target.id, 0)) > target.stale_after]
    if late:
        raise HTTPException(503, "Monitoring worker has not completed a recent cycle")
    return {"status": "healthy", "last_cycle": max(recorded.values(), default=None), "targets": len(recorded)}


@app.get("/targets")
def targets():
    return CONFIG.public()


@app.get("/summary")
def summary(hours: int = Query(default=24, ge=1, le=168)):
    result = app.state.store.summary(hours, CONFIG.targets, CONFIG.planes)
    result.update(retention_days=RETENTION)
    return result


@app.get("/history")
def history(target: str | None = Query(default=None, max_length=40),
            hours: int = Query(default=24, ge=1, le=168),
            metric: str | None = Query(default=None, max_length=40)):
    target = target or CONFIG.planes[0].headline
    configured = known_target(target)
    if metric is not None and metric not in configured.metric_names:
        raise HTTPException(422, f"{target} records no metric {metric!r}")
    unit = "ms" if metric is None else metric_unit(metric)
    return {"target": target, "metric": metric, "unit": unit,
            "points": app.state.store.history(target, hours, metric=metric)}


@app.get("/slos")
def slos():
    return slo_math.evaluate_all(app.state.store, CONFIG)


@app.get("/incidents")
def incidents(limit: int = Query(default=30, ge=1, le=100)):
    return {"events": app.state.store.events(limit)}


@app.get("/audit")
def audit(limit: int = Query(default=30, ge=1, le=100)):
    return {"actions": app.state.store.audit(limit)}
