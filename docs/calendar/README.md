# Office Room Calendar

Implemented independently on `feature/office-room-calendar`, based on `origin/main` at `1b30c3059a44ab6572c8af16694914d0fb18d93f`. Requirements: [owner's spec](spec.md), reconciled in [Phase 0](phase-0-audit.md).

- Dedicated PostgreSQL migration; rooms `01`–`07`, initially `bookable=false`, default policy `BLOCK_WHEN_BUSY`.
- USER/ADMIN login, persistent sessions, CSRF/Origin checks, audit history; distinct robot read credentials.
- Booking CRUD with database-level concurrency protection, durable create idempotency and optimistic revision checks. Atomic weekly series preserves local wall time; edit/cancel affects one occurrence.
- FREE/BUSY/UNKNOWN evidence, separate delivery decisions, complete arrival windows, next fitting slot, room version and short expiry.
- Responsive same-origin website `/calendar/`: vanilla JS and self-hosted FullCalendar day/week/month views. No Node/CDN needed at runtime.
- Read-only robot endpoint and default-disabled additive NLP integration. Existing task/batch/status/priority/order/idempotency behavior preserved.

Calendar evidence does not prove physical occupancy or grant navigation permission. Robot-side enforcement remains a separate integration. Nothing has been pushed, merged or deployed.

| Document | Contents |
| --- | --- |
| [Architecture / ERD](architecture.md) | Components, data and transaction boundaries |
| [API contract](api-contract.md) | Routes, credentials, examples and decision semantics |
| [Development runbook](runbook.md) | PostgreSQL, migrations, users, backend, website, tests |
| [Robot guide](robot-integration.md) | Remote HTTPS checks, freshness and fake robot client |
| [Phase reports](phase-reports.md) | Files, input → processing → output, verification and remaining work per phase |
| [Test report](test-report.md) | Actual results, limits and diff summary |
| [Execution contract](execution-contract.md) | Phase 7A preliminary execution state and actual single/batch DTO examples |
| [Robot team handoff](robot-team-handoff.md) | Staging endpoints, decision mapping and Robot team's remaining work |
| [Phase 7A deployment](phase-7a-deployment.md) | Owner-only deploy and smoke steps; no automatic deployment |
| [Phase 7A report](phase-7a-server-report.md) | Audit, minimal diff and updated server verification |

Existing `render.yaml` and NLP-only runtime requirements are unchanged. Future staging setup needs explicit calendar dependencies and a separate database; this task has not deployed it.
