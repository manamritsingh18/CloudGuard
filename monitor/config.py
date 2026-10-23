"""Probe targets, planes and SLOs come from targets.yaml, validated once when the monitor starts.

${NAME} and ${NAME:-default} in string values are replaced from the environment (the default is
used when NAME is unset or empty, as in a shell). Every problem in the file is reported at once.
"""
from dataclasses import dataclass, field
import base64
import json
import os
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlsplit

import yaml

DEFAULT_PATH = Path(__file__).with_name("targets.yaml")
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
METRIC_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
OBJECTIVES = ("availability", "latency", "metric")
TLS_METRIC = "tls_days_left"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Plane:
    id: str
    name: str
    description: str
    headline: str


@dataclass(frozen=True)
class Target:
    id: str
    name: str
    plane: str
    url: str
    interval: float
    timeout: float
    critical: bool = True
    provider: str = ""
    service_model: str = ""
    description: str = ""
    method: str = "GET"
    headers: dict = field(default_factory=dict)
    expect_status: tuple = ()
    expect_json: dict = field(default_factory=dict)
    body_contains: tuple = ()
    body_excludes: tuple = ()
    metrics: dict = field(default_factory=dict)

    @property
    def stale_after(self):
        return max(15.0, self.interval * 3 + self.timeout)

    @property
    def metric_names(self):
        names = list(self.metrics)
        if self.url.startswith("https://"):
            names.append(TLS_METRIC)
        return names

    def public(self):
        """Everything the dashboard may see. Headers are never returned: they can hold keys."""
        return {"id": self.id, "name": self.name, "plane": self.plane, "url": self.url,
                "critical": self.critical, "provider": self.provider, "service_model": self.service_model,
                "description": self.description, "interval_seconds": self.interval,
                "timeout_seconds": self.timeout, "stale_after_seconds": self.stale_after,
                "metrics": self.metric_names}


@dataclass(frozen=True)
class Slo:
    id: str
    name: str
    target: str
    objective: str
    goal_percent: float
    window_hours: float
    threshold_ms: float = None
    metric: str = None
    min_value: float = None
    max_value: float = None
    sla_percent: float = None
    burn_window_hours: float = 1.0
    description: str = ""

    def public(self):
        return {"id": self.id, "name": self.name, "target": self.target, "objective": self.objective,
                "goal_percent": self.goal_percent, "window_hours": self.window_hours,
                "threshold_ms": self.threshold_ms, "metric": self.metric, "min": self.min_value,
                "max": self.max_value, "sla_percent": self.sla_percent,
                "burn_window_hours": self.burn_window_hours, "description": self.description}


@dataclass(frozen=True)
class Config:
    planes: tuple
    targets: tuple
    slos: tuple

    def target(self, target_id):
        return next((target for target in self.targets if target.id == target_id), None)

    def public(self):
        return {"planes": [vars(plane) for plane in self.planes],
                "targets": [target.public() for target in self.targets],
                "slos": [slo.public() for slo in self.slos]}


def substitute(value, env):
    if isinstance(value, str):
        def replace(match):
            name, default = match.group(1), match.group(2)
            if env.get(name):
                return env[name]
            if default is not None:
                return default
            raise ConfigError(f"Environment variable {name} is not set and has no default")
        return PLACEHOLDER.sub(replace, value)
    if isinstance(value, list):
        return [substitute(item, env) for item in value]
    if isinstance(value, dict):
        return {key: substitute(item, env) for key, item in value.items()}
    return value


def looks_secret(value):
    """A Supabase secret key, or a JWT whose role claim is service_role: both bypass RLS."""
    value = str(value).strip()
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    if value.startswith("sb_secret_"):
        return True
    parts = value.split(".")
    if len(parts) != 3:
        return False
    try:
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except ValueError:
        return False
    return isinstance(claims, dict) and claims.get("role") == "service_role"


class _Reader:
    """Collects every problem instead of stopping at the first, so one run fixes the whole file."""

    def __init__(self):
        self.problems = []

    def number(self, where, value, low, high, default=None):
        if value is None:
            if default is None:
                self.problems.append(f"{where}: is required")
                return None
            value = default
        try:
            number = float(value)
        except (TypeError, ValueError):
            self.problems.append(f"{where}: {value!r} is not a number")
            return default
        if isinstance(value, bool) or not low <= number <= high:
            self.problems.append(f"{where}: {value!r} must be between {low:g} and {high:g}")
            return default
        return number

    def text(self, where, value, default=""):
        if value is None:
            return default
        if not isinstance(value, str):
            self.problems.append(f"{where}: must be text")
            return default
        return value.strip()

    def texts(self, where, value):
        values = [] if value is None else value if isinstance(value, list) else [value]
        if not all(isinstance(item, str) and item for item in values):
            self.problems.append(f"{where}: must be text or a list of text")
            return ()
        return tuple(values)

    def identifier(self, where, value, seen):
        if not isinstance(value, str) or not ID_PATTERN.match(value):
            self.problems.append(f"{where}: id must be 1-40 lowercase letters, digits or dashes")
            return None
        if value in seen:
            self.problems.append(f"{where}: duplicate id {value!r}")
        seen.add(value)
        return value


def parse(raw, env=None, max_window_hours=720):
    env = os.environ if env is None else env
    if not isinstance(raw, dict):
        raise ConfigError("Targets file must be a mapping with planes, targets and slos")
    raw = substitute(raw, env)
    read = _Reader()
    planes, targets, slos = [], [], []

    plane_ids = set()
    for index, item in enumerate(raw.get("planes") or []):
        where = f"planes[{index}]"
        if not isinstance(item, dict):
            read.problems.append(f"{where}: must be a mapping")
            continue
        plane_id = read.identifier(where, item.get("id"), plane_ids)
        planes.append(dict(id=plane_id, name=read.text(f"{where}.name", item.get("name"), plane_id),
                           description=read.text(f"{where}.description", item.get("description")),
                           headline=item.get("headline")))
    if not planes:
        read.problems.append("planes: at least one plane is required")

    target_ids = set()
    for index, item in enumerate(raw.get("targets") or []):
        where = f"targets[{index}]"
        if not isinstance(item, dict):
            read.problems.append(f"{where}: must be a mapping")
            continue
        target_id = read.identifier(where, item.get("id"), target_ids)
        where = f"targets.{target_id or index}"
        plane = item.get("plane")
        if plane not in plane_ids:
            read.problems.append(f"{where}.plane: {plane!r} is not one of {sorted(plane_ids)}")
        url = item.get("url")
        parts = urlsplit(url) if isinstance(url, str) else None
        if not parts or parts.scheme not in ("http", "https") or not parts.hostname:
            read.problems.append(f"{where}.url: must be an http(s) URL")
        elif any(looks_secret(value) for _, value in parse_qsl(parts.query)):
            read.problems.append(f"{where}.url: contains a secret key; CloudGuard only uses public keys")
        interval = read.number(f"{where}.interval_seconds", item.get("interval_seconds"), 1, 3600)
        timeout = read.number(f"{where}.timeout_seconds", item.get("timeout_seconds"), 0.1, 60)
        if interval and timeout and timeout > interval:
            read.problems.append(f"{where}.timeout_seconds: must not exceed interval_seconds")
        method = str(item.get("method", "GET")).upper()
        if method not in ("GET", "HEAD"):
            read.problems.append(f"{where}.method: only GET and HEAD are allowed (probes never change data)")
        critical = item.get("critical", True)
        if not isinstance(critical, bool):
            read.problems.append(f"{where}.critical: must be true or false")

        headers = item.get("headers") or {}
        if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
            read.problems.append(f"{where}.headers: must map names to text values")
            headers = {}
        for name, value in headers.items():
            if looks_secret(value):
                read.problems.append(f"{where}.headers.{name}: is a secret or service_role key; "
                                     "CloudGuard only needs the publishable key")

        expect = item.get("expect") or {}
        if not isinstance(expect, dict):
            read.problems.append(f"{where}.expect: must be a mapping")
            expect = {}
        unknown = set(expect) - {"status", "json", "body_contains", "body_excludes"}
        if unknown:
            read.problems.append(f"{where}.expect: unknown keys {sorted(unknown)}")
        statuses = expect.get("status")
        statuses = () if statuses is None else tuple(statuses) if isinstance(statuses, list) else (statuses,)
        if not all(isinstance(code, int) and not isinstance(code, bool) and 100 <= code <= 599 for code in statuses):
            read.problems.append(f"{where}.expect.status: must be HTTP status codes")
            statuses = ()
        expect_json = {}
        for path, allowed in (expect.get("json") or {}).items():
            allowed = tuple(allowed) if isinstance(allowed, list) else (allowed,)
            if not isinstance(path, str) or not path or not all(
                    value is None or isinstance(value, (str, int, float, bool)) for value in allowed):
                read.problems.append(f"{where}.expect.json.{path}: values must be plain values or lists of them")
                continue
            expect_json[path] = allowed

        metrics = item.get("metrics") or {}
        if not isinstance(metrics, dict):
            read.problems.append(f"{where}.metrics: must map metric names to JSON paths")
            metrics = {}
        for name, path in metrics.items():
            if not isinstance(name, str) or not METRIC_PATTERN.match(name) or name == TLS_METRIC:
                read.problems.append(f"{where}.metrics.{name}: names are lowercase_with_underscores "
                                     f"and {TLS_METRIC} is reserved")
            if not isinstance(path, str) or not path:
                read.problems.append(f"{where}.metrics.{name}: path must be text")

        targets.append(Target(
            id=target_id, name=read.text(f"{where}.name", item.get("name"), target_id), plane=plane,
            url=url, interval=interval, timeout=timeout, critical=critical is True,
            provider=read.text(f"{where}.provider", item.get("provider")),
            service_model=read.text(f"{where}.service_model", item.get("service_model")),
            description=read.text(f"{where}.description", item.get("description")), method=method,
            headers=dict(headers), expect_status=statuses, expect_json=expect_json,
            body_contains=read.texts(f"{where}.expect.body_contains", expect.get("body_contains")),
            body_excludes=read.texts(f"{where}.expect.body_excludes", expect.get("body_excludes")),
            metrics=dict(metrics)))
    if not targets:
        read.problems.append("targets: at least one target is required")

    by_id = {target.id: target for target in targets}
    for plane in planes:
        members = [target for target in targets if target.plane == plane["id"]]
        if not members:
            read.problems.append(f"planes.{plane['id']}: has no targets")
        elif plane["headline"] is None:
            plane["headline"] = members[0].id
        elif plane["headline"] not in {target.id for target in members}:
            read.problems.append(f"planes.{plane['id']}.headline: must be a target in this plane")

    slo_ids = set()
    for index, item in enumerate(raw.get("slos") or []):
        where = f"slos[{index}]"
        if not isinstance(item, dict):
            read.problems.append(f"{where}: must be a mapping")
            continue
        slo_id = read.identifier(where, item.get("id"), slo_ids)
        where = f"slos.{slo_id or index}"
        target = by_id.get(item.get("target"))
        if target is None:
            read.problems.append(f"{where}.target: {item.get('target')!r} is not a target id")
        objective = item.get("objective")
        if objective not in OBJECTIVES:
            read.problems.append(f"{where}.objective: must be one of {list(OBJECTIVES)}")
        if str(item.get("goal_percent")).strip() in ("100", "100.0"):
            read.problems.append(f"{where}.goal_percent: 100% leaves no error budget; pick a goal below 100")
            goal = None
        else:
            goal = read.number(f"{where}.goal_percent", item.get("goal_percent"), 50, 99.999)
        window = read.number(f"{where}.window_hours", item.get("window_hours"), 1, max_window_hours)
        sla = read.number(f"{where}.sla_percent", item.get("sla_percent"), 1, 99.999) \
            if item.get("sla_percent") is not None else None
        if sla is not None and goal is not None and sla > goal:
            read.problems.append(f"{where}.sla_percent: the promise to users must not be stricter than the goal")
        burn = read.number(f"{where}.burn_window_hours", item.get("burn_window_hours"), 0.25,
                           window or max_window_hours, default=1.0)
        threshold = metric = low = high = None
        if objective == "latency":
            threshold = read.number(f"{where}.threshold_ms", item.get("threshold_ms"), 1, 600000)
        elif objective == "metric":
            metric = item.get("metric")
            if target is not None and metric not in target.metric_names:
                read.problems.append(f"{where}.metric: {metric!r} is not recorded by {target.id} "
                                     f"(available: {target.metric_names})")
            low = read.number(f"{where}.min", item.get("min"), -1e12, 1e12) if item.get("min") is not None else None
            high = read.number(f"{where}.max", item.get("max"), -1e12, 1e12) if item.get("max") is not None else None
            if low is None and high is None:
                read.problems.append(f"{where}: a metric objective needs min, max or both")
        slos.append(Slo(id=slo_id, name=read.text(f"{where}.name", item.get("name"), slo_id),
                        target=item.get("target"), objective=objective, goal_percent=goal,
                        window_hours=window, threshold_ms=threshold, metric=metric, min_value=low,
                        max_value=high, sla_percent=sla, burn_window_hours=burn,
                        description=read.text(f"{where}.description", item.get("description"))))

    if read.problems:
        raise ConfigError("Invalid targets file:\n- " + "\n- ".join(read.problems))
    return Config(planes=tuple(Plane(**plane) for plane in planes), targets=tuple(targets), slos=tuple(slos))


def load(path=None, env=None, max_window_hours=720):
    env = os.environ if env is None else env
    path = Path(path or env.get("TARGETS_FILE") or DEFAULT_PATH)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"Cannot read targets file {path}: {exc.strerror}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Targets file {path} is not valid YAML: {exc}") from exc
    return parse(raw, env, max_window_hours)
