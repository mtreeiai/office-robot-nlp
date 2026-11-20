# Verification and diff report

This is the historical Phase 0–6 report. For the additive execution contract, current regression counts and subsequent diff, see [Phase 7A server report](phase-7a-server-report.md).

Date: 2026-11-19. Windows / Python 3.11.9; PostgreSQL 17 Alpine (dedicated loopback Docker database); FastAPI 0.151.0; Playwright 1.58.0 / Chromium 145. Branch `feature/office-room-calendar`, created directly from fetched `origin/main` commit `1b30c3059a44ab6572c8af16694914d0fb18d93f`.

## Actual results

| Verification | Actual result |
| --- | --- |
| Original main regression before implementation | **348/348 passed**, no skips |
| New Calendar unit / PostgreSQL / API tests | **41/41 passed**, 9.838 seconds in focused run |
| Complete final regression | **389/389 passed**, 11.852 seconds, no skips/failures |
| Real Chromium + PostgreSQL + loopback HTTP | PASS; details below |
| `compileall` for Calendar/NLP/tests/scripts | PASS |
| `node --check` custom JS/build script; local asset build | PASS |
| `pip check` | No broken requirements |
| `git diff --check` | PASS |

Commands, from repository root with disposable test DB already running:

```powershell
$env:CALENDAR_TEST_DATABASE_URL='postgresql://postgres@127.0.0.1:55440/office_calendar_test'
.venv\Scripts\python.exe -m unittest discover -s tests -p test_calendar.py -v
.venv\Scripts\python.exe -m unittest discover -s tests -p test_*.py -v
.venv\Scripts\python.exe -m scripts.calendar_browser_smoke
.venv\Scripts\python.exe -m compileall -q office_room_calendar office_robot_nlp tests scripts
node --check office_room_calendar/web/app.js
node --check office_room_calendar/web/build.cjs
.venv\Scripts\python.exe -m pip check
git diff --check
```

Reproduction setup: [runbook](runbook.md). All PostgreSQL tests used ONLY `office_calendar_test` on `127.0.0.1:55440`, with random test schemas created/dropped per test. The browser test used its own random schema and loopback server. The specific disposable container is stopped after verification. No production or Integration staging database was accessed. No live Gemini/OpenAI/Ollama call was made; tests use fakes or block provider network access. Existing expected provider-failure output in regression logs is mocked test data.

Ignored local evidence: `.venv/calendar-base-regression.txt`, `.venv/calendar-tests.txt`, `.venv/calendar-full-regression.txt`, `.venv/calendar-browser-smoke.txt`, `.venv/calendar-browser/*.png`. Screenshots were visually inspected. Existing Starlette HTTPX deprecation warning remains non-failing.

### Covered behavior

- Real migration creates seven safe default rooms, records checksums and preserves configuration on rerun.
- PostgreSQL exclusion constraint rejects direct SQL overlap; concurrent service booking creates allow one success only. Adjacent bookings and different rooms are allowed.
- Durable create replay handles concurrent duplicate keys; different content conflicts. Revision checks reject stale edits. Failed edit/series create rolls back bookings, audit and versions together.
- USER ownership and ADMIN permissions, session/password digest storage, expiry/logout/revocation, persistent login throttle, Secure/HttpOnly cookie attributes, Origin/CSRF rejection, credential crossover and strict payload validation.
- Free/busy/unknown boundaries, arrival window including full handoff, merged busy intervals, next fitting slot, cancellation, TTL/version changes, all policies, business-hour merging/timezones, weekly DST behavior and imaginary-time rejection.
- DB failure returns unknown evidence or a structured safe 503; no secret/internal DB error is exposed. Preliminary or expired checks cannot grant ALLOW.
- NLP flag off retains the exact old envelope. Flag on preserves task/batch statuses, IDs/registration, priority, strict order, sequence and replay, adds deterministic evidence/feedback, and tolerates calendar DB outage without inventing availability.
- Backend/application recreated against the same PostgreSQL state retains bookings, sessions and audit. Browser smoke stops the HTTP application, creates a new app/server and restores the actual browser's existing login and meeting.
- Chromium: login, admin room approval, day/week calendar rendering, create/conflict/edit/cancel, other-user view-only controls, text-only HTML-like title, selected-room retention across background refresh, desktop/mobile layouts, no mobile horizontal overflow, outage clears stale free cards. Static asset/CSP checks pass; no uncaught browser JS exceptions.
- Standard-library Fake Robot Client successfully authenticates and reads the actual local HTTP endpoint without exposing its token or sending any motion command.

## Limits / not verified

- No Render deployment or remote HTTPS staging/real robot test was performed. Robot-side enforcement, ROS/Nav2 and scheduling are outside this implementation; an availability API alone does not enforce movement behavior.
- No Firefox/Safari test, accessibility audit, load test, PostgreSQL outage/restart at the container/host level, HA or backup/restore drill. Application restart persistence was tested against a running database.
- Evidence expires and changes with room version; it is not a reservation. There is no manager-approval workflow, series-wide edit/cancel, public account lifecycle UI or password reset in this version.
- The existing NLP in-memory session persistence/one-worker limitation remains unchanged. Calendar data and Calendar sessions persist independently in PostgreSQL.

## Files and diff summary

Five existing tracked files changed. Thirty-four task files added (including generated assets/documentation); the user-supplied `docs/calendar/spec.md` was read and preserved, not generated or edited. Git's ordinary diff statistics exclude new untracked files until staging; no staging/commit was performed.

| Files | Change |
| --- | --- |
| `.env.example`, `.gitignore` | Optional Calendar configuration; ignore Node modules |
| `requirements-dev.txt` | Include calendar test dependencies, preserve HTTPX pin |
| `office_robot_nlp/http_api.py` | Optional mounts, separate credential boundaries, additive flag-enabled response |
| `office_robot_nlp/session_bridge.py` | Optional transactional response enrichment before existing cache/index publication |
| `requirements-calendar.txt`, `requirements-calendar-browser.txt` | Optional runtime and actual-browser test dependencies |
| `office_room_calendar/__init__.py`, `config.py`, `errors.py` | Independent module/settings/error types |
| `office_room_calendar/database.py`, `migrations/001_calendar.sql` | Dedicated DB transactions, explicit migrations/schema/exclusion |
| `office_room_calendar/auth.py`, `admin.py` | Credentials/sessions/CSRF/throttle/audit and restricted provisioning |
| `office_room_calendar/service.py`, `availability.py` | Room/booking transactions and deterministic policy evidence |
| `office_room_calendar/http.py`, `integration.py` | Browser/robot APIs, website mounts and NLP evidence extension |
| `office_room_calendar/web/index.html`, `app.css`, `app.js` | Responsive website and forms |
| `office_room_calendar/web/package.json`, `package-lock.json`, `build.cjs` | Reproducible pinned local asset build |
| `office_room_calendar/web/vendor/fullcalendar.min.js`, `fullcalendar-vi.min.js`, `fullcalendar-LICENSE.md`, `preact-LICENSE.txt` | Standard bundle, Vietnamese locale and third-party licenses |
| `tests/test_calendar.py` | 41 focused tests; real disposable PostgreSQL with safe target guard |
| `scripts/calendar_browser_smoke.py`, `calendar_fake_robot.py` | Browser/persistence smoke and remote read client |
| `docs/calendar/README.md`, `phase-0-audit.md`, `phase-reports.md`, `architecture.md`, `api-contract.md`, `runbook.md`, `robot-integration.md`, `test-report.md` | Required reports, diagrams, contracts and reproducible instructions |

No Integration v1 source or ROS changes, no modifications to existing NLP DTOs/policies or `render.yaml`, no production DB migration, push, merge or deployment. Unrelated pre-existing untracked files were preserved. Next action is owner review and a separately authorized staging/robot integration test.
