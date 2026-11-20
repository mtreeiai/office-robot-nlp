# Phase 7A — Robot team handoff

Server repository only: `mtreeiai/office-robot-nlp`. Target staging base: **https://office-robot-nlp-1.onrender.com**. This is the owner's target, not a claim that this release/configuration is already deployed. Server-side verification is local with fake language understanding and dedicated PostgreSQL. No Robot repository, HMI, ROS/Nav2 component, scheduler or execution gate was accessed or changed.

## 1. Endpoints and credentials

| Purpose | Endpoint | Credential |
| --- | --- | --- |
| Health (existing) | GET `/health` | Bearer `<API_ACCESS_TOKEN>` |
| Create NLP session | POST `/v1/sessions`, JSON `{}` | Bearer `<API_ACCESS_TOKEN>` |
| Interpret a message | POST `/v1/sessions/{sessionId}/messages` | Same backend bearer |
| Existing task/batch monitor | GET `/v1/tasks/{taskId}`, GET `/v1/batches/{batchId}` | Same backend bearer |
| Check Calendar window | POST `/v1/robot/availability/check` | Bearer `<CALENDAR_ROBOT_TOKEN>` — separate persisted read token |

Credentials are provisioned/transferred privately by the owner. The Robot read token is a named hashed DB credential, not a publicly exposed website credential. Setting an environment variable alone does not insert it into the token table. Reuse the existing provisioned token; no credential rotation or provisioning is required by Phase 7A. Backend bearer cannot authenticate the Calendar read endpoint, and Robot read token cannot authenticate NLP or booking writes. Browser USER/ADMIN sessions remain a third, separate boundary.

## 2. NLP exchange

```http
POST /v1/sessions
Authorization: Bearer <API_ACCESS_TOKEN>
Content-Type: application/json

{}
```

201:

```json
{"sessionId":"session_11111111111141118111111111111111","status":"ACTIVE"}
```

```http
POST /v1/sessions/session_11111111111141118111111111111111/messages
Authorization: Bearer <API_ACCESS_TOKEN>
Content-Type: application/json

{"messageId":"m1","text":"Giao tài liệu đến phòng 01."}
```

The complete 200 response is [nlp-single.json](examples/nlp-single.json), generated from the actual API. The corresponding [three-room priority batch](examples/nlp-batch.json) contains the full request/response too. These fixtures use an illustrative frozen clock and fake NLU, not live staging data. Every existing field remains: sessionId/messageId/dialogueStatus/reply/batch, plus the two feature-enabled arrays.

`TASK_READY` means NLP validated/registered a task. A single still has one task in `batch.tasks`, null batchId/orderPolicy and its existing orderPreview. It does not mean the robot may navigate. Each task gets:

```json
{"taskId":"task_session_11111111111141118111111111111111_001","roomId":"01","status":"PENDING_AVAILABILITY_CHECK","allowed":false,"reasonCode":"ARRIVAL_WINDOW_REQUIRED"}
```

Read advisory schedule information from roomAvailability and render the deterministic Vietnamese reply if useful. Do not decide physical execution from text, task readiness, FREE or preliminary deliveryDecision. With no ready envelope, both arrays are empty. If the fields are absent, enrichment is disabled or an older server is running; absence must not become permission. Full schema: [execution contract](execution-contract.md).

## 3. Concrete Calendar window

Supported canonical room IDs: string **01–07**. Existing spoken aliases and zero-padded forms such as phòng 01 resolve to these IDs. Coordinates are outside Calendar and are not supplied by this contract.

```http
POST /v1/robot/availability/check
Authorization: Bearer <CALENDAR_ROBOT_TOKEN>
Content-Type: application/json

{
  "roomId":"01",
  "operation":"DOCUMENT_DELIVERY",
  "arrivalWindow":{"start":"2026-11-23T09:00:00+07:00","end":"2026-11-23T09:05:00+07:00"}
}
```

Complete exact response examples: [FREE/ALLOW](examples/arrival-free.json) and [BUSY/DEFER](examples/arrival-busy.json). Fixture requests use equivalent UTC timestamps (02:00–02:05 UTC is 09:00–09:05 in Vietnam). The API returns all existing fields: roomId, calendarStatus, deliveryDecision, deliveryAllowed, deliveryPolicy, reasonCode, checkType, arrivalWindow, checkedAt, validUntil, calendarVersion, busyUntil, nextAvailableFrom, calendarChanged and dataSource. No new endpoint or final execution-approval field.

The Robot supplies its estimated arrival through the end of handoff, not just an instantaneous ETA or the original speech time. Require offset-aware times; API timestamps are canonical UTC, Vietnamese display normally uses Asia/Ho_Chi_Minh. Synchronize clocks. Window duration must be positive and ≤8 hours; start no more than 30 days ahead. Never submit a naive local timestamp.

Omitted arrivalWindow asks for PRELIMINARY, using configurable seven-minute travel / three-minute handoff estimates, not actual measured robot travel. It cannot yield final permission. Explicit valid window yields ARRIVAL_WINDOW. An expired window gives UNKNOWN/WINDOW_EXPIRED rather than ALLOW. Invalid ranges/offsets yield 422.

## 4. Decision mapping

| Final Calendar result | Robot integration requirement (team to implement) |
| --- | --- |
| ALLOW, deliveryAllowed=true, ARRIVAL_WINDOW | Eligible only with fresh matching evidence and all local execution/safety conditions |
| DEFER | Do not proceed on that result; consider nextAvailableFrom and recheck any new window |
| DENY | Do not proceed under the current policy |
| NEEDS_APPROVAL | Manager approval has not been supplied by this API; do not treat as ALLOW |
| UNKNOWN, missing/unrecognized fields | Eligibility unverified; do not proceed on this result |

Policies remain unchanged: BLOCK_WHEN_BUSY permits unbooked windows; ALLOW_DOOR_HANDOFF can return BUSY **and** ALLOW; BUSINESS_HOURS_ONLY requires the whole unbooked window inside configured hours; MANAGER_APPROVAL always requires an external approval process; DISABLED denies. Disabled room returns UNKNOWN calendar status with DENY delivery decision. Bookability controls meeting booking separately from document-delivery policy.

FREE alone does not mean deliveryAllowed or recipient presence. BUSY alone does not imply DENY: respect the actual delivery policy. Neither `executionDecisions.allowed=false` nor the separate final ALLOW changes task priority/constraints/order. No automatic task skipping or scheduling is supplied.

## 5. Freshness, retries and task correlation

- Validate the returned room/window against the request, `checkType=ARRIVAL_WINDOW`, deliveryDecision/deliveryAllowed consistency, non-null version and nonexpired validUntil. Default TTL is 30 seconds, capped by window end; UNKNOWN expires immediately.
- Recheck immediately before the Robot's execution decision and after delays/reconnects/changed ETA or handoff duration. Newer calendarVersion invalidates older evidence even within TTL. Optional knownCalendarVersion reports whether the current snapshot differs; false is not proof that no later write can happen.
- A suggested free slot is not reserved. This service has no push invalidation, reservation or atomic lock spanning robot motion. A concurrent calendar edit after the response remains possible; the Robot team must handle its integration policy and local safety authority.
- NLP replay with the same sessionId/messageId/text returns the original cached arrays/reply, even after schedule changes/expiry. It does not perform a fresh Calendar query. Same ID with changed text returns 409. New message IDs can register new tasks; do not replace transport retries with new IDs automatically.
- Session/task IDs remain in memory in one process. After server restart, old sessions may return 404; reconcile robot state before recreating or replaying work. Calendar sessions/bookings persist separately.
- For batches join both NLP arrays to `batch.tasks` by exact taskId. Two tasks in the same room retain different IDs. Retain taskId + requested window in the local HTTP request context: the final Calendar API accepts/returns roomId and intentionally has no taskId. Do not add taskId to its strict payload or correlate concurrent calls solely by roomId.
- Final checks are stateless reads; they do not change the original NLP executionDecisions or grant a persisted Server execution approval. Keep the two stages distinct in the Robot's own implementation.

## 6. Failures

| Condition | Handling requirement |
| --- | --- |
| Network timeout, TLS error, malformed JSON/shape | No verified permission; preserve task/request context and reconcile |
| 401 | Missing/invalid/revoked or wrong-scope token; correct credentials privately |
| 403 | Access denied by a server/proxy/permission boundary; do not bypass; HTTP 403 is different from a normal 200 DENY policy result |
| 404 | Missing in-memory NLP session/task or missing resource; reconcile, do not assume execution status |
| 409 MESSAGE_ID_CONFLICT | Reused message ID with different text; correct request identity |
| 422 | Invalid operation/room/time/window/payload; correct it, do not infer FREE |
| 503 PROVIDER_UNAVAILABLE | Existing NLP provider failed; retry the same message only when appropriate; no successful task invented |
| 503 CALENDAR_UNAVAILABLE | Calendar DB unavailable during authentication/management; no Calendar permission |
| 200 with UNKNOWN | Read/authentication succeeded far enough to return unverified evidence; still no permission |

Error envelope: `{"error":{"code":"...","message":"..."}}`. Do not expose credentials in UI/logs. Use bounded HTTP timeouts in the Robot client; this document does not implement retries, scheduling or motion. A healthy `/health` alone does not verify provider or Calendar DB readiness.

## 7. Before any Nav2 goal — Robot team's remaining work

Implement and test in the Robot team's own repository: HMI rendering of pending/advisory state, separate credential handling, reliable HTTP/task/window correlation, an execution gate using fresh final evidence, handling of all policy/error/expiry/reconnect states, preservation of existing user constraints and order, task/execution-state reconciliation, and local safety/navigation checks. Actual navigation, handover/presence checks, cancellation authority and execution status remain the Robot team's responsibility. Server-side tests do not establish any of these behaviors.

Recommended acceptance: feature off/on; single FREE/BUSY; same-room multiple tasks; multi-room priority/strict order with unchanged constraints; clarification; replay; provider/DB/network outage; invalid/revoked tokens; bad/expired/changed windows; version changes; every policy including BUSY door handoff and FREE manager approval; application/robot reconnect. First use the Server fake client/local test suite, then jointly test authorized staging and actual Robot behavior. No Robot/Nav2 E2E verification is claimed.

Owner deploy/smoke instructions: [Phase 7A deployment](phase-7a-deployment.md). Server results/limits: [Phase 7A report](phase-7a-server-report.md).
