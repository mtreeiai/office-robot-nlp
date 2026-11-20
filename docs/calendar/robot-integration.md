# Robot integration guide — read-only calendar

This module provides calendar evidence. It has not added a scheduler, ROS node, Nav2 hook or robot-side enforcement. Existing `TASK_READY` means the same thing as before; it is not evidence that the calendar permitted execution. With NLP enrichment enabled, executionDecisions explicitly remains PENDING_AVAILABILITY_CHECK/allowed=false per task until a separate arrival-window check is consumed by the Robot. The Server does not persist or grant robot execution authorization.

Phase 7A staging target: `https://office-robot-nlp-1.onrender.com` (deployment not verified by local tests). See [execution contract](execution-contract.md), [Robot team handoff](robot-team-handoff.md) and [owner deployment/smoke instructions](phase-7a-deployment.md).

## Credentials / connection

Backend operator provisions a named random robot read token using the Calendar CLI, then sends it privately to the robot team. Use `CALENDAR_ROBOT_TOKEN`, never browser passwords or backend API_ACCESS_TOKEN. A robot token authenticates only `POST /v1/robot/availability/check`; booking create/update/cancel are rejected. NLP requests continue to use their existing backend credential separately.

For another machine, use an authorized HTTPS staging origin with a valid certificate, e.g. `https://<calendar-staging-host>`. Database access is unnecessary and must stay private. Localhost HTTP is for same-machine development only. Do not expose the development trust-authenticated PostgreSQL port.

## Fake Robot Client

After authorized staging setup, copy/checkout this repo on the robot development machine. The client needs only Python's standard library and does not import ROS or language providers.

PowerShell:

```powershell
# Supply the token privately in the environment, not in command-line arguments.
$env:CALENDAR_ROBOT_TOKEN='<privately provisioned read token>'
python -m scripts.calendar_fake_robot --base-url 'https://<calendar-staging-host>' --room 02 --arrival-start '2026-11-20T14:30:00+07:00' --duration-seconds 300
```

Linux:

```bash
# CALENDAR_ROBOT_TOKEN must already be exported from private configuration.
python3 -m scripts.calendar_fake_robot --base-url 'https://<calendar-staging-host>' --room 02 --arrival-start '2026-11-20T14:30:00+07:00' --duration-seconds 300
```

Choose an actual future time within 30 days; the sample date is illustrative. Include the entire expected arrival/handover interval. Omitting `--arrival-start` requests PRELIMINARY evidence: configured seven-minute travel and three-minute handoff are estimates, not measured robot ETA, and cannot yield ALLOW. `--known-version 8` compares a previous version to the current snapshot. The client refuses remote HTTP and redirects so it does not forward credentials to another origin. It sends no motion command.

Direct request:

```http
POST /v1/robot/availability/check
Authorization: Bearer <robot-read-token>
Content-Type: application/json

{"roomId":"02","operation":"DOCUMENT_DELIVERY","arrivalWindow":{"start":"2026-11-20T14:30:00+07:00","end":"2026-11-20T14:35:00+07:00"},"knownCalendarVersion":8}
```

See [complete response/error contract](api-contract.md). Same-machine smoke is verified automatically against a real loopback HTTP server and PostgreSQL by the browser smoke script. Calling an actual remote staging host/Render and robot-side decisions are not yet verified.

## Consumer behavior to implement/test separately

1. Resolve canonical room ID `01`–`07`; obtain current execution ETA and complete handoff window.
2. Query the read endpoint. Network failure, timeout, invalid response, 401/403/503 or UNKNOWN must not become FREE/ALLOW. Do not treat an HTTP 200 alone as permission.
3. Check `checkType=ARRIVAL_WINDOW`, operation/room/window, nonexpired `validUntil`, `deliveryDecision`, `calendarVersion` and current local safety requirements. Synchronize server/robot clocks. Refresh immediately before execution and after delays/reconnects/window changes.
4. DEFER: consider the suggested nextAvailableFrom, then recheck that new window; do not assume it is reserved. DENY: respect policy; NEEDS_APPROVAL: require an external manager decision (no approval token/workflow exists here). UNKNOWN: reconcile/ask operator.
5. Calendar BUSY can coexist with ALLOW under ALLOW_DOOR_HANDOFF. FREE under MANAGER_APPROVAL does not allow delivery. Keep calendarStatus and deliveryDecision separate.
6. Any newer version invalidates old evidence, even before TTL expiry. The API cannot lock out a write that occurs after its response; integrate robot-side rechecks and operator policy accordingly. TTL is not a reservation or permanent navigation permit.

NLP enrichment is advisory and PRELIMINARY. Its message replay cache intentionally returns exactly the original evidence and executionDecisions; query this independent API for freshness instead of resending speech with the same message ID. No task registration, priority or order policy changes occur in Calendar. Match each advisory and stage entry to batch.tasks by exact taskId, including multiple tasks with the same room. The final read API accepts/returns roomId, not taskId; retain the requesting task and window locally for correlation, without adding unsupported fields to the strict request payload.

## Joint staging acceptance

Admin approves a room for booking; create a meeting; verify an overlapping arrival returns BUSY/DEFER and a nonoverlapping full window returns FREE/ALLOW under BLOCK_WHEN_BUSY. Cancel/edit as owner/admin and verify version and evidence change. Try an unauthorized user write, robot booking write, invalid token, expired window, DB outage and application restart. Repeat concurrent overlap tests in the disposable test environment. Verify door-handoff/manager/business policies individually if enabled. Then separately demonstrate that the actual robot honors those decisions without bypassing local safety.
