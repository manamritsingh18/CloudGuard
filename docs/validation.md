# Observed validation — 21 October 2026

Tests ran on Windows with Docker Desktop's Linux engine. This document reports
observed results, not predicted results. The final stack was left running with all
four containers healthy.

## Final repeatable verification

Command: `.\scripts\verify.ps1 -FailureRecovery`.
The complete saved output is [verification-output.txt](verification-output.txt).

| Check | Observed result |
|---|---|
| Compose configuration | `docker compose config --quiet` passed |
| Image builds | Backend, monitor, NGINX pull, and test image succeeded |
| NGINX configuration | `nginx -t` reported syntax OK and successful test |
| Automated unit/API suite | **16 passed**, one dependency deprecation warning, 1.99s in the final workflow |
| Healthy traffic | 40 successful measured requests; 20 app-1, 20 app-2 |
| Audited app1 stop | Requested and succeeded actions persisted; monitor recorded app-1 incident |
| One-instance failure | Overall degraded; **40/40** application requests succeeded through app-2 |
| Audited app1 start | Requested and succeeded actions persisted; monitor recorded recovery |
| Restored traffic | After upstream rejoin, **40/40** succeeded; 20 app-1, 20 app-2 |
| Final service status | app1, app2, monitor, and nginx all healthy; only 127.0.0.1:8080 published |

The live checks also assert the landing page, dashboard, local assets, NGINX health,
worker health, summary/history APIs, and input validation through the proxy.

## Additional checks actually performed

- JavaScript syntax checks passed for all three frontend scripts.
- A real 60.1-second stability observation after the connection-pooling fix recorded
  **12 new checks for each of app-1, app-2, and gateway, with zero new failures**.
- An unexpected app-1 Uvicorn child-process exit using `scripts/replica.ps1 -Action crash`
  caused Docker's restart count to increase from **0 to 1**. The container returned
  healthy; 20 subsequent requests succeeded, split 10/10. Audit records persisted.
- SQLite history survived monitor image rebuilds and container replacements. A final
  direct inspection found **1,059 persisted samples** covering the original start
  timestamp through the later validation; no history reset was performed.
- Browser interactions verified: architecture selection, contextual explanation switching,
  expandable FAQ, real application request tester, chart target/window selection,
  incident type/component filters, instance inspection, recovery dialog, theme switching,
  focus mode, and pause/resume controls.
- The audit dialog displayed 10 persisted actions at inspection time, including the
  latest successful app-1 start.
- Responsive checks at a 390×844 viewport and desktop size found no horizontal page
  overflow. The mobile document width and scroll width both measured 375 CSS pixels
  after the browser scrollbar; desktop widths also matched.
- Browser error logs were empty during final dashboard inspection. User-facing text
  checks found no student, faculty, professor, classmate, or viva labels in either page.

## Bugs found and corrected during implementation

1. Reusing HTTP connections at the same five-second interval as Uvicorn's idle
   timeout produced intermittent connection errors. The monitor now uses fresh
   connections for each probe. The subsequent stability observation had zero new failures.
2. A short initial observation window produced only one chart bucket when grouped
   against an entire requested day. Bucket width now adapts to actual observations,
   while remaining bounded to 120 points. Unit tests verify count conservation.
3. An initial recovery assertion ran before NGINX's passive failure timeout expired.
   The replica's independent probe can recover first; the live check now waits for
   both upstream identities before measuring restored traffic. The final recovery
   run required 11 warm-up requests, followed by 40 successful measured requests.

Old probe errors and deliberate failures remain in SQLite. They are genuine records
from the development session and were not erased to improve availability percentages.
The chart, totals, and incident log intentionally retain them until retention expires.

## Limits of this verification

One-instance stop/failover, manual recovery, and unexpected-process restart were
tested. This is not a guarantee of perfect failover under load, arbitrary concurrent
failures, database corruption, host failure, or all non-idempotent request patterns.
Both-replicas-down is supported by the read-only checker but was not included in
the final workflow. Accessibility checks were practical keyboard/layout checks,
not a complete assistive-technology audit.

Login, authenticated roles, and a file vault remain optional future work. There is
no prerequisite currently blocking startup or core operation. Host Python was not
available through the Windows launcher, so Python execution and testing used Docker.

## Screenshots

Screenshots in `screenshots/` are real browser captures. Final landing, desktop,
mobile, and audit screenshots show the implemented interface. The replica-failure
capture was taken earlier during the same actual stop test, before the visual redesign.
Screenshots contain historical values and do not substitute for current API state.
