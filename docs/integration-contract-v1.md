# Office Robot NLP HTTP integration contract v1

Phase 8C.1-A. The PyQt5 client communicates with a separate NLP process using UTF-8 JSON over HTTP. The NLP process has no PyQt5 or ROS2 dependency and does not modify the [robot repository](https://github.com/buinhuduc2006hd-star/office-robot).

## Start on Windows

From the project root, using Python 3.11 or later:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:CLU_PROVIDER = 'gemini'
$env:GEMINI_API_KEY = '<your-key>'
$env:NLG_ENABLED = 'false'
.venv\Scripts\python.exe -m office_robot_nlp.http_api
```

Base URL: `http://127.0.0.1:8000`. This module is the supported local entry point: it validates binding/access-control settings and starts exactly one Uvicorn worker. Render uses the same `create_api` factory through Uvicorn; see [Render deployment](deploy-render.md). Do not use multiple workers, reload, or independently load-balanced instances: their memory is not shared. No `.env` loader is installed; configure environment variables in the process that starts the server. Locally, `/health` works without a provider key. On Render, startup validates mandatory configuration without a provider call; health remains authenticated. Session creation initializes the existing application/provider configuration; unavailable provider setup can return 503. Existing Ollama session initialization includes its existing local health request.

Runtime dependencies are FastAPI and Uvicorn (`requirements.txt`). Pydantic and Starlette are installed by FastAPI. Development/testing additionally uses HTTPX (`requirements-dev.txt`), following the [FastAPI TestClient workflow](https://fastapi.tiangolo.com/tutorial/testing/). No SDK for Qt, ROS, or a database is installed.

| Variable | Default / behavior |
| --- | --- |
| `API_HOST` | `127.0.0.1` locally; must be `0.0.0.0` on Render |
| `API_PORT` | Local `8000`, integer 1–65535; ignored on Render |
| `PORT`, `RENDER` | Render supplies `PORT` and `RENDER=true`; the factory uses PORT on Render |
| `API_ACCESS_TOKEN` | Optional on loopback; required for every other bind address |
| `CLU_PROVIDER` | `gemini`; existing `openai` and `ollama` also supported |
| `GEMINI_API_KEY` | Required for real Gemini conversations; server-side only |
| `GEMINI_MODEL` | Existing adapter default (`gemini-3.8-flash`); explicitly configurable |
| `GEMINI_BASE_URL`, `GEMINI_TIMEOUT` | Existing adapter configuration |
| `NLG_ENABLED` | `false`; existing optional NLG when `true` |
| `NLG_PROVIDER` | `gemini` when optional NLG is enabled |
| `ROOM_REGISTRY_PATH` | `data/office_rooms.json` |
| `ROOM_ALIASES_PATH` | `data/office_room_aliases.json` |
| `OPENAI_*`, `OLLAMA_*` | Existing adapter conventions; see `.env.example` |

Relative semantic configuration paths are resolved from the repository root by the existing registry loader. Invalid semantic configuration prevents session initialization; there is no fictional map fallback. `DEBUG_CONVERSATION` applies to the text CLI, not HTTP logging.

For access from another machine, set `API_HOST` to the intended interface and explicitly set a random shared `API_ACCESS_TOKEN`. Every request, including `/health`, then requires `Authorization: Bearer <token>`. Use a trusted integration network or a TLS-terminating reverse proxy; this minimal server does not provide TLS. The token grants access to all sessions/resources, with no per-user roles. It is not a multi-user authentication system. Do not distribute provider keys to the GUI. Request bodies, authorization headers, provider errors and tracebacks are not logged by these routes; Uvicorn access logging is disabled. Interactive documentation endpoints are disabled.

## Architecture and state

```text
PyQt5 GUI -> FastAPI JSON routes -> NlpSessionBridge
                                  -> one TextConversationApplication per session
                                     -> existing CLU / DialogueManager
                                     -> existing semantic registry / batch registration
                                     -> existing monitoring / ResponsePlan / NLG
```

Routes perform validation and transport mapping only. `api_dto.py` explicitly maps domain fields to JSON DTOs, including recipient text; internal dataclass names/representations are never serialized. A single task remains an internal `TaskReady`. Only a genuine `BatchReady` registers a domain `DeliveryBatch`.

Sessions have opaque UUID-based server-generated IDs, their own application, pending clarification/draft, task/batch associations and message history. Task and batch IDs use the session namespace and the existing application counters, so they are unique across the running server. Treat all IDs as opaque strings; never derive meaning or construct IDs in the GUI.

A per-session lock encloses idempotency lookup, interpretation, registration and response publication. Different messages in that session cannot overlap. Lock acquisition order determines processing order for concurrently submitted messages; there is no FIFO guarantee. Different sessions can interpret messages concurrently. A shared lock protects session ID allocation and atomic publication of task/batch lookup indexes, without holding it during provider interpretation. Existing batch registration validates all items before committing. An adapter transaction restores conversation, counters and monitoring state if processing/DTO creation fails.

Sessions, messages, task records, batches and cached responses live only in this process. Restart loses everything, including idempotency history. There is no expiry, deletion, persistence, cross-process coordination, queue, or capacity guarantee. Memory use grows with conversations/history. Deploy one process for this phase.

## Endpoints

All request objects reject unexpected fields. Strings must be nonblank and types are strict. Send `Content-Type: application/json`. Except errors, message POST responses use HTTP 200, including social and conversational clarification.

| Method | Path | Success | Purpose |
| --- | --- | --- | --- |
| POST | `/v1/sessions` | 201 | Create a session with body `{}` |
| POST | `/v1/sessions/{sessionId}/messages` | 200 | Interpret one message in that session |
| GET | `/v1/tasks/{taskId}` | 200 | Read the current task monitoring snapshot |
| GET | `/v1/batches/{batchId}` | 200 | Read original batch membership and current snapshots |
| GET | `/health` | 200 | Check HTTP service health only |

There are no execution, event ingestion, loading, dispatch, simulation or navigation endpoints. Text CLI commands starting with `/`, including `/sim`, are rejected with 422 `INVALID_MESSAGE`; use GET endpoints for inspection.

### Create session

Request: `POST /v1/sessions` with `{}`.

Response (201):

```json
{"sessionId":"session_0fd9e06b38014b26a2bd723f3d008779","status":"ACTIVE"}
```

IDs below are shortened for readability and are illustrative. Actual IDs must be taken from responses.

### Message request and unified response

Request fields: `messageId: string`, `text: string`. `messageId` is client-generated and scoped to the session. Preserve the same ID and exact text when retrying.

Response fields: `sessionId`, `messageId`, `dialogueStatus`, `reply`, `batch` (object or null). `reply` is user-facing Vietnamese text produced by the existing response pipeline; its exact wording is not a stable machine interface.

| `dialogueStatus` | Actual application result |
| --- | --- |
| `TASK_READY` | `TaskReady` |
| `BATCH_READY` | `BatchReady` |
| `CLARIFICATION_REQUIRED` | Single or batch clarification |
| `SOCIAL` | Social acknowledgement; pending batch social can instead return clarification |
| `UNSUPPORTED` | Unsupported request |
| `DIALOGUE_ERROR` | Dialogue/context error |
| `DECISION_READY` | Existing validated dialogue decision, without HTTP robot dispatch |
| `TASK_UPDATE_ACCEPTED` | Existing dialogue task update result |
| `PENDING_CANCELLED` | Pending request cancelled |

Only `TASK_READY` and `BATCH_READY` contain a non-null `batch`. Provider failure uses structured HTTP 503, not a fabricated dialogue result/task.

Single example request:

```json
{"messageId":"msg_single","text":"Giao tài liệu đến phòng 2."}
```

Complete single example response:

```json
{
  "sessionId": "session_example",
  "messageId": "msg_single",
  "dialogueStatus": "TASK_READY",
  "reply": "Mình đã ghi nhận yêu cầu giao tài liệu đến phòng 2.",
  "batch": {
    "batchId": null,
    "orderPolicy": null,
    "tasks": [{
      "taskId": "task_single",
      "sequenceIndex": 0,
      "documentText": "tài liệu",
      "documentId": null,
      "recipientText": null,
      "destinationText": "phòng 2",
      "destinationId": "02",
      "priority": "NORMAL",
      "constraints": {"ifBusy": null, "ifClosed": null},
      "lifecycle": "READY",
      "handoff": "NOT_SENT",
      "documentLoad": "UNCONFIRMED"
    }],
    "orderPreview": [{"position": 0, "taskId": "task_single"}]
  }
}
```

Null `batchId` and `orderPolicy` identify an API envelope for one real task. No persistent batch is created; GET that task by its task ID. A real batch has a non-null `batchId` and `PRIORITY` or `STRICT_ORDER` policy.

Multi example request:

```json
{"messageId":"msg_multi","text":"Giao hợp đồng đến phòng 2, báo cáo đến phòng 5 và hồ sơ đến phòng 7. Ưu tiên hồ sơ trước."}
```

Complete multi example response (reply wording illustrative):

```json
{
  "sessionId": "session_example",
  "messageId": "msg_multi",
  "dialogueStatus": "BATCH_READY",
  "reply": "Đã ghi nhận 3 nhiệm vụ. Thứ tự dự kiến: hồ sơ đến phòng 7 trước, sau đó hợp đồng đến phòng 2, sau đó báo cáo đến phòng 5. Robot chưa thực hiện giao.",
  "batch": {
    "batchId": "batch_example",
    "orderPolicy": "PRIORITY",
    "tasks": [
      {
        "taskId": "task_contract", "sequenceIndex": 0,
        "documentText": "hợp đồng", "documentId": null, "recipientText": null,
        "destinationText": "phòng 2", "destinationId": "02", "priority": "NORMAL",
        "constraints": {"ifBusy": null, "ifClosed": null},
        "lifecycle": "READY", "handoff": "NOT_SENT", "documentLoad": "UNCONFIRMED"
      },
      {
        "taskId": "task_report", "sequenceIndex": 1,
        "documentText": "báo cáo", "documentId": null, "recipientText": null,
        "destinationText": "phòng 5", "destinationId": "05", "priority": "NORMAL",
        "constraints": {"ifBusy": null, "ifClosed": null},
        "lifecycle": "READY", "handoff": "NOT_SENT", "documentLoad": "UNCONFIRMED"
      },
      {
        "taskId": "task_dossier", "sequenceIndex": 2,
        "documentText": "hồ sơ", "documentId": null, "recipientText": null,
        "destinationText": "phòng 7", "destinationId": "07", "priority": "HIGH",
        "constraints": {"ifBusy": null, "ifClosed": null},
        "lifecycle": "READY", "handoff": "NOT_SENT", "documentLoad": "UNCONFIRMED"
      }
    ],
    "orderPreview": [
      {"position": 0, "taskId": "task_dossier"},
      {"position": 1, "taskId": "task_contract"},
      {"position": 2, "taskId": "task_report"}
    ]
  }
}
```

`tasks` always remains in original request order. `sequenceIndex` and API preview `position` are zero-based. The serializer calls the existing `build_order_preview`; it only converts the internal one-based preview position to the API convention. PRIORITY uses HIGH, NORMAL, LOW with original sequence as the tie-breaker. STRICT_ORDER preserves the original sequence, including after an explicit conflict choice. A priority phrase alone does not fix the whole batch sequence.

Preview is a semantic order suggestion, not a schedule, route, reservation, navigation confirmation or progress indicator. Rendering preview must not change task states to QUEUED/STARTED. `READY`, `NOT_SENT` and `UNCONFIRMED` remain independent dimensions. `documentId` is unknown/null at intake; semantic document text does not identify a loaded physical document. `ifBusy` / `ifClosed` remain null unless the user explicitly supplies a policy; ASK_USER is not an inferred default.

### Multi-turn clarification and Option A

In the same session, send fresh message IDs:

1. `Giao hợp đồng đến phòng 2 và báo cáo đến phòng 8.` -> `CLARIFICATION_REQUIRED`, `batch:null`, no partial tasks registered.
2. Optional `Chào bạn` -> conversational acknowledgement/clarification, draft preserved.
3. `Phòng 5.` -> entire batch revalidated and registered atomically, `BATCH_READY`.

Complete clarification response shape (reply illustrative):

```json
{
  "sessionId": "session_example",
  "messageId": "msg_draft",
  "dialogueStatus": "CLARIFICATION_REQUIRED",
  "reply": "Bạn muốn giao báo cáo đến phòng nào khác?",
  "batch": null
}
```

For `Giao đúng thứ tự báo cáo đến phòng 5 rồi hợp đồng đến phòng 2, nhưng ưu tiên hợp đồng cao hơn.`, the existing conflict question returns clarification and null batch. Answer `theo mức ưu tiên` with a new ID: the next response is immediately `BATCH_READY`, policy PRIORITY and contract-first preview; no second confirmation. Answering `giữ đúng thứ tự` instead preserves STRICT_ORDER and report-first preview. All tasks remain READY. Internal `orderConfirmed` records conflict-choice acknowledgement; false on a directly valid batch does not mean a second confirmation is required. It is not a robot execution acknowledgement and is not exposed as a new API flag.

### GET task: current snapshot

`GET /v1/tasks/task_single` returns all task fields from the envelope plus `sessionId`, `batchId`, source fields, `lastEvent`, `semantic`, and `robotState`:

```json
{
  "taskId": "task_single", "sequenceIndex": 0,
  "documentText": "tài liệu", "documentId": null, "recipientText": null,
  "destinationText": "phòng 2", "destinationId": "02", "priority": "NORMAL",
  "constraints": {"ifBusy": null, "ifClosed": null},
  "lifecycle": "READY", "handoff": "NOT_SENT", "documentLoad": "UNCONFIRMED",
  "sessionId": "session_example", "batchId": null,
  "lifecycleSource": "NONE", "handoffSource": "NONE", "documentLoadSource": "NONE",
  "lastEvent": null,
  "semantic": {
    "mapId": "office_amcl", "frameId": "map", "doorId": "main_door",
    "stagingPose": {"x": 23.9, "y": -4.15, "yaw": 1.347, "yawDegrees": 77.2}
  },
  "robotState": {
    "robotId": null, "connection": "NOT_CONNECTED", "operatingState": "UNKNOWN",
    "locationId": null, "currentTaskId": null, "carryingDocumentIds": null,
    "lastUpdatedAt": null, "source": "NONE"
  }
}
```

Semantic metadata is null when no corresponding registry/staging pose is available; coordinates are never derived from a room anchor. Pose x/y use the existing map coordinates in metres; yaw is radians, yawDegrees is the supplied degree value. This metadata is not a navigation command.

Source values are `NONE`, `AUTOMATION`, `SIMULATED` from the existing monitor (HTTP never enables simulation). `lastEvent`, when present, is `{type, source, executionEvent, occurredAt}`; executionEvent is nullable, occurredAt is an ISO-8601 timestamp. `lastUpdatedAt` likewise uses ISO-8601 or null. Lifecycle/handoff/document-load values come directly from existing monitor enums. `carryingDocumentIds:null` means unknown; `[]` means an explicitly known empty list. A READY task never implies robot acceptance or document loading.

### GET batch: membership plus current monitoring

`GET /v1/batches/{batchId}` returns `{batchId, sessionId, orderPolicy, tasks, orderPreview, robotState}`. `tasks` is a list of the complete **TaskSnapshot** objects specified immediately above, in original sequence. `orderPreview` contains `{position, taskId}` entries as in message responses. `robotState` has the same complete shape as the task snapshot's robotState and describes the robot separately from individual task states.

Every GET reads current records under the session lock and calls no language provider. A batch can contain a QUEUED task and a READY task; GET preserves both. Message retries return the original cached creation response even if monitoring later changes; use GET for live snapshots. No monitor mutations are exposed in this phase.

### Health

`GET /health` returns:

```json
{"status":"ok","service":"office-robot-nlp","apiVersion":"v1"}
```

This checks the HTTP process only. It does not initialize a provider/session, call Gemini, or claim robot/provider connectivity. On Render, keep this endpoint protected and use Render's default TCP probe, leaving the HTTP health-check path unset.

## Idempotency and retry

Within one session, same messageId plus exactly the same text returns the previous successful HTTP 200 result, including clarification/social results. No parsing or registration repeats. Same ID with different text (including whitespace changes) returns 409. Identical IDs in different sessions are independent. Cached responses are immutable from the caller's perspective.

A failed provider/internal attempt reserves the message ID's text but does not cache a successful result. Retry the same ID/text after 503 or a transient 500; a changed text requires a new ID. The failed turn's application state is rolled back, preserving earlier tasks/pending drafts. A previously successful clarification replayed after later completion returns its original response without restoring the old draft. This guarantees one registration for retries only while the process/session history exists. A restart invalidates IDs and retry history; create a new session and ask the operator how to recover intent instead of automatically resubmitting all deliveries.

## Structured errors

```json
{"error":{"code":"SESSION_NOT_FOUND","message":"Session not found."}}
```

| HTTP | Code | Meaning / client action |
| --- | --- | --- |
| 401 | `UNAUTHORIZED` | Missing/wrong configured bearer token |
| 404 | `SESSION_NOT_FOUND` | Session absent or lost after restart |
| 404 | `TASK_NOT_FOUND`, `BATCH_NOT_FOUND` | Unknown resource ID |
| 409 | `MESSAGE_ID_CONFLICT` | Same session/messageId used with changed text |
| 422 | `INVALID_REQUEST` | Missing/blank/wrong-type fields, malformed JSON, extra fields |
| 422 | `INVALID_MESSAGE` | CLI slash command supplied as conversation text |
| 503 | `PROVIDER_UNAVAILABLE` | Provider setup/request failure, overload (including upstream 429/503), timeout; retry same ID/text |
| 500 | `INTERNAL_ERROR` | Safe generic unexpected error; no traceback or provider details |
| Other HTTP routing status | `HTTP_ERROR` | Unsupported method/path or other framework HTTP error |

Unknown rooms produce HTTP 200 clarification, never a resource 404. Public responses omit exception strings, credentials and provider request internals. There is no automatic retry loop or Retry-After guarantee. Client-side retry/backoff should preserve the pending message ID/text.

## Curl and GUI integration

Portable shell examples (replace returned IDs; on PowerShell use `curl.exe` and JSON files via `--data-binary @request.json` if shell quoting differs):

```bash
curl -s http://127.0.0.1:8000/health
curl -s -X POST http://127.0.0.1:8000/v1/sessions -H 'Content-Type: application/json' -d '{}'
curl -s -X POST http://127.0.0.1:8000/v1/sessions/SESSION_ID/messages \
  -H 'Content-Type: application/json' \
  -d '{"messageId":"msg_single","text":"Giao tài liệu đến phòng 2."}'
curl -s -X POST http://127.0.0.1:8000/v1/sessions/SESSION_ID/messages \
  -H 'Content-Type: application/json' \
  -d '{"messageId":"msg_multi","text":"Giao hợp đồng đến phòng 2, báo cáo đến phòng 5 và hồ sơ đến phòng 7. Ưu tiên hồ sơ trước."}'
curl -s http://127.0.0.1:8000/v1/tasks/TASK_ID
curl -s http://127.0.0.1:8000/v1/batches/BATCH_ID
```

When a token is configured, add `-H 'Authorization: Bearer TOKEN'` to every call. A complete PowerShell request without native curl JSON quoting issues:

```powershell
$base = 'http://127.0.0.1:8000'
$session = Invoke-RestMethod -Method Post -Uri "$base/v1/sessions" -ContentType 'application/json' -Body '{}'
$payload = @{ messageId = 'msg_001'; text = 'Giao tài liệu đến phòng 2.' } | ConvertTo-Json
$result = Invoke-RestMethod -Method Post -Uri "$base/v1/sessions/$($session.sessionId)/messages" -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($payload))
$taskId = $result.batch.tasks[0].taskId
Invoke-RestMethod -Uri "$base/v1/tasks/$taskId"
```

The GUI should keep one session ID per conversation, generate a new message ID per user turn, show `reply`, and handle the same `batch.tasks` list for single/multi results. Show `tasks` as original request order and `orderPreview` as a separate proposed order. Show independent monitor dimensions and robot connectivity separately. Send clarification replies to the same session. Use an asynchronous Qt network client or worker so blocking HTTP never freezes the UI. GET may wait for an ongoing same-session message to finish; choose timeouts to accommodate provider timeout and serialize turns on the client. A timed-out POST can still finish on the server: retry using the same ID/text. Never turn preview/READY into a navigation or dispatch instruction.

## Offline verification and limits

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m unittest discover -s tests -p "test_http_api.py" -v
.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
.venv\Scripts\python.exe -m compileall office_robot_nlp tests
.venv\Scripts\python.exe http_conversation_demo.py
```

The deterministic demo uses an in-process HTTP client and fake parser; it needs no key/network socket. It prints complete JSON requests/responses for health, session creation, TaskReady, unknown-room/social/followup BatchReady, GET task/batch monitoring, three-item priority preview, conflict choice and idempotent retry. Generated IDs vary; semantic outcomes are deterministic. `batch_conversation_demo.py` and `text_conversation.py` remain available.

This phase supplies semantic conversation and monitoring inspection only. There is no robot gateway, ROS communication, scheduler, route optimization, physical control, loading/acceptance API, GUI implementation, persistence or multi-user access isolation. Provider connectivity and physical robot integration have not been verified by offline tests.
