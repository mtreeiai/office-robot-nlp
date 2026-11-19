# Office Room Calendar — Phase 0 audit

Date: 2026-11-19. Development branch: `feature/office-room-calendar`.

## Verified branch isolation

- Inspected status, current branch and commit before changes. Initial branch was `main`; tracked files were clean.
- Fetched `origin/main`, then created `feature/office-room-calendar` directly from that ref.
- Branch HEAD and origin/main both equal `1b30c3059a44ab6572c8af16694914d0fb18d93f` at creation.
- No merge, cherry-pick, code import or dependency on `feature/office-robot-integration-v1`.
- Preserved existing untracked `experiments/`, `office-robot-config/`, `office_robot_edge_core.egg-info/` and `request.json`. Do not stage these as calendar work.
- No push, merge, main modification or deployment performed.

## Specification access — resolved

The initial Notion link was inaccessible. The owner subsequently supplied local [spec.md](spec.md), which was read in full before implementation. Original link:

`https://app.notion.com/p/L-CH-ROBOT-41c62d30625180ecaa8fc8dd31f5bf32`

No authenticated Notion connector was available; the local specification resolved the document dependency.

The explicit Implementation Task takes precedence over older spec passages saying develop on main, stop after Phase 0 or replace TASK_READY with ROOM_BUSY. It authorizes Phase 1–6 on this feature branch and requires backward compatibility; the later spec recommendations agree. Defaults: all rooms bookable=false, all policies BLOCK_WHEN_BUSY; owner/admin writes, separate robot read token. See [phase reports](phase-reports.md) for implementation results.

## Existing architecture

| Component | Finding | Reuse / constraint |
| --- | --- | --- |
| `office_robot_nlp/http_api.py` | Existing FastAPI `create_api` factory; no module-level app | Keep factory/start command; add isolated calendar routing only after auth boundary is defined |
| Existing bearer middleware | API_ACCESS_TOKEN protects all paths including health when configured | Browser must never receive this server token; calendar needs its own user authentication boundary |
| Error middleware | Structured 401/404/409/422/503/500 handling; no raw traceback to clients | Preserve existing NLP errors; specify UNKNOWN availability behavior for DB outages separately |
| `office_robot_nlp/session_bridge.py` | Independent in-memory sessions, per-session locking and message idempotency | Preserve single/batch registration and replay semantics; calendar lookup must not register/schedule tasks |
| `text_conversation.py` / DialogueManager | TaskReady / BatchReady / clarification flows | Feature-flag calendar integration after validated destination; no changes to existing result statuses |
| Gemini adapter | Existing Interactions request, structured CLU validation and safe diagnostic logging | Gemini interprets language only; deterministic calendar service supplies actual calendar evidence |
| `office_robot_nlp/semantic_map.py` | Seven canonical IDs 01–07; spoken room names and aliases | Reuse identifiers/names only; no calendar decision or UI derived from Nav2 coordinates |
| `office_robot_nlp/api_dto.py` | Explicit JSON DTOs for unified single/multi envelopes and monitoring | Preserve field names and types; calendar evidence should use an additive/separate contract |
| Dependencies | FastAPI/Uvicorn runtime; HTTPX for tests | No calendar ORM, frontend framework or DB layer currently exists on origin/main |
| Render config | One worker, factory startup, protected health, no automatic deploy | Preserve NLP profile; calendar DB setup/migrations must be explicit, never target production by default |
| Existing tests | Unit and in-process API tests with fake provider boundaries | Baseline must pass before and after calendar implementation; no live LLM calls |

The current origin/main source does not contain the Integration v1 operational backend. No calendar feature will rely on that branch or its models/migrations/ROS components.

## Implementation plan established during audit

1. **Database/authentication:** isolated calendar module/schema/migrations, UTC timestamptz booking intervals, persistent user/session/robot credentials and audit records. Seed canonical room IDs with bookable=false until explicitly configured. Prefer a PostgreSQL range exclusion constraint for confirmed-booking overlap protection, including concurrent transactions. Read complete auth/role requirements before choosing final session/token contracts.
2. **Booking API:** create/list/update/cancel with owner/admin checks, transactionally audited changes and conflict protection. Determine idempotency/optimistic-concurrency behavior from the specification; do not invent a public contract ahead of it.
3. **Availability engine:** deterministic FREE/BUSY/UNKNOWN, separate delivery-decision policy, arrival-window checks, next-slot search, calendar version and checkedAt/validUntil. Unknown never becomes free; a free calendar does not assert physical emptiness or navigation permission.
4. **Website:** propose same-origin HTML/CSS/JavaScript served by FastAPI, avoiding a new frontend framework/build service for this small repository. Login, room dashboard, booking calendar/forms and admin room management; browser security and loading/error states. Finalize after checking the full specification.
5. **NLP/robot:** read-only authenticated robot endpoint, deterministic Vietnamese calendar feedback, default-disabled NLP availability feature flag; no ROS/Nav2 changes and no breaking NLP DTO/status changes.
6. **Verification/docs:** dedicated PostgreSQL development/test instance, migration/concurrency/auth/API/availability/restart tests, real browser checks, baseline NLP regression, architecture/ERD/API contract and robot read-only guide. Distinguish verified behavior from untested external deployment.

At the end of the original audit, no implementation had been added. After local spec review, Phase 1–6 proceeded using the confirmed defaults and isolated schema.

## Phase 0 verification

- `.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v`: **348/348 baseline tests passed**, no skipped tests and no live provider request.
- `.venv\Scripts\python.exe -m compileall office_robot_nlp tests`: passed.
- `git diff --check`: passed.
- Branch at audit: `feature/office-room-calendar`; no tracked implementation changes then. The audit was the first task-owned artifact; later implementation is described separately.
- Existing non-failing Starlette HTTPX deprecation warning remains. Untracked cache directories from other branches are not calendar implementation and were not imported or staged.

Document access has been resolved. Subsequent phases use a dedicated calendar module and disposable PostgreSQL test database, without Integration v1 dependencies.

## References reviewed for the proposed approach

- [PostgreSQL range types and exclusion constraints](https://www.postgresql.org/docs/current/rangetypes.html)
- [OWASP authentication guidance](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)
- [OWASP CSRF prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)
- [FastAPI mounted applications](https://fastapi.tiangolo.com/advanced/sub-applications/)
