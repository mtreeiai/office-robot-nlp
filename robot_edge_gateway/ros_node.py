"""Humble/Nav2 adapter. Motion remains disabled without fresh local authority.

This module intentionally imports ROS only in main, so backend/offline tests have
no ROS runtime dependency. It must not run alongside another navigation owner
unless the local mission controller enforces exclusive delivery authorization.
"""

import json
import math
import os
import queue
import threading
import time
from uuid import UUID

from robot_edge_gateway.gateway import RobotEdgeGateway
from robot_edge_gateway.store import EdgeStore
from robot_edge_gateway.transport import HttpsBackend


def main(args=None):
    import rclpy
    from rclpy.action import ActionClient
    from rclpy.node import Node
    from rclpy.clock import Clock, ClockType
    from action_msgs.msg import GoalStatus
    from robot_interfaces.action import DeliveryGo
    from std_msgs.msg import Bool, String
    from unique_identifier_msgs.msg import UUID as GoalUUID

    rclpy.init(args=args)
    node = Node("office_robot_edge_gateway")
    watchdog_clock = Clock(clock_type=ClockType.STEADY_TIME)
    node.declare_parameter("allow_motion", False)
    node.declare_parameter("navigation_action", "/navigation/delivery_go")
    node.declare_parameter("local_state_path", "office_robot_edge.sqlite3")
    results, scans = queue.Queue(), queue.Queue()

    class Navigator:
        def __init__(self):
            self.client = ActionClient(node, DeliveryGo, node.get_parameter("navigation_action").value)
            self.allowed_at = self.stopped_at = 0.0
            self.allowed = self.stopped = False
            self.busy, self.handle, self.cancel_requested = False, None, False
            self.scope = (None, None)
            self.lock = threading.RLock()

        def authorized(self):
            return (node.get_parameter("allow_motion").value and self.allowed
                    and time.monotonic() - self.allowed_at < 1.5)

        def safe_stopped(self):
            return self.stopped and time.monotonic() - self.stopped_at < 1.5 and not self.busy

        def start(self, goal_id, pose, callback):
            with self.lock:
                if not self.authorized() or self.busy or not self.client.server_is_ready():
                    raise ValueError("Local navigation authority/server is unavailable")
                goal = DeliveryGo.Goal()
                goal.assignment_id, goal.boot_id = self.scope
                goal.goal_id = goal_id
                goal.pose.header.frame_id = pose["frameId"]
                goal.pose.header.stamp = node.get_clock().now().to_msg()
                goal.pose.pose.position.x, goal.pose.pose.position.y = float(pose["x"]), float(pose["y"])
                goal.pose.pose.orientation.z = math.sin(pose["yaw"] / 2)
                goal.pose.pose.orientation.w = math.cos(pose["yaw"] / 2)
                self.busy, self.cancel_requested = True, False
                goal_uuid = GoalUUID(uuid=list(UUID(goal_id).bytes))

                def result_ready(future):
                    try:
                        response = future.result()
                        success = response.status == GoalStatus.STATUS_SUCCEEDED and response.result.success
                    except Exception:
                        success = False
                    with self.lock:
                        self.busy, self.handle = False, None
                    results.put((goal_id, success))  # Navigation success is ARRIVED only.

                def accepted(future):
                    try:
                        handle = future.result()
                        if not handle.accepted:
                            raise ValueError("Goal rejected")
                        with self.lock:
                            self.handle = handle
                            if self.cancel_requested or not self.authorized():
                                handle.cancel_goal_async()
                        handle.get_result_async().add_done_callback(result_ready)
                    except Exception:
                        with self.lock:
                            self.busy = False
                        results.put((goal_id, False))

                self.client.send_goal_async(goal, goal_uuid=goal_uuid).add_done_callback(accepted)

        def cancel(self):
            with self.lock:
                self.cancel_requested = True
                if self.handle is not None:
                    self.handle.cancel_goal_async()
                # Cancellation acceptance is not proof the robot is stopped.

    navigator = Navigator()

    def authority(message):
        try:
            packet = json.loads(message.data)
            allowed = (packet.get("authorized") is True and
                       (packet.get("assignmentId"), packet.get("bootId")) == navigator.scope)
        except (ValueError, TypeError, AttributeError):
            allowed = False
        navigator.allowed, navigator.allowed_at = allowed, time.monotonic()
        if not allowed:
            navigator.cancel()

    def stopped(message):
        navigator.stopped, navigator.stopped_at = message.data, time.monotonic()

    def scan(message):
        try:
            content = json.loads(message.data)
            if set(content) != {"challengeId", "token"} or not all(isinstance(v, str) and v for v in content.values()):
                raise ValueError("Invalid scan")
            scans.put(content)
        except (ValueError, TypeError):
            node.get_logger().warning("QR scan format rejected; no scan content logged.")

    node.create_subscription(String, "/office_robot/delivery_authorized", authority, 1)
    node.create_subscription(Bool, "/office_robot/safe_stopped", stopped, 10)
    node.create_subscription(String, "/office_robot/qr_scan", scan, 10)
    node.create_timer(0.1, lambda: navigator.cancel() if navigator.busy and not navigator.authorized() else None, clock=watchdog_clock)
    try:
        transport = HttpsBackend(os.environ["NLP_API_URL"], os.environ["NLP_API_TOKEN"], os.environ["ROBOT_GATEWAY_TOKEN"])
        store = EdgeStore(node.get_parameter("local_state_path").value)
        gateway = RobotEdgeGateway(store, transport, navigator)
    except Exception:
        node.destroy_node()
        rclpy.shutdown()
        raise SystemExit("Gateway configuration failed; check local state and credentials privately.") from None
    stop = threading.Event()
    request_pub = node.create_publisher(String, "/office_robot/delivery_request", 1)

    def request_authority():
        scope = (store.active(), gateway.boot_id)
        if navigator.scope != scope:
            navigator.allowed = False
        navigator.scope = scope
        request_pub.publish(String(data=json.dumps({"assignmentId": scope[0], "bootId": scope[1]})))

    node.create_timer(0.2, request_authority, clock=watchdog_clock)

    def worker():
        while not stop.is_set():
            try:
                while not results.empty():
                    gateway.navigation_result(*results.get_nowait())
                while not scans.empty():
                    gateway.scan_qr(scans.get_nowait())
                gateway.tick()
            except Exception:
                node.get_logger().warning("Gateway synchronization blocked; durable state retained, no automatic motion retry.")
            stop.wait(1.0)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        rclpy.spin(node)
    finally:
        stop.set()
        navigator.cancel()
        thread.join(timeout=20)
        if not thread.is_alive():
            store.close()
        node.destroy_node()
        rclpy.shutdown()
