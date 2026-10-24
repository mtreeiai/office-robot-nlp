# Phase 8C.1-A implementation and verification

## Architecture and endpoints

The HTTP service adapts the existing application through `NlpSessionBridge`; it does not reimplement CLU, dialogue, registration or lifecycle rules in routes. Transport DTOs explicitly serialize the existing domain data. The standalone service imports neither Qt nor ROS.

Implemented: POST `/v1/sessions`, POST `/v1/sessions/{sessionId}/messages`, GET `/v1/tasks/{taskId}`, GET `/v1/batches/{batchId}`, GET `/health`.

## Files for this phase

Created:

- `office_robot_nlp/api_dto.py`: request/response schemas, single/batch/monitoring adapters.
- `office_robot_nlp/session_bridge.py`: independent session applications, per-session locks, globally unique resource indexes, idempotency.
- `office_robot_nlp/http_api.py`: FastAPI routes, safe errors, bind/token configuration, Uvicorn entry point.
- `http_conversation_demo.py`: offline fake-parser HTTP demonstration.
- `tests/test_http_api.py`: 40 HTTP/bridge tests.
- `requirements.txt`, `requirements-dev.txt`: runtime and test dependencies.
- `docs/integration-contract-v1.md`: shared GUI contract and complete JSON examples.
- This verification report.

Modified:

- `text_conversation.py`: optional session ID/namespace, common task ID allocator, adapter transaction checkpoint/rollback. Default CLI IDs and internal single-task result remain compatible.
- `.env.example`: API host, port and optional/required bearer token configuration.

Earlier Phase 8B changes already present in the working tree were preserved. No existing test files were edited for this phase.

## Startup and examples

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:CLU_PROVIDER = 'gemini'
$env:GEMINI_API_KEY = '<your-key>'
.venv\Scripts\python.exe -m office_robot_nlp.http_api
```

The [integration contract](integration-contract-v1.md) includes full installation/environment instructions, complete single and three-item message requests/responses, clarification JSON, a complete GET task snapshot, GET batch schema, errors, curl and PowerShell examples. Default base URL is `http://127.0.0.1:8000`.

## Unified serialization and ordering

A `TaskReady` becomes a one-item API batch envelope with `batchId:null`, `orderPolicy:null` and preview position 0. It does not create a domain batch. A real `BatchReady` retains its registered batch ID and policy. Both shapes contain the same task fields.

Batch preview uses the existing deterministic ordering function, converting only its position convention to zero-based API positions. Stored sequence indexes remain unchanged. Strict ordering and Option A conflict choices are preserved. GET reads current independent monitoring dimensions rather than replaying creation snapshots or mutating execution state.

## Sessions, concurrency and idempotency

Each session has an independent existing application and pending state. Session UUID namespaces prevent task/batch ID collisions across sessions. A per-session lock serializes messages and GET snapshots; a shared lock protects ID allocation and index publication. Different sessions can make provider calls concurrently.

Same session/message ID and exact text replays the successful response with no parser/registration call. Changed text returns 409. Failed provider/internal turns retain the message ID/text reservation, restore application state and permit a retry of the same ID/text. Successful old clarification replay does not restore stale pending state. All history is lost on process restart.

## Errors and access control

Structured responses cover invalid requests (422), absent session/task/batch (404), conflicting ID reuse (409), provider failures including upstream 429/503 (503), and safe generic unexpected failures (500). Provider details/credentials/tracebacks are not returned or logged by the HTTP boundary. Unknown rooms remain HTTP 200 conversational clarifications. Slash commands cannot serve as an HTTP control surface.

Binding defaults to loopback. A configured non-loopback host requires an explicitly supplied bearer token; when present, it protects all endpoints. This is shared integration access control, without per-user roles. Use the documented entry point and one server process.

## Verification results

Executed with the project's Python 3.11 virtual environment:

| Verification | Result |
| --- | --- |
| `python -m unittest discover -s tests -p "test_*.py" -v` | **302 passed**, including all 262 existing tests |
| `python -m unittest discover -s tests -p "test_http_api.py" -v` | **40 passed** |
| `python -m compileall office_robot_nlp tests` | Passed |
| `python -X utf8 http_conversation_demo.py` | Passed; session, TaskReady, room clarification/social/followup, BatchReady, GET snapshots, priority/conflict previews, idempotency |
| `python -X utf8 batch_conversation_demo.py` | Passed; four batches, nine READY tasks; existing CLI demo retained |
| `python -m office_robot_nlp.http_api`, then actual loopback GET `/health` | Started successfully; returned exact documented health JSON; stopped after verification |
| `python -m pip check` | No broken requirements |
| `git diff --check` | Passed (only pre-existing CRLF normalization notices) |

Tests block provider network requests and use fake parsers/in-process HTTP clients. No real Gemini, OpenAI or Ollama request was made. The existing evaluation failure-reporting test deliberately prints an offline provider error; its test passes. Installed Starlette emits a non-failing deprecation warning about its supported HTTPX compatibility path; no tests are skipped or warnings hidden.

The 40 new tests cover session uniqueness/isolation, single-task compatibility and clarification, original associations, priority and strict previews, source/current monitoring and pose data, social-preserved pending drafts, both immediate Option A choices, exact-content retry/conflict, concurrent identical/conflicting messages, sequential same-session turns, independent concurrent sessions/global IDs, provider failures and rollback, DTO-failure rollback, safe validation/auth errors, GET/health provider isolation, and the offline demo.

## GUI handoff and limitations

Send `docs/integration-contract-v1.md` to the PyQt5 team. Keep one session per conversation and a stable message ID/text for retries. Render the common task list and preview separately; use GET for current monitoring. Keep network operations off the UI thread and preserve the session for clarification turns. A timeout can occur while a request continues on the server; retry its original ID instead of submitting a duplicate intent.

State and idempotency are process-local and unbounded in this minimal phase; restart loses them. Use one worker, without reload/load balancing. No persistence, session expiry, multi-user isolation, TLS termination, physical/provider connectivity certification, scheduling, dispatch, robot acceptance/loading endpoints, ROS integration or GUI changes were added. The robot repository was not modified. Work stops at Phase 8C.1-A.
