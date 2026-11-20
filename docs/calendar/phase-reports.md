# Phase reports

Phase 0–6 history is retained below. Subsequent Server execution-stage work and current results: [Phase 7A report](phase-7a-server-report.md), [execution contract](execution-contract.md).

Branch `feature/office-room-calendar`; independent base `origin/main` at `1b30c3059a44ab6572c8af16694914d0fb18d93f`. Scope follows the explicit Implementation Task plus the supplied local spec. Historical Phase 0 and subsequent implementation are separated below. Verification names refer to executable tests in `tests/test_calendar.py`; the final full run covers every phase together.

## Phase 0 — Audit

Implemented: inspected FastAPI/auth, DTOs, bridge transaction/cache, resolver/registry, language adapters, requirements, Render profile and 348 baseline tests. Created the requested branch from fetched main, preserving unrelated untracked files. Read complete local specification after inaccessible Notion link was replaced by the owner.

Files: [phase-0-audit.md](phase-0-audit.md). Input → processing → output: repository/spec → auth/DB/compatibility boundary audit → isolated module/schema plan. No API/schema in this phase. Baseline unittest: 348 passed; compile and diff checks passed. Initial document blocker resolved. Next: PostgreSQL/auth; no unresolved architecture approval required under the explicit task.

## Phase 1 — Database and authentication

Implemented: explicit checksummed migration, seven unbookable rooms, policies/versions, users, Argon2id, durable cookie sessions and robot read-token digests, login throttling and audit records. Migration is transactional, repeatable, preserves confirmed room configuration and requires a dedicated database name containing calendar.

Files: `office_room_calendar/{__init__,config,database,auth,errors,admin}.py`, `migrations/001_calendar.sql`, `requirements-calendar.txt`, `.env.example`, `requirements-dev.txt`.

Input → processing → output: restricted provisioning CLI → validate config/account + hash credentials → persisted users/token digests/audit; login → verify password + persist session → scoped HttpOnly cookie and CSRF token. User reads/writes revalidate current account state. API login/session/logout and examples: [contract](api-contract.md). Schema/ERD: [architecture](architecture.md).

Verification: initial real migration seeded exactly seven rooms; migration repeatability, password/session digest storage, expiry/logout/disabled-account/token checks, persistent login throttle and secure cookie tests pass. The full run also includes owner/admin and origin/CSRF boundaries. Run commands: [runbook](runbook.md).

Limits: no public self-registration, account-management UI or password-reset workflow; privileged CLI provisioning and restricted DB revocation only. Next: booking operations with database concurrency enforcement.

## Phase 2 — Booking API

Implemented: create/list/update/cancel, owner/admin controls, database range exclusion, durable create idempotency, expected revisions, audit/version transactions. Added weekly create only after single CRUD; entire series commits or rolls back; individual occurrences can be changed/cancelled.

Files: `office_room_calendar/service.py`, booking DTOs/routes in `http.py`, migration booking/series/idempotency entities. Input → processing → output: authenticated operator + offset times/request key → room eligibility, sorted room locks, DB exclusion, atomic audit/version/replay record → booking DTO or structured 409/422. Routes/examples: POST/GET/PATCH/DELETE `/v1/calendar/bookings` in [contract](api-contract.md).

Verification: simultaneous overlap creates yield one success and one conflict; direct SQL also cannot bypass exclusion; concurrent same-key retry creates once; changed-key content conflicts; non-owner denied; stale revision rejected; cancellation frees interval; conflicting edit rolls back audit/version; weekly occurrence conflict rolls back whole series. API CRUD and validation tests pass against PostgreSQL.

Limits: no bulk series edit/cancel, invitations or approval lifecycle for meeting bookings. Next: deterministic availability using the confirmed schedule.

## Phase 3 — Availability

Implemented: FREE/BUSY/UNKNOWN separate from ALLOW/DEFER/DENY/NEEDS_APPROVAL/UNKNOWN, all five policies, full arrival windows, merged busy/opening intervals, next fitting gap, room version, short TTL and preliminary check type. UTC storage plus IANA working hours/weekly recurrence. DB failure/expired windows never become ALLOW.

Files: `office_room_calendar/availability.py`, room configuration/time helpers in `service.py`. Input → processing → output: canonical ID + explicit window (or labelled estimates) → one read-only consistent PG snapshot, interval and policy checks → structured evidence. Browser availability/free-slots routes and examples: [contract](api-contract.md). No additional tables beyond Phase 1.

Verification: free/busy boundaries, future arrival and complete handoff interval, adjacent busy intervals, next gap fitting duration, cancellation/version/TTL invalidation, door handoff while BUSY, manager/disabled/business-hour policies, contiguous opening hours, timezone/DST rejection, DB outage UNKNOWN. All pass. Vietnamese feedback is deterministic; no provider request.

Limits: 30-day next-slot search, eight-hour explicit arrival window, no reservations or occupancy sensor. Manager approval is a decision, not an implemented approval workflow. Next: website.

## Phase 4 — Website

Implemented: same-origin responsive HTML/CSS/JS, login restoration, room dashboard, day/week/month FullCalendar, booking create/edit/cancel/weekly creation, admin room/policy management, loading and error states. User titles render as text; browser timezone is labelled. Credential separation and backend authorization are enforced beyond UI controls. Background refresh retains an open form's selected room and removes stale dashboard cards on failure.

Files: `office_room_calendar/web/{index.html,app.css,app.js,package.json,package-lock.json,build.cjs,vendor/*}`, website mounting/security headers in `http.py`, `.gitignore`. Input → processing → output: forms/calendar selection → authenticated Calendar API → server validation → render returned state or actionable error; no browser SQL/LLM/robot control. Website `/calendar/`; no DB changes here.

Verification: real Chromium on desktop 1440×1000 and mobile 390×844; login, admin confirmation, create/conflict/edit/cancel, view-only other user's event, XSS title text, background refresh, no mobile overflow, application restart and network outage feedback pass. Screenshots visually inspected and kept in ignored `.venv/calendar-browser/`. Node syntax/build and static-asset/CSP tests pass.

Limits: Chromium tested; Firefox/Safari/accessibility audit not performed. Standard FullCalendar is pinned/self-hosted; its internal Preact dependency does not create another application framework/server. Node is needed only to regenerate assets. Next: NLP/robot.

## Phase 5 — NLP and robot read API

Implemented: separate robot credential boundary and read-only POST check; optional post-resolver ready-task/batch enrichment, additive roomAvailability array and deterministic Vietnamese feedback. Enrichment occurs inside existing conversation transaction before response publication/cache. Flag off keeps the old envelope; task/batch snapshot DTOs and all statuses remain unchanged.

Files: `office_room_calendar/{http,integration}.py`, small hooks in `office_robot_nlp/{http_api,session_bridge}.py`, `scripts/calendar_fake_robot.py`. Input → processing → output: validated NLP destination → deterministic Calendar check → evidence appended with existing ready response; direct robot window → token-authenticated read snapshot → fresh evidence. Full request/response/auth examples: [API](api-contract.md), [robot guide](robot-integration.md).

Verification: fake providers exercise single, multi, strict-order, priority/conflict, clarification, exact message replay and DB outage. No extra registration/status changes; every original regression passes. Robot/browser/backend credential crossover and robot booking/NLP writes rejected. Standard-library Fake Robot Client successfully queried the local HTTP server without provider/ROS calls and without printing its token.

Limits: NLP enrichment is PRELIMINARY; replayed evidence can expire. Real robot enforcement and remote Render/HTTPS staging calls are not tested and are not implied. Next: complete regression/docs; robot-side integration is separate.

## Phase 6 — Verification and documentation

Implemented: dedicated PostgreSQL suite, actual-browser/Fake Robot smoke script and the documentation set: audit, architecture/ERD, API contract, runbook, robot guide, phase reports and test report.

Files: `tests/test_calendar.py`, `scripts/calendar_browser_smoke.py`, `requirements-calendar-browser.txt`, `docs/calendar/*.md`. The supplied `spec.md` was read and preserved unchanged. Input → processing → output: disposable loopback test database + mocked language provider → unit/API/concurrency/security/persistence/browser checks → recorded results and reproducible commands. No new endpoint/schema in this phase.

Final verification: [test report](test-report.md). No live LLM calls, production/staging migrations, push, merge, main edits or deployment. Local test container is stopped after verification. The source/requirements, not developer caches, are the review deliverable.

Remaining work outside this task: owner review, explicitly approved independent staging setup and joint remote robot tests, then separately implement/test robot-side evidence enforcement. No further implementation phase started.
