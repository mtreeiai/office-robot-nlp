"""Mixin for robot_mission_control; no HTTP/backend authority over safety."""

import json

from robot_edge_gateway.authority import MissionPolicy


class DeliveryMissionMixin:
    def install_delivery_integration(self):
        from std_msgs.msg import Bool, String
        from rclpy.clock import Clock, ClockType
        self.declare_parameter("delivery_integration_enabled", False)
        self.delivery_policy = MissionPolicy()
        self.delivery_watchdog_clock = Clock(clock_type=ClockType.STEADY_TIME)
        self.delivery_authority_pub = self.create_publisher(String, "/navigation/authority", 1)
        self.create_subscription(String, "/office_robot/delivery_request", self.delivery_request, 1)
        self.create_subscription(Bool, "/emergency_stop", lambda message: self.delivery_policy.emergency(message.data), 1)

    def delivery_request(self, message):
        try:
            packet = json.loads(message.data)
            if (not isinstance(packet, dict) or set(packet) != {"assignmentId", "bootId"}
                    or not isinstance(packet["bootId"], str)
                    or packet["assignmentId"] is not None and not isinstance(packet["assignmentId"], str)):
                raise ValueError("Invalid gateway identity")
            self.delivery_policy.request(packet["assignmentId"], packet["bootId"])
        except (ValueError, TypeError):
            self.delivery_policy.request(None, None)

    def delivery_robot_status(self, message):
        emergency_state = message.robot_state.upper() in {"EMERGENCY", "ESTOP", "E_STOP", "FAULT"}
        healthy = message.lidar_ok and message.odom_ok and message.localization_ok and not emergency_state
        self.delivery_policy.status(healthy, message.battery_low or message.water_low)

    def tick_with_delivery(self):
        from std_msgs.msg import String
        # Sensor-derived resource flags are not read back from BT actions, some
        # of which in the upstream code clear simulated flags internally.
        packet = self.delivery_policy.packet()
        enabled = self.get_parameter("delivery_integration_enabled").value
        if not enabled or self.blackboard.get("CUSTOMER_detected", False):
            if packet["mode"] == "DELIVERY":
                packet.update(mode="LOCAL", assignmentId=None, bootId=None)
        self.delivery_authority_pub.publish(String(data=json.dumps(packet)))
        if packet["mode"] == "BLOCKED":
            return  # Navigation manager cancels; no simulated BT completion runs.
        if packet["mode"] == "DELIVERY":
            return  # Delivery replaces patrol only; urgent branches already won.
        self.tree.tick(self.blackboard)
