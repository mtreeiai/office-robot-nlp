import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from office_robot_nlp.clu import FakeLanguageUnderstandingParser
from office_robot_nlp.demo_catalog import demo_map_catalog
from office_robot_nlp.dialogue_manager import (
    ClarificationRequired, SocialAcknowledged, TaskReady, TaskUpdateAccepted, UnsupportedRequest,
)
from office_robot_nlp.domain.conversation import ConversationMode
from office_robot_nlp.domain.intent import Action, Priority
from office_robot_nlp.response_plan import ResponseAct
from office_robot_nlp.semantic_map import SemanticMapConfigurationError
from office_robot_nlp.task_lifecycle import TaskLifecycleState
from text_conversation import create_application, main, run_console


def command(destination=None, recipient="anh Phong", priority="HIGH"):
    return {
        "type": "TASK_COMMAND",
        "parsedIntent": {
            "intent": "DELIVER_DOCUMENT", "destinationText": destination,
            "documentText": "tài liệu này", "recipientText": recipient,
            "priority": priority, "constraints": {"ifBusy": "WAIT", "ifClosed": "RETRY"},
            "missingFields": ["destination"] if destination is None else [],
        },
    }


class Phase8AIntegrationTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {
            "NLG_ENABLED": "false", "DEBUG_CONVERSATION": "false",
            "ROOM_REGISTRY_PATH": "data/office_rooms.json",
            "ROOM_ALIASES_PATH": "data/office_room_aliases.json",
        })
        environment.start()
        self.addCleanup(environment.stop)

    def app(self, responses):
        return create_application(parser=FakeLanguageUnderstandingParser(responses))

    def assert_preserved(self, resolved):
        self.assertEqual(resolved.document.originalText, "tài liệu này")
        self.assertEqual(resolved.recipientText, "anh Phong")
        self.assertEqual(resolved.priority, Priority.HIGH)
        self.assertEqual(resolved.constraints.ifBusy, Action.WAIT)
        self.assertEqual(resolved.constraints.ifClosed, Action.RETRY)

    def test_room_three_task_ready_and_automation_lookup(self):
        app = self.app({"Giao tài liệu tới phòng 3": command("phòng 3")})
        turn = app.process_text("Giao tài liệu tới phòng 3")
        self.assertIsInstance(turn.output, TaskReady)
        self.assertEqual(turn.output.resolvedIntent.destination.locationId, "03")
        self.assert_preserved(turn.output.resolvedIntent)
        self.assertEqual(app.active_task.state, TaskLifecycleState.READY)
        self.assertEqual(turn.responsePlan.act, ResponseAct.TASK_RECORDED)
        self.assertEqual(turn.responsePlan.facts["lifecycleState"], "READY")
        self.assertIn("ghi nhận", turn.responseText)
        for claim in ("bắt đầu", "đang di chuyển", "đã tới", "đã giao thành công"):
            self.assertNotIn(claim, turn.responseText)
        pose = app.room_registry.get_staging_pose("03", "main_door")
        self.assertEqual((pose.x, pose.y, pose.yaw, pose.frame_id), (13.6, 0.65, -2.570, "map"))

    def test_missing_destination_then_room_five_preserves_all_fields(self):
        app = self.app({
            "Giao tài liệu này.": command(),
            "Phòng 5.": {"type": "CLARIFICATION_ANSWER", "field": "destination", "value": "Phòng 5"},
        })
        first = app.process_text("Giao tài liệu này.")
        self.assertIsInstance(first.output, ClarificationRequired)
        self.assertIsNone(app.active_task)
        second = app.process_text("Phòng 5.")
        self.assertIsInstance(second.output, TaskReady)
        self.assertEqual(second.output.resolvedIntent.destination.locationId, "05")
        self.assert_preserved(second.output.resolvedIntent)
        self.assertEqual(app.active_task.state, TaskLifecycleState.READY)

    def test_unknown_room_preserves_pending_fields_until_valid_clarification(self):
        app = self.app({
            "Giao tới phòng 8": command("phòng 8"),
            "Phòng 5": {"type": "CLARIFICATION_ANSWER", "field": "destination", "value": "Phòng 5"},
        })
        turn = app.process_text("Giao tới phòng 8")
        self.assertIsInstance(turn.output, ClarificationRequired)
        self.assertEqual(turn.responsePlan.act, ResponseAct.UNKNOWN_LOCATION)
        self.assertIn("danh sách địa điểm hiện tại", turn.responseText)
        self.assertNotIn("không tồn tại", turn.responseText)
        self.assertNotIn("di chuyển", turn.responseText)
        self.assertIsNone(app.active_task)
        self.assertEqual(app._task_sequence, 0)
        self.assertEqual(turn.state.mode, ConversationMode.AWAITING_TASK_INFORMATION)
        pending = turn.state.pendingIntent
        self.assertEqual(pending.documentText, "tài liệu này")
        self.assertEqual(pending.recipientText, "anh Phong")
        self.assertEqual(pending.priority, Priority.HIGH)
        self.assertEqual(pending.constraints.ifBusy, Action.WAIT)
        self.assertEqual(pending.constraints.ifClosed, Action.RETRY)
        ready = app.process_text("Phòng 5")
        self.assertEqual(ready.output.resolvedIntent.destination.locationId, "05")
        self.assert_preserved(ready.output.resolvedIntent)

    def test_unknown_destination_update_retains_pending_task(self):
        app = self.app({
            "Giao tài liệu": command(),
            "Phòng 10": {"type": "TASK_UPDATE", "destinationText": "phòng 10"},
        })
        app.process_text("Giao tài liệu")
        turn = app.process_text("Phòng 10")
        self.assertIsInstance(turn.output, ClarificationRequired)
        self.assertEqual(turn.state.pendingIntent.destinationText, "phòng 10")
        self.assertEqual(turn.state.pendingIntent.recipientText, "anh Phong")
        self.assertEqual(turn.state.pendingIntent.priority, Priority.HIGH)
        self.assertIsNone(app.active_task)

    def test_urgent_recipient_and_room_two(self):
        text = "Giao gấp tài liệu cho anh Phong ở phòng 2."
        app = self.app({text: command("phòng 2")})
        turn = app.process_text(text)
        self.assertEqual(turn.output.resolvedIntent.destination.locationId, "02")
        self.assert_preserved(turn.output.resolvedIntent)
        self.assertIsNone(turn.output.resolvedIntent.document.documentId)
        self.assertNotIn("xác nhận", turn.responseText)

    def test_recipient_only_never_supplies_destination(self):
        app = self.app({"Giao tài liệu cho anh Phong": command()})
        turn = app.process_text("Giao tài liệu cho anh Phong")
        self.assertIsInstance(turn.output, ClarificationRequired)
        self.assertIsNone(turn.state.pendingIntent.destinationText)
        self.assertEqual(turn.state.pendingIntent.recipientText, "anh Phong")
        self.assertIsNone(app.active_task)

    def test_social_and_task_updates_work_with_real_registry(self):
        app = self.app({
            "Chào": {"type": "SOCIAL"},
            "Giao tài liệu": command(recipient=None, priority="NORMAL"),
            "Cho anh Phong": {"type": "TASK_UPDATE", "recipientText": "anh Phong"},
            "Gấp nhé": {"type": "TASK_UPDATE", "priority": "HIGH"},
            "Tới phòng 2": {"type": "TASK_UPDATE", "destinationText": "phòng 2"},
        })
        self.assertIsInstance(app.process_text("Chào").output, SocialAcknowledged)
        app.process_text("Giao tài liệu")
        pending = app.state
        self.assertIsInstance(app.process_text("Chào").output, SocialAcknowledged)
        self.assertEqual(app.state, pending)
        self.assertIsInstance(app.process_text("Cho anh Phong").output, TaskUpdateAccepted)
        self.assertIsNone(app.state.pendingIntent.destinationText)
        self.assertIsInstance(app.process_text("Gấp nhé").output, TaskUpdateAccepted)
        turn = app.process_text("Tới phòng 2")
        self.assertIsInstance(turn.output, TaskReady)
        self.assert_preserved(turn.output.resolvedIntent)
        self.assertEqual(turn.output.resolvedIntent.destination.locationId, "02")
        self.assertEqual(app.active_task.state, TaskLifecycleState.READY)

    def test_multiple_destinations_follow_existing_unknown_clu_flow(self):
        text = "Giao tài liệu tới phòng 1 và phòng 2."
        app = self.app({text: {"type": "UNKNOWN", "reason": "multiple destinations unsupported"}})
        self.assertIsInstance(app.process_text(text).output, UnsupportedRequest)
        self.assertIsNone(app.active_task)

    def test_explicit_room_list_cannot_be_truncated_by_parser(self):
        text = "Giao tài liệu tới phòng 1 và phòng 2."
        app = self.app({text: command("phòng 1")})
        turn = app.process_text(text)
        self.assertIsInstance(turn.output, UnsupportedRequest)
        self.assertIsNone(app.active_task)
        self.assertEqual(app._task_sequence, 0)

    def test_multiple_destination_clarification_preserves_pending_task(self):
        app = self.app({
            "Giao tài liệu": command(),
            "Phòng 1 và phòng 2": {"type": "CLARIFICATION_ANSWER", "field": "destination", "value": "Phòng 1"},
        })
        app.process_text("Giao tài liệu")
        pending = app.state
        turn = app.process_text("Phòng 1 và phòng 2")
        self.assertIsInstance(turn.output, ClarificationRequired)
        self.assertEqual(app.state, pending)
        self.assertIsNone(app.active_task)

    def test_default_runtime_catalog_has_no_fictional_rooms(self):
        app = self.app({"Giao tới phòng A": command("phòng A")})
        self.assertEqual([location.id for location in app.map_catalog.locations],
                         [f"{number:02d}" for number in range(1, 8)])
        self.assertIsNotNone(app.room_registry)
        self.assertIsInstance(app.process_text("Giao tới phòng A").output, ClarificationRequired)
        self.assertIsNone(app.active_task)

    def test_registry_loading_and_listing_never_construct_or_call_provider(self):
        with patch("text_conversation.build_runtime") as build, \
                patch("text_conversation.GeminiNaturalResponseGenerator") as nlg, \
                patch("urllib.request.urlopen", side_effect=AssertionError("Network not permitted")), \
                patch.dict(os.environ, {"NLG_ENABLED": "true"}):
            output = io.StringIO()
            with redirect_stdout(output):
                main(["--list-rooms"])
            build.assert_not_called()
            nlg.assert_not_called()
            self.assertIn("office_amcl", output.getvalue())
            self.assertIn("01: Phòng 1", output.getvalue())
            self.assertIn("07: Phòng 7", output.getvalue())
            self.assertNotIn("meeting_room", output.getvalue())

    def test_invalid_registry_fails_before_provider_setup(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"ROOM_REGISTRY_PATH": str(Path(directory) / "missing.json")}), \
                patch("text_conversation.build_runtime") as build:
            with self.assertRaisesRegex(SemanticMapConfigurationError, "Cannot load.*missing.json"):
                create_application()
            output = io.StringIO()
            with redirect_stdout(output):
                main([])
            self.assertIn("Setup error:", output.getvalue())
            self.assertIn("missing.json", output.getvalue())
            build.assert_not_called()

    def test_repository_relative_paths_work_from_other_directory(self):
        previous = Path.cwd()
        try:
            with tempfile.TemporaryDirectory() as directory:
                os.chdir(directory)
                app = self.app({})
                self.assertEqual(len(app.room_registry.rooms), 7)
                os.chdir(previous)
        finally:
            os.chdir(previous)

    def test_explicit_legacy_fixture_is_still_supported(self):
        app = create_application(parser=FakeLanguageUnderstandingParser({}), map_catalog=demo_map_catalog())
        self.assertIsNone(app.room_registry)
        self.assertEqual(app.map_catalog.locations[0].id, "meeting_room_A")
        with self.assertRaisesRegex(ValueError, "either map_catalog or registry paths"):
            create_application(parser=FakeLanguageUnderstandingParser({}), map_catalog=demo_map_catalog(),
                               registry_path="data/office_rooms.json")

    def test_debug_console_shows_official_id_and_ready_state(self):
        text = "Giao tới phòng 3"
        app = self.app({text: command("phòng 3")})
        output = io.StringIO()
        with patch("builtins.input", side_effect=[text, "exit"]), \
                patch.dict(os.environ, {"DEBUG_CONVERSATION": "true"}), redirect_stdout(output):
            run_console(app)
        self.assertIn("destinationId: 03", output.getvalue())
        self.assertIn("destinationText: phòng 3", output.getvalue())
        self.assertIn("TaskLifecycleState: READY", output.getvalue())
        self.assertNotIn("meeting_room_A", output.getvalue())


if __name__ == "__main__":
    unittest.main()
