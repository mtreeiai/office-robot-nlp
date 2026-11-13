"""Durable gateway controller. All callbacks are serialized by the local controller."""

from threading import RLock
from uuid import uuid4


class RobotEdgeGateway:
    def __init__(self, store, transport, navigator):
        self.store, self.transport, self.navigator = store, transport, navigator
        self.boot_id = str(uuid4())
        self._lock = RLock()
        self.store.recover()

    def flush(self):
        for event in self.store.pending():
            self.transport.event(event)  # Only durably accepted events leave SQLite.
            self.store.acknowledge(event["eventId"])

    def tick(self):
        with self._lock:
            self.flush()
            active = self.store.active()
            if active:
                assignment = self.transport.assignment(active)
                if assignment["resolvedAt"] is not None:
                    if not self.navigator.safe_stopped():
                        return
                    with self.store.transaction() as connection:
                        connection.execute("UPDATE assignments SET active=0 WHERE id=?", (active,))
                    active = None
                elif assignment["state"] == "CANCEL_REQUESTED":
                    # A cancel request does not itself establish a safe stop.
                    self.navigator.cancel()
                    if self.navigator.safe_stopped():
                        with self.store.transaction() as connection:
                            self.store.emit(connection, active, "CANCELLED_SAFE")
                        self.flush()
                    self.transport.heartbeat(self.boot_id, active, self.navigator.safe_stopped())
                    return
            self.transport.heartbeat(self.boot_id, active, self.navigator.safe_stopped())
            if active is None:
                assignment = self.transport.poll()
                if assignment is None:
                    return
                # An unresolved backend assignment unknown locally is never re-driven.
                self.store.accept(assignment)
                self.flush()
                active = self.store.active()
            assignment = self.transport.assignment(active)
            if assignment["state"] != "ACKED" or not self.navigator.authorized():
                return
            ready = [goal for goal in assignment["goals"] if goal["state"] == "READY"]
            if len(ready) != 1:
                return
            goal = ready[0]
            if self.store.begin_navigation(goal):
                # Backend accepts the intent before Nav2 is contacted. A crash in
                # either gap leaves durable uncertainty; it never retries motion.
                try:
                    self.flush()
                    self.navigator.start(goal["id"], goal["pose"], self.navigation_result)
                except Exception:
                    self.store.recover()
                    raise

    def navigation_result(self, goal_id, success):
        with self._lock:
            self.store.navigation_result(goal_id, success)

    def scan_qr(self, challenge):
        with self._lock:
            active = self.store.active()
            if active is None or not self.navigator.safe_stopped():
                raise ValueError("QR requires an active, safely stopped mission")
            return self.store.scan(active, challenge)


class FakeNavigator:
    """Explicit test double: never imported/selected as a production navigation fallback."""
    def __init__(self):
        self.starts = []
        self.current = None
        self.allowed = True

    def authorized(self):
        return self.allowed

    def safe_stopped(self):
        return self.current is None

    def start(self, goal_id, pose, callback):
        if self.current is not None or not self.allowed:
            raise ValueError("Fake navigator is busy or unauthorized")
        self.starts.append(goal_id)
        self.current = (goal_id, callback)

    def finish(self, success=True):
        goal_id, callback = self.current
        self.current = None
        callback(goal_id, success)

    def cancel(self):
        self.current = None


class FakeRobotGateway(RobotEdgeGateway):
    def __init__(self, store, transport):
        super().__init__(store, transport, FakeNavigator())
