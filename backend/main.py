
from fastapi import FastAPI, Query
import os
import time

app = FastAPI(title="CloudGuard Application", version="1.0.0", docs_url=None, redoc_url=None)

INSTANCE_ID = os.getenv("INSTANCE_ID", "app-local")
START_TIME = time.monotonic()


@app.get("/")
def home():
    return {
        "project": "CloudGuard",
        "message": "CloudGuard is running!",
        "instance": INSTANCE_ID
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "instance": INSTANCE_ID,
        "uptime_seconds": round(time.monotonic() - START_TIME, 2)
    }


@app.get("/echo")
def echo(message: str = Query(min_length=1, max_length=200)):
    """Small validated application request for demonstrating load balancing."""
    return {"instance": INSTANCE_ID, "message": message}
