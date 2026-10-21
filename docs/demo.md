# Five-minute presentation and viva sequence

## Before entering the room

Start Docker Desktop, run `docker compose up -d --build --wait --wait-timeout 180`,
and check `docker compose ps`. Open `http://localhost:8080/` and
`http://localhost:8080/dashboard`. Allow a few probe cycles. Keep PowerShell in the
project folder. Have the README and actual screenshots available as evidence.

## 0:00–1:00 — Explain the architecture

Use the landing page's component buttons. Explain that a browser request follows
Browser → NGINX → one replica. The monitor is a separate worker that checks both
replicas and the gateway, and persists samples in SQLite. Explain containers versus
full virtual machines, and why internal services do not need published host ports.

## 1:00–2:00 — Show live measurements

Open the dashboard and optionally enable focus mode. Inspect app-1. Change the
chart target to app-2, then the gateway. Send several application requests and read
the responding instance identities. Background probes also pass through the gateway,
so identities need not alternate perfectly for an individual user's requests.

Explain availability as successful probes / total probes in the displayed window,
and latency as the average of successful probe round trips. Browser request-test
latency has a separate label. A freshly started lab is not evidence of production uptime.

## 2:00–3:15 — Fail a replica

```powershell
.\scripts\replica.ps1 -Action stop -Service app1
```

Wait for app-1 to become unhealthy. Show the incident and the last successful
check. Send requests again: app-2 should serve them. The gateway's availability
ratio can remain high even when an individual replica's observed ratio falls.

## 3:15–4:00 — Recover

```powershell
.\scripts\replica.ps1 -Action start -Service app1
```

Wait for a successful app-1 sample, the recovery event, and healthy overall state.
Inspect recent events. Explain why failed samples remain rather than resetting
availability to 100% on recovery.

## 4:00–5:00 — Describe self-healing and limitations

Docker restarts an unexpectedly exited application process using `unless-stopped`.
An explicit operator stop intentionally stays stopped. NGINX routes around eligible
upstream failures, while the monitor records health; those are separate responsibilities.

If useful, show the optional crash command and Docker restart count from the README.
A fast restart can happen between probe samples, so inspect the audit trace and
restart count rather than promising that every crash appears in the health log.

## Short viva answers

| Question | Answer |
|---|---|
| Why two replicas? | A single replica can fail while the other continues serving requests. |
| What does NGINX do? | Reverse proxying, round-robin balancing, eligible retries, and static hosting. |
| What makes the data real? | Timestamped independent HTTP probes persisted in SQLite; no generated monitoring numbers. |
| Why SQLite? | Durable local storage with a small operational footprint; sufficient for a single-worker lab. |
| Does an unhealthy health check restart Docker? | No. A restart policy responds to process exit, not merely an unhealthy flag. |
| Is the SLA production uptime? | No. It is an observed probe success ratio over the actual displayed sample window. |
| What happens when the monitor is down? | New samples stop; the UI becomes unavailable or stale; missing time is not invented. |
| Is this highly available across hosts? | No. Docker Desktop, NGINX, monitoring, and SQLite each remain single-host dependencies. |
| How are administrative actions restricted? | Docker control stays in local PowerShell; no public action API or Docker socket is exposed. |
| Is there authentication? | No login/roles in this local core version. Local Docker access gates operator actions. |

Stop with `docker compose down` after the demonstration if desired. This preserves
the monitoring volume for later review.
