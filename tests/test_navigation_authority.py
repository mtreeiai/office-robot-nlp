import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from robot_edge_gateway.authority import MissionPolicy, NavigationAuthority
from robot_edge_gateway.navigation_ledger import DeliveryGoalLedger
from scripts.apply_ros_stack_overlay import apply_overlay


class MissionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.policy = MissionPolicy(clock=lambda: self.now)

    def healthy(self):
        self.policy.status(True, False)
        self.policy.emergency(False)

    def test_startup_and_stale_safety_are_blocked(self):
        self.assertEqual(self.policy.packet()["mode"], "BLOCKED")
        self.healthy()
        self.assertEqual(self.policy.packet()["mode"], "LOCAL")
        self.now += 1.6
        self.assertEqual(self.policy.packet()["mode"], "BLOCKED")

    def test_delivery_lease_is_bound_to_assignment_and_gateway_boot(self):
        self.healthy()
        self.policy.request("assignment", "boot")
        packet = self.policy.packet()
        self.assertEqual((packet["mode"], packet["assignmentId"], packet["bootId"]), ("DELIVERY", "assignment", "boot"))

    def test_urgent_resource_request_preempts_delivery_without_healthy_default(self):
        self.healthy()
        self.policy.request("assignment", "boot")
        self.policy.status(True, True)
        self.assertEqual(self.policy.packet()["mode"], "LOCAL")
        self.policy.status(False, False)
        self.assertEqual(self.policy.packet()["mode"], "BLOCKED")

    def test_emergency_overrides_all_other_modes(self):
        self.healthy()
        self.policy.request("assignment", "boot")
        self.policy.emergency(True)
        self.assertEqual(self.policy.packet()["mode"], "BLOCKED")

    def test_expired_gateway_request_releases_delivery_not_safety(self):
        self.healthy()
        self.policy.request("assignment", "boot")
        self.now += 1.6
        self.healthy()
        self.assertEqual(self.policy.packet()["mode"], "LOCAL")


class NavigationAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.gate = NavigationAuthority(clock=lambda: self.now)
        self.sequence = 0
        self.scope = ("assignment", "gateway-boot")

    def grant(self, mode="DELIVERY", controller="mission", scope=None):
        self.sequence += 1
        scope = scope or self.scope
        return self.gate.grant({"controllerId": controller, "sequence": self.sequence,
            "mode": mode, "assignmentId": scope[0] if mode == "DELIVERY" else None,
            "bootId": scope[1] if mode == "DELIVERY" else None})

    def stopped(self):
        self.gate.odometry([0] * 6)
        self.gate.nav_status(False)
        self.now += 0.6
        self.gate.odometry([0] * 6)
        self.gate.nav_status(False)

    def ready(self):
        self.grant()
        self.stopped()

    def test_no_motion_from_grant_without_physical_stop_evidence(self):
        self.grant()
        self.assertFalse(self.gate.reserve("DELIVERY", self.scope, "g1"))
        self.assertFalse(self.gate.safe_stopped())

    def test_delivery_blocks_direct_go_even_during_qr_wait(self):
        self.ready()
        self.assertFalse(self.gate.reserve("LOCAL"))
        self.assertTrue(self.gate.reserve("DELIVERY", self.scope, "g1"))
        self.gate.finish()
        self.assertTrue(self.gate.safe_stopped())
        self.assertFalse(self.gate.reserve("LOCAL"))

    def test_wrong_assignment_or_gateway_boot_is_rejected(self):
        self.ready()
        for scope in (("other", self.scope[1]), (self.scope[0], "old-boot")):
            self.assertFalse(self.gate.reserve("DELIVERY", scope, "g1"))

    def test_concurrent_goals_share_one_atomic_admission_slot(self):
        self.ready()
        barrier = threading.Barrier(8)

        def reserve(index):
            barrier.wait(timeout=3)
            return self.gate.reserve("DELIVERY", self.scope, f"goal-{index}")

        with ThreadPoolExecutor(8) as pool:
            self.assertEqual(sum(pool.map(reserve, range(8))), 1)

    def test_revocation_before_goal_execution_sticks_after_regrant(self):
        self.ready()
        self.assertTrue(self.gate.reserve("DELIVERY", self.scope, "g1"))
        self.grant("BLOCKED")
        self.assertTrue(self.gate.needs_cancel())
        self.grant()
        self.assertTrue(self.gate.needs_cancel())
        self.assertFalse(self.gate.reserve("DELIVERY", self.scope, "g2"))

    def test_cancel_acceptance_does_not_release_slot_until_terminal_and_stopped(self):
        self.ready()
        self.gate.reserve("DELIVERY", self.scope, "g1")
        self.gate.nav_status(True)
        self.grant("LOCAL")
        self.assertTrue(self.gate.needs_cancel())
        self.assertFalse(self.gate.reserve("LOCAL"))
        self.gate.finish()
        self.assertFalse(self.gate.reserve("LOCAL"))
        self.gate.nav_status(False)
        self.assertTrue(self.gate.reserve("LOCAL"))

    def test_uncertain_nav2_outcome_never_releases_resource_on_zero_odom_alone(self):
        self.ready()
        self.gate.reserve("DELIVERY", self.scope, "g1")
        self.gate.finish(uncertain=True)
        self.grant("LOCAL")
        self.assertTrue(self.gate.needs_cancel())
        self.assertFalse(self.gate.safe_stopped())
        self.assertFalse(self.gate.reserve("LOCAL"))

    def test_expired_lease_revokes_and_cannot_be_revived_by_late_renewal(self):
        self.ready()
        self.gate.reserve("DELIVERY", self.scope, "g1")
        self.now += 1.6
        self.grant()
        self.assertTrue(self.gate.needs_cancel())

    def test_replayed_grant_does_not_extend_freshness(self):
        self.ready()
        packet = dict(self.gate.packet)
        self.now += 1.6
        self.assertFalse(self.gate.grant(packet))
        self.assertFalse(self.gate.delivery_allowed(self.scope))

    def test_controller_restart_revokes_and_old_controller_cannot_return(self):
        self.ready()
        self.gate.reserve("DELIVERY", self.scope, "g1")
        self.grant(controller="new-mission")
        self.assertTrue(self.gate.needs_cancel())
        self.assertFalse(self.grant(controller="mission"))

    def test_duplicate_completed_delivery_goal_id_does_not_dispatch_twice(self):
        self.ready()
        self.assertTrue(self.gate.reserve("DELIVERY", self.scope, "g1"))
        self.gate.finish()
        self.assertFalse(self.gate.reserve("DELIVERY", self.scope, "g1"))
        self.assertTrue(self.gate.reserve("DELIVERY", self.scope, "g2"))

    def test_stale_odometry_nan_or_unknown_nav2_status_blocks_admission(self):
        self.ready()
        self.gate.odometry([float("nan")] + [0] * 5)
        self.assertFalse(self.gate.safe_stopped())
        self.stopped()
        self.now += 2
        self.grant()
        self.assertFalse(self.gate.safe_stopped())

    def test_observation_gap_resets_stopped_dwell(self):
        self.ready()
        self.now += 2
        self.grant()
        self.gate.odometry([0] * 6)
        self.gate.nav_status(False)
        self.assertFalse(self.gate.safe_stopped())

    def test_foreign_active_nav2_goal_blocks_startup_admission(self):
        self.ready()
        self.gate.nav_status(True)
        self.assertFalse(self.gate.reserve("DELIVERY", self.scope, "g1"))


class OverlayInstallerTests(unittest.TestCase):
    def test_unknown_edits_are_rejected_before_any_file_is_written(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            overlay, target = root / "overlay", root / "target"
            for directory in (overlay / "src", target / "src"):
                directory.mkdir(parents=True)
            (overlay / "src/a.py").write_text("new")
            (overlay / "src/b.py").write_text("new")
            (target / "src/a.py").write_text("old")
            (target / "src/b.py").write_text("user-edited")
            expected = hashlib.sha256(b"old").hexdigest()
            (overlay / "manifest.json").write_text(json.dumps({"files": {"src/a.py": expected, "src/b.py": expected}}))
            with self.assertRaises(ValueError):
                apply_overlay(target, overlay)
            self.assertEqual((target / "src/a.py").read_text(), "old")

    def test_overlay_apply_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "overlay/src").mkdir(parents=True)
            (root / "overlay/src/new.py").write_text("new")
            (root / "overlay/manifest.json").write_text(json.dumps({"files": {"src/new.py": None}}))
            self.assertEqual(apply_overlay(root / "target", root / "overlay"), 1)
            self.assertEqual(apply_overlay(root / "target", root / "overlay"), 0)


class NavigationLedgerTests(unittest.TestCase):
    def test_manager_restart_cannot_readmit_completed_or_changed_goal(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "navigation.sqlite3"
            ledger = DeliveryGoalLedger(path)
            self.assertTrue(ledger.claim("g1", {"assignmentId": "a1"}))
            ledger.close()
            ledger = DeliveryGoalLedger(path)
            try:
                self.assertFalse(ledger.claim("g1", {"assignmentId": "a1"}))
                self.assertFalse(ledger.claim("g1", {"assignmentId": "changed"}))
                self.assertTrue(ledger.claim("g2", {"assignmentId": "a2"}))
            finally:
                ledger.close()

    def test_concurrent_claims_publish_one_durable_admission(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = DeliveryGoalLedger(Path(temp) / "navigation.sqlite3")
            try:
                with ThreadPoolExecutor(6) as pool:
                    self.assertEqual(sum(pool.map(lambda _: ledger.claim("g1", {}), range(6))), 1)
            finally:
                ledger.close()


if __name__ == "__main__":
    unittest.main()
