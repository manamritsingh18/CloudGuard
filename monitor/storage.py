"""SQLite storage and sample-based metrics. All persisted times are UTC epoch seconds."""
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
import time

LAB_TARGETS = ("app-1", "app-2")
METRIC_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def plane_status(statuses):
    """statuses: (status, critical) pairs. A failing critical target is an outage."""
    if not statuses or any(status in ("stale", "unknown") for status, _ in statuses):
        return "unknown"
    if any(status != "healthy" and critical for status, critical in statuses):
        return "outage"
    if any(status != "healthy" for status, _ in statuses):
        return "degraded"
    return "healthy"


def metric_unit(name):
    if name.endswith("_ms"):
        return "ms"
    if name.endswith("_s"):
        return "s"
    if "days" in name:
        return "days"
    return ""


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS samples (
                    id INTEGER PRIMARY KEY, target TEXT NOT NULL, timestamp REAL NOT NULL,
                    healthy INTEGER NOT NULL, latency_ms REAL NOT NULL, error TEXT,
                    status_code INTEGER, metrics TEXT);
                CREATE INDEX IF NOT EXISTS samples_target_time ON samples(target, timestamp);
                CREATE INDEX IF NOT EXISTS samples_time ON samples(timestamp);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, target TEXT NOT NULL, timestamp REAL NOT NULL,
                    kind TEXT NOT NULL, message TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS events_time ON events(timestamp);
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY, timestamp REAL NOT NULL, target TEXT NOT NULL,
                    action TEXT NOT NULL, outcome TEXT NOT NULL, actor TEXT NOT NULL);
            """)
            # Volumes created by CloudGuard 1.x keep their history; only the new columns are added.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(samples)")}
            for column, kind in (("status_code", "INTEGER"), ("metrics", "TEXT")):
                if column not in columns:
                    db.execute(f"ALTER TABLE samples ADD COLUMN {column} {kind}")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def record_batch(self, samples, retention_days=None):
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
                metrics = sample.get("metrics")
                db.execute("INSERT INTO samples(target,timestamp,healthy,latency_ms,error,status_code,metrics) "
                           "VALUES (?,?,?,?,?,?,?)",
                           (sample["target"], sample["timestamp"], healthy, sample["latency_ms"], sample["error"],
                            sample.get("status_code"), json.dumps(metrics) if metrics else None))
        if retention_days is not None:
            self.prune(retention_days)

    def prune(self, retention_days):
        cutoff = time.time() - retention_days * 86400
        with self.connect() as db:
            db.execute("DELETE FROM samples WHERE timestamp < ?", (cutoff,))
            db.execute("DELETE FROM events WHERE timestamp < ?", (cutoff,))
            db.execute("DELETE FROM audit WHERE timestamp < ?", (cutoff,))

    def record_action(self, target, action, outcome):
        if target not in LAB_TARGETS or action not in ("stop", "start", "crash") or outcome not in ("requested", "succeeded", "failed"):
            raise ValueError("Invalid audit action")
        with self.connect() as db:
            db.execute("INSERT INTO audit(timestamp,target,action,outcome,actor) VALUES (?,?,?,?,?)",
                       (time.time(), target, action, outcome, "local-cli"))

    def audit(self, limit):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM audit ORDER BY timestamp DESC,id DESC LIMIT ?", (limit,))]

    def summary(self, hours, targets, planes=(), now=None):
        """targets: config.Target objects; planes: config.Plane objects to roll statuses up into."""
        now = time.time() if now is None else now
        results = []
        with self.connect() as db:
            for target in targets:
                totals = dict(db.execute("""
                    SELECT COUNT(*) total_checks, COALESCE(SUM(healthy),0) successful_checks,
                      AVG(CASE WHEN healthy=1 THEN latency_ms END) average_latency_ms,
                      MIN(timestamp) window_start, MAX(timestamp) window_end,
                      MAX(CASE WHEN healthy=1 THEN timestamp END) last_success
                    FROM samples WHERE target=? AND timestamp>=?
                """, (target.id, now - hours * 3600)).fetchone())
                latest = db.execute("SELECT * FROM samples WHERE target=? ORDER BY timestamp DESC,id DESC LIMIT 1",
                                    (target.id,)).fetchone()
                total = totals["total_checks"]
                status = "unknown"
                if latest:
                    status = "stale" if now - latest["timestamp"] > target.stale_after else ("healthy" if latest["healthy"] else "unhealthy")
                results.append({
                    "target": target.id, "name": target.name, "plane": target.plane, "critical": target.critical,
                    "provider": target.provider, "service_model": target.service_model,
                    "interval_seconds": target.interval, **totals, "status": status,
                    "failed_checks": total - totals["successful_checks"],
                    "availability_percent": 100 * totals["successful_checks"] / total if total else None,
                    "current_latency_ms": latest["latency_ms"] if latest and latest["healthy"] and status != "stale" else None,
                    "last_check": latest["timestamp"] if latest else None,
                    "last_error": latest["error"] if latest else None,
                    "last_status_code": latest["status_code"] if latest else None,
                    "latest_metrics": json.loads(latest["metrics"]) if latest and latest["metrics"] else {},
                })
        rollup = []
        for plane in planes:
            members = [item for item in results if item["plane"] == plane.id]
            rollup.append({"id": plane.id, "name": plane.name, "description": plane.description,
                           "headline": plane.headline,
                           "status": plane_status([(item["status"], item["critical"]) for item in members]),
                           "failing": [item["target"] for item in members if item["status"] != "healthy"]})
        return {"generated_at": now, "requested_window_hours": hours, "planes": rollup, "targets": results}

    def history(self, target, hours, now=None, metric=None):
        """At most 120 points of the average successful latency, or of one recorded metric."""
        now = time.time() if now is None else now
        start = now - hours * 3600
        if metric is None:
            value, where = "AVG(CASE WHEN healthy=1 THEN latency_ms END)", ""
            params = ()
        else:
            if not METRIC_PATTERN.match(metric):
                raise ValueError("Invalid metric name")
            value, where = "AVG(json_extract(metrics, ?))", " AND json_extract(metrics, ?) IS NOT NULL"
            params = (f"$.{metric}",)
        # A null value means that bucket had no successful sample (or no metric).
        with self.connect() as db:
            first = db.execute(f"SELECT MIN(timestamp) FROM samples WHERE target=? AND timestamp>=?{where}",
                               (target, start, *params)).fetchone()[0]
            # Adapt to the actual observed period, so a newly started lab has a useful chart.
            start = first if first is not None else start
            width = max(1, (now - start) / 119.999)
            rows = db.execute(f"""
                SELECT MIN(timestamp) timestamp, MAX(timestamp) last_timestamp, {value} value,
                  COUNT(*) checks, SUM(1-healthy) failures
                FROM samples WHERE target=? AND timestamp>=?{where}
                GROUP BY CAST((timestamp-?)/? AS INTEGER) ORDER BY timestamp
            """, (*params, target, start, *params, start, width)).fetchall()
        return [dict(row) for row in rows]

    def sli_counts(self, slo, start):
        """Good and valid checks for one SLO since start, plus the observed period."""
        if slo.objective == "availability":
            good, valid, params = "SUM(healthy)", "COUNT(*)", ()
        elif slo.objective == "latency":
            good, valid = "SUM(CASE WHEN healthy=1 AND latency_ms<=? THEN 1 ELSE 0 END)", "SUM(healthy)"
            params = (slo.threshold_ms,)
        else:
            checks = ["v IS NOT NULL"]
            params = ()
            if slo.min_value is not None:
                checks.append("v>=?")
                params += (slo.min_value,)
            if slo.max_value is not None:
                checks.append("v<=?")
                params += (slo.max_value,)
            good, valid = f"SUM(CASE WHEN {' AND '.join(checks)} THEN 1 ELSE 0 END)", "COUNT(v)"
        source = "samples" if slo.objective != "metric" else \
            "(SELECT *, json_extract(metrics, ?) v FROM samples)"
        source_params = () if slo.objective != "metric" else (f"$.{slo.metric}",)
        with self.connect() as db:
            row = db.execute(f"""
                SELECT COALESCE({good},0) good, COALESCE({valid},0) valid,
                  MIN(timestamp) first, MAX(timestamp) last
                FROM {source} WHERE target=? AND timestamp>=?
            """, (*params, *source_params, slo.target, start)).fetchone()
        return dict(row)

    def events(self, limit):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM events ORDER BY timestamp DESC,id DESC LIMIT ?", (limit,))]
