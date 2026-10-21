"""SQLite storage and sample-based metrics. All persisted times are UTC epoch seconds."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time

TARGETS = ("app-1", "app-2", "gateway")


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS samples (
                    id INTEGER PRIMARY KEY, target TEXT NOT NULL, timestamp REAL NOT NULL,
                    healthy INTEGER NOT NULL, latency_ms REAL NOT NULL, error TEXT);
                CREATE INDEX IF NOT EXISTS samples_target_time ON samples(target, timestamp);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, target TEXT NOT NULL, timestamp REAL NOT NULL,
                    kind TEXT NOT NULL, message TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS events_time ON events(timestamp);
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY, timestamp REAL NOT NULL, target TEXT NOT NULL,
                    action TEXT NOT NULL, outcome TEXT NOT NULL, actor TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def record_batch(self, samples, retention_days=7):
        with self.connect() as db:
            for sample in samples:
                previous = db.execute(
                    "SELECT healthy FROM samples WHERE target=? ORDER BY timestamp DESC, id DESC LIMIT 1",
                    (sample["target"],),
                ).fetchone()
                healthy = int(sample["healthy"])
                if (previous is None and not healthy) or (previous is not None and previous["healthy"] != healthy):
                    kind = "recovery" if healthy else "incident"
                    message = "Health check succeeded again" if healthy else sample["error"]
                    db.execute("INSERT INTO events(target,timestamp,kind,message) VALUES (?,?,?,?)",
                               (sample["target"], sample["timestamp"], kind, message))
                db.execute("INSERT INTO samples(target,timestamp,healthy,latency_ms,error) VALUES (?,?,?,?,?)",
                           (sample["target"], sample["timestamp"], healthy, sample["latency_ms"], sample["error"]))
            cutoff = time.time() - retention_days * 86400
            db.execute("DELETE FROM samples WHERE timestamp < ?", (cutoff,))
            db.execute("DELETE FROM events WHERE timestamp < ?", (cutoff,))
            db.execute("DELETE FROM audit WHERE timestamp < ?", (cutoff,))

    def record_action(self, target, action, outcome):
        if target not in ("app-1", "app-2") or action not in ("stop", "start", "crash") or outcome not in ("requested", "succeeded", "failed"):
            raise ValueError("Invalid audit action")
        with self.connect() as db:
            db.execute("INSERT INTO audit(timestamp,target,action,outcome,actor) VALUES (?,?,?,?,?)",
                       (time.time(), target, action, outcome, "local-cli"))

    def audit(self, limit):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM audit ORDER BY timestamp DESC,id DESC LIMIT ?", (limit,))]

    def summary(self, hours, stale_after, now=None):
        now = time.time() if now is None else now
        targets = []
        with self.connect() as db:
            for target in TARGETS:
                totals = dict(db.execute("""
                    SELECT COUNT(*) total_checks, COALESCE(SUM(healthy),0) successful_checks,
                      AVG(CASE WHEN healthy=1 THEN latency_ms END) average_latency_ms,
                      MIN(timestamp) window_start, MAX(timestamp) window_end,
                      MAX(CASE WHEN healthy=1 THEN timestamp END) last_success
                    FROM samples WHERE target=? AND timestamp>=?
                """, (target, now - hours * 3600)).fetchone())
                latest = db.execute("SELECT * FROM samples WHERE target=? ORDER BY timestamp DESC,id DESC LIMIT 1",
                                    (target,)).fetchone()
                total = totals["total_checks"]
                status = "unknown"
                if latest:
                    status = "stale" if now - latest["timestamp"] > stale_after else ("healthy" if latest["healthy"] else "unhealthy")
                targets.append({
                    "target": target, **totals, "status": status,
                    "failed_checks": total - totals["successful_checks"],
                    "availability_percent": 100 * totals["successful_checks"] / total if total else None,
                    "current_latency_ms": latest["latency_ms"] if latest and latest["healthy"] and status != "stale" else None,
                    "last_check": latest["timestamp"] if latest else None,
                    "last_error": latest["error"] if latest else None,
                })
        statuses = {item["target"]: item["status"] for item in targets}
        if "stale" in statuses.values() or "unknown" in statuses.values():
            overall = "unknown"
        elif statuses["gateway"] != "healthy":
            overall = "outage"
        elif all(value == "healthy" for value in statuses.values()):
            overall = "healthy"
        else:
            overall = "degraded"
        return {"generated_at": now, "requested_window_hours": hours, "overall_status": overall, "targets": targets}

    def history(self, target, hours, now=None):
        now = time.time() if now is None else now
        start = now - hours * 3600
        # At most 120 points. A null latency means that bucket had only failures.
        with self.connect() as db:
            first = db.execute("SELECT MIN(timestamp) FROM samples WHERE target=? AND timestamp>=?",
                               (target, start)).fetchone()[0]
            # Adapt to the actual observed period, so a newly started lab has a useful chart.
            start = first if first is not None else start
            width = max(1, (now - start) / 119.999)
            rows = db.execute("""
                SELECT MIN(timestamp) timestamp, MAX(timestamp) last_timestamp,
                  AVG(CASE WHEN healthy=1 THEN latency_ms END) latency_ms,
                  COUNT(*) checks, SUM(1-healthy) failures
                FROM samples WHERE target=? AND timestamp>=?
                GROUP BY CAST((timestamp-?)/? AS INTEGER) ORDER BY timestamp
            """, (target, start, start, width)).fetchall()
        return [dict(row) for row in rows]

    def events(self, limit):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM events ORDER BY timestamp DESC,id DESC LIMIT ?", (limit,))]
