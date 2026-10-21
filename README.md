# CloudGuard — Self-Healing Mini Cloud

A working local mini cloud with two FastAPI application replicas, NGINX load
balancing, independent health monitoring, persistent SQLite measurements, and a
responsive operations dashboard. No paid cloud, API account, or host Python installation
is required. Designed for Professional Elective-II: Virtualisation and Cloud Computing.

## Quick start (Windows PowerShell)

Install Docker Desktop with Linux containers and Compose enabled, and start its
engine. The first build needs internet access to download public images and Python
packages. Subsequent operation uses local services and local frontend assets.

```powershell
cd C:\Users\ApolloAdmin\OneDrive\Desktop\cloudguard
docker version
docker compose config --quiet
docker compose up -d --build --wait --wait-timeout 180
docker compose ps
```

Open the [landing page](http://localhost:8080/) or the
[operations dashboard](http://localhost:8080/dashboard). Allow about 5–15 seconds
for the first measurements. A genuine startup probe failure may be recorded while
NGINX is coming online; history is not reset or hidden.

Stop the stack while keeping measurements:

```powershell
docker compose down
```

Start it again with the same `up` command. The named volume survives normal `down`,
image rebuilds, and container replacement. Do not use `down -v` if you want to keep
history: that option deletes the database volume.

## Architecture

```mermaid
flowchart LR
    B[Browser: localhost:8080] --> N[NGINX: static pages + reverse proxy]
    N --> A1[app1:8000 / app-1]
    N --> A2[app2:8000 / app-2]
    N --> M[monitor:8000 / read-only metrics API]
    M -. health probe .-> A1
    M -. health probe .-> A2
    M -. gateway probe .-> N
    M --> DB[(SQLite / named Docker volume)]
```

Only NGINX publishes a port, bound to `127.0.0.1`. Other services communicate using
Compose DNS on a private bridge network. The gateway probe traverses NGINX's actual
application upstream, rather than treating a healthy replica as proof that the proxy
works. NGINX resolves service addresses dynamically after container replacement.

| Component | Responsibility |
|---|---|
| `backend/` | FastAPI `/`, `/health`, and validated `/echo`; visible replica identity |
| `monitor/` | One asynchronous probe worker, SQLite storage, metrics and incident APIs |
| `frontend/` | Static landing page and dashboard; local CSS, canvas chart, vanilla JavaScript |
| `nginx/` | Static hosting, round-robin upstreams, eligible error retry, security headers |
| `scripts/` | Local operator actions with persistent audit records; verification workflow |
| `tests/` | Isolated unit/API tests plus real running-stack checks |
| `docs/` | Demonstration guide, design references, and observed validation results |
| `screenshots/` | Real browser captures of the running system |

`database/` is reserved for future utilities. The active database is in the Docker
volume, not the OneDrive project folder, to avoid SQLite locking/sync conflicts.

## Dashboard interactions

- Live service and per-instance health, current response time, successful-probe
  average latency, observed availability, and check totals.
- Window selection (1 hour, 24 hours, 7 days) and chart target selection.
- Canvas chart inspection with pointer movement or keyboard left/right arrows after focus.
- Incident type/component filters and a scrollable recent-transition table.
- Per-instance detail dialogs, real application request testing, and a recovery guide.
- Dark/light themes and focus mode for an uncluttered operational view.
- Pause/resume automatic **UI refresh**; the worker continues collecting samples.
- An interactive architecture introduction at `/`, with subtle pointer highlights.

No frontend framework, external fonts, hosted JavaScript, or internet-dependent
chart library is required. Reduced-motion settings and touch devices disable
pointer effects. Decorative hero movement is labelled an architecture illustration,
not live traffic. All health colours and displayed metrics come from recorded probes.

## API routes

All URLs below use `http://localhost:8080`:

| URL | Returns |
|---|---|
| `/healthz` | NGINX liveness only; not proof that the application works |
| `/api/app/` | Application identity and message |
| `/api/app/health` | Responding replica's health and monotonic process uptime |
| `/api/app/echo?message=hello` | Validated message (1–200 characters) and replica identity |
| `/api/monitor/health` | Monitoring-worker liveness; 503 if no recent completed cycle |
| `/api/monitor/summary?hours=24` | Real aggregates, latest sample, last successful probe in window |
| `/api/monitor/history?target=gateway&hours=24` | Up to 120 adaptive time buckets |
| `/api/monitor/incidents?limit=30` | Persisted failure/recovery transitions, newest first |
| `/api/monitor/audit?limit=30` | Local operator actions, newest first |
| `/api/app/openapi.json` | Application API schema |
| `/api/monitor/openapi.json` | Monitoring API schema |

`hours` must be 1–168, `limit` 1–100, and history targets one of `app-1`, `app-2`,
or `gateway`. Invalid requests return 422. Interactive FastAPI docs are disabled
because their default UI downloads third-party JavaScript; JSON schemas work offline.

```powershell
Invoke-RestMethod http://localhost:8080/api/app/health
1..10 | ForEach-Object { (Invoke-RestMethod http://localhost:8080/api/app/).instance }
Invoke-RestMethod http://localhost:8080/api/monitor/summary | ConvertTo-Json -Depth 5
```

## Failure and recovery demonstration

Keep the dashboard open. Establish that both replicas are healthy, then:

```powershell
docker compose stop app1
# Wait about 5–15 seconds and inspect the dashboard.
1..10 | ForEach-Object { (Invoke-RestMethod http://localhost:8080/api/app/).instance }
docker compose start app1
# Wait for a successful probe and the app-1 recovery event.
```

While app1 is stopped, the expected state is **degraded**: app-1 is unhealthy but
the gateway remains available through app-2. After recovery, the current state is
healthy; historical failed checks remain in the calculation.

For the same operations **with operator audit records**, use:

```powershell
.\scripts\replica.ps1 -Action stop -Service app1
.\scripts\replica.ps1 -Action start -Service app1
```

The wrapper records requested and succeeded/failed actions in SQLite. Direct Docker
commands do not identify an operator to CloudGuard; their effects are recorded as
health transitions only. Audit records use the actor `local-cli`, not an authenticated
person. This is an operational trace, not a tamper-proof compliance log.

To demonstrate automatic restart after an **unexpected application process exit**:

```powershell
.\scripts\replica.ps1 -Action crash -Service app1
docker inspect --format '{{.RestartCount}}' cloudguard-app1-1
docker compose ps
```

The script kills only the Uvicorn child process inside the selected replica.
Docker's `unless-stopped` policy restarts the container. A brief crash can recover
between probes, so a new monitor incident is not guaranteed; the audit record and
Docker restart count provide separate evidence.

Three distinct mechanisms matter:

1. **Detection:** the monitor records a failing probe and one incident per transition.
2. **Failover:** NGINX retries another upstream for connection errors, timeouts, and
   configured 502/503/504 responses (at most two tries). This is not perfect failover
   for every possible request or failure mode; non-idempotent requests are not forcibly retried.
3. **Restart:** Docker restarts an unexpectedly exited process. A deliberately
   stopped container stays stopped until `docker compose start app1`.

An unhealthy Docker health check alone does **not** restart a running container.
This project does not mount the Docker socket or grant the monitor host control.

## How measurements are calculated

Each cycle independently probes app-1, app-2, and the NGINX application route.
Healthy means HTTP success, valid JSON, `status=healthy`, and the expected identity.
Failures include timeouts, connection errors, bad status, malformed JSON, and wrong identity.
Fresh probe connections avoid idle keep-alive timeout races.

- **Observed availability:** `100 × successful recorded checks / all recorded checks`
  for the target inside the selected rolling window. An empty window returns `null`,
  displayed as a dash. These are probe success ratios, not measured percentage of
  wall-clock uptime or a production SLA guarantee.
- **Average latency:** arithmetic mean of successful probe round-trip times only.
  Failed attempt duration is stored, but not included in successful-response averages.
- **Current latency:** the latest healthy, fresh probe; otherwise a dash. Dashboard
  top cards describe the **gateway**, while replica cards show their independent data.
- **Browser request latency:** separately labelled round-trip time measured by the
  request tester, not added to periodic probe totals.
- **Observation window:** first and last actual recorded sample in the selected period.
  Checks before a shutdown remain, but time while the monitor is off is unobserved.
  No successful checks are invented for gaps, and gaps are not counted as downtime.
- **History:** up to 120 buckets adapt to the actual observed period. Each bucket
  averages successful samples and reports total/failing checks. Red marks indicate
  failures; all-failure buckets have no successful latency.
- **Incidents:** an initial unhealthy sample or healthy→unhealthy transition; repeated
  failures do not duplicate events. Recovery means unhealthy→healthy, not initial startup success.
- **Overall status:** healthy if both replicas and gateway have fresh healthy samples;
  degraded if gateway is healthy but a replica fails; outage if gateway fails; unknown
  if any sample is missing or stale. Staleness is `max(15, 3 × interval + timeout)` seconds.

SQLite stores UTC epoch timestamps. The browser formats them in the viewer's local
timezone. The worker uses monotonic time for durations, WAL mode for SQLite, bounded
API parameters, and seven-day retention by default. Old records are pruned on each
successful recording cycle. A single worker owns writes; do not scale the monitor.

## Configuration

Defaults are in Compose and `.env.example`; no `.env` is required. To customize:

```powershell
Copy-Item .env.example .env
# Edit .env, then:
docker compose up -d --build --wait --wait-timeout 180
```

| Variable | Default | Allowed / meaning |
|---|---|---|
| `CLOUDGUARD_PORT` | 8080 | Free host TCP port; remains bound to localhost |
| `CHECK_INTERVAL_SECONDS` | 5 | 1–300 seconds |
| `CHECK_TIMEOUT_SECONDS` | 2 | 0.1–30 seconds per probe |
| `RETENTION_DAYS` | 7 | 1–365 days; UI window is capped at seven days |

After editing `nginx/nginx.conf`, run `docker compose exec nginx nginx -t`, then
`docker compose exec nginx nginx -s reload`. Static frontend changes need a browser
refresh. Backend and monitor source changes need an image rebuild.

## Tests

The test image includes Python, so the Windows Python launcher is not needed:

```powershell
docker build -f tests/Dockerfile -t cloudguard-tests .
docker run --rm cloudguard-tests
docker run --rm --network cloudguard_cloudguard cloudguard-tests python tests/live_check.py --expect healthy --requests 40
docker run --rm --network cloudguard_cloudguard cloudguard-tests python tests/stability.py
```

The last command observes a minute of real cycles and fails if any new failed probe
appears. Do not inject failures during that check.

For repeatable unit, smoke, and optional live failure tests:

```powershell
.\scripts\verify.ps1
.\scripts\verify.ps1 -FailureRecovery
```

The failure option temporarily stops app1, checks traffic via app2, and restores app1
in a `finally` block. See [actual validation results](docs/validation.md) and the
[five-minute submission sequence](docs/demo.md). Screenshot files are actual browser
captures; their values are historical observations, not promised current metrics.

## Security and limitations

- Python services run as non-root with read-only root filesystems, dropped capabilities,
  and no-new-privileges; SQLite alone has a writable named volume.
- Only localhost NGINX is published. No public hosting, cloud credentials, Docker socket,
  or browser administrative mutation endpoint is used.
- Input lengths and query values are constrained; SQL uses parameters. Monitoring errors
  return safe messages. NGINX sets CSP, framing protection, MIME protection, and no-store.
- Local Docker access is required for replica administration. Login and role-based
  authentication, encrypted transport, and the optional file vault are not implemented.
  Do not expose this local lab as a public production service without further hardening.
- One Docker Desktop host, one NGINX instance, one worker, and one database are single
  points of failure. Samples are periodic, so short failures can go undetected.
- No contractual SLA target, synthetic uptime, guaranteed request losslessness, or
  automatic healing of a running-but-unhealthy process is claimed.
- Dependencies are pinned at the direct-package level; full transitive locking, dependency
  scanning, TLS, authenticated audit identities, and scheduled database backups are future work.

## Troubleshooting

- Engine unavailable: start Docker Desktop, switch to Linux containers, and run
  `docker version` until the Server section is present. No project command changes system settings.
- Port busy: set `CLOUDGUARD_PORT` to a free port in `.env` and rerun `up`; use that port in URLs.
- Inspect errors: `docker compose ps`, `docker compose logs --tail 100 monitor nginx app1 app2`.
- Monitor unhealthy: check interval/timeout ranges, volume permissions, and worker logs.
- Missing history: the volume belongs to this Compose project (`cloudguard`); use the
  same project and do not remove its volume. Removing/recreating containers is safe.
- If PowerShell policy blocks scripts, use the documented direct Docker commands or
  seek an approved script-execution method; changing machine policy is not required.

## Academic concepts

Docker Desktop runs Linux containers on its Linux environment; containers isolate
processes while sharing that Linux kernel. This is OS-level virtualisation, distinct
from a full VM for every application. Two replicas demonstrate horizontal replication;
NGINX demonstrates reverse proxying, service discovery, and load balancing. Compose
provides repeatable provisioning; the network isolates internal services; a named
volume decouples data lifetime from containers. Probes, incident transitions, failover,
restart policy, and observed availability demonstrate monitoring, fault tolerance,
recovery, and honest SLA measurement within a local cloud-like system.
