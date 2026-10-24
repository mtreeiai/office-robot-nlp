import copy
import io
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from threading import Barrier, Event
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from http_conversation_demo import (
    CONFLICT, SINGLE, STRICT, THREE, TWO, UNKNOWN_ROOM, demo_responses, run_demo,
)
from office_robot_nlp.clu import FakeLanguageUnderstandingParser
from office_robot_nlp.dialogue_manager import TaskReady
from office_robot_nlp.gemini_adapter import GeminiProviderRequestError
from office_robot_nlp.http_api import ApiSettings, create_api
from office_robot_nlp.runtime_monitor import (
    ConnectionState, MonitoringEventType, RobotStateSnapshot, StateSource, TaskMonitoringEvent,
)
from office_robot_nlp.semantic_map import load_semantic_registry
from office_robot_nlp.session_bridge import NlpSessionBridge
from office_robot_nlp.task_lifecycle import TaskExecutionEvent
from text_conversation import TextConversationApplication


class HttpApiTests(unittest.TestCase):
    def setUp(self):
        network = patch("urllib.request.urlopen", side_effect=AssertionError("Live provider forbidden"))
        network.start()
        self.addCleanup(network.stop)
        self.registry = load_semantic_registry("data/office_rooms.json", "data/office_room_aliases.json")
        self.apps = {}
        self.responses = demo_responses()

        def factory(session_id):
            parser = Mock(wraps=FakeLanguageUnderstandingParser(copy.deepcopy(self.responses)))
            app = TextConversationApplication(parser, self.registry.map_catalog,
                                              room_registry=self.registry, session_id=session_id)
            self.apps[session_id] = app
            return app

        self.bridge = NlpSessionBridge(factory)
        self.client = TestClient(create_api(self.bridge, ApiSettings()))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def session(self):
        response = self.client.post("/v1/sessions", json={})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], "ACTIVE")
        return response.json()["sessionId"]

    def send(self, sid, text, mid="m1"):
        return self.client.post(f"/v1/sessions/{sid}/messages", json={"messageId": mid, "text": text})

    def ready(self, text=THREE):
        sid = self.session()
        response = self.send(sid, text)
        self.assertEqual(response.status_code, 200, response.text)
        return sid, response.json()

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(set(response.json()), {"error"})
        self.assertEqual(set(response.json()["error"]), {"code", "message"})
        self.assertEqual(response.json()["error"]["code"], code)

    def test_session_ids_are_unique_opaque_and_not_text_demo(self):
        ids = {self.session() for _ in range(12)}
        self.assertEqual(len(ids), 12)
        for sid in ids:
            self.assertNotEqual(sid, "text-demo")
            self.assertEqual(self.apps[sid].state.conversationId, sid)

    def test_session_state_and_message_history_are_isolated(self):
        first, second = self.session(), self.session()
        self.assertEqual(self.send(first, UNKNOWN_ROOM).json()["dialogueStatus"], "CLARIFICATION_REQUIRED")
        self.assertIsNone(self.apps[second].state.pendingBatch)
        self.assertEqual(self.send(second, SINGLE).json()["dialogueStatus"], "TASK_READY")
        self.assertIsNotNone(self.apps[first].state.pendingBatch)
        self.assertEqual(len(self.apps[first].tasks), 0)

    def test_unknown_session(self):
        self.assert_error(self.send("missing", SINGLE), 404, "SESSION_NOT_FOUND")

    def test_single_task_envelope_and_existing_internal_result(self):
        sid = self.session()
        app = self.apps[sid]
        with patch.object(app, "process_text", wraps=app.process_text) as process:
            response = self.send(sid, SINGLE)
            self.assertEqual(response.status_code, 200, response.text)
            process.assert_called_once_with(SINGLE)
        result = response.json()
        self.assertEqual(set(result), {"sessionId", "messageId", "dialogueStatus", "reply", "batch"})
        self.assertEqual(result["dialogueStatus"], "TASK_READY")
        envelope = result["batch"]
        self.assertIsNone(envelope["batchId"])
        self.assertIsNone(envelope["orderPolicy"])
        self.assertEqual(len(envelope["tasks"]), 1)
        self.assertFalse(app.batches)
        task = envelope["tasks"][0]
        self.assertEqual(task["destinationId"], "02")
        self.assertEqual(task["sequenceIndex"], 0)
        self.assertEqual(task["constraints"], {"ifBusy": None, "ifClosed": None})
        self.assertEqual(envelope["orderPreview"], [{"position": 0, "taskId": task["taskId"]}])
        direct = TextConversationApplication(FakeLanguageUnderstandingParser(demo_responses()), self.registry.map_catalog)
        self.assertIsInstance(direct.process_text(SINGLE).output, TaskReady)
        self.assertEqual(direct.active_task.taskId, "task_001")

    def test_single_get_monitor_semantic_pose_and_null_physical_claims(self):
        sid, result = self.ready(SINGLE)
        task = result["batch"]["tasks"][0]
        current = self.client.get("/v1/tasks/" + task["taskId"]).json()
        self.assertEqual(current["sessionId"], sid)
        self.assertIsNone(current["batchId"])
        self.assertEqual(current["lifecycle"], "READY")
        self.assertEqual(current["handoff"], "NOT_SENT")
        self.assertEqual(current["documentLoad"], "UNCONFIRMED")
        self.assertEqual(current["semantic"], {"mapId": "office_amcl", "frameId": "map", "doorId": "main_door",
                                               "stagingPose": {"x": 23.9, "y": -4.15, "yaw": 1.347, "yawDegrees": 77.2}})
        self.assertIsNone(current["robotState"]["carryingDocumentIds"])
        self.assertEqual(current["robotState"]["connection"], "NOT_CONNECTED")

    def test_single_missing_room_clarifies_through_http_without_domain_batch(self):
        self.responses["Giao tài liệu."] = copy.deepcopy(self.responses[SINGLE])
        self.responses["Giao tài liệu."]["parsedIntent"]["destinationText"] = None
        self.responses["Giao tài liệu."]["parsedIntent"]["missingFields"] = ["destination"]
        sid = self.session()
        pending = self.send(sid, "Giao tài liệu.").json()
        self.assertEqual(pending["dialogueStatus"], "CLARIFICATION_REQUIRED")
        self.assertIsNone(pending["batch"])
        self.assertFalse(self.apps[sid].tasks)
        ready = self.send(sid, "Phòng 5.", "room").json()
        self.assertEqual(ready["dialogueStatus"], "TASK_READY")
        self.assertEqual(ready["batch"]["tasks"][0]["destinationId"], "05")
        self.assertIsNone(ready["batch"]["batchId"])
        self.assertFalse(self.apps[sid].batches)

    def test_get_with_no_registry_does_not_fabricate_coordinates(self):
        sid, ready = self.ready(SINGLE)
        self.apps[sid].room_registry = None
        task_id = ready["batch"]["tasks"][0]["taskId"]
        response = self.client.get("/v1/tasks/" + task_id)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["semantic"])

    def test_two_document_batch_associations(self):
        sid, result = self.ready(TWO)
        self.assertEqual(result["dialogueStatus"], "BATCH_READY")
        envelope = result["batch"]
        self.assertIsNotNone(envelope["batchId"])
        self.assertEqual([(t["documentText"], t["destinationId"]) for t in envelope["tasks"]],
                         [("hợp đồng", "02"), ("báo cáo", "05")])
        stored = self.client.get("/v1/batches/" + envelope["batchId"]).json()
        self.assertEqual(stored["sessionId"], sid)
        self.assertEqual([t["batchId"] for t in stored["tasks"]], [envelope["batchId"]] * 2)

    def test_three_documents_priority_preview_preserves_original_sequence(self):
        sid, result = self.ready()
        batch = result["batch"]
        tasks = batch["tasks"]
        self.assertEqual([(t["documentText"], t["destinationId"], t["sequenceIndex"]) for t in tasks],
                         [("hợp đồng", "02", 0), ("báo cáo", "05", 1), ("hồ sơ", "07", 2)])
        self.assertEqual(batch["orderPreview"], [{"position": i, "taskId": tasks[j]["taskId"]}
                                                 for i, j in enumerate((2, 0, 1))])
        self.assertEqual([r.sequenceIndex for r in self.apps[sid].tasks.values()], [0, 1, 2])
        self.assertIn("hồ sơ đến phòng 7 trước", result["reply"])

    def test_serializer_uses_existing_preview_function(self):
        from office_robot_nlp.order_preview import build_order_preview
        with patch("office_robot_nlp.api_dto.build_order_preview", wraps=build_order_preview) as preview:
            _, result = self.ready()
            preview.assert_called_once()
            self.client.get("/v1/batches/" + result["batch"]["batchId"])
            self.assertEqual(preview.call_count, 2)

    def test_explicit_strict_order_preserved(self):
        _, result = self.ready(STRICT)
        batch = result["batch"]
        self.assertEqual(batch["orderPolicy"], "STRICT_ORDER")
        self.assertEqual([p["taskId"] for p in batch["orderPreview"]], [t["taskId"] for t in batch["tasks"]])

    def test_monitor_reads_current_independent_states_without_overwriting(self):
        sid, result = self.ready(TWO)
        app = self.apps[sid]
        first, second = [t["taskId"] for t in result["batch"]["tasks"]]
        # Existing in-process automation boundary, deliberately no HTTP mutation endpoint.
        app.process_monitoring_event(TaskMonitoringEvent(first, MonitoringEventType.TASK_ACCEPTED, StateSource.AUTOMATION))
        app.process_execution_event(TaskExecutionEvent.TASK_QUEUED, task_id=first)
        app.update_robot_state(RobotStateSnapshot(connectionState=ConnectionState.CONNECTED,
                                                  carryingDocumentIds=(), source=StateSource.AUTOMATION))
        before = copy.deepcopy(dict(app.tasks))
        response = self.client.get("/v1/batches/" + result["batch"]["batchId"])
        self.assertEqual(response.status_code, 200, response.text)
        tasks = response.json()["tasks"]
        self.assertEqual([(t["lifecycle"], t["handoff"]) for t in tasks], [("QUEUED", "ACCEPTED"), ("READY", "NOT_SENT")])
        current = self.client.get("/v1/tasks/" + first).json()
        self.assertEqual(current["lastEvent"]["executionEvent"], "TASK_QUEUED")
        self.assertEqual(current["robotState"]["carryingDocumentIds"], [])
        self.assertEqual(self.client.get("/v1/tasks/" + second).json()["lifecycleSource"], "NONE")
        self.assertEqual(dict(app.tasks), before)
        self.assertEqual(result["batch"]["tasks"][0]["lifecycle"], "READY")

    def test_unknown_room_social_and_followup_register_one_atomic_batch(self):
        sid = self.session()
        first = self.send(sid, UNKNOWN_ROOM).json()
        self.assertEqual(first["dialogueStatus"], "CLARIFICATION_REQUIRED")
        self.assertIsNone(first["batch"])
        self.assertFalse(self.apps[sid].tasks)
        self.assertFalse(self.apps[sid].batches)
        social = self.send(sid, "Chào bạn", "social").json()
        self.assertEqual(social["dialogueStatus"], "CLARIFICATION_REQUIRED")
        self.assertIsNone(social["batch"])
        ready = self.send(sid, "Phòng 5.", "followup").json()
        self.assertEqual(ready["dialogueStatus"], "BATCH_READY")
        self.assertEqual([t["destinationId"] for t in ready["batch"]["tasks"]], ["02", "05"])
        self.assertEqual((len(self.apps[sid].tasks), len(self.apps[sid].batches)), (2, 1))

    def test_conflict_option_a_registers_immediately_for_both_policy_choices(self):
        for answer, policy, preview_rooms in (("theo mức ưu tiên", "PRIORITY", ["02", "05"]),
                                               ("giữ đúng thứ tự", "STRICT_ORDER", ["05", "02"])):
            with self.subTest(policy=policy):
                sid = self.session()
                pending = self.send(sid, CONFLICT).json()
                self.assertEqual(pending["dialogueStatus"], "CLARIFICATION_REQUIRED")
                self.assertIsNone(pending["batch"])
                ready = self.send(sid, answer, "choice").json()
                self.assertEqual(ready["dialogueStatus"], "BATCH_READY")
                batch = ready["batch"]
                self.assertEqual(batch["orderPolicy"], policy)
                by_id = {t["taskId"]: t for t in batch["tasks"]}
                self.assertEqual([by_id[p["taskId"]]["destinationId"] for p in batch["orderPreview"]], preview_rooms)
                self.assertTrue(all(t["lifecycle"] == "READY" for t in batch["tasks"]))

    def test_social_unsupported_and_cancellation_have_no_tasks(self):
        sid = self.session()
        self.assertEqual(self.send(sid, "Chào bạn").json()["dialogueStatus"], "SOCIAL")
        unknown = self.send(sid, "unsupported request", "unknown").json()
        self.assertIn(unknown["dialogueStatus"], ("UNSUPPORTED", "DIALOGUE_ERROR"))
        self.assertIsNone(unknown["batch"])
        self.send(sid, UNKNOWN_ROOM, "draft")
        cancel = self.send(sid, "Hủy", "cancel").json()
        self.assertEqual(cancel["dialogueStatus"], "PENDING_CANCELLED")
        self.assertIsNone(cancel["batch"])
        self.assertFalse(self.apps[sid].tasks)

    def test_idempotent_single_and_batch_without_provider_or_registration_repeat(self):
        for text in (SINGLE, THREE):
            with self.subTest(text=text):
                sid, first = self.ready(text)
                app = self.apps[sid]
                counts = (len(app.tasks), len(app.batches), app.parser.parse.call_count)
                self.assertEqual(self.send(sid, text).json(), first)
                self.assertEqual((len(app.tasks), len(app.batches), app.parser.parse.call_count), counts)

    def test_cached_response_is_original_while_get_is_current(self):
        sid, first = self.ready(SINGLE)
        task_id = first["batch"]["tasks"][0]["taskId"]
        self.apps[sid].process_execution_event(TaskExecutionEvent.TASK_QUEUED, task_id=task_id)
        self.assertEqual(self.send(sid, SINGLE).json(), first)
        self.assertEqual(self.client.get("/v1/tasks/" + task_id).json()["lifecycle"], "QUEUED")

    def test_bridge_cache_cannot_be_mutated_by_caller(self):
        sid = self.session()
        first = self.bridge.handle_user_message(sid, "m1", SINGLE)
        first.batch.tasks.clear()
        self.assertEqual(len(self.bridge.handle_user_message(sid, "m1", SINGLE).batch.tasks), 1)

    def test_idempotency_conflict_uses_exact_content(self):
        sid, _ = self.ready(SINGLE)
        self.assert_error(self.send(sid, SINGLE + " "), 409, "MESSAGE_ID_CONFLICT")
        self.assert_error(self.send(sid, "/sim task_queued"), 409, "MESSAGE_ID_CONFLICT")
        self.assertEqual(self.apps[sid].parser.parse.call_count, 1)

    def test_retried_old_clarification_does_not_restore_old_pending_state(self):
        sid = self.session()
        first = self.send(sid, UNKNOWN_ROOM).json()
        self.send(sid, "Phòng 5.", "room")
        self.assertEqual(self.send(sid, UNKNOWN_ROOM).json(), first)
        self.assertIsNone(self.apps[sid].state.pendingBatch)
        self.assertEqual(len(self.apps[sid].tasks), 2)

    def test_same_session_concurrent_messages_process_sequentially(self):
        sid = self.session()
        app = self.apps[sid]
        original = FakeLanguageUnderstandingParser(copy.deepcopy(self.responses)).parse
        entered, release, second_called = Event(), Event(), Event()
        contexts = []

        def parse(text, context):
            contexts.append(context)
            if text == UNKNOWN_ROOM:
                entered.set()
                if not release.wait(5):
                    raise AssertionError("Test release timed out")
            else:
                second_called.set()
            return original(text, context)

        app.parser.parse.side_effect = parse
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(self.send, sid, UNKNOWN_ROOM, "draft")
            self.assertTrue(entered.wait(5))
            second = pool.submit(self.send, sid, "Phòng 5.", "room")
            try:
                self.assertFalse(second_called.wait(0.1))
            finally:
                release.set()
            self.assertEqual(first.result(timeout=5).json()["dialogueStatus"], "CLARIFICATION_REQUIRED")
            self.assertEqual(second.result(timeout=5).json()["dialogueStatus"], "BATCH_READY")
        self.assertIsNotNone(contexts[1].pendingBatch)
        self.assertEqual(len(app.tasks), 2)

    def test_concurrent_duplicate_message_runs_parser_once(self):
        sid = self.session()
        gate = Barrier(6)

        def send():
            gate.wait(timeout=5)
            return self.send(sid, THREE).json()

        with ThreadPoolExecutor(6) as pool:
            results = list(pool.map(lambda _: send(), range(6)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(self.apps[sid].parser.parse.call_count, 1)
        self.assertEqual(len(self.apps[sid].tasks), 3)

    def test_concurrent_conflicting_reuse_commits_only_one_request(self):
        sid = self.session()
        gate = Barrier(2)

        def send(text):
            gate.wait(timeout=5)
            return self.send(sid, text)

        with ThreadPoolExecutor(2) as pool:
            responses = list(pool.map(send, (SINGLE, THREE)))
        self.assertEqual(sorted(r.status_code for r in responses), [200, 409])
        self.assertEqual(self.apps[sid].parser.parse.call_count, 1)
        success = next(r.json() for r in responses if r.status_code == 200)
        self.assertEqual(len(self.apps[sid].tasks), len(success["batch"]["tasks"]))

    def test_task_ids_remain_unique_across_mixed_single_and_batch_sessions(self):
        first, second = self.session(), self.session()
        tasks = []
        for sid in (first, second):
            for index, text in enumerate((SINGLE, TWO, SINGLE)):
                response = self.send(sid, text, str(index)).json()
                tasks.extend(t["taskId"] for t in response["batch"]["tasks"])
        self.assertEqual(len(tasks), 8)
        self.assertEqual(len(set(tasks)), 8)

    def test_failed_initial_batch_provider_request_creates_no_records(self):
        sid = self.session()
        for status in (429, 503):
            self.apps[sid].parser.parse.side_effect = GeminiProviderRequestError("private", status_code=status)
            response = self.send(sid, THREE)
            self.assert_error(response, 503, "PROVIDER_UNAVAILABLE")
            self.assertFalse(self.apps[sid].tasks)
            self.assertFalse(self.apps[sid].batches)
            self.assertIsNone(self.apps[sid].state.pendingBatch)

    def test_bridge_is_independent_of_transport_routes(self):
        sid = self.bridge.create_session().sessionId
        result = self.bridge.handle_user_message(sid, "message", THREE)
        self.assertEqual(result.dialogueStatus, "BATCH_READY")
        self.assertEqual(self.bridge.get_task(result.batch.tasks[0].taskId).sessionId, sid)
        self.assertEqual(self.bridge.get_batch(result.batch.batchId).sessionId, sid)

    def test_concurrent_sessions_have_global_unique_ids_and_independent_provider_calls(self):
        ids = [self.session() for _ in range(5)]
        gate = Barrier(5)
        for sid in ids:
            parser = self.apps[sid].parser
            original = FakeLanguageUnderstandingParser(copy.deepcopy(self.responses)).parse

            def parse(text, context, original=original):
                gate.wait(timeout=5)  # Fails if a global lock encloses provider calls.
                return original(text, context)

            parser.parse.side_effect = parse
        with ThreadPoolExecutor(5) as pool:
            results = list(pool.map(lambda sid: self.send(sid, THREE).json(), ids))
        task_ids = [t["taskId"] for r in results for t in r["batch"]["tasks"]]
        batch_ids = [r["batch"]["batchId"] for r in results]
        self.assertEqual(len(set(task_ids)), 15)
        self.assertEqual(len(set(batch_ids)), 5)
        for tid in task_ids:
            self.assertEqual(self.client.get("/v1/tasks/" + tid).status_code, 200)

    def test_provider_429_and_503_preserve_tasks_pending_state_and_allow_retry(self):
        for status in (429, 503):
            with self.subTest(status=status):
                sid, _ = self.ready(SINGLE)
                self.send(sid, UNKNOWN_ROOM, "draft")
                app = self.apps[sid]
                saved = copy.deepcopy((app.state, dict(app.tasks), dict(app.batches)))
                app.parser.parse.side_effect = GeminiProviderRequestError("secret-api-key provider request internals", status_code=status)
                response = self.send(sid, "Phòng 5.", "retry")
                self.assert_error(response, 503, "PROVIDER_UNAVAILABLE")
                self.assertNotIn("secret-api-key", response.text)
                self.assertEqual((app.state, dict(app.tasks), dict(app.batches)), saved)
                self.assert_error(self.send(sid, "changed", "retry"), 409, "MESSAGE_ID_CONFLICT")
                app.parser.parse.side_effect = None
                ready = self.send(sid, "Phòng 5.", "retry")
                self.assertEqual(ready.json()["dialogueStatus"], "BATCH_READY")
                self.assertEqual((len(app.tasks), len(app.batches)), (3, 1))

    def test_internal_failure_is_safe_and_rolls_back_completed_intake(self):
        sid = self.session()
        app = self.apps[sid]
        with patch("office_robot_nlp.session_bridge.batch_dto", side_effect=ValueError("secret traceback")):
            response = self.send(sid, THREE)
        self.assert_error(response, 500, "INTERNAL_ERROR")
        self.assertNotIn("secret", response.text)
        self.assertFalse(app.tasks)
        self.assertFalse(app.batches)
        self.assertIsNone(app.state.pendingBatch)
        self.assertEqual(self.send(sid, THREE).json()["dialogueStatus"], "BATCH_READY")
        self.assertEqual(len(app.tasks), 3)

    def test_unknown_task_and_batch(self):
        self.assert_error(self.client.get("/v1/tasks/missing"), 404, "TASK_NOT_FOUND")
        self.assert_error(self.client.get("/v1/batches/missing"), 404, "BATCH_NOT_FOUND")

    def test_get_endpoints_and_health_never_parse(self):
        sid, ready = self.ready(TWO)
        parser = self.apps[sid].parser
        parser.parse.side_effect = AssertionError("GET must not parse")
        self.assertEqual(self.client.get("/health").json(), {"status": "ok", "service": "office-robot-nlp", "apiVersion": "v1"})
        self.assertEqual(self.client.get("/v1/tasks/" + ready["batch"]["tasks"][0]["taskId"]).status_code, 200)
        self.assertEqual(self.client.get("/v1/batches/" + ready["batch"]["batchId"]).status_code, 200)
        self.assertEqual(parser.parse.call_count, 1)

    def test_health_does_not_even_construct_provider_or_session(self):
        factory = Mock(side_effect=AssertionError("No provider factory for health"))
        with TestClient(create_api(NlpSessionBridge(factory), ApiSettings())) as client:
            self.assertEqual(client.get("/health").status_code, 200)
        factory.assert_not_called()

    def test_invalid_payloads_have_safe_structured_errors(self):
        sid = self.session()
        for body in ({}, {"messageId": "x"}, {"messageId": 5, "text": SINGLE},
                     {"messageId": "x", "text": " "}, {"messageId": "", "text": SINGLE},
                     {"messageId": "x", "text": SINGLE, "secret": "secret-value"}):
            with self.subTest(body=body):
                response = self.client.post(f"/v1/sessions/{sid}/messages", json=body)
                self.assert_error(response, 422, "INVALID_REQUEST")
                self.assertNotIn("secret-value", response.text)
        self.assert_error(self.client.post("/v1/sessions", json={"unexpected": True}), 422, "INVALID_REQUEST")
        self.assert_error(self.client.post(f"/v1/sessions/{sid}/messages", content="{bad", headers={"content-type": "application/json"}),
                          422, "INVALID_REQUEST")
        self.assertFalse(self.apps[sid].tasks)

    def test_cli_commands_are_not_http_control_surface(self):
        sid = self.session()
        self.assert_error(self.send(sid, "/sim task_queued"), 422, "INVALID_MESSAGE")
        self.assertEqual(self.apps[sid].parser.parse.call_count, 0)
        self.assertFalse(self.apps[sid].tasks)

    def test_response_is_json_without_internal_dataclass_fields(self):
        _, ready = self.ready()
        text = json.dumps(ready)
        for forbidden in ("ResolvedIntent(", "BatchReady(", "TaskMonitoringRecord(", "_recipientText", "parsedIntent"):
            self.assertNotIn(forbidden, text)
        self.assertTrue(all(t["documentId"] is None for t in ready["batch"]["tasks"]))

    def test_nonloopback_binding_requires_access_control(self):
        for host in ("0.0.0.0", "::", "192.168.1.10", "robot-nlp.example"):
            with self.subTest(host=host), self.assertRaisesRegex(ValueError, "API_ACCESS_TOKEN"):
                ApiSettings(host=host)
        for host in ("127.0.0.1", "127.0.0.2", "::1", "localhost"):
            ApiSettings(host=host)

    def test_bearer_protects_all_routes_and_token_is_not_in_errors_or_repr(self):
        token = "test-secret-token"
        settings = ApiSettings(host="0.0.0.0", access_token=token)
        self.assertNotIn(token, repr(settings))
        with TestClient(create_api(self.bridge, settings)) as client:
            for path in ("/health", "/v1/tasks/missing", "/v1/batches/missing"):
                for header in ({}, {"Authorization": "Bearer wrong"}):
                    response = client.get(path, headers=header)
                    self.assert_error(response, 401, "UNAUTHORIZED")
                    self.assertNotIn(token, response.text)
            self.assertEqual(client.get("/health", headers={"Authorization": f"Bearer {token}"}).status_code, 200)
            self.assertEqual(client.post("/v1/sessions", json={}, headers={"Authorization": f"Bearer {token}"}).status_code, 201)

    def test_settings_read_environment_and_validate(self):
        with patch.dict(os.environ, {"API_HOST": "localhost", "API_PORT": "8123"}, clear=True):
            self.assertEqual(ApiSettings.from_environment(), ApiSettings("localhost", 8123))
        for kwargs in ({"port": 0}, {"port": 65536}, {"host": ""}, {"access_token": ""}, {"access_token": "a b"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ApiSettings(**kwargs)

    def test_provider_setup_failure_is_safe(self):
        factory = Mock(side_effect=GeminiProviderRequestError("secret setup"))
        with TestClient(create_api(NlpSessionBridge(factory), ApiSettings())) as client:
            response = client.post("/v1/sessions", json={})
            self.assert_error(response, 503, "PROVIDER_UNAVAILABLE")
            self.assertNotIn("secret", response.text)

    def test_http_demo_runs_without_credentials(self):
        with redirect_stdout(io.StringIO()) as output:
            run_demo(self.client)
        self.assertIn("Verified HTTP demo", output.getvalue())


if __name__ == "__main__":
    unittest.main()
