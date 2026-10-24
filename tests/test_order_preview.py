import copy
import io
import os
import unittest
from contextlib import redirect_stdout
from dataclasses import FrozenInstanceError, asdict
from unittest.mock import Mock, patch

from office_robot_nlp.clu import FakeLanguageUnderstandingParser
from office_robot_nlp.dialogue_manager import BatchClarificationRequired, BatchReady, TaskReady
from office_robot_nlp.domain.batch import OrderPolicy
from office_robot_nlp.domain.conversation import ConversationMode
from office_robot_nlp.order_preview import build_order_preview
from office_robot_nlp.runtime_monitor import DocumentLoadState, RobotStateSnapshot, TaskHandoffState
from office_robot_nlp.task_lifecycle import TaskLifecycleState
from text_conversation import create_application, run_console


def candidate(priorities=("NORMAL", "NORMAL", "HIGH"), policy="PRIORITY"):
    documents = ("hợp đồng", "báo cáo", "hồ sơ")
    rooms = (2, 5, 7)
    return {"type": "BATCH_COMMAND", "orderPolicy": policy, "allocationStatus": "CLEAR", "tasks": [
        {"documentText": documents[index] if index < 3 else f"tài liệu {index + 1}",
         "destinationText": f"phòng {rooms[index] if index < 3 else index + 1}", "priority": priority,
         "recipientText": "anh Phong" if index == 0 else None,
         "constraints": {"ifBusy": "WAIT" if index == 0 else None, "ifClosed": None}}
        for index, priority in enumerate(priorities)
    ]}


def conflict():
    return {"type": "BATCH_COMMAND", "orderPolicy": "STRICT_ORDER", "allocationStatus": "CLEAR", "tasks": [
        {"documentText": "báo cáo", "destinationText": "phòng 5", "priority": "NORMAL"},
        {"documentText": "hợp đồng", "destinationText": "phòng 2", "priority": "HIGH"},
    ]}


class OrderPreviewTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {
            "NLG_ENABLED": "false", "DEBUG_CONVERSATION": "false",
            "ROOM_REGISTRY_PATH": "data/office_rooms.json", "ROOM_ALIASES_PATH": "data/office_room_aliases.json",
        })
        environment.start()
        self.addCleanup(environment.stop)
        blocked = patch("urllib.request.urlopen", side_effect=AssertionError("No real providers"))
        blocked.start()
        self.addCleanup(blocked.stop)

    def app(self, responses=None, simulation=False):
        parser = Mock(wraps=FakeLanguageUnderstandingParser(responses or {"request": candidate()}))
        return create_application(parser=parser, simulation_enabled=simulation)

    def ready(self, payload=None, simulation=False):
        app = self.app({"request": payload or candidate()}, simulation)
        turn = app.process_text("request")
        self.assertIsInstance(turn.output, BatchReady)
        return app, turn

    def preview(self, app):
        return build_order_preview(app.batches["batch_001"], app.tasks)

    def test_high_last_keeps_stored_order_and_explains_the_different_preview(self):
        text = "Giao hợp đồng đến phòng 2, báo cáo đến phòng 5 và hồ sơ đến phòng 7. Ưu tiên hồ sơ trước."
        app = self.app({text: candidate()})
        turn = app.process_text(text)
        batch = app.batches["batch_001"]
        self.assertEqual(batch.orderPolicy, OrderPolicy.PRIORITY)
        self.assertEqual([member.taskId for member in batch.tasks], ["task_001", "task_002", "task_003"])
        self.assertEqual([member.sequenceIndex for member in batch.tasks], [0, 1, 2])
        preview = self.preview(app)
        self.assertEqual([item.taskId for item in preview], ["task_003", "task_001", "task_002"])
        self.assertEqual([item.sequenceIndex for item in preview], [2, 0, 1])
        self.assertEqual([item.previewPosition for item in preview], [1, 2, 3])
        self.assertEqual([item.destinationId for item in preview], ["07", "02", "05"])
        self.assertIn("hồ sơ đến phòng 7 trước, sau đó hợp đồng đến phòng 2, sau đó báo cáo đến phòng 5", turn.responseText)
        self.assertTrue(all(record.lifecycle.state is TaskLifecycleState.READY for record in app.tasks.values()))

    def test_equal_priority_uses_sequence_index_even_if_record_mapping_is_reversed(self):
        app, _ = self.ready(candidate(("NORMAL",) * 3))
        records = dict(reversed(list(app.tasks.items())))
        preview = build_order_preview(app.batches["batch_001"], records)
        self.assertEqual([item.taskId for item in preview], ["task_001", "task_002", "task_003"])

    def test_mixed_priorities_sort_high_normal_low_with_stable_ties(self):
        app, _ = self.ready(candidate(("LOW", "HIGH", "NORMAL", "HIGH", "NORMAL", "LOW")))
        preview = self.preview(app)
        self.assertEqual([item.taskId for item in preview],
                         ["task_002", "task_004", "task_003", "task_005", "task_001", "task_006"])
        self.assertEqual([item.priority.value for item in preview], ["HIGH", "HIGH", "NORMAL", "NORMAL", "LOW", "LOW"])

    def test_nonconflicting_strict_order_is_immediately_ready_without_confirmation(self):
        app, turn = self.ready(candidate(("HIGH", "NORMAL", "LOW"), "STRICT_ORDER"))
        self.assertFalse(turn.output.draft.orderConfirmed)
        self.assertEqual([item.taskId for item in self.preview(app)], ["task_001", "task_002", "task_003"])
        self.assertIn("giữ đúng thứ tự", turn.responseText)
        self.assertIn("giữ nguyên thứ tự yêu cầu", turn.responseText)
        self.assertEqual(app.state.mode, ConversationMode.IDLE)

    def check_conflict_choice(self, policy, answer):
        app = self.app({"conflict": conflict(), answer: {"type": "BATCH_ORDER", "orderPolicy": policy}})
        first = app.process_text("conflict")
        self.assertIsInstance(first.output, BatchClarificationRequired)
        self.assertEqual((len(app.tasks), len(app.batches)), (0, 0))
        self.assertEqual(app.state.pendingBatch.awaitingField, "order")
        with patch.object(app, "register_batch", wraps=app.register_batch) as register:
            chosen = app.process_text(answer)
            register.assert_called_once()
            self.assertIsInstance(chosen.output, BatchReady)
            self.assertTrue(chosen.output.draft.orderConfirmed)
            self.assertTrue(chosen.responsePlan.facts["orderPolicySelectedByUser"])
            self.assertEqual(app.state.mode, ConversationMode.IDLE)
            self.assertIsNone(app.state.pendingBatch)
            self.assertEqual((len(app.tasks), len(app.batches)), (2, 1))
            self.assertNotIn("đồng ý", chosen.responseText.casefold())
            self.assertNotIn("xác nhận", chosen.responseText.casefold())
            self.assertNotIn("?", chosen.responseText)
            app.process_text("/batch batch_001")
            app.process_text("/batches")
            # A stale repeat of a choice is no longer valid in IDLE, so cannot duplicate intake.
            app.process_text(answer)
            register.assert_called_once()
        return app, chosen

    def test_conflict_then_priority_registers_once_and_explains_selected_preview(self):
        app, chosen = self.check_conflict_choice("PRIORITY", "theo mức ưu tiên")
        self.assertEqual(app.batches["batch_001"].orderPolicy, OrderPolicy.PRIORITY)
        self.assertEqual([item.taskId for item in self.preview(app)], ["task_002", "task_001"])
        self.assertIn("Đã chọn giao theo mức ưu tiên.", chosen.responseText)
        self.assertIn("hợp đồng đến phòng 2 trước, sau đó báo cáo đến phòng 5", chosen.responseText)

    def test_conflict_then_strict_registers_once_and_keeps_requested_preview(self):
        app, chosen = self.check_conflict_choice("STRICT_ORDER", "giữ đúng thứ tự")
        self.assertEqual(app.batches["batch_001"].orderPolicy, OrderPolicy.STRICT_ORDER)
        self.assertEqual([item.taskId for item in self.preview(app)], ["task_001", "task_002"])
        self.assertIn("Đã chọn giữ đúng thứ tự bạn yêu cầu.", chosen.responseText)
        self.assertIn("báo cáo đến phòng 5 trước, sau đó hợp đồng đến phòng 2", chosen.responseText)

    def test_invalid_clarification_keeps_conflict_and_registers_nothing(self):
        app = self.app({"conflict": conflict(), "unclear": {"type": "UNKNOWN", "reason": "ambiguous choice"},
                        "unsupported": {"type": "TASK_UPDATE", "priority": "HIGH"}})
        app.process_text("conflict")
        before = copy.deepcopy(app.state)
        for text in ("unclear", "unsupported"):
            turn = app.process_text(text)
            self.assertIsInstance(turn.output, BatchClarificationRequired)
            self.assertEqual(app.state, before)
            self.assertEqual((len(app.tasks), len(app.batches)), (0, 0))

    def test_repeated_preview_calls_are_read_only_for_all_runtime_state(self):
        app, _ = self.ready()
        batch = app.batches["batch_001"]
        original_references = batch.tasks
        before = copy.deepcopy((batch, dict(app.tasks), app.robot_state, app.state,
                                app._task_sequence, app._batch_sequence, app.active_task))
        app.parser.reset_mock()
        with patch.object(app, "register_batch", side_effect=AssertionError("Preview cannot register tasks")):
            first = self.preview(app)
            for _ in range(3):
                self.assertEqual(self.preview(app), first)
        self.assertIs(batch.tasks, original_references)
        self.assertEqual((batch, dict(app.tasks), app.robot_state, app.state,
                          app._task_sequence, app._batch_sequence, app.active_task), before)
        self.assertEqual(app.robot_state, RobotStateSnapshot())
        for record in app.tasks.values():
            self.assertEqual(record.handoff, TaskHandoffState.NOT_SENT)
            self.assertEqual(record.documentLoad, DocumentLoadState.UNCONFIRMED)
        app.parser.parse.assert_not_called()
        with self.assertRaises(FrozenInstanceError):
            first[0].previewPosition = 99

    def test_batch_cli_shows_original_preview_and_actual_provenance_without_provider(self):
        app, _ = self.ready(simulation=True)
        app.process_text("/sim document-loaded task_001")
        app.process_text("/sim task-queued task_001")
        app.parser.reset_mock()
        app.response_generator = Mock()
        text = app.process_text("/batch batch_001").responseText
        original = text.split("[ORIGINAL REQUEST ORDER]", 1)[1].split("TASK 1", 1)[0]
        preview = text.split("[ORDER PREVIEW — NOT EXECUTED]", 1)[1].split("[EXECUTION STATUS]", 1)[0]
        self.assertLess(original.index("hợp đồng"), original.index("hồ sơ"))
        self.assertLess(preview.index("hồ sơ"), preview.index("hợp đồng"))
        self.assertIn("sequenceIndex=2", preview)
        self.assertIn("taskId=task_003", preview)
        self.assertIn("Lifecycle      : QUEUED", text)
        self.assertIn("Load Source    : SIMULATED", text)
        self.assertIn("Scheduler      : NOT_IMPLEMENTED", text)
        self.assertIn("Robot Accepted : NO", text)
        self.assertIn("Physical Delivery: NOT_STARTED", text)
        app.parser.parse.assert_not_called()
        app.response_generator.generate.assert_not_called()

    def test_response_plan_uses_runtime_task_ids_and_same_preview_as_monitor(self):
        app, turn = self.ready()
        expected = [{**asdict(item), "priority": item.priority.value} for item in self.preview(app)]
        self.assertEqual(turn.responsePlan.facts["orderPreview"], expected)
        self.assertEqual(turn.responsePlan.facts["taskIds"], ["task_001", "task_002", "task_003"])
        self.assertEqual([task["sequenceIndex"] for task in turn.responsePlan.facts["tasks"]], [0, 1, 2])
        self.assertFalse(turn.responsePlan.facts["orderPolicySelectedByUser"])

    def test_optional_nlg_cannot_replace_preview_or_claim_physical_execution(self):
        app = self.app()
        app.response_generator = Mock()
        app.response_generator.generate.return_value = (
            "Robot đã tiếp nhận, đã nạp tài liệu và đang di chuyển. Đã giao thành công."
        )
        turn = app.process_text("request")
        app.response_generator.generate.assert_not_called()
        for claim in ("đã tiếp nhận", "đã nạp", "đang di chuyển", "đã giao", "chắc chắn", "đủ sức"):
            self.assertNotIn(claim, turn.responseText.casefold())
        self.assertIn("Thứ tự dự kiến", turn.responseText)
        self.assertIn("điều kiện vận hành", turn.responseText)
        self.assertFalse(turn.responsePlan.facts["robotAccepted"])
        self.assertTrue(all(record.lifecycle.state is TaskLifecycleState.READY for record in app.tasks.values()))

    def test_console_after_choice_prints_preview_without_another_confirmation(self):
        app = self.app({"conflict": conflict(), "theo mức ưu tiên": {"type": "BATCH_ORDER", "orderPolicy": "PRIORITY"}})
        output = io.StringIO()
        with patch.dict(os.environ, {"DEBUG_CONVERSATION": "true"}), \
                patch("builtins.input", side_effect=["conflict", "theo mức ưu tiên", "exit"]), redirect_stdout(output):
            run_console(app)
        text = output.getvalue()
        self.assertIn("DELIVERY BATCH MONITOR", text)
        self.assertIn("[ORIGINAL REQUEST ORDER]", text)
        self.assertIn("[ORDER PREVIEW — NOT EXECUTED]", text)
        self.assertIn("Đã chọn giao theo mức ưu tiên.", text)
        self.assertNotIn("Bạn có đồng ý", text)
        self.assertEqual((len(app.tasks), len(app.batches)), (2, 1))

    def test_ordinary_priority_false_confirmation_flag_does_not_add_an_extra_step(self):
        app, turn = self.ready()
        self.assertFalse(turn.output.draft.orderConfirmed)
        self.assertEqual((len(app.tasks), len(app.batches)), (3, 1))
        self.assertEqual(app.state.mode, ConversationMode.IDLE)
        self.assertIsNone(app.state.pendingBatch)
        self.assertNotIn("?", turn.responseText)

    def test_single_task_pipeline_and_optional_nlg_remain_unchanged(self):
        payload = {"type": "TASK_COMMAND", "parsedIntent": {
            "intent": "DELIVER_DOCUMENT", "destinationText": "phòng 2", "documentText": "tài liệu",
            "priority": "NORMAL", "constraints": {"ifBusy": None, "ifClosed": None}, "missingFields": [],
        }}
        app = self.app({"single": payload})
        app.response_generator = Mock()
        app.response_generator.generate.return_value = "Mình đã ghi nhận nhiệm vụ."
        turn = app.process_text("single")
        self.assertIsInstance(turn.output, TaskReady)
        self.assertNotIn("orderPreview", turn.responsePlan.facts)
        app.response_generator.generate.assert_called_once()
        self.assertEqual(turn.responseText, "Mình đã ghi nhận nhiệm vụ.")
        self.assertEqual(len(app.batches), 0)

    def test_preview_only_includes_members_and_cannot_create_missing_records(self):
        app, _ = self.ready()
        app.process_text("request")
        first_batch = app.batches["batch_001"]
        preview = build_order_preview(first_batch, app.tasks)
        self.assertEqual({item.taskId for item in preview}, {"task_001", "task_002", "task_003"})
        incomplete = dict(app.tasks)
        incomplete.pop("task_003")
        before = copy.deepcopy(incomplete)
        with self.assertRaises(KeyError):
            build_order_preview(first_batch, incomplete)
        self.assertEqual(incomplete, before)


if __name__ == "__main__":
    unittest.main()
