# Phase 7A — Server Calendar execution contract

This contract extends the existing NLP v1 response when **both** `CALENDAR_ENABLED=true` and `CALENDAR_NLP_ENABLED=true`. It adds stage metadata; it does not dispatch, schedule, approve execution or update a task lifecycle. Feature disabled: legacy response fields/semantics stay intact and neither extension field appears.

## Three separate meanings

| Field / response | Meaning | Execution implication |
| --- | --- | --- |
| `dialogueStatus=TASK_READY` / `BATCH_READY` | Existing NLP validation/registration succeeded | Arrival-window verification still required |
| `roomAvailability[]` | Deterministic Calendar evidence for estimated arrival, one item per ready task | PRELIMINARY, advisory only |
| `executionDecisions[]` | Explicit preliminary stage of each ready task | Always pending and allowed=false |
| `/v1/robot/availability/check` with explicit arrivalWindow | Authoritative Calendar snapshot for that exact room/window | Robot must evaluate fresh Calendar result and local execution/safety conditions independently |

Even a final Calendar ALLOW is not a Nav2 permission or proof of recipient presence. The Server does not implement the Robot's execution gate. A subsequent final check does not change executionDecisions in the original/cached NLP response.

## Actual NLP shape

`POST /v1/sessions` with JSON `{}` returns `{"sessionId":"<opaque ID>","status":"ACTIVE"}`. Send `{"messageId":"<unique message ID>","text":"Giao tài liệu đến phòng 01."}` to `/v1/sessions/{sessionId}/messages` with the existing backend bearer.

Existing response fields are unchanged:

| Field | Shape |
| --- | --- |
| sessionId, messageId | Existing strings |
| dialogueStatus | Existing status literal |
| reply | Existing Vietnamese reply plus deterministic Calendar feedback when a ready envelope exists |
| batch | Existing envelope, or null for non-ready turns |
| batch.batchId / orderPolicy | Both null for single; existing ID/policy for a batch |
| batch.tasks[] | taskId, sequenceIndex, documentText, documentId, recipientText, destinationText, destinationId, priority, constraints, lifecycle, handoff, documentLoad |
| batch.orderPreview[] | Existing `{position,taskId}` preview; not altered by Calendar |
| roomAvailability[] | Existing Calendar evidence plus taskId |
| executionDecisions[] | New, typed stage entries described below |

The single task is inside `batch.tasks[0]`; there is no new top-level task object. Full request/response fixtures were generated from the actual local API with fake language understanding, PostgreSQL and a frozen illustrative clock:

- [Single task: room 01](examples/nlp-single.json): TASK_READY, batchId/orderPolicy null, one task and one entry in each extension array. The reply explicitly requires checking the actual arrival/handover window.
- [Three-room priority batch](examples/nlp-batch.json): tasks to 02/05/07 keep original sequence indices 0/1/2 and NORMAL/NORMAL/HIGH; the existing preview places task 3 first. Each task retains its own evidence and pending stage, without reordering the tasks or aggregating authorization.

These JSON documents contain complete examples, not replacements or abbreviated alternative DTOs. IDs/times are illustrative and credentials are absent. Displayed example timestamps are not fresh staging evidence.

New stage schema, identical for single and batch:

```json
{
  "taskId":"task_session_11111111111141118111111111111111_001",
  "roomId":"01",
  "status":"PENDING_AVAILABILITY_CHECK",
  "allowed":false,
  "reasonCode":"ARRIVAL_WINDOW_REQUIRED"
}
```

The fields status/allowed/reasonCode have fixed literals, independent of Calendar policy. No secondary policy engine or aggregate allowed flag is introduced. Consumers must not convert advisory FREE or ALLOW into a different stage value. The current Calendar implementation additionally downgrades preliminary ALLOW to UNKNOWN/ETA_NOT_PROVIDED; this stronger existing rule remains unchanged. A defensive regression also supplies hypothetical advisory ALLOW and verifies executionDecisions.allowed remains false.

## Association and eligibility

- Entries are built only for existing tasks in a ready response envelope. Exact `taskId` joins both arrays to `batch.tasks`; `roomId` equals that task's canonical destinationId. Preserve IDs verbatim; do not parse their textual format.
- Two tasks sharing room 01 have different taskIds and separate entries. Do not deduplicate task decisions by room or treat one window check as permission for every task in that room.
- Task IDs are allocated by the existing application, namespaced by HTTP session and sequenced locally. They remain stable for the task and exact message replay while the owning in-memory session exists. This is not durable task identity across server restart; the existing NLP session limitation is unchanged.
- Clarification, invalid/unresolved destination, dialogue error, social, cancellation, updates/decisions without a new ready envelope: `batch=null`, `roomAvailability=[]`, `executionDecisions=[]`. Prior existing tasks are not silently authorized. A completed clarification gets entries only when the original pipeline emits a ready envelope.
- Priority, constraints, strict-order policy, original sequence, orderPreview and task registration/lifecycle are not changed by enrichment. This contract defines no batch scheduler, skipping, reordering or retry queue.

## Preliminary versus final

NLP calls the existing service without an explicit window, using configured travel/handoff estimates (defaults seven/three minutes). It always marks `checkType=PRELIMINARY`. No Calendar data comes from Gemini. Busy, unavailable, manager-approval, disabled and business-hours feedback is deterministic Python text with Vietnamese display times; canonical evidence timestamps stay UTC.

Robot calls the existing `POST /v1/robot/availability/check` using a **separate** robot read token, operation DOCUMENT_DELIVERY and its full actual expected arrival/handover interval. [FREE/ALLOW example](examples/arrival-free.json), [BUSY/DEFER example](examples/arrival-busy.json), [complete API contract](api-contract.md). Final response intentionally has no taskId or executionDecisions: the caller keeps its requesting task/window context. Do not add taskId to the strict request.

| Evidence field | Consumer meaning |
| --- | --- |
| calendarStatus | FREE/BUSY/UNKNOWN schedule evidence, not occupancy |
| deliveryDecision | ALLOW/DEFER/DENY/NEEDS_APPROVAL/UNKNOWN after existing policy |
| deliveryAllowed | True only when the Calendar decision is ALLOW; not full robot authorization |
| checkType | ARRIVAL_WINDOW required for a concrete check; PRELIMINARY cannot authorize execution |
| arrivalWindow | Exact checked start/end; changes require a fresh check |
| checkedAt / validUntil | UTC evidence timestamp / expiry; UNKNOWN expires immediately |
| calendarVersion | Room version in the consistent read snapshot; newer version invalidates older evidence |
| busyUntil / nextAvailableFrom | Advisory timing; suggested slot is not reserved |

Default TTL 30 seconds, capped by window end. Unknown data never permits execution. Changes after response are possible; there is no server reservation, push invalidation or transaction spanning robot motion. Recheck when delayed/reconnected and immediately before the Robot's execution decision, as implemented/tested by its team.

## Compatibility, replay and errors

The base MessageResponse/TaskDTO/BatchDTO and task/batch monitoring responses are unchanged. The Calendar-specific subclass adds both arrays only behind the existing feature flag. Clients using strict legacy JSON decoders must either support the enabled extension or keep NLP Calendar integration disabled; there is no new status such as ROOM_BUSY replacing TASK_READY.

Same session/messageId/text replays the **entire original response**, including now-expired evidence and pending stage; it does not query Calendar again. Same messageId with changed text returns 409 MESSAGE_ID_CONFLICT. New message IDs can create new tasks; do not use a new ID as a transport retry. Existing sessions/tasks are lost on application restart; do not treat SESSION_NOT_FOUND as an instruction to replay physical work automatically.

API errors keep `{error:{code,message}}`. Calendar query outage yields UNKNOWN evidence and pending stage; an outage while verifying a robot token can yield 503 CALENDAR_UNAVAILABLE. NLP provider outage remains 503 PROVIDER_UNAVAILABLE, never a successful task or permission. Missing/invalid/revoked tokens return 401; treat any 403, malformed response, timeout, unavailable service or unknown status as unverified. Detailed handling and Robot responsibilities: [handoff](robot-team-handoff.md).
