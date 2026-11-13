import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from robot_edge_gateway.gateway import FakeNavigator, FakeRobotGateway, RobotEdgeGateway
from robot_edge_gateway.store import EdgeStore
from robot_edge_gateway.transport import HttpsBackend, NoRedirects


def offered():
    return {"assignmentId": "a1", "robotId": "r1", "taskId": "t1", "state": "OFFERED", "resolvedAt": None,
            "lastSequence": 0, "goals": [{"id": "g1", "assignment_id": "a1", "leg": "PICKUP", "state": "BLOCKED",
              "pose": {"x": 1, "y": 0, "yaw": 0, "frameId": "map"}},
              {"id": "g2", "assignment_id": "a1", "leg": "DELIVERY", "state": "BLOCKED",
               "pose": {"x": 2, "y": 0, "yaw": 0, "frameId": "map"}}]}


class EdgeStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "edge.sqlite3"
        self.store = EdgeStore(self.path)
        self.addCleanup(lambda: self.store.close())

    def test_assignment_duplicate_persists_one_ack_and_one_mission(self):
        self.assertTrue(self.store.accept(offered()))
        self.assertFalse(self.store.accept(offered()))
        self.assertEqual(len(self.store.pending()), 1)
        self.assertEqual(self.store.pending()[0]["kind"], "ACK")

    def test_second_unresolved_assignment_is_blocked_by_sqlite_constraint(self):
        self.store.accept(offered())
        second = offered()
        second["assignmentId"] = "a2"
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.accept(second)
        self.assertEqual(self.store.active(), "a1")

    def test_changed_duplicate_assignment_is_rejected(self):
        mission = offered()
        self.store.accept(mission)
        mission["goals"][0]["pose"]["x"] = 200
        with self.assertRaises(ValueError):
            self.store.accept(mission)

    def test_outbox_survives_restart_and_retains_event_identity_sequence(self):
        self.store.accept(offered())
        before = self.store.pending()
        self.store.close()
        self.store = EdgeStore(self.path)
        self.assertEqual(self.store.pending(), before)

    def test_crash_after_start_intent_does_not_restart_navigation(self):
        mission = offered()
        self.store.accept(mission)
        self.assertTrue(self.store.begin_navigation(mission["goals"][0]))
        self.store.close()
        self.store = EdgeStore(self.path)
        self.store.recover()
        self.assertEqual(self.store.state("g1"), "UNKNOWN")
        self.assertFalse(self.store.begin_navigation(mission["goals"][0]))
        self.assertEqual([event["kind"] for event in self.store.pending()], ["ACK", "NAV_STARTED", "EXECUTION_UNKNOWN"])

    def test_duplicate_navigation_callbacks_do_not_duplicate_arrival(self):
        mission = offered()
        self.store.accept(mission)
        self.store.begin_navigation(mission["goals"][0])
        self.assertTrue(self.store.navigation_result("g1", True))
        self.assertFalse(self.store.navigation_result("g1", True))
        self.assertEqual([event["kind"] for event in self.store.pending()].count("ARRIVED"), 1)

    def test_repeated_qr_frames_emit_one_durable_event(self):
        self.store.accept(offered())
        challenge = {"challengeId": "qr-1", "token": "test-qr-token"}
        self.assertTrue(self.store.scan("a1", challenge))
        self.assertFalse(self.store.scan("a1", challenge))
        self.store.close()
        self.store = EdgeStore(self.path)
        self.assertFalse(self.store.scan("a1", challenge))
        self.assertEqual([event["kind"] for event in self.store.pending()].count("QR_SCANNED"), 1)

    def test_failed_network_delivery_does_not_acknowledge_outbox_event(self):
        transport = Mock()
        transport.event.side_effect = OSError("test disconnect")
        self.store.accept(offered())
        gateway = FakeRobotGateway(self.store, transport)
        event = self.store.pending()[0]
        with self.assertRaises(OSError):
            gateway.flush()
        self.assertEqual(self.store.pending()[0], event)

    def test_backend_ack_loss_retries_same_event(self):
        self.store.accept(offered())
        transport = Mock()
        transport.event.side_effect = [OSError("ACK response lost"), {}]
        gateway = FakeRobotGateway(self.store, transport)
        with self.assertRaises(OSError):
            gateway.flush()
        gateway.flush()
        self.assertEqual(transport.event.call_args_list[0], transport.event.call_args_list[1])
        self.assertFalse(self.store.pending())

    def test_local_authority_denial_prevents_goal_start(self):
        assignment = offered()
        transport = Mock()
        transport.poll.return_value = assignment
        accepted = copy.deepcopy(assignment)
        accepted["state"] = "ACKED"
        accepted["goals"][0]["state"] = "READY"
        transport.assignment.return_value = accepted
        navigator = FakeNavigator()
        navigator.allowed = False
        gateway = RobotEdgeGateway(self.store, transport, navigator)
        gateway.tick()
        self.assertFalse(navigator.starts)

    def test_unknown_assignment_from_server_is_not_adopted_as_fresh_motion(self):
        transport = Mock()
        assignment = offered()
        assignment["state"] = "NAVIGATING_PICKUP"
        transport.poll.return_value = assignment
        gateway = FakeRobotGateway(self.store, transport)
        with self.assertRaises(ValueError):
            gateway.tick()
        self.assertFalse(gateway.navigator.starts)
        self.assertIsNone(self.store.active())

    def test_gateway_requires_https_and_never_follows_token_redirects(self):
        with self.assertRaises(ValueError):
            HttpsBackend("http://example.invalid", "api", "robot")
        self.assertIsNone(NoRedirects().redirect_request(None, None, 302, None, None, "https://other.invalid"))


if __name__ == "__main__":
    unittest.main()
