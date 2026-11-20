# Phase 7A — Server implementation and verification report

Date: 2026-11-20 (Asia/Saigon). Repository: office-robot-nlp only. Base branch `feature/office-room-calendar`, commit `a1cedc2571ffa313b9c1ca066af269ffa92fbadb`; new branch `feature/calendar-server-handoff` created directly from it. Tracked base was clean; unrelated untracked experiments/, office-robot-config/, office_robot_edge_core.egg-info/ and request.json were preserved. No main/Integration branch, Robot repository, ROS/Nav2, HMI, scheduler, execution gate or navigation file was changed. No commit/push/merge/deploy or remote credentials/data access.

## Audit before implementation

Inspected the requested HTTP factory, session bridge, base DTOs, integration/availability/HTTP/config modules, Calendar tests, API/robot documentation, semantic registry/resolver and task allocation. Baseline full regression on isolated PostgreSQL: **389/389 passed**, no skips (12.506s).

| Question | Actual finding / retained behavior |
| --- | --- |
| Single / batch DTO | Both use batch.tasks/orderPreview. Single has null batchId/orderPolicy. Non-ready has batch=null. Base envelope fields are sessionId/messageId/dialogueStatus/reply/batch. |
| taskId allocation | Existing application allocates session-namespaced sequential IDs; bridge publishes indexes and caches the response atomically. IDs are stable within the session/replay, not durable across restart. |
| Enrichment insertion | Existing optional callback runs after validated TaskReady/BatchReady envelope creation inside conversation_transaction, before index/cache publication. No second task registration. |
| Feature gate | CALENDAR_ENABLED mounts Calendar; CALENDAR_NLP_ENABLED installs the callback/additive JSON response. NLP flag without Calendar fails configuration. Disabled mode preserves the old envelope. |
| Preliminary calculation | Existing service estimates travel/handoff and reads deterministic policy/schedule. PRELIMINARY ALLOW is already downgraded to UNKNOWN/ETA_NOT_PROVIDED; other existing policy outcomes remain. |
| Final API | Existing POST /v1/robot/availability/check uses separate persisted read-token digest, validates operation/window and returns the unchanged evidence object. It accepts no taskId and creates no execution approval record. |
| Replay | Same session/messageId/text returns a deep copy of the complete cached response without requery. Changed text with same ID returns 409. Old evidence can expire. |
| Correct functionality | Policy engine, consistent read snapshot, UTC/version/TTL, auth boundaries, base DTOs, task lifecycle, priority/strict order, registration and single/batch behavior already work; retained. |

### Verified gaps and decisions

1. No executionDecisions field existed. Added fixed per-task stage metadata behind the existing NLP flag; no new policy engine.
2. The exact requested room text failed deterministic resolution: phòng 1 resolved, phòng 01 did not. Appended approved zero-padded aliases for all seven canonical IDs. No resolver broadening, ID conversion, removal of old aliases or geometry change.
3. Disabled-room evidence is UNKNOWN/DENY, but feedback's early UNKNOWN return omitted the denial. Feedback now explicitly states disabled/no delivery, and UNKNOWN preliminary replies also require the real arrival/handover window. Missing working-hours eligibility and absent manager approval are stated clearly.
4. The illustrative prompt allows hypothetical preliminary advisory ALLOW, whereas the existing service is stricter and downgrades it. Kept that stronger existing behavior; a mock-ALLOW regression independently proves the new stage never authorizes execution.

These are additive/feedback changes, with no breaking endpoint/schema/status change. No PostgreSQL migration or dependency change is needed.

## Implementation flow

NLP + resolver → existing validated task registration/envelope → existing deterministic preliminary Calendar query → roomAvailability plus one fixed executionDecisions entry per existing task → existing cache/publication → client receives ready task with pending execution state. Non-ready responses get two empty arrays and no invented task.

Robot separately calls the unchanged authenticated read API with its own full arrival/handover window. That returns Calendar policy evidence only. The Robot team must correlate the request to its task and implement its execution/safety handling; nothing here sends a goal, updates a task lifecycle or persists an approval.

Each executionDecisions item is `{taskId,roomId,status:"PENDING_AVAILABILITY_CHECK",allowed:false,reasonCode:"ARRIVAL_WINDOW_REQUIRED"}`. IDs/rooms derive from existing tasks, not advisory content or Gemini. Same-room tasks stay separate. Priority/orderPreview/sequence/constraints remain untouched. The base DTO and HTTP/bridge modules required no further edits.

## Files / implementation diff

| File | Change |
| --- | --- |
| office_room_calendar/integration.py | Typed fixed stage DTO and feature-gated per-task executionDecisions alongside unchanged evidence enrichment |
| office_room_calendar/availability.py | Vietnamese feedback only; policy/calculation/response fields unchanged |
| data/office_room_aliases.json | Append zero-padded spoken forms, preserving every old alias and all canonical IDs |
| tests/test_calendar.py | Reuse isolated PG fixtures; ten new HTTP/integration cases and outage assertion; helpers accept controlled fake responses/environment flags |
| tests/test_calendar_execution_contract.py | Eight pure contract/feedback/disabled-feature tests, including hypothetical advisory ALLOW and explicit constraints |
| tests/test_semantic_map.py | One new padded-alias regression; expected alias tuple expanded while retaining all old assertions/geometry tests |
| docs/calendar/execution-contract.md | Exact contract, stages, eligibility, IDs/replay and compatibility |
| docs/calendar/robot-team-handoff.md | Staging endpoints/auth, windows, mapping, errors and Robot responsibilities |
| docs/calendar/phase-7a-deployment.md | Owner-only reviewed-source deployment and smoke steps |
| docs/calendar/phase-7a-server-report.md | This audit/diff/test/readiness report |
| docs/calendar/examples/{nlp-single,nlp-batch,arrival-free,arrival-busy}.json | Four complete actual API request/response fixtures from fake NLU/local PG/frozen illustrative clock; no credentials |
| docs/calendar/api-contract.md, robot-integration.md, README.md | Consistent extension/auth/task-correlation guidance and links |
| docs/calendar/phase-reports.md, test-report.md | Preserve historical Phase 0–6 results and identify the current Phase 7A report |

Total: **10 existing files modified, 9 new files**. No changes to http.py, config.py, migrations, render.yaml, requirements, existing NLP base DTOs/bridge/factory, robot code or physical execution. Ordinary git diff statistics exclude the new untracked files until staging; all new files are explicitly listed here. No staging/commit performed by the agent.

## Verification actually executed

| Check | Result |
| --- | --- |
| Baseline before code | 389/389 passed, no skips |
| Focused Calendar + new contract suite | 59/59 passed, no skips (13.475s) |
| Full regression after change | **408/408 passed**, no skips/failures; all original tests retained plus 19 new tests |
| Real Chromium + local PostgreSQL + HTTP | PASS: existing login, room approval, booking create/conflict/edit/cancel, owner controls, text-only XSS title, app restart, desktop/mobile, refresh retention and outage feedback |
| Existing Fake Robot Client against local HTTP | PASS inside browser smoke; no actual Robot or provider |
| Four JSON example fixtures | Generated from local API; NLP response model and exact task/window associations validated |
| Python compileall; dependency check; git diff check | PASS |

New coverage: both flags disabled; factory environment flags enabled with the exact room-01 instruction; FREE/BUSY preliminary evidence from the real deterministic service; pending state even for mock advisory ALLOW; multi-room priority/strict order and explicit constraints; same-room distinct task IDs; unresolved/conflicting/social/other non-ready empty arrays; successful clarification; DB outage and exact cached replay after calendar change; final window FREE/ALLOW and BUSY/DEFER; every policy including BUSY door handoff and FREE approval/disabled; preliminary all policies; invalid/revoked/wrong-scope tokens; invalid/expired windows; safe 503/UNKNOWN.

Tests used only a new disposable loopback PostgreSQL 17 container (port 55442) and randomly isolated schemas, never the Render DB or Integration staging. The temporary test container is stopped after verification. Existing Starlette HTTPX deprecation and Git Windows line-ending notices are non-failing. No live Gemini/OpenAI/Ollama request was made. Browser/Fake Robot tests are server-side integration checks, not Robot execution tests.

Reproduce with a dedicated local test instance as described in [runbook](runbook.md); privately supply a disposable loopback database DSN whose name contains calendar and test:

```powershell
$env:CALENDAR_TEST_DATABASE_URL='<disposable local Calendar test DSN>'
.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_calendar*.py' -v
.venv\Scripts\python.exe -m unittest discover -s tests -p test_*.py -v
.venv\Scripts\python.exe -m scripts.calendar_browser_smoke
.venv\Scripts\python.exe -m compileall -q office_room_calendar office_robot_nlp tests scripts
.venv\Scripts\python.exe -m pip check
git diff --check
```

Ignored local evidence: .venv/phase7a-baseline.txt, phase7a-calendar-tests.txt, phase7a-full-regression.txt, phase7a-browser-smoke.txt and calendar-browser screenshots. No secrets/real connection strings in the new handoff artifacts. Actual tests are local; target staging configuration/deployment is intentionally not inspected or claimed.

## Compatibility and readiness

The final Calendar endpoint, authentication, response fields and five policies remain compatible; matrix tests compare the complete endpoint response to the existing service. No final authorization endpoint, scheduler or schema was added. `TASK_READY` / `BATCH_READY` retain their old meaning. Both extension arrays disappear when integration is disabled. Feature-enabled strict decoders must accept the documented additive fields.

Server scope is implemented and locally verified, ready for owner review and controlled staging smoke. Exact deployment/verification instructions: [Phase 7A deployment](phase-7a-deployment.md). The checked-in existing render.yaml is NLP-only; owner must verify staging installs Calendar dependencies and activates both flags, without changing credentials/data for this release.

Remaining external work: owner publishes/deploys the reviewed server commit; supplies the two credential scopes privately and verifies live provider/Calendar behavior. Robot team independently implements HMI/client/task-window correlation, its execution gate, freshness/error/reconnect behavior, local safety/constraint/order enforcement and actual navigation integration. No live Render smoke, actual Gemini interpretation of the sample instruction, Robot/Nav2 E2E, recipient presence, motion safety, reservation, approval workflow or durable NLP task state is claimed. The API does not by itself make the Robot safe to execute.
