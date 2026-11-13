"""Real Humble/DDS action integration with a fake Nav2 server, never hardware.

This checks ROS wiring/admission/cancellation, not Gazebo physics or Nav2 planning.
"""

import json
import threading
import time
from uuid import uuid4

import rclpy
from rclpy.action import ActionClient, ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import qos_profile_action_status_default
from rclpy.task import Future
from action_msgs.msg import GoalStatusArray, GoalStatus
from nav2_msgs.action import NavigateToPose
from nav_msgs.msg import Odometry
from std_msgs.msg import Bool, String
from robot_interfaces.msg import RobotStatus
from robot_interfaces.srv import GetLocation
from robot_interfaces.action import DirectGo, DeliveryGo
from robot_mission_control.mission_controller_node import MissionControllerNode
from robot_navigation_manager.navigation_manager_node import NavigationManagerNode
from robot_edge_gateway.navigation_ledger import DeliveryGoalLedger


def wait(predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("ROS integration condition timed out")
        time.sleep(0.02)


class FakeNav2(Node):
    def __init__(self):
        super().__init__("test_fake_nav2")
        self.group = ReentrantCallbackGroup()
        self.entries, self.starts, self.cancels = [], 0, 0
        self.complete, self.delay_accept = False, 0
        self.accepting = threading.Event()
        self.server = ActionServer(self, NavigateToPose, "navigate_to_pose", self.execute,
            goal_callback=self.goal, cancel_callback=self.cancel, callback_group=self.group)
        self.status = self.create_publisher(GoalStatusArray, "navigate_to_pose/_action/status", qos_profile_action_status_default)
        self.create_timer(0.05, self.tick, callback_group=self.group)

    def goal(self, request):
        self.accepting.set()
        if self.delay_accept:
            time.sleep(self.delay_accept)
        return GoalResponse.ACCEPT

    def cancel(self, handle):
        self.cancels += 1
        return CancelResponse.ACCEPT

    async def execute(self, handle):
        self.starts += 1
        future = Future()
        self.entries.append((handle, future))
        await future
        if handle.is_cancel_requested:
            handle.canceled()
        else:
            handle.succeed()
        return NavigateToPose.Result()

    def tick(self):
        statuses = []
        for handle, future in list(self.entries):
            item = GoalStatus()
            item.goal_info.goal_id = handle.goal_id
            item.status = handle.status
            statuses.append(item)
            if not future.done() and (self.complete or handle.is_cancel_requested):
                future.set_result(True)
        message = GoalStatusArray()
        message.status_list = statuses
        self.status.publish(message)


class Signals(Node):
    def __init__(self):
        super().__init__("test_local_signals")
        self.enabled, self.estop, self.moving = False, False, False
        self.battery, self.water, self.request_enabled = False, False, True
        self.scope = (None, str(uuid4()))
        self.robot_pub = self.create_publisher(RobotStatus, "/robot_status", 10)
        self.odom_pub = self.create_publisher(Odometry, "/odom", 10)
        self.estop_pub = self.create_publisher(Bool, "/emergency_stop", 1)
        self.request_pub = self.create_publisher(String, "/office_robot/delivery_request", 1)
        self.create_timer(0.05, self.tick)
        self.create_service(GetLocation, "/config/get_location", self.location)
        self.direct = ActionClient(self, DirectGo, "/navigation/direct_go")
        self.delivery = ActionClient(self, DeliveryGo, "/navigation/delivery_go")

    def location(self, request, response):
        response.success = True
        response.x, response.y, response.yaw = 1.0, 0.0, 0.0
        return response

    def tick(self):
        if not self.enabled:
            return
        status = RobotStatus()
        status.lidar_ok = status.odom_ok = status.localization_ok = True
        status.battery_low, status.water_low = self.battery, self.water
        self.robot_pub.publish(status)
        odom = Odometry()
        odom.twist.twist.linear.x = 0.2 if self.moving else 0.0
        self.odom_pub.publish(odom)
        self.estop_pub.publish(Bool(data=self.estop))
        if self.request_enabled:
            self.request_pub.publish(String(data=json.dumps({"assignmentId": self.scope[0], "bootId": self.scope[1]})))

    def direct_goal(self):
        goal = DirectGo.Goal()
        goal.target_location = "test-room"
        return self.direct.send_goal_async(goal)

    def delivery_goal(self, scope=None, goal_id=None):
        goal = DeliveryGo.Goal()
        goal.assignment_id, goal.boot_id = scope or self.scope
        goal.goal_id = goal_id or str(uuid4())
        goal.pose.header.frame_id = "map"
        goal.pose.pose.orientation.w = 1.0
        return self.delivery.send_goal_async(goal)


def main():
    rclpy.init(args=["--ros-args", "-p", "delivery_integration_enabled:=true"])
    nav2, signals = FakeNav2(), Signals()
    manager, mission = NavigationManagerNode(), MissionControllerNode()
    try:
        duplicate = DeliveryGoalLedger(manager.get_parameter("delivery_goal_ledger_path").value, acquire_owner=True)
    except RuntimeError:
        pass
    else:
        duplicate.close()
        raise AssertionError("Two local navigation-manager owners must not share the ledger")
    executor = MultiThreadedExecutor(num_threads=8)
    for node in (nav2, signals, manager, mission):
        executor.add_node(node)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    try:
        wait(lambda: signals.direct.server_is_ready() and signals.delivery.server_is_ready())
        rejected = signals.direct_goal()
        wait(rejected.done)
        assert not rejected.result().accepted, "Missing safety signals must reject motion"
        signals.enabled = True
        wait(manager.authority.safe_stopped)
        wait(lambda: manager.authority.packet and manager.authority.packet["mode"] == "LOCAL")

        first, competing = signals.direct_goal(), signals.direct_goal()
        wait(lambda: first.done() and competing.done())
        assert sum(handle.result().accepted for handle in (first, competing)) == 1
        wait(lambda: nav2.starts == 1)
        signals.moving, signals.estop = True, True
        wait(lambda: nav2.cancels >= 1)
        wait(lambda: manager.authority.slot and manager.authority.slot["finished"])
        signals.estop = False
        wait(lambda: manager.authority.packet["mode"] == "LOCAL")
        rejected = signals.direct_goal()
        wait(rejected.done)
        assert not rejected.result().accepted, "Cancellation is not stopped evidence"
        signals.moving = False
        wait(manager.authority.safe_stopped)

        signals.scope = (str(uuid4()), signals.scope[1])
        wait(lambda: manager.authority.packet["mode"] == "DELIVERY")
        direct, wrong = signals.direct_goal(), signals.delivery_goal(scope=("wrong-assignment", "wrong-boot"))
        wait(lambda: direct.done() and wrong.done())
        assert not direct.result().accepted and not wrong.result().accepted
        delivery_id = str(uuid4())
        delivery = signals.delivery_goal(goal_id=delivery_id)
        wait(delivery.done)
        assert delivery.result().accepted
        wait(lambda: nav2.starts == 2)
        result = delivery.result().get_result_async()
        nav2.complete = True
        wait(result.done)
        assert result.result().result.success
        nav2.complete = False
        wait(manager.authority.safe_stopped)
        repeated, direct = signals.delivery_goal(goal_id=delivery_id), signals.direct_goal()
        wait(lambda: repeated.done() and direct.done())
        assert not repeated.result().accepted and not direct.result().accepted

        for flag in ("battery", "water"):
            delivery = signals.delivery_goal()
            wait(delivery.done)
            assert delivery.result().accepted
            before = nav2.cancels
            wait(lambda: manager.nav2_goal_handle is not None)
            setattr(signals, flag, True)
            wait(lambda: nav2.cancels > before)
            wait(manager.authority.safe_stopped)
            setattr(signals, flag, False)
            wait(lambda: manager.authority.packet["mode"] == "DELIVERY")

        # Loss of the gateway lease must revoke a moving goal even with fresh health.
        delivery = signals.delivery_goal()
        wait(delivery.done)
        assert delivery.result().accepted
        wait(lambda: manager.nav2_goal_handle is not None)
        before = nav2.cancels
        # Local watchdogs must still run when the simulation clock is paused.
        manager.set_parameters([Parameter("use_sim_time", value=True)])
        mission.set_parameters([Parameter("use_sim_time", value=True)])
        signals.request_enabled = False
        wait(lambda: nav2.cancels > before)
        wait(manager.authority.safe_stopped)

        # Revocation before a late Nav2 acceptance must still cancel that goal.
        signals.scope = (None, signals.scope[1])
        signals.request_enabled = True
        wait(lambda: manager.authority.packet["mode"] == "LOCAL")
        nav2.delay_accept = 0.5
        nav2.accepting.clear()
        late = signals.direct_goal()
        wait(late.done)
        assert late.result().accepted
        wait(nav2.accepting.is_set)
        late_result = late.result().get_result_async()
        before = nav2.cancels
        signals.estop = True
        wait(lambda: nav2.cancels > before)
        wait(late_result.done)
        assert not late_result.result().result.success
        print("PASS: Humble colcon + real DDS actions: fail-closed startup, concurrent admission, scoped delivery, E-stop/resource/lease preemption, stopped evidence, duplicate goals and late-ACK cancellation.")
    finally:
        executor.shutdown(timeout_sec=5)
        thread.join(timeout=5)
        for node in (mission, manager, signals, nav2):
            node.destroy_node()
        manager.goal_ledger.close()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
