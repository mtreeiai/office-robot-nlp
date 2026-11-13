"""ROS-independent local admission policy for the selected mission/nav stack.

Navigation manager is the only Nav2 client. All methods are guarded by one lock;
an action goal reserves the slot in goal_callback, before execute_callback runs.
"""

import math
import threading
import time
from uuid import uuid4


class MissionPolicy:
    def __init__(self, clock=time.monotonic, ttl=1.5):
        self.clock, self.ttl = clock, ttl
        self.controller_id, self.sequence = str(uuid4()), 0
        self.status_at = self.estop_at = self.request_at = None
        self.healthy, self.urgent, self.estop = False, False, True
        self.scope = None

    def status(self, healthy, urgent):
        self.status_at, self.healthy, self.urgent = self.clock(), bool(healthy), bool(urgent)

    def emergency(self, active):
        self.estop_at, self.estop = self.clock(), bool(active)

    def request(self, assignment_id, boot_id):
        self.request_at = self.clock()
        self.scope = (assignment_id, boot_id) if assignment_id and boot_id else None

    def packet(self):
        self.sequence += 1
        fresh = lambda stamp: stamp is not None and 0 <= self.clock() - stamp < self.ttl
        mode = "BLOCKED"
        scope = None
        if fresh(self.status_at) and fresh(self.estop_at) and self.healthy and not self.estop:
            mode = "LOCAL"
            if not self.urgent and fresh(self.request_at) and self.scope:
                mode, scope = "DELIVERY", self.scope
        return {"controllerId": self.controller_id, "sequence": self.sequence, "mode": mode,
                "assignmentId": scope[0] if scope else None, "bootId": scope[1] if scope else None}


class NavigationAuthority:
    def __init__(self, clock=time.monotonic, ttl=1.5, stopped_dwell=0.5):
        self.clock, self.ttl, self.dwell = clock, ttl, stopped_dwell
        self.lock = threading.RLock()
        self.packet, self.packet_at = None, None
        self.stationary_since = self.odom_at = None
        self.nav_status_at, self.nav_active = None, True
        self.slot = None
        self.seen_delivery_goals = set()
        self.retired_controllers = set()

    def grant(self, packet):
        with self.lock:
            expired = not self._fresh(self.packet_at)
            valid = (isinstance(packet, dict) and packet.get("mode") in {"BLOCKED", "LOCAL", "DELIVERY"}
                     and isinstance(packet.get("controllerId"), str) and bool(packet["controllerId"])
                     and type(packet.get("sequence")) is int and packet["sequence"] > 0)
            if valid and packet["mode"] == "DELIVERY":
                valid = all(isinstance(packet.get(key), str) and packet[key] for key in ("assignmentId", "bootId"))
            if not valid:
                self.packet_at = None
                return False
            if packet["controllerId"] in self.retired_controllers:
                return False
            if self.packet:
                if packet["controllerId"] == self.packet["controllerId"]:
                    if packet["sequence"] <= self.packet["sequence"]:
                        return False  # Replays cannot renew a lease.
                else:
                    self.retired_controllers.add(self.packet["controllerId"])
                    if self.slot:
                        self.slot["revoked"] = True
            self.packet, self.packet_at = dict(packet), self.clock()
            if self.slot and (expired or not self._owns()):
                self.slot["revoked"] = True  # A later grant cannot revive a revoked goal.
            return True

    def odometry(self, components):
        with self.lock:
            if not self._fresh(self.odom_at):
                self.stationary_since = None
            self.odom_at = self.clock()
            stopped = len(components) == 6 and all(math.isfinite(x) and abs(x) <= 0.02 for x in components)
            if not stopped:
                self.stationary_since = None
            elif self.stationary_since is None:
                self.stationary_since = self.clock()

    def nav_status(self, active):
        with self.lock:
            self.nav_active, self.nav_status_at = bool(active), self.clock()

    def _fresh(self, stamp):
        return stamp is not None and 0 <= self.clock() - stamp < self.ttl

    def _physically_stopped(self):
        return (self._fresh(self.odom_at) and self.stationary_since is not None
                and self.clock() - self.stationary_since >= self.dwell
                and self._fresh(self.nav_status_at) and not self.nav_active)

    def _owns(self):
        if not self.slot or not self._fresh(self.packet_at) or not self.packet:
            return False
        if self.slot["kind"] == "LOCAL":
            return self.packet["mode"] == "LOCAL"
        return (self.packet["mode"] == "DELIVERY" and self.slot["scope"] ==
                (self.packet["assignmentId"], self.packet["bootId"]))

    def _release(self):
        if self.slot and self.slot["finished"] and not self.slot["uncertain"] and self._physically_stopped():
            self.slot = None

    def safe_stopped(self):
        with self.lock:
            self._release()
            return self.slot is None and self._physically_stopped()

    def delivery_allowed(self, scope):
        with self.lock:
            self._release()
            return (self._fresh(self.packet_at) and self.packet and self.packet["mode"] == "DELIVERY"
                    and scope == (self.packet["assignmentId"], self.packet["bootId"]))

    def reserve(self, kind, scope=None, goal_id=None):
        with self.lock:
            if not self.safe_stopped() or not self._fresh(self.packet_at):
                return False
            if kind == "LOCAL":
                allowed = self.packet["mode"] == "LOCAL"
            else:
                allowed = self.delivery_allowed(scope) and goal_id and goal_id not in self.seen_delivery_goals
            if not allowed:
                return False
            self.slot = {"kind": kind, "scope": scope, "finished": False, "uncertain": False, "revoked": False}
            if kind == "DELIVERY":
                self.seen_delivery_goals.add(goal_id)
            return True

    def needs_cancel(self):
        with self.lock:
            self._release()
            if self.slot and self.slot["uncertain"]:
                return True
            if not self.slot or self.slot["finished"]:
                return False
            if not self._owns() or not self._fresh(self.odom_at) or not self._fresh(self.nav_status_at):
                self.slot["revoked"] = True
            return self.slot["revoked"]

    def finish(self, uncertain=False):
        with self.lock:
            if self.slot:
                self.slot["finished"], self.slot["uncertain"] = True, uncertain
            self._release()
