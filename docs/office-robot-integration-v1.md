# Office Robot Integration v1 — implemented development slice

Contract baseline: 2026-11-13. **V1 is not yet complete against the full Definition of Done.** Steps 1–5 have backend/edge implementations and real PostgreSQL + fake-navigation integration tests. The selected robot_mission_control + robot_navigation_manager integration is implemented and tested with real Humble/DDS actions against a fake Nav2 server; see [selected-stack integration](ros2-selected-stack.md). Actual Nav2 planning/Gazebo missions and controlled hardware tests remain. Motion defaults to disabled. No physical robot was commanded and no cloud database/service was deployed.

## Architecture and authority

The existing `office_robot_nlp` message/session/task/batch endpoints and JSON shapes remain. New `office_robot_backend` modules implement durable operational records in PostgreSQL. Installing/configuring this backend is explicit; without DATABASE_URL, the NLP-only service behaves as before. `robot_edge_gateway` is a separate ROS-independent Python component with SQLite/WAL and an ordered event outbox. `ros2/office_robot_edge_gateway` packages its optional ROS 2 adapter.

```text
Existing NLP v1 -> successful semantic snapshot
                  -> explicit Draft intake with per-task pickup selection
PostgreSQL: Draft -> validation -> atomic operator confirm -> queue
                  -> priority/FIFO scheduler + dependencies -> assignment/outbox
Authenticated HTTPS pull/event ingestion <-> SQLite gateway/outbox
                                              -> local authority -> Nav2 pickup
                                              -> pickup QR -> Nav2 delivery
                                              -> handover QR -> durable completion
```

PostgreSQL is the sole operational authority. The existing in-memory NLP monitoring objects remain semantic/legacy monitors; do not use their READY value as an execution status. Query the operational request/assignment endpoints for execution progress. Operational task IDs are distinct UUIDs linked by source_task_id to the original NLP ID; a single-task envelope still has no fake domain batch. No new NLP pickup inference or LLM-driven motion policy was added: missing pickup selection is rejected.

The gateway polls a persisted assignment; this is at-least-once delivery, never an exactly-once network claim. Durable identities, ordered events and local start-intent records prevent duplicate driving. Unknown motion is held for explicit reconciliation, not retried. Local controller/safety authorization always outranks the backend. The edge never publishes cmd_vel or disables obstacle/E-stop authority.

## Database and setup

Install the optional dependencies, configure DATABASE_URL privately, and apply migrations **before** enabling operational routes:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-operations.txt
.venv\Scripts\python.exe -m office_robot_backend.admin migrate
# Set OPERATOR_TOKEN and ROBOT_GATEWAY_TOKEN privately, then:
.venv\Scripts\python.exe -m office_robot_backend.admin provision --locations path/to/approved-locations.json
.venv\Scripts\python.exe -m office_robot_nlp.http_api
```

Keep the existing API_ACCESS_TOKEN required on public binds. Use PostgreSQL TLS/credentials and backups appropriate to the deployment. Do not put connection URLs/tokens into source, command output or chat. Migration is transactional and checksum-verified with `operational_migrations`; startup does not automatically alter database schema. No SQLite backend fallback exists.

For local development only, `docker compose -f compose.operations.yaml up -d postgres` creates PostgreSQL bound to 127.0.0.1:5433 with a named persistent volume. It requires POSTGRES_PASSWORD supplied privately; use the matching DATABASE_URL. This is not a cloud deployment. Never use the production database for the test-schema create/drop workflow.

For a future Render operational deployment, change its build command explicitly to `pip install -r requirements-operations.txt` before setting DATABASE_URL, apply migrations/provisioning through an authorized administrative environment, and keep the existing one-worker/token/HTTPS settings. The checked-in NLP-only Blueprint is deliberately not switched to a database deployment automatically. SQLite belongs on the robot's persistent filesystem, not the ephemeral Render instance.

Provisioning file shape (coordinates below are **synthetic example coordinates, not robot-approved geometry**):

```json
{
  "operatorId": "operator-1", "robotId": "office-robot-1",
  "mapId": "operator-approved-map-version", "frameId": "map",
  "locations": [
    {"id":"pickup-a","name":"Pickup A","pickupAllowed":true,"deliveryAllowed":false,"pose":{"x":0.0,"y":0.0,"yaw":0.0,"frameId":"map"}},
    {"id":"pickup-b","name":"Pickup B","pickupAllowed":true,"deliveryAllowed":false,"pose":{"x":1.0,"y":0.0,"yaw":0.0,"frameId":"map"}},
    {"id":"02","name":"Approved Room 2 staging","pickupAllowed":false,"deliveryAllowed":true,"pose":{"x":2.0,"y":0.0,"yaw":0.0,"frameId":"map"}}
  ]
}
```

For real operation, import the seven existing registry staging poses unchanged and add surveyed/approved pickup poses; no real pickup coordinates were supplied in the contract. Map-version IDs must identify the matching Nav2 map/frame. Same-version pose/role changes are rejected; create a new version rather than rewriting geometry. Disabling locations/maps requires an explicit administrative database operation; confirmation and scheduling revalidate enabled roles/map state. This development slice does not supply a map-management GUI or automatic map switching.

The migration creates all 13 approved entities: operator_accounts, map_versions, locations, delivery_requests, delivery_tasks, task_dependencies, robots, robot_assignments, navigation_goals, qr_challenges, execution_events, outbox_events. Tokens are stored as SHA-256 digests of high-entropy credentials (minimum 24 characters for provisioned operator/robot tokens), not plaintext passwords. Roles are separate credentials; no password/login system is claimed.

Constraints enforce one configured robot, one unresolved assignment per robot, one assignment per task, one package per task, unique source NLP task, request key, event ID and assignment sequence. Foreign keys bind pickup and destination to the same map version. Strict-order batches create predecessor chains without changing task priority or original sequence indexes. Explicit within-request dependency edges `[predecessorIndex, dependentIndex]` are validated as a DAG. A failed/cancelled predecessor does not silently unblock dependents.

Operational writes use a PostgreSQL transaction-level advisory lock shared across server processes. This intentionally favors simple correctness for one robot over throughput. Scheduler also locks candidate rows. Confirmation validates the whole batch, then atomically allocates FIFO queue_order values in original order. Queue selection is HIGH before NORMAL before LOW, then confirmation FIFO, respecting mandatory dependencies and current map/location eligibility. It does not optimize routes or deadlines. No external network call occurs while holding an operational transaction.

## Additional HTTP endpoints

All use the existing structured error envelope. Public calls still need `Authorization: Bearer <API_ACCESS_TOKEN>`. Operator routes additionally require `X-Operator-Token`; gateway routes require `X-Robot-Token`. HTTP transport must be HTTPS remotely. Existing NLP routes/fields are unchanged.

| Method | Path | Authorization / behavior |
| --- | --- | --- |
| POST | `/v1/operations/requests` | Operator; create/replay a Draft from a successful cached NLP result |
| GET | `/v1/operations/requests/{requestId}` | Owning operator; current persisted task states/associations |
| POST | `/v1/operations/requests/{requestId}/confirm` | Owning operator; atomic DRAFT → CONFIRMED and all tasks → QUEUED |
| POST | `/v1/operations/requests/{requestId}/cancel` | Owning operator; cancel unassigned work, request safe cancellation of active work |
| POST | `/v1/operations/assignments/{assignmentId}/qr` | Owning operator; issue/rotate challenge only after arrival at the requested stage |
| POST | `/v1/operations/assignments/{assignmentId}/reconcile` | Owning operator; explicit stopped/package-accounted resolution after failure/unknown/cancellation |
| POST | `/v1/gateway/heartbeat` | Robot; report boot ID, local assignment and fresh local stopped evidence |
| GET | `/v1/gateway/assignment` | Robot; get the one unresolved assignment or atomically select eligible queued work |
| GET | `/v1/gateway/assignments/{assignmentId}` | Robot; current mission and immutable goals |
| POST | `/v1/gateway/events` | Robot; durable idempotent contiguous-sequence event ingestion |

Polling GET assignment is intentionally a scheduler/claim operation for the authenticated gateway, not a read-only operator monitor. Repeated polling returns the same unresolved assignment. No network retry creates another assignment.

### NLP to Draft, then confirmation

First use the existing `/v1/sessions/{sessionId}/messages`. On TASK_READY/BATCH_READY capture the task IDs. Then POST operations/requests:

```json
{
  "idempotencyKey":"operator-request-001",
  "sessionId":"<returned NLP session>",
  "messageId":"<successful NLP message>",
  "mapId":"<approved version>",
  "pickups":{"<first NLP task ID>":"pickup-a","<second NLP task ID>":"pickup-b"},
  "dependencies":[[0,1]]
}
```

Use exactly one valid pickup ID per task. `dependencies` is optional; strict ordering automatically adds the full chain. Response: `{requestId,state,orderPolicy,tasks,createdAt,confirmedAt}`. Task fields in this new operational endpoint are explicit database column names (id, source_task_id, sequence_index, document_text, package_count, map_version_id, pickup_location_id, delivery_location_id, priority, state, queue_order, request_id). Priority is stored as LOW=0/NORMAL=1/HIGH=2. Existing NLP task DTO names/values are unchanged.

POST `.../{requestId}/confirm` with no body after reviewing the complete Draft. A direct NLP result or Draft never enters the scheduler. Same operator/idempotencyKey and identical source references/pickups/map/dependencies replays the durable request; changed input returns 409. The original semantic snapshot is stored, and creation retry still works after the NLP session disappears. A different key cannot register an already-used source task again. Semantic intake not yet persisted as a Draft remains subject to the old in-memory NLP-session limitation.

### Gateway protocol

Heartbeat body:

```json
{"bootId":"<new process UUID>","activeAssignmentId":null,"safeStopped":true}
```

Unknown/stale state is not eligible for new work. Initial IDLE requires a fresh stopped report with no local assignment. A changed boot ID or mismatched active assignment after acceptance blocks normal transitions; it never proves the robot stopped. Even completed delivery requires a fresh edge-idle report before new work.

Assignment response: `{assignmentId,robotId,taskId,state,resolvedAt,lastSequence,goals}`. Goals contain immutable IDs, assignment_id, leg PICKUP/DELIVERY, pose `{x,y,yaw,frameId}` and state BLOCKED/READY/STARTED/ARRIVED/FAILED. Neither leg is READY before ACK; DELIVERY stays BLOCKED until a valid pickup QR. Goal IDs double as Nav2 action UUIDs.

Event body:

```json
{"eventId":"<durable UUID>","assignmentId":"<assigned ID>","sequence":1,"kind":"ACK","payload":{}}
```

Allowed events: ACK, NAV_STARTED, ARRIVED, QR_SCANNED, FAILED, EXECUTION_UNKNOWN, CANCELLED_SAFE. NAV_STARTED/ARRIVED payload is `{goalId}`. Sequence is contiguous per assignment and allocated/persisted in SQLite, not regenerated on retry. Same event ID and content replays; changed content, sequence gaps or illegal transitions are rejected. Events and resulting states/outbox notifications commit together. No event named COMPLETED is accepted from the gateway.

After arrival, issue QR with `{"leg":"PICKUP"}` or `{"leg":"DELIVERY"}`. The authenticated operator receives `{challengeId,token,assignmentId,leg}` to encode/display as a QR. The scanner submits `{"challengeId":"...","token":"..."}` locally; gateway emits QR_SCANNED with that payload. Backend validates assignment, current stage, one-time digest and ten-minute expiry. Rotation expires earlier unconsumed challenges. Tokens are not stored in backend execution-event bodies; they can temporarily exist in the edge's durable unsent-event payload, so protect the SQLite directory with OS permissions and backups/access controls. There is no camera/recipient identity inference.

PICKUP QR releases the delivery navigation goal. ARRIVED at delivery leaves WAITING_DELIVERY_QR. Only the valid delivery QR transaction marks the operational task COMPLETED and resolves the assignment. Duplicate arrival/QR events do not complete twice. An invalid QR/event is rejected without advancing the backend sequence; the conservative gateway retains rejected events instead of skipping them. Correcting an invalid queued scan requires controlled local/outbox reconciliation in this development slice; a production scanner UX/rejection-repair protocol remains to be implemented. It must not silently drop execution events or renumber accepted events.

Cancellation never releases an active robot by itself. Gateway asks Nav2 to cancel, but safe cancellation needs fresh local stopped evidence. FAILED, UNKNOWN and CANCELLED assignments remain unresolved. Owning operator must account for the package, obtain a fresh stopped report for that same assignment, and POST reconcile with `{"packageAccountedFor":true}`. Reconciliation terminates FAILED/CANCELLED, never COMPLETED; queued dependents remain blocked. A subsequent fresh edge-idle report is required. There is no automatic failover/reassignment or implicit replay of unknown motion. Supported recovery is conservative termination/reconciliation; automatic mid-mission resumption is not claimed.

## Edge process and ROS 2 handoff

`EdgeStore` uses SQLite WAL, synchronous=FULL, one active local assignment and atomic event/sequence updates. ACK is persisted before transmission. Navigation start intent and NAV_STARTED event are durable before Nav2 is called. Crash in either gap leaves UNKNOWN; START_REQUESTED goals are never driven again. Outbox delivery is ordered and remains until a successful backend response; a lost ACK response retries the same ID/content. Duplicate action callbacks are suppressed. Local-authority denial prevents driving even when the backend has queued work.

The operator confirmed `robot_mission_control + robot_navigation_manager`, using Humble as in the repository README. The [reviewed seven-file stack overlay](../ros2/stack_overlay/manifest.json) now integrates exclusive admission and safety preemption into those nodes. The gateway calls typed DeliveryGo through navigation manager, which is the sole managed Nav2 client. The overlay was applied to the isolated local checkout; no upstream GitHub push was performed. A reference fetch encountered a Windows-invalid filename; unrelated paths were not rewritten or treated as intended deletions.

On the Linux ROS machine, copy the prepared package into the **selected robot workspace** and install the shared core from this repo:

```bash
source /opt/ros/humble/setup.bash
cd /path/to/nlp-repository
python3 -m scripts.apply_ros_stack_overlay ~/ros2_ws
python3 -m pip install --no-deps -e /path/to/nlp-repository
cp -R /path/to/nlp-repository/ros2/office_robot_edge_gateway ~/ros2_ws/src/
cd ~/ros2_ws
rosdep install --from-paths src/robot_interfaces src/robot_mission_control src/robot_navigation_manager src/office_robot_edge_gateway --ignore-src -r -y
colcon build --packages-select robot_interfaces robot_mission_control robot_navigation_manager office_robot_edge_gateway --symlink-install
source install/setup.bash
```

Configure NLP_API_URL, NLP_API_TOKEN and ROBOT_GATEWAY_TOKEN privately. Use a persistent local SQLite path, then start in locked mode:

```bash
ros2 run office_robot_edge_gateway gateway --ros-args \
  -p allow_motion:=false -p use_sim_time:=true \
  -p local_state_path:=/private/persistent/office_robot_edge.sqlite3
```

The adapter uses scoped DeliveryGo through navigation manager; it never assumes cancel acceptance is stopped evidence. `/office_robot/delivery_authorized` is now scoped String JSON (assignment/boot/authorized), `/office_robot/safe_stopped` is Bool, and both must be fresh within 1.5 seconds. `allow_motion=true` is also required. Authorization loss cancels the active request. `/office_robot/qr_scan` is String containing the two QR fields; payloads/credentials are not logged. Network/SQLite processing runs off the ROS executor thread.

**The selected-stack authority integration is implemented and passes real ROS action tests; physical safety is not yet validated.** Do not supply blanket authorized/stopped signals on hardware. Stop old duplicate manager processes and use the reviewed replacement launch. Connect the actual E-stop source and verify actual Nav2 status freshness, map/localization and odometry before simulation enablement. Package license/maintainer metadata is a placeholder to be completed by repository owners before distribution.

Remaining simulation acceptance work requires actual Nav2 planning/Gazebo with both QR stages, real safety/status sources, network/restart/cancellation recovery and package accounting. Humble/DDS admission and cancellation were tested in an isolated container with fake Nav2, separately from PostgreSQL/edge mission tests; this is not a complete backend-to-Gazebo mission. Hardware testing follows simulation acceptance and an operator-controlled safety procedure; no hardware connection or test is claimed.

## Verification and completion status

PostgreSQL tests require a dedicated test database and create/drop randomly named schemas only inside that database. Set OFFICE_ROBOT_TEST_DATABASE_URL, never a production URL. Without it the PostgreSQL integration class explicitly skips; this is not a database verification pass.

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
# Configure OFFICE_ROBOT_TEST_DATABASE_URL privately, then:
.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
.venv\Scripts\python.exe -m compileall office_robot_nlp office_robot_backend robot_edge_gateway ros2 tests
.venv\Scripts\python.exe -m pip check
git diff --check
```

Tests cover PostgreSQL migrations/constraints/transactions, concurrent scheduler and confirmation, Draft source idempotency after NLP restart, priority/FIFO and strict dependencies, invalid locations/QR/events, two-leg completion, SQLite crashes/outbox ACK loss, and fake-gateway HTTP/PostgreSQL missions. No live LLM or real robot is used. A fake mission test is a backend/edge protocol integration test, not Gazebo/Nav2 simulation or hardware evidence.

Remaining DoD gates are explicit: actual Nav2/Gazebo missions, real E-stop/status wiring, surveyed pickup/map provisioning, production rejection-repair/operator UX, and controlled hardware validation. The selected mission-controller integration and ROS action wiring are now implemented/tested as documented separately. Do not label the complete v1 system ready for robot execution until the remaining gates pass.

### Initial backend/edge verification (before selected-stack follow-up)

- **387 tests passed**, including all 348 previous tests, 27 new PostgreSQL/HTTP/FakeRobotGateway tests and 12 SQLite/edge tests. The dedicated PostgreSQL test URL was configured for this run; none of those tests was skipped.
- PostgreSQL server **17.12** ran in an isolated temporary Docker container bound only to 127.0.0.1. Tests created/dropped uniquely named schemas; no production database was used. The temporary container was stopped after verification.
- `compileall office_robot_nlp office_robot_backend robot_edge_gateway ros2 tests`, `pip check` and `git diff --check` passed. Existing Starlette's non-failing HTTPX deprecation warning and Git CRLF notices remain.
- The edge-core editable Python package built and installed successfully. This does not claim a ROS colcon build, Nav2 simulation or hardware test.
- ROS reference revision inspected: `48f90ce027c99bc864d666dbf047dbd86e2d82e1`. The new package was also copied into the isolated local reference checkout at `.venv/office-robot-reference/src/office_robot_edge_gateway`; canonical reviewable sources are under `ros2/office_robot_edge_gateway` here. Existing mission-control/navigation source was inspected, not rewritten or pushed.

Subsequent selected-stack changes, colcon builds and real DDS action results are documented in [ros2-selected-stack.md](ros2-selected-stack.md); the historical 387-test count above is the baseline for that follow-up.
