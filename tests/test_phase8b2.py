import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from office_robot_nlp.batch_validation import validate_batch
from office_robot_nlp.clu import FakeLanguageUnderstandingParser, decode_language_understanding_result
from office_robot_nlp.dialogue_manager import BatchClarificationRequired, BatchReady, TaskReady
from office_robot_nlp.domain.batch import OrderPolicy
from office_robot_nlp.domain.conversation import ConversationMode
from office_robot_nlp.entity_resolver import resolve_destination
from office_robot_nlp.gemini_adapter import GeminiProviderRequestError
from office_robot_nlp.runtime_monitor import (
    ConnectionState, DocumentLoadState, RobotStateSnapshot, StateSource, TaskHandoffState,
)
from office_robot_nlp.task_lifecycle import TaskLifecycleState, create_ready_task
from text_conversation import LocalCommandResult, ProviderFailure, create_application, run_console


def item(document="hợp đồng", destination="phòng 2", **fields):
    return {"documentText": document, "destinationText": destination, **fields}


def batch(*items, **fields):
    return {"type": "BATCH_COMMAND", "tasks": list(items or (item(), item("báo cáo", "phòng 5"))), **fields}


def single():
    return {"type": "TASK_COMMAND", "parsedIntent": {
        "intent": "DELIVER_DOCUMENT", "documentText": "tài liệu này", "destinationText": "phòng 2",
        "recipientText": "anh Phong", "priority": "NORMAL",
        "constraints": {"ifBusy": None, "ifClosed": None}, "missingFields": [],
    }}


def answer(value):
    return {"type": "CLARIFICATION_ANSWER", "field": "destination", "value": value}


class Phase8B2Tests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {
            "NLG_ENABLED": "false", "DEBUG_CONVERSATION": "false",
            "ROOM_REGISTRY_PATH": "data/office_rooms.json", "ROOM_ALIASES_PATH": "data/office_room_aliases.json",
        })
        environment.start()
        self.addCleanup(environment.stop)
        network = patch("urllib.request.urlopen", side_effect=AssertionError("Live provider calls forbidden"))
        network.start()
        self.addCleanup(network.stop)

    def app(self, responses=None, simulation=False):
        parser = Mock(wraps=FakeLanguageUnderstandingParser(responses or {"request": batch()}))
        return create_application(parser=parser, simulation_enabled=simulation)

    def ready(self, payload=None, simulation=False):
        app = self.app({"request": payload or batch()}, simulation)
        turn = app.process_text("request")
        self.assertIsInstance(turn.output, BatchReady)
        return app, turn

    def state_fingerprint(self, app):
        return (app.state, dict(app.tasks), dict(app.batches), app.robot_state,
                app._task_sequence, app._batch_sequence, app.active_task)

    def test_two_tasks_preserve_associations_and_independent_safe_defaults(self):
        app, turn = self.ready(batch(item(recipientText="anh Phong"), item("báo cáo", "phòng 5", recipientText="chị Lan")))
        self.assertEqual(list(app.batches), ["batch_001"])
        stored = app.batches["batch_001"]
        self.assertEqual(stored.sessionId, app.state.conversationId)
        self.assertEqual(stored.orderPolicy, OrderPolicy.PRIORITY)
        self.assertEqual([member.sequenceIndex for member in stored.tasks], [0, 1])
        self.assertEqual([member.taskId for member in stored.tasks], ["task_001", "task_002"])
        for index, (document, destination, recipient) in enumerate((
            ("hợp đồng", "02", "anh Phong"), ("báo cáo", "05", "chị Lan"),
        )):
            record = app.tasks[stored.tasks[index].taskId]
            intent = record.lifecycle.resolvedIntent
            self.assertEqual((intent.document.originalText, intent.destination.locationId, intent.recipientText),
                             (document, destination, recipient))
            self.assertIs(type(intent.destination.locationId), str)
            self.assertEqual(intent.priority.value, "NORMAL")
            self.assertIsNone(intent.document.documentId)
            self.assertEqual(record.lifecycle.state, TaskLifecycleState.READY)
            self.assertEqual(record.handoff, TaskHandoffState.NOT_SENT)
            self.assertEqual(record.documentLoad, DocumentLoadState.UNCONFIRMED)
            self.assertEqual((record.lifecycleSource, record.handoffSource, record.documentLoadSource),
                             (StateSource.NONE,) * 3)
            self.assertEqual(record.sequenceIndex, index)
            self.assertEqual(record.batchId, "batch_001")
            self.assertFalse(hasattr(record, "compartmentId"))
        self.assertEqual(app.robot_state, RobotStateSnapshot())
        self.assertEqual(turn.responsePlan.facts["batchId"], "batch_001")

    def test_three_tasks_preserve_priority_and_original_order_without_sorting(self):
        app, turn = self.ready(batch(item(priority="NORMAL"), item("báo cáo", "phòng 5", priority="HIGH"),
                                     item("hồ sơ", "phòng 7", priority="LOW")))
        intents = [record.lifecycle.resolvedIntent for record in app.tasks.values()]
        self.assertEqual([intent.destination.locationId for intent in intents], ["02", "05", "07"])
        self.assertEqual([intent.priority.value for intent in intents], ["NORMAL", "HIGH", "LOW"])
        self.assertEqual([intent.document.originalText for intent in intents], ["hợp đồng", "báo cáo", "hồ sơ"])
        self.assertIn("3 nhiệm vụ", turn.responseText)
        self.assertIn("Ưu tiên cao: báo cáo đến phòng 5", turn.responseText)

    def test_every_room_uses_existing_resolver_and_no_room_count_capacity_limit(self):
        payload = batch(*(item(f"tài liệu riêng {index}", f"phòng {index % 7 + 1}") for index in range(12)))
        with patch("office_robot_nlp.batch_validation.resolve_destination", wraps=resolve_destination) as resolver:
            app, _ = self.ready(payload)
        self.assertEqual(len(app.tasks), 12)
        for number in range(1, 8):
            self.assertTrue(any(call.args[0] == f"phòng {number}" for call in resolver.call_args_list))
        self.assertEqual([record.lifecycle.resolvedIntent.destination.locationId for record in app.tasks.values()],
                         [f"{index % 7 + 1:02d}" for index in range(12)])

    def test_same_room_with_two_explicit_documents_creates_distinct_tasks(self):
        app, _ = self.ready(batch(item("hợp đồng", "phòng 2"), item("báo cáo", "phòng 2"), allocationStatus="CLEAR"))
        self.assertEqual(len(app.tasks), 2)
        self.assertEqual([record.lifecycle.resolvedIntent.destination.locationId for record in app.tasks.values()], ["02", "02"])

    def test_unknown_room_creates_only_pending_draft_and_resolves_every_item(self):
        app = self.app({"request": batch(item(priority="HIGH"), item("báo cáo", "phòng 8"))})
        turn = app.process_text("request")
        self.assertIsInstance(turn.output, BatchClarificationRequired)
        self.assertEqual(app.state.mode, ConversationMode.AWAITING_BATCH_INFORMATION)
        self.assertEqual(len(app.tasks), 0)
        self.assertEqual(len(app.batches), 0)
        self.assertEqual((app._task_sequence, app._batch_sequence), (0, 0))
        self.assertEqual(app.state.pendingBatch.resolved[0].destination.locationId, "02")
        self.assertIsNone(app.state.pendingBatch.resolved[1])
        self.assertEqual(app.state.pendingBatch.awaitingIndex, 1)
        self.assertIn("phòng 8", turn.responseText)
        self.assertIn("báo cáo", turn.responseText)

    def test_clarification_changes_only_invalid_item_and_preserves_all_other_fields(self):
        app = self.app({
            "request": batch(item(priority="HIGH", recipientText="anh Phong", constraints={"ifBusy": "WAIT", "ifClosed": "RETRY"}),
                             item("báo cáo", "phòng 8", recipientText="chị Lan", priority="LOW",
                                  constraints={"ifBusy": "ASK_USER", "ifClosed": "ASK_USER"})),
            "room": answer("Phòng 5"),
        })
        app.process_text("request")
        first = app.state.pendingBatch.draft.tasks[0]
        turn = app.process_text("room")
        self.assertIsInstance(turn.output, BatchReady)
        self.assertIs(turn.output.draft.tasks[0], first)
        first_intent = app.tasks["task_001"].lifecycle.resolvedIntent
        second_intent = app.tasks["task_002"].lifecycle.resolvedIntent
        self.assertEqual((first_intent.destination.locationId, first_intent.priority.value, first_intent.recipientText),
                         ("02", "HIGH", "anh Phong"))
        self.assertEqual((second_intent.destination.locationId, second_intent.priority.value, second_intent.recipientText),
                         ("05", "LOW", "chị Lan"))
        self.assertEqual(first_intent.constraints.ifClosed.value, "RETRY")
        self.assertEqual(second_intent.constraints.ifBusy.value, "ASK_USER")

    def test_repeated_invalid_answers_social_and_unknown_preserve_pending_details(self):
        app = self.app({"request": batch(item(), item("báo cáo", "phòng 8")),
                        "bad": answer("phòng A"), "bad2": answer("phòng 10"),
                        "hello": {"type": "SOCIAL"}, "noise": {"type": "UNKNOWN"}, "good": answer("phòng 5")})
        app.process_text("request")
        first = app.state.pendingBatch.draft.tasks[0]
        for text in ("bad", "hello", "bad2", "noise"):
            app.process_text(text)
            self.assertIs(app.state.pendingBatch.draft.tasks[0], first)
            self.assertEqual(len(app.tasks), 0)
        self.assertIsInstance(app.process_text("good").output, BatchReady)

    def test_two_invalid_items_are_clarified_one_at_a_time_not_both(self):
        app = self.app({"request": batch(item(destination="phòng 8"), item("báo cáo", "phòng A")),
                        "first": answer("phòng 2"), "second": answer("phòng 5")})
        app.process_text("request")
        self.assertEqual(app.state.pendingBatch.awaitingIndex, 0)
        app.process_text("first")
        self.assertEqual(app.state.pendingBatch.awaitingIndex, 1)
        self.assertEqual(app.state.pendingBatch.draft.tasks[1].destinationText, "phòng A")
        self.assertEqual(len(app.tasks), 0)
        app.process_text("second")
        self.assertEqual(len(app.tasks), 2)

    def test_targeted_task_update_preserves_other_items(self):
        app = self.app({"request": batch(item(priority="LOW"), item("báo cáo", "phòng 8")),
                        "update": {"type": "TASK_UPDATE", "recipientText": "chị Lan", "priority": "HIGH"},
                        "good": {"type": "TASK_UPDATE", "destinationText": "phòng 5"}})
        app.process_text("request")
        app.process_text("update")
        self.assertEqual(app.state.pendingBatch.draft.tasks[0].priority.value, "LOW")
        self.assertIsNone(app.state.pendingBatch.draft.tasks[0].recipientText)
        self.assertEqual(len(app.tasks), 0)
        app.process_text("good")
        self.assertEqual(app.tasks["task_002"].lifecycle.resolvedIntent.recipientText, "chị Lan")
        self.assertEqual(app.tasks["task_002"].lifecycle.resolvedIntent.priority.value, "HIGH")

    def test_explicit_wrong_item_target_cannot_modify_the_current_clarification(self):
        app = self.app({"request": batch(item(), item("báo cáo", "phòng 8")),
                        "wrong": {**answer("phòng 7"), "itemIndex": 0},
                        "wrong-update": {"type": "TASK_UPDATE", "destinationText": "phòng 7", "itemIndex": 0},
                        "correct": {**answer("phòng 5"), "itemIndex": 1}})
        app.process_text("request")
        previous = app.state
        for text in ("wrong", "wrong-update"):
            app.process_text(text)
            self.assertIs(app.state, previous)
            self.assertEqual(len(app.tasks), 0)
        app.process_text("correct")
        self.assertEqual(app.tasks["task_001"].lifecycle.resolvedIntent.destination.locationId, "02")
        self.assertEqual(app.tasks["task_002"].lifecycle.resolvedIntent.destination.locationId, "05")

    def test_explicit_cancellation_discards_only_pending_draft(self):
        app = self.app({"single": single(), "request": batch(item(), item("báo cáo", "phòng 8")),
                        "cancel": {"type": "CANCEL_PENDING"}})
        app.process_text("single")
        previous = dict(app.tasks)
        app.process_text("request")
        app.process_text("cancel")
        self.assertEqual(app.state.mode, ConversationMode.IDLE)
        self.assertIsNone(app.state.pendingBatch)
        self.assertEqual(dict(app.tasks), previous)
        self.assertEqual(len(app.batches), 0)
        self.assertEqual(app._task_sequence, 1)

    def test_ambiguous_document_allocation_never_invents_copies(self):
        app = self.app({"request": batch(item("tài liệu", "phòng 2"), item("tài liệu", "phòng 5"), allocationStatus="AMBIGUOUS"),
                        "short": answer("phòng 5")})
        turn = app.process_text("request")
        self.assertIsInstance(turn.output, BatchClarificationRequired)
        self.assertEqual(app.state.pendingBatch.awaitingField, "allocation")
        self.assertIsNone(app.state.pendingBatch.awaitingIndex)
        previous = app.state
        app.process_text("short")
        self.assertIs(app.state, previous)
        self.assertEqual(len(app.tasks), 0)

    def test_explicit_allocation_answer_changes_documents_but_keeps_known_fields(self):
        app = self.app({
            "request": batch(item("tài liệu", "phòng 2", priority="HIGH", recipientText="anh Phong"),
                             item("tài liệu", "phòng 5"), allocationStatus="AMBIGUOUS"),
            "mapping": batch(item("bản hợp đồng 1", "phòng 2"), item("bản hợp đồng 2", "phòng 5"), allocationStatus="CLEAR"),
        })
        app.process_text("request")
        self.assertIsInstance(app.process_text("mapping").output, BatchReady)
        self.assertEqual(app.tasks["task_001"].lifecycle.resolvedIntent.document.originalText, "bản hợp đồng 1")
        self.assertEqual(app.tasks["task_001"].lifecycle.resolvedIntent.priority.value, "HIGH")
        self.assertEqual(app.tasks["task_001"].lifecycle.resolvedIntent.recipientText, "anh Phong")

    def test_allocation_answer_cannot_overwrite_known_destinations(self):
        app = self.app({"request": batch(item("tài liệu", "phòng 2"), item("tài liệu", "phòng 5"), allocationStatus="AMBIGUOUS"),
                        "wrong": batch(item("hợp đồng", "phòng 7"), item("báo cáo", "phòng 5"))})
        app.process_text("request")
        previous = app.state
        app.process_text("wrong")
        self.assertIs(app.state, previous)
        self.assertEqual(len(app.tasks), 0)

    def test_missing_document_is_requested_for_only_that_item(self):
        app = self.app({"request": batch(item(), item(None, "phòng 5"), allocationStatus="CLEAR"),
                        "document": {"type": "CLARIFICATION_ANSWER", "field": "document", "value": "báo cáo"}})
        app.process_text("request")
        self.assertEqual((app.state.pendingBatch.awaitingIndex, app.state.awaitingField), (1, "document"))
        app.process_text("document")
        self.assertEqual(app.tasks["task_001"].lifecycle.resolvedIntent.document.originalText, "hợp đồng")
        self.assertEqual(app.tasks["task_002"].lifecycle.resolvedIntent.document.originalText, "báo cáo")

    def test_explicit_strict_order_is_stored_without_execution(self):
        app, _ = self.ready(batch(item(priority="HIGH"), item("báo cáo", "phòng 5"),
                                 item("hồ sơ", "phòng 7"), orderPolicy="STRICT_ORDER"))
        self.assertEqual(app.batches["batch_001"].orderPolicy, OrderPolicy.STRICT_ORDER)
        self.assertTrue(all(record.lifecycle.state is TaskLifecycleState.READY for record in app.tasks.values()))
        self.assertEqual([member.sequenceIndex for member in app.batches["batch_001"].tasks], [0, 1, 2])

    def test_conflicting_priority_and_strict_order_wait_for_explicit_choice(self):
        for choice in ("STRICT_ORDER", "PRIORITY"):
            with self.subTest(choice=choice):
                app = self.app({"request": batch(item(), item("báo cáo", "phòng 5", priority="HIGH"), orderPolicy="STRICT_ORDER"),
                                "choice": {"type": "BATCH_ORDER", "orderPolicy": choice}})
                app.process_text("request")
                self.assertEqual(app.state.pendingBatch.awaitingField, "order")
                self.assertEqual(len(app.tasks), 0)
                app.process_text("choice")
                self.assertEqual(app.batches["batch_001"].orderPolicy.value, choice)
                self.assertEqual(app.tasks["task_002"].lifecycle.resolvedIntent.priority.value, "HIGH")

    def test_provider_timeout_429_and_503_preserve_registered_and_pending_state(self):
        for error in (TimeoutError("timeout"), GeminiProviderRequestError("quota", status_code=429),
                      GeminiProviderRequestError("unavailable", status_code=503)):
            with self.subTest(error=error):
                app = self.app({"valid": batch(), "pending": batch(item(), item("hồ sơ", "phòng 8"))})
                app.process_text("valid")
                app.process_text("pending")
                before = self.state_fingerprint(app)
                app.parser.parse.side_effect = error
                self.assertIsInstance(app.process_text("Phòng 7").output, ProviderFailure)
                self.assertEqual(self.state_fingerprint(app), before)

    def test_internal_failure_rolls_back_all_records_and_retry_keeps_semantics(self):
        app = self.app({"single": single(), "request": batch(), "retry": {"type": "BATCH_CONFIRM"}})
        app.process_text("single")
        old_tasks = dict(app.tasks)
        calls = 0
        def fail_second(task_id, intent):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("injected registration failure")
            return create_ready_task(task_id, intent)
        with patch("text_conversation.create_ready_task", side_effect=fail_second):
            turn = app.process_text("request")
        self.assertIsInstance(turn.output, BatchClarificationRequired)
        self.assertEqual(app.state.pendingBatch.awaitingField, "confirmation")
        self.assertEqual(dict(app.tasks), old_tasks)
        self.assertEqual(len(app.batches), 0)
        self.assertEqual((app._task_sequence, app._batch_sequence), (1, 0))
        self.assertIsInstance(app.process_text("retry").output, BatchReady)
        self.assertEqual([member.taskId for member in app.batches["batch_001"].tasks], ["task_002", "task_003"])

    def test_duplicate_generated_task_ids_cannot_partially_register(self):
        app = self.app()
        with patch.object(app, "_next_task_ids", return_value=(("same", "same"), 2)):
            turn = app.process_text("request")
        self.assertIsInstance(turn.output, BatchClarificationRequired)
        self.assertEqual(len(app.tasks), 0)
        self.assertEqual(len(app.batches), 0)
        self.assertEqual(app._task_sequence, 0)

    def test_collision_with_existing_task_id_does_not_overwrite_it(self):
        app = self.app({"single": single(), "request": batch()})
        app.process_text("single")
        old = app.tasks["task_001"]
        with patch.object(app, "_next_task_ids", return_value=(("task_001", "task_002"), 2)):
            app.process_text("request")
        self.assertEqual(list(app.tasks), ["task_001"])
        self.assertIs(app.tasks["task_001"], old)
        self.assertEqual(len(app.batches), 0)

    def test_multiple_batches_and_single_tasks_share_unique_ids_and_old_single_contract(self):
        app = self.app({"batch": batch(), "single": single()})
        app.process_text("batch")
        single_turn = app.process_text("single")
        self.assertIsInstance(single_turn.output, TaskReady)
        self.assertIsNone(app.tasks["task_003"].batchId)
        app.process_text("batch")
        self.assertEqual(list(app.tasks), [f"task_{index:03d}" for index in range(1, 6)])
        self.assertEqual(list(app.batches), ["batch_001", "batch_002"])
        self.assertEqual([member.taskId for member in app.batches["batch_002"].tasks], ["task_004", "task_005"])

    def test_registration_revalidates_forged_resolved_ids_and_document_ids(self):
        app = self.app()
        decoded = decode_language_understanding_result(batch(), app.state).result
        validated = validate_batch(decoded.draft, app.map_catalog)
        ready = BatchReady(decoded.draft, validated.resolved)
        ready.resolvedIntents[0].destination.locationId = "08"
        with self.assertRaises(ValueError):
            app.register_batch(ready)
        validated = validate_batch(decoded.draft, app.map_catalog)
        ready = BatchReady(decoded.draft, validated.resolved)
        ready.resolvedIntents[0].document.documentId = "invented"
        with self.assertRaises(ValueError):
            app.register_batch(ready)
        self.assertEqual(len(app.tasks), 0)
        self.assertEqual(len(app.batches), 0)

    def test_batch_commands_are_local_and_old_commands_remain_available(self):
        app, _ = self.ready()
        app.parser.reset_mock()
        app.response_generator = Mock()
        for command_text in ("/batches", "/batch batch_001", "/batch unknown", "/batch", "/status", "/tasks", "/task task_001", "/help"):
            with self.subTest(command=command_text):
                turn = app.process_text(command_text)
                self.assertIsInstance(turn.output, LocalCommandResult)
        self.assertIn("batch_001 | 2 | VALID", app.process_text("/batches").responseText)
        self.assertIn("Unknown batch ID", app.process_text("/batch absent").responseText)
        self.assertIn("/batches", app.process_text("/help").responseText)
        app.parser.parse.assert_not_called()
        app.response_generator.generate.assert_not_called()

    def test_batch_monitor_reads_live_independent_member_records_and_provenance(self):
        app, _ = self.ready(batch(item("bản hợp đồng", "phòng 7", priority="LOW"),
                                 item("bản báo cáo", "phòng 2", recipientText="chị Lan")), simulation=True)
        app.process_text("/sim document-loaded task_001")
        app.process_text("/sim task-queued task_001")
        snapshot = app.process_text("/batch batch_001").responseText
        first, second = snapshot.split("TASK 2", 1)
        self.assertIn("Destination ID : 07", first)
        self.assertIn("Document Load  : LOADED", first)
        self.assertIn("Lifecycle      : QUEUED", first)
        self.assertIn("Load Source    : SIMULATED", first)
        self.assertIn("Destination ID : 02", second)
        self.assertIn("Document Load  : UNCONFIRMED", second)
        self.assertIn("Lifecycle      : READY", second)
        self.assertIn("Recipient      : chị Lan", second)
        self.assertIn("Physical capacity: UNKNOWN", second)
        self.assertEqual(app.robot_state, RobotStateSnapshot())

    def test_batch_recording_uses_safe_template_even_if_optional_generator_overclaims(self):
        app = self.app()
        app.response_generator = Mock()
        app.response_generator.generate.return_value = "Robot đã nhận tài liệu và đang di chuyển, đủ sức mang tất cả."
        turn = app.process_text("request")
        self.assertIn("ghi nhận 2 nhiệm vụ", turn.responseText)
        self.assertIn("hợp đồng đến phòng 2", turn.responseText)
        self.assertIn("báo cáo đến phòng 5", turn.responseText)
        self.assertNotIn("đang di chuyển", turn.responseText)
        app.response_generator.generate.assert_not_called()

    def test_batch_does_not_change_a_confirmed_robot_snapshot(self):
        app = self.app()
        robot = RobotStateSnapshot(robotId="physical-R", connectionState=ConnectionState.CONNECTED,
                                   locationId="01", currentTaskId="external-task", source=StateSource.AUTOMATION)
        app.update_robot_state(robot)
        app.process_text("request")
        self.assertIs(app.robot_state, robot)
        self.assertEqual(app.tasks["task_001"].handoff, TaskHandoffState.NOT_SENT)

    def test_unsupported_compound_action_does_not_create_partial_delivery(self):
        text = "Giao hợp đồng đến phòng 2 rồi mang về cho tôi."
        app = self.app({text: {"type": "UNKNOWN", "reason": "unsupported return action"}})
        app.process_text(text)
        self.assertEqual(len(app.tasks), 0)
        self.assertEqual(len(app.batches), 0)

    def test_pending_batch_cannot_be_replaced_by_a_new_unrelated_command(self):
        app = self.app({"request": batch(item(), item("báo cáo", "phòng 8")), "other": single()})
        app.process_text("request")
        pending = app.state
        app.process_text("other")
        self.assertIs(app.state, pending)
        self.assertEqual(len(app.tasks), 0)

    def test_batch_debug_toggle_retains_natural_responses(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                app = self.app()
                output = io.StringIO()
                with patch.dict(os.environ, {"DEBUG_CONVERSATION": str(enabled).lower()}), \
                        patch("builtins.input", side_effect=["request", "exit"]), redirect_stdout(output):
                    run_console(app)
                self.assertIn("ghi nhận 2 nhiệm vụ", output.getvalue())
                self.assertEqual("DELIVERY BATCH MONITOR" in output.getvalue(), enabled)

    def test_empty_batch_inspection_and_read_only_registry(self):
        app = self.app()
        self.assertIn("No locally registered batches", app.process_text("/batches").responseText)
        self.assertIn("Unknown batch ID", app.process_text("/batch missing").responseText)
        app.process_text("request")
        with self.assertRaises(TypeError):
            app.batches["other"] = app.batches["batch_001"]


if __name__ == "__main__":
    unittest.main()
