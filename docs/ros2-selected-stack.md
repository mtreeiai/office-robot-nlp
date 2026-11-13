# Delivery authority for robot_mission_control + robot_navigation_manager

Selected by the operator on 2026-11-13. This extends the reviewed ROS repository revision `48f90ce027c99bc864d666dbf047dbd86e2d82e1`. The backend/QR/task semantics in [Office Robot Integration v1](office-robot-integration-v1.md) are retained.

## Implemented boundary

`robot_navigation_manager` is now the **only managed Nav2 client**. The gateway no longer calls NavigateToPose directly. It calls the new `robot_interfaces/action/DeliveryGo` action at `/navigation/delivery_go`, carrying assignment_id, gateway boot_id, immutable goal_id and the validated pose. Existing HMI/patrol clients retain `/navigation/direct_go` and its DirectGo action.

Both action servers share one locked admission policy. The slot is reserved in goal_callback before asynchronous execute_callback, including while a location lookup or Nav2 acceptance is pending. DirectGo is rejected for the entire delivery lease, including the QR waiting intervals. DeliveryGo must match the current assignment and gateway boot, have a finite normalized pose in the configured map frame, and use an unused backend goal UUID.

Navigation manager also records every accepted delivery goal ID before returning ACCEPT in its local SQLite ledger. Replayed/changed goal IDs remain rejected across manager restarts. A Linux file lock prevents a second manager from opening the same ledger concurrently; all launches must use the same fixed persistent ledger path, not create new paths to bypass ownership. Do not delete either SQLite database to make a failed mission run again.

## Mission priority and failure behavior

Mission controller issues scoped LOCAL / DELIVERY / BLOCKED authority packets. Fresh health and fresh explicit E-stop state are required; startup no longer assumes the three sensor-health flags are true. E-stop, sensor faults or stale safety input produce BLOCKED. Battery/water urgency and existing customer service preempt delivery using LOCAL. Delivery takes priority over patrol. Existing non-delivery BT branches remain otherwise unchanged; some upstream charging/refill/customer branches are simulated/log-only and are **not certified physical recovery actions** by this change.

Sensor-derived resource urgency is kept separately from mutable BT flags, so a simulated BT flag clear cannot grant delivery against a current low-battery/water report. Missing gateway request heartbeat returns the controller to LOCAL. Invalid input or stale/replayed grants cannot extend the existing goal's lease. Controller restart revokes the old goal; a later grant cannot revive that revoked goal.

Revocation cancels the current Nav2 request, including a request acknowledged late. The slot remains held until a terminal Nav2 outcome **and** fresh stationary odometry/status evidence. A cancel request or accepted cancellation alone does not establish a stopped robot. Unknown/timed-out Nav2 outcomes hold the resource, request cancellation and require controlled reconciliation; there is no automatic motion retry.

Watchdogs use STEADY_TIME/monotonic time, so pausing `/clock` cannot pause lease expiration or safety cancellation. Nav2 goal timestamps still use the ROS clock. This software gate complements the robot's independent physical safety/E-stop system; it does not replace it or prove a hardware stopping distance.

## Local protocol and required sources

| Topic | Producer → consumer | Meaning |
| --- | --- | --- |
| `/robot_status` (RobotStatus) | Existing system monitor → mission controller | Health and resource flags, required fresh within 1.5 seconds |
| `/emergency_stop` (Bool) | Actual local safety source → mission controller | Explicit E-stop state, required fresh within 1.5 seconds; absent means BLOCKED |
| `/office_robot/delivery_request` (String JSON) | Gateway → mission controller | `{assignmentId,bootId}`, refreshed every 0.2 seconds |
| `/navigation/authority` (String JSON) | Mission controller → navigation manager | `{controllerId,sequence,mode,assignmentId,bootId}`, refreshed every 0.1 seconds |
| `/office_robot/delivery_authorized` (String JSON) | Navigation manager → gateway | `{assignmentId,bootId,authorized}` after scoped local policy validation |
| `/office_robot/safe_stopped` (Bool) | Navigation manager → gateway | Confirmed local idle/stationary evidence, not merely Nav2 cancel acceptance |
| `/odom` (Odometry) | Real/sim odometry → navigation manager | Fresh finite twist; all six components within 0.02 for at least 0.5 seconds |
| `navigate_to_pose/_action/status` (GoalStatusArray) | Nav2 action-status source → navigation manager | Fresh actual goal-state evidence; accepted/executing/canceling blocks idle |
| `/office_robot/qr_scan` (String JSON) | Local scanner → gateway | `{challengeId,token}`, never logged |

The gateway's authorization topic changed from the earlier preparatory Bool prototype to scoped JSON; deploy all overlay packages and the gateway together. This is an internal ROS change; HTTP NLP v1 field shapes are unchanged.

Default status/odometry freshness is 1.5 seconds. The action test server publishes actual tracked goal states at 20 Hz. **Before real Nav2 enablement, verify that the selected Nav2 deployment provides fresh status at this boundary.** An event-only/stale status stream will fail closed; do not feed a blanket idle heartbeat or fabricate stopped/authorized messages. If that deployment needs a proper status observer, validate it with actual Nav2 rather than weakening the gate. Likewise, connect `/emergency_stop` to the real safety source; this repository did not establish what physical E-stop publisher the hardware uses.

These topics/actions assume a trusted local ROS graph and exclusive routing through the patched manager. Third-party nodes that directly command Nav2 or cmd_vel bypass this software arbitration and must not be enabled as competing motion owners. No SROS2 deployment/security certification is claimed.

## Apply to the selected ROS repository

Canonical reviewable overlay: `ros2/stack_overlay`. Seven files are added/modified: DeliveryGo.action, robot_interfaces CMake/package metadata, mission controller source/metadata, navigation manager source/metadata. Installation checks the inspected source hashes and refuses unknown local edits before writing any target. It is idempotent for an already-applied matching overlay.

On the ROS Linux machine:

```bash
cd /path/to/nlp-repository
python3 -m scripts.apply_ros_stack_overlay /path/to/office-robot
python3 -m pip install --no-deps -e /path/to/nlp-repository
cp -R ros2/office_robot_edge_gateway /path/to/office-robot/src/
cd /path/to/office-robot
source /opt/ros/humble/setup.bash
rosdep install --from-paths src/robot_interfaces src/robot_mission_control src/robot_navigation_manager src/office_robot_edge_gateway --ignore-src -r -y
colcon build --packages-select robot_interfaces robot_mission_control robot_navigation_manager office_robot_edge_gateway
source install/setup.bash
```

If hashes differ, review/merge that branch's actual changes; the installer has no force mode. Do not copy the partial Windows reference checkout wholesale or interpret its unrelated missing working-tree paths as intended deletions. Only the allowlisted overlay files are part of this change. No commit or push to the robot repository was performed.

Stop the previous mission-controller/navigation-manager processes before launching their replacements; leave the selected localization/Nav2/safety/odometry stack available. Configure HTTPS gateway credentials privately as previously documented. Start the integration with **motion locked**:

```bash
ros2 launch office_robot_edge_gateway selected_stack.launch.py \
  use_sim_time:=true allow_motion:=false \
  edge_state_path:=/private/persistent/office_robot_edge.sqlite3 \
  navigation_ledger_path:=/private/persistent/office_robot_navigation.sqlite3
```

The launch file starts the two patched managers and gateway, not a second Nav2 stack. Motion remains disabled unless allow_motion is explicitly enabled **and** all local scoped/safety prerequisites hold. Topic sources, map/localization and node uniqueness must be validated in actual simulation before hardware. Do not run synthetic false E-stop/true-authorization publishers on hardware.

## Verification

Unit tests in `test_navigation_authority.py` exercise priority/staleness, lease replay/controller restart, atomic concurrent admission, sticky revocation, cancel/stop distinction, persistent goal IDs, and safe overlay installation.

The reproducible Docker context contains only allowlisted code/packages, no .env, credentials, maps, databases or model files:

```bash
python -m scripts.prepare_ros_stack_test /path/to/office-robot .venv/ros2-stack-test-context
docker build -t office-robot-ros2-stack-test .venv/ros2-stack-test-context
docker run --rm --network none office-robot-ros2-stack-test
```

Verified here with ROS 2 Humble in Docker: **all four packages build with colcon**, and the real rclpy/DDS action integration harness passes. Scenarios include missing safety signals, two simultaneous DirectGo requests, delivery lease blocking DirectGo, wrong assignment/boot, duplicate goal UUID, normal navigation result forwarding, E-stop/resource/lease preemption, paused simulation clock, cancellation without stopped evidence and late Nav2 acceptance cancellation.

The Nav2 server in this harness is explicitly fake; it uses real generated Nav2/action interfaces and DDS but does not plan or move a robot. This is not an actual Nav2 planner/Gazebo test, a complete backend-to-Gazebo mission or a hardware result. Those acceptance gates, surveyed pickup poses, real status/E-stop wiring and remaining operator/scanner recovery UX still prevent declaring Integration v1 complete.

### Final results for the selected-stack follow-up

- **410/410 unittest tests passed**, including the 387-test backend/NLP/edge baseline and 23 new authority/ledger/overlay tests. The isolated PostgreSQL test URL was set; no PostgreSQL tests were skipped.
- **Four colcon packages built**: robot_interfaces, robot_mission_control, robot_navigation_manager, office_robot_edge_gateway, using Python 3.10 in ROS 2 Humble. Installed Nav2 message package: ros-humble-nav2-msgs 1.1.20.
- **Real Humble/DDS action harness passed** inside a `--network none` Docker container. It also verifies that a second manager cannot acquire the same local ledger lock.
- Installed `selected_stack.launch.py --show-args` passed and confirms `allow_motion=false`, `use_sim_time=true`, and separate persistent edge/manager SQLite paths.
- `compileall`, `pip check`, root/ROS-overlay `git diff --check` passed; existing non-failing HTTPX deprecation and CRLF notices remain. Overlay reapplication reports zero changed files (idempotent).
- The temporary PostgreSQL container and ROS test containers were stopped/removed. The local build image remains available for reproducibility. No production database, provider quota, hardware or remote repository mutation was used.

New source files are under robot_edge_gateway (authority, mission integration, navigation ledger), ros2/stack_overlay, the selected-stack launch file, the safe overlay/context scripts, and tests/test_navigation_authority.py plus tests/ros2. The gateway ROS adapter and package metadata were updated to use DeliveryGo. Backend scheduling, QR semantics and HTTP NLP v1 were not changed by this follow-up.
