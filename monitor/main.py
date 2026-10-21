"""A single monitoring worker probes replicas and the actual NGINX route independently."""
import asyncio
from contextlib import asynccontextmanager
import logging
import os
import time

import httpx
from fastapi import FastAPI, Query, HTTPException
from storage import Store

logger = logging.getLogger("cloudguard.monitor")
INTERVAL = float(os.getenv("CHECK_INTERVAL_SECONDS", "5"))
TIMEOUT = float(os.getenv("CHECK_TIMEOUT_SECONDS", "2"))
RETENTION = int(os.getenv("RETENTION_DAYS", "7"))
if not (1 <= INTERVAL <= 300 and 0.1 <= TIMEOUT <= 30 and 1 <= RETENTION <= 365):
    raise ValueError("Invalid monitoring configuration: interval 1–300s, timeout 0.1–30s, retention 1–365 days")
STALE_AFTER = max(15, INTERVAL * 3 + TIMEOUT)
URLS = {
    "app-1": os.getenv("APP1_HEALTH_URL", "http://app1:8000/health"),
    "app-2": os.getenv("APP2_HEALTH_URL", "http://app2:8000/health"),
    "gateway": os.getenv("GATEWAY_HEALTH_URL", "http://nginx/api/app/health"),
}


async def probe(client, target, url):
    started = time.perf_counter()
    healthy, error = False, None
    try:
        response = await client.get(url)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("status") != "healthy":
            error = "Invalid health response"
        elif payload.get("instance") not in ((target,) if target != "gateway" else ("app-1", "app-2")):
            error = "Unexpected instance identity"
        else:
            healthy = True
    except httpx.TimeoutException:
        error = "Health check timed out"
    except httpx.HTTPStatusError as exc:
        error = f"HTTP {exc.response.status_code}"
    except httpx.RequestError:
        error = "Connection failed"
    except (ValueError, TypeError):
        error = "Invalid health response"
    return {"target": target, "timestamp": time.time(), "healthy": healthy,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3), "error": error}


async def worker(app):
    # Fresh probe connections avoid racing Uvicorn's 5s idle keep-alive timeout
    # when our default sampling period is also 5s.
    async with httpx.AsyncClient(timeout=TIMEOUT, trust_env=False, follow_redirects=False,
                                 limits=httpx.Limits(max_keepalive_connections=0)) as client:
        while True:
            started = time.monotonic()
            try:
                samples = await asyncio.gather(*(probe(client, name, url) for name, url in URLS.items()))
                await asyncio.to_thread(app.state.store.record_batch, samples, RETENTION)
                app.state.last_cycle = time.time()
            except Exception:
                logger.exception("Monitoring cycle failed")
            await asyncio.sleep(max(0.1, INTERVAL - (time.monotonic() - started)))


@asynccontextmanager
async def lifespan(app):
    app.state.store = Store(os.getenv("DB_PATH", "/data/cloudguard.db"))
    app.state.last_cycle = None
    task = asyncio.create_task(worker(app))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="CloudGuard Monitoring", version="1.0.0", lifespan=lifespan,
              docs_url=None, redoc_url=None)


@app.exception_handler(Exception)
async def unexpected_error(request, exc):
    from fastapi.responses import JSONResponse
    logger.error("Monitoring API request failed", exc_info=exc)
    return JSONResponse(status_code=503, content={"detail": "Monitoring temporarily unavailable"})


@app.get("/health")
def health():
    last = app.state.last_cycle
    if last is None or time.time() - last > STALE_AFTER:
        raise HTTPException(503, "Monitoring worker has not completed a recent cycle")
    return {"status": "healthy", "last_cycle": last}


@app.get("/summary")
def summary(hours: int = Query(default=24, ge=1, le=168)):
    result = app.state.store.summary(hours, STALE_AFTER)
    result.update(check_interval_seconds=INTERVAL, stale_after_seconds=STALE_AFTER, retention_days=RETENTION)
    return result


@app.get("/history")
def history(target: str = Query(default="gateway", pattern="^(app-1|app-2|gateway)$"),
            hours: int = Query(default=24, ge=1, le=168)):
    return {"target": target, "points": app.state.store.history(target, hours)}


@app.get("/incidents")
def incidents(limit: int = Query(default=30, ge=1, le=100)):
    return {"events": app.state.store.events(limit)}


@app.get("/audit")
def audit(limit: int = Query(default=30, ge=1, le=100)):
    return {"actions": app.state.store.audit(limit)}
