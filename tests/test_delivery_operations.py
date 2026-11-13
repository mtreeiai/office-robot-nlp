import copy
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4
import tempfile
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from fastapi.testclient import TestClient

from http_conversation_demo import SINGLE, THREE, STRICT, demo_bridge
from office_robot_backend.repository import PostgresRepository
from office_robot_backend.service import DeliveryService, OperationError, utcnow
from office_robot_nlp.http_api import ApiSettings, create_api


TEST_DSN = os.environ.get("OFFICE_ROBOT_TEST_DATABASE_URL")


@unittest.skipUnless(TEST_DSN, "Set OFFICE_ROBOT_TEST_DATABASE_URL to an isolated PostgreSQL test database")
class DeliveryOperationsTests(unittest.TestCase):
    def setUp(self):
        self.schema = "test_delivery_" + uuid4().hex
        with psycopg.connect(TEST_DSN, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.drop_schema)
        self.dsn = make_conninfo(TEST_DSN, options=f"-c search_path={self.schema}")
        self.repo = PostgresRepository(self.dsn)
        self.repo.migrate()
        self.now = utcnow()
        self.service = DeliveryService(self.repo, clock=lambda: self.now)
        locations = [{"id": room, "name": room, "pickupAllowed": room in {"P1", "P2"},
                      "deliveryAllowed": room in {"02", "05", "07"},
                      "pose": {"x": index, "y": 0, "yaw": 0, "frameId": "map"}}
                     for index, room in enumerate(("P1", "P2", "02", "05", "07"))]
        self.service.provision("operator", "operator-test-credential-long", "robot", "robot-test-credential-long", "map-v1", locations)
        self.bridge = demo_bridge()
        sid = self.bridge.create_session().sessionId
        self.semantic = self.bridge.handle_user_message(sid, "single", SINGLE).model_dump(mode="json")
        self.multi = self.bridge.handle_user_message(sid, "multi", THREE).model_dump(mode="json")
        self.strict = self.bridge.handle_user_message(sid, "strict", STRICT).model_dump(mode="json")
        self.seq = {}

    def drop_schema(self):
        with psycopg.connect(TEST_DSN, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def draft(self, semantic=None, key=None, **kwargs):
        semantic = semantic or self.semantic
        pickups = {task["taskId"]: "P1" for task in semantic["batch"]["tasks"]}
        return self.service.draft("operator", key or str(uuid4()), semantic, "map-v1", kwargs.pop("pickups", pickups), **kwargs)

    def idle(self):
        self.service.heartbeat("robot", "boot-1", None, True)

    def assignment(self, semantic=None):
        request = self.draft(semantic)
        self.service.confirm("operator", request["requestId"])
        self.idle()
        return self.service.schedule("robot")

    def event(self, assignment, kind, payload=None, sequence=None, event_id=None):
        aid = assignment["assignmentId"]
        if sequence is None:
            sequence = self.seq.get(aid, 0) + 1
        event = {"eventId": event_id or str(uuid4()), "assignmentId": aid, "sequence": sequence,
                 "kind": kind, "payload": payload or {}}
        result = self.service.ingest("robot", event)
        self.seq[aid] = sequence
        return result, event

    def arrive(self, assignment, leg):
        goal = next(goal for goal in assignment["goals"] if goal["leg"] == leg)
        self.event(assignment, "NAV_STARTED", {"goalId": goal["id"]})
        return self.event(assignment, "ARRIVED", {"goalId": goal["id"]})[0]

    def qr(self, assignment, leg):
        challenge = self.service.issue_qr("operator", assignment["assignmentId"], leg)
        return self.event(assignment, "QR_SCANNED", {"challengeId": challenge["challengeId"], "token": challenge["token"]})

    def complete(self, assignment):
        self.event(assignment, "ACK")
        self.arrive(assignment, "PICKUP")
        self.qr(assignment, "PICKUP")
        self.arrive(assignment, "DELIVERY")
        return self.qr(assignment, "DELIVERY")[0]

    def test_migration_is_idempotent_and_contains_all_approved_entities(self):
        self.repo.migrate()
        with self.repo.transaction() as tx:
            tables = {row["tablename"] for row in tx.all("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()")}
        self.assertTrue({"operator_accounts", "map_versions", "locations", "delivery_requests", "delivery_tasks",
                         "task_dependencies", "robots", "robot_assignments", "navigation_goals", "qr_challenges",
                         "execution_events", "outbox_events"} <= tables)

    def test_nlp_ready_only_creates_draft_and_never_schedules(self):
        request = self.draft()
        self.idle()
        self.assertEqual(request["state"], "DRAFT")
        self.assertEqual(request["tasks"][0]["state"], "DRAFT")
        self.assertIsNone(self.service.schedule("robot"))

    def test_missing_pickup_or_invalid_role_does_not_create_partial_draft(self):
        for pickups in ({}, {self.semantic["batch"]["tasks"][0]["taskId"]: "02"}):
            with self.subTest(pickups=pickups), self.assertRaises(OperationError):
                self.draft(pickups=pickups)
        with self.repo.transaction() as tx:
            self.assertEqual(tx.one("SELECT count(*) AS n FROM delivery_tasks")["n"], 0)

    def test_multiple_pickup_locations_preserve_document_associations(self):
        tasks = self.multi["batch"]["tasks"]
        draft = self.draft(self.multi, pickups={task["taskId"]: "P1" if index % 2 else "P2" for index, task in enumerate(tasks)})
        self.assertEqual([t["pickup_location_id"] for t in draft["tasks"]], ["P2", "P1", "P2"])
        self.assertEqual([t["delivery_location_id"] for t in draft["tasks"]], ["02", "05", "07"])
        self.assertTrue(all(t["package_count"] == 1 for t in draft["tasks"]))

    def test_request_idempotency_and_duplicate_source_are_durable(self):
        first = self.draft(key="same")
        self.assertEqual(first, self.draft(key="same"))
        with self.assertRaises(OperationError):
            self.draft(key="different")
        with self.assertRaises(OperationError):
            self.draft(self.multi, key="same")
        restarted = DeliveryService(PostgresRepository(self.dsn))
        self.assertEqual(restarted.request("operator", first["requestId"]), first)

    def test_entire_batch_confirmation_is_atomic_when_one_location_disabled(self):
        draft = self.draft(self.multi)
        with self.repo.transaction() as tx:
            tx.execute("UPDATE locations SET enabled=false WHERE id='07'")
        with self.assertRaises(OperationError):
            self.service.confirm("operator", draft["requestId"])
        current = self.service.request("operator", draft["requestId"])
        self.assertEqual(current["state"], "DRAFT")
        self.assertTrue(all(t["state"] == "DRAFT" and t["queue_order"] is None for t in current["tasks"]))

    def test_confirm_is_idempotent_and_priority_then_fifo_is_used(self):
        first = self.draft()
        self.service.confirm("operator", first["requestId"])
        second = self.draft(self.multi)
        confirmed = self.service.confirm("operator", second["requestId"])
        self.assertEqual(self.service.confirm("operator", second["requestId"]), confirmed)
        self.idle()
        selected = self.service.schedule("robot")
        self.assertEqual(selected["taskId"], second["tasks"][2]["id"])
        self.complete(selected)
        self.idle()
        self.assertEqual(self.service.schedule("robot")["taskId"], first["tasks"][0]["id"])

    def test_strict_order_dependencies_override_priority_without_reordering_storage(self):
        strict = copy.deepcopy(self.multi)
        strict["batch"]["orderPolicy"] = "STRICT_ORDER"
        request = self.draft(strict)
        self.service.confirm("operator", request["requestId"])
        self.idle()
        self.assertEqual(self.service.schedule("robot")["taskId"], request["tasks"][0]["id"])
        self.assertEqual([t["sequence_index"] for t in request["tasks"]], [0, 1, 2])

    def test_cyclic_dependencies_rejected_before_registration(self):
        with self.assertRaises(OperationError):
            self.draft(self.multi, dependencies=[(0, 1), (1, 0)])

    def test_concurrent_schedulers_offer_exactly_one_assignment(self):
        request = self.draft(self.multi)
        self.service.confirm("operator", request["requestId"])
        self.idle()
        with ThreadPoolExecutor(6) as pool:
            assignments = list(pool.map(lambda _: self.service.schedule("robot"), range(6)))
        self.assertEqual(len({a["assignmentId"] for a in assignments}), 1)
        with self.repo.transaction() as tx:
            self.assertEqual(tx.one("SELECT count(*) AS n FROM robot_assignments")["n"], 1)
            self.assertEqual(tx.one("SELECT count(*) AS n FROM outbox_events")["n"], 1)

    def test_postgres_constraint_rejects_second_unresolved_assignment(self):
        assignment = self.assignment(self.multi)
        with self.assertRaises(psycopg.errors.UniqueViolation), self.repo.transaction() as tx:
            other = tx.one("SELECT id FROM delivery_tasks WHERE id<>%s LIMIT 1", (assignment["taskId"],))
            tx.insert("robot_assignments", id=str(uuid4()), robot_id="robot", task_id=other["id"], state="OFFERED")

    def test_nav_arrival_alone_never_completes_a_delivery(self):
        assignment = self.assignment()
        self.event(assignment, "ACK")
        arrived = self.arrive(assignment, "PICKUP")
        self.assertEqual(arrived["state"], "WAITING_PICKUP_QR")
        with self.assertRaises(OperationError):
            self.arrive(assignment, "DELIVERY")
        with self.assertRaises(OperationError):
            self.service.issue_qr("operator", assignment["assignmentId"], "DELIVERY")

    def test_two_legs_both_qr_required_and_completion_is_durable(self):
        assignment = self.assignment()
        self.event(assignment, "ACK")
        self.arrive(assignment, "PICKUP")
        self.qr(assignment, "PICKUP")
        arrived = self.arrive(assignment, "DELIVERY")
        self.assertEqual(arrived["state"], "WAITING_DELIVERY_QR")
        self.assertIsNone(arrived["resolvedAt"])
        completed, _ = self.qr(assignment, "DELIVERY")
        self.assertEqual(completed["state"], "COMPLETED")
        self.assertIsNotNone(DeliveryService(PostgresRepository(self.dsn)).assignment("robot", assignment["assignmentId"])["resolvedAt"])
        self.assertIsNone(self.service.schedule("robot"))  # Needs fresh edge-idle report.

    def test_qr_wrong_expired_rotated_or_cross_assignment_is_rejected(self):
        assignment = self.assignment()
        with self.assertRaises(OperationError):
            self.service.issue_qr("operator", assignment["assignmentId"], "PICKUP")
        self.event(assignment, "ACK")
        self.arrive(assignment, "PICKUP")
        qr = self.service.issue_qr("operator", assignment["assignmentId"], "PICKUP")
        with self.assertRaises(OperationError):
            self.event(assignment, "QR_SCANNED", {"challengeId": qr["challengeId"], "token": "wrong"})
        self.now += timedelta(minutes=11)
        with self.assertRaises(OperationError):
            self.event(assignment, "QR_SCANNED", {"challengeId": qr["challengeId"], "token": qr["token"]})
        qr = self.service.issue_qr("operator", assignment["assignmentId"], "PICKUP")
        self.service.issue_qr("operator", assignment["assignmentId"], "PICKUP")
        with self.assertRaises(OperationError):
            self.event(assignment, "QR_SCANNED", {"challengeId": qr["challengeId"], "token": qr["token"]})

    def test_duplicate_event_is_idempotent_but_changed_payload_and_sequence_rejected(self):
        assignment = self.assignment()
        _, event = self.event(assignment, "ACK")
        self.assertEqual(self.service.ingest("robot", event)["lastSequence"], 1)
        with self.assertRaises(OperationError):
            self.service.ingest("robot", {**event, "kind": "FAILED"})
        with self.assertRaises(OperationError):
            self.event(assignment, "FAILED", sequence=3)

    def test_qr_token_is_not_persisted_in_execution_event_body(self):
        assignment = self.assignment()
        self.event(assignment, "ACK")
        self.arrive(assignment, "PICKUP")
        _, event = self.qr(assignment, "PICKUP")
        with self.repo.transaction() as tx:
            row = tx.one("SELECT payload FROM execution_events WHERE id=%s", (event["eventId"],))
        self.assertNotIn("token", row["payload"])

    def test_stale_robot_and_new_boot_block_reassignment(self):
        request = self.draft()
        self.service.confirm("operator", request["requestId"])
        self.idle()
        self.now += timedelta(seconds=61)
        self.assertIsNone(self.service.schedule("robot"))
        self.idle()
        assignment = self.service.schedule("robot")
        self.event(assignment, "ACK")
        self.service.heartbeat("robot", "new-boot", assignment["assignmentId"], False)
        self.assertEqual(self.service.schedule("robot")["state"], "UNKNOWN")

    def test_failed_assignment_holds_robot_until_stopped_and_package_reconciled(self):
        assignment = self.assignment(self.multi)
        self.event(assignment, "FAILED")
        with self.assertRaises(OperationError):
            self.service.reconcile("operator", assignment["assignmentId"], True)
        self.service.heartbeat("robot", "boot-1", assignment["assignmentId"], True)
        with self.assertRaises(OperationError):
            self.service.reconcile("operator", assignment["assignmentId"], False)
        resolved = self.service.reconcile("operator", assignment["assignmentId"], True)
        self.assertIsNotNone(resolved["resolvedAt"])
        self.assertNotEqual(resolved["state"], "COMPLETED")
        self.idle()
        self.assertNotEqual(self.service.schedule("robot")["assignmentId"], assignment["assignmentId"])

    def test_cancel_cannot_release_active_robot_or_fake_completion(self):
        assignment = self.assignment()
        with self.repo.transaction() as tx:
            task = tx.one("SELECT request_id FROM delivery_tasks WHERE id=%s", (assignment["taskId"],))
        self.service.cancel("operator", task["request_id"])
        self.assertEqual(self.service.schedule("robot")["state"], "CANCEL_REQUESTED")
        self.event(assignment, "CANCELLED_SAFE")
        self.assertIsNone(self.service.assignment("robot", assignment["assignmentId"])["resolvedAt"])

    def test_existing_http_contract_and_explicit_draft_confirm_survive_nlp_restart(self):
        settings = ApiSettings(access_token="global-test-token")
        headers = {"Authorization": "Bearer global-test-token", "X-Operator-Token": "operator-test-credential-long"}
        source = self.semantic
        payload = {"idempotencyKey": "api-draft", "sessionId": source["sessionId"], "messageId": source["messageId"],
                   "mapId": "map-v1", "pickups": {source["batch"]["tasks"][0]["taskId"]: "P1"}}
        with TestClient(create_api(self.bridge, settings, self.service)) as client:
            response = client.post("/v1/operations/requests", json=payload, headers=headers)
            self.assertEqual(response.status_code, 201, response.text)
            request_id = response.json()["requestId"]
            self.assertEqual(client.post(f"/v1/operations/requests/{request_id}/confirm", headers=headers).json()["state"], "CONFIRMED")
            self.assertEqual(client.get("/v1/tasks/" + source["batch"]["tasks"][0]["taskId"], headers=headers).json()["lifecycle"], "READY")
        with TestClient(create_api(demo_bridge(), settings, DeliveryService(PostgresRepository(self.dsn)))) as client:
            response = client.post("/v1/operations/requests", json=payload, headers=headers)
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(response.json()["requestId"], request_id)

    def test_operational_auth_is_required_in_addition_to_existing_api_token(self):
        with TestClient(create_api(self.bridge, ApiSettings(access_token="global-test-token"), self.service)) as client:
            self.assertEqual(client.get("/v1/gateway/assignment").status_code, 401)
            self.assertEqual(client.get("/v1/gateway/assignment", headers={"Authorization": "Bearer global-test-token"}).status_code, 401)
            self.assertEqual(client.get("/v1/gateway/assignment", headers={"Authorization": "Bearer global-test-token", "X-Robot-Token": "robot-test-credential-long"}).status_code, 200)

    def edge(self):
        from robot_edge_gateway.gateway import FakeRobotGateway
        from robot_edge_gateway.store import EdgeStore
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "edge.sqlite3"
        client = TestClient(create_api(self.bridge, ApiSettings(access_token="global-test-token"), self.service))
        client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)

        class LocalTransport:
            lost_kind = None

            def call(inner, method, url, body=None):
                response = client.request(method, url, json=body, headers={"Authorization": "Bearer global-test-token", "X-Robot-Token": "robot-test-credential-long"})
                if response.status_code >= 400:
                    raise ValueError(f"Fake HTTP error {response.status_code}")
                return response.json()

            def event(inner, body):
                result = inner.call("POST", "/v1/gateway/events", body)
                if body["kind"] == inner.lost_kind:
                    inner.lost_kind = None
                    raise OSError("Simulated lost HTTP response after backend commit")
                return result

            def heartbeat(inner, boot_id, active_id, stopped):
                return inner.call("POST", "/v1/gateway/heartbeat", {"bootId": boot_id, "activeAssignmentId": active_id, "safeStopped": stopped})

            def poll(inner):
                return inner.call("GET", "/v1/gateway/assignment")["assignment"]

            def assignment(inner, assignment_id):
                return inner.call("GET", "/v1/gateway/assignments/" + assignment_id)

        store = EdgeStore(path)
        gateway = FakeRobotGateway(store, LocalTransport())
        self.addCleanup(lambda: gateway.store.close())
        return gateway, path

    def test_fake_gateway_http_postgres_two_legs_and_qr_complete_durably(self):
        request = self.draft()
        self.service.confirm("operator", request["requestId"])
        gateway, _ = self.edge()
        gateway.tick()
        aid = gateway.store.active()
        self.assertEqual(len(gateway.navigator.starts), 1)
        gateway.tick()
        self.assertEqual(len(gateway.navigator.starts), 1)
        gateway.navigator.finish()
        gateway.tick()
        self.assertEqual(self.service.assignment("robot", aid)["state"], "WAITING_PICKUP_QR")
        gateway.scan_qr(self.service.issue_qr("operator", aid, "PICKUP"))
        gateway.tick()
        self.assertEqual(len(gateway.navigator.starts), 2)
        gateway.navigator.finish()
        gateway.tick()
        self.assertEqual(self.service.request("operator", request["requestId"])["tasks"][0]["state"], "ASSIGNED")
        gateway.scan_qr(self.service.issue_qr("operator", aid, "DELIVERY"))
        gateway.tick()
        self.assertIsNone(gateway.store.active())
        self.assertFalse(gateway.store.pending())
        self.assertEqual(DeliveryService(PostgresRepository(self.dsn)).request("operator", request["requestId"])["tasks"][0]["state"], "COMPLETED")

    def test_lost_backend_ack_replays_same_sqlite_event_without_duplicate_dispatch(self):
        request = self.draft()
        self.service.confirm("operator", request["requestId"])
        gateway, _ = self.edge()
        gateway.transport.lost_kind = "ACK"
        with self.assertRaises(OSError):
            gateway.tick()
        self.assertFalse(gateway.navigator.starts)
        pending = gateway.store.pending()[0]
        gateway.tick()
        self.assertEqual(len(gateway.navigator.starts), 1)
        with self.repo.transaction() as tx:
            self.assertEqual(tx.one("SELECT count(*) AS n FROM execution_events WHERE id=%s", (pending["eventId"],))["n"], 1)

    def test_gateway_restart_never_redrives_unknown_motion(self):
        from robot_edge_gateway.gateway import FakeRobotGateway
        from robot_edge_gateway.store import EdgeStore
        request = self.draft()
        self.service.confirm("operator", request["requestId"])
        gateway, path = self.edge()
        gateway.tick()
        aid = gateway.store.active()
        gateway.navigator.cancel()  # Explicit fake local stop; no hardware assertion.
        gateway.store.close()
        restarted = FakeRobotGateway(EdgeStore(path), gateway.transport)
        self.addCleanup(restarted.store.close)
        # Prevent the original cleanup from closing an already closed handle twice.
        gateway.store = MockClosedStore()
        restarted.tick()
        self.assertFalse(restarted.navigator.starts)
        self.assertEqual(self.service.assignment("robot", aid)["state"], "UNKNOWN")
        self.assertIsNone(self.service.assignment("robot", aid)["resolvedAt"])

    def test_concurrent_draft_creation_and_confirmation_are_atomic_and_idempotent(self):
        with ThreadPoolExecutor(4) as pool:
            requests = list(pool.map(lambda _: self.draft(self.multi, key="parallel"), range(4)))
        self.assertEqual(len({request["requestId"] for request in requests}), 1)
        with ThreadPoolExecutor(4) as pool:
            confirmed = list(pool.map(lambda _: self.service.confirm("operator", requests[0]["requestId"]), range(4)))
        self.assertTrue(all(request == confirmed[0] for request in confirmed))
        with self.repo.transaction() as tx:
            self.assertEqual(tx.one("SELECT count(*) AS n FROM delivery_tasks")["n"], 3)

    def test_database_rejects_multi_package_task_and_second_robot(self):
        request = self.draft()
        with self.assertRaises(psycopg.errors.CheckViolation), self.repo.transaction() as tx:
            tx.update("delivery_tasks", request["tasks"][0]["id"], package_count=2)
        with self.assertRaises(psycopg.errors.UniqueViolation), self.repo.transaction() as tx:
            tx.insert("robots", id="second", token_hash="second-hash", map_version_id="map-v1")

    def test_json_dependencies_are_accepted_and_cycles_rejected_by_http(self):
        source = self.multi
        payload = {"idempotencyKey": "dependencies", "sessionId": source["sessionId"], "messageId": source["messageId"],
                   "mapId": "map-v1", "pickups": {task["taskId"]: "P1" for task in source["batch"]["tasks"]}, "dependencies": [[0, 2]]}
        headers = {"Authorization": "Bearer global-test-token", "X-Operator-Token": "operator-test-credential-long"}
        with TestClient(create_api(self.bridge, ApiSettings(access_token="global-test-token"), self.service)) as client:
            response = client.post("/v1/operations/requests", json=payload, headers=headers)
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(client.post("/v1/operations/requests", json=payload, headers=headers).status_code, 201)
            response = client.post("/v1/operations/requests", json={**payload, "idempotencyKey": "cycle", "dependencies": [[0, 2], [2, 0]]}, headers=headers)
            self.assertEqual(response.status_code, 422, response.text)


class MockClosedStore:
    def close(self):
        pass


if __name__ == "__main__":
    unittest.main()
