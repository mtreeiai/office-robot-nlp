import json
import unittest
from dataclasses import replace
from pathlib import Path

from office_robot_nlp.clu import FakeLanguageUnderstandingParser, RobotDecisionResult
from office_robot_nlp.demo_catalog import demo_map_catalog
from office_robot_nlp.dialogue_manager import (
    ClarificationRequired,
    DecisionReady,
    DialogueError,
    TaskReady,
    UnsupportedRequest,
)
from office_robot_nlp.domain.conversation import ConversationMode
from office_robot_nlp.domain.intent import Action
from office_robot_nlp.domain.map import MapCatalog, MapLocation
from office_robot_nlp.gemini_adapter import GeminiAdapterError
from text_conversation import ProviderFailure, TextConversationApplication, render_result


FIXTURES = Path(__file__).parent / "fixtures"


def payload(destination="phòng A", priority="NORMAL", if_busy=None):
    return {
        "type": "TASK_COMMAND",
        "parsedIntent": {
            "intent": "DELIVER_DOCUMENT",
            "destinationText": destination,
            "documentText": "tài liệu này",
            "priority": priority,
            "constraints": {"ifBusy": if_busy, "ifClosed": None},
            "missingFields": ["destination"] if destination is None else [],
        },
    }


def catalog_from_fixture(name: str) -> MapCatalog:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return MapCatalog(raw["version"], [MapLocation(**location) for location in raw["locations"]])


def application(responses, catalog=None):
    return TextConversationApplication(
        FakeLanguageUnderstandingParser(responses),
        catalog or demo_map_catalog(),
    )


class FailingParser:
    def parse(self, text, context):
        raise GeminiAdapterError("provider unavailable")


class InvalidDecisionParser:
    def parse(self, text, context):
        return RobotDecisionResult(Action.RETRY)


class TextConversationIntegrationTests(unittest.TestCase):
    def test_complete_task_in_one_turn(self):
        app = application({"Đưa tài liệu tới phòng A.": payload("phòng A")})
        turn = app.process_text("Đưa tài liệu tới phòng A.")
        self.assertIsInstance(turn.output, TaskReady)
        self.assertEqual(turn.output.resolvedIntent.destination.locationId, "meeting_room_A")
        self.assertEqual(turn.state.mode, ConversationMode.IDLE)

    def test_missing_destination_completes_across_two_turns(self):
        app = application({
            "Giao tài liệu này.": payload(None),
            "Phòng A.": {"type": "CLARIFICATION_ANSWER", "field": "destination", "value": "Phòng A"},
        })
        first = app.process_text("Giao tài liệu này.")
        self.assertIsInstance(first.output, ClarificationRequired)
        self.assertEqual(first.state.mode, ConversationMode.AWAITING_TASK_INFORMATION)
        second = app.process_text("Phòng A.")
        self.assertIsInstance(second.output, TaskReady)
        self.assertEqual(second.output.resolvedIntent.destination.locationId, "meeting_room_A")
        self.assertEqual(second.state.mode, ConversationMode.IDLE)

    def test_priority_and_busy_policy_survive_clarification(self):
        app = application({
            "Chuyển gấp tài liệu này.": payload(None, "HIGH", "WAIT"),
            "Phòng B.": {"type": "CLARIFICATION_ANSWER", "field": "destination", "value": "Phòng B"},
        })
        self.assertIsInstance(app.process_text("Chuyển gấp tài liệu này.").output, ClarificationRequired)
        turn = app.process_text("Phòng B.")
        self.assertIsInstance(turn.output, TaskReady)
        self.assertEqual(turn.output.resolvedIntent.priority.value, "HIGH")
        self.assertEqual(turn.output.resolvedIntent.constraints.ifBusy, Action.WAIT)

    def test_unknown_and_ambiguous_locations_do_not_become_task_ready(self):
        unknown_app = application({"Đưa tài liệu tới phòng Z.": payload("phòng Z")})
        unknown = unknown_app.process_text("Đưa tài liệu tới phòng Z.")
        self.assertIsInstance(unknown.output, ClarificationRequired)
        self.assertNotIsInstance(unknown.output, TaskReady)

        ambiguous_app = application(
            {"Đưa tài liệu tới phòng chung.": payload("phòng chung")},
            catalog_from_fixture("ambiguous_map_catalog.json"),
        )
        ambiguous = ambiguous_app.process_text("Đưa tài liệu tới phòng chung.")
        self.assertIsInstance(ambiguous.output, ClarificationRequired)
        self.assertEqual(ambiguous.output.candidates,
                         ("meeting_room_A", "meeting_room_B"))

    def test_unsupported_request_has_no_task(self):
        app = application({"Mở Youtube cho tôi.": {"type": "UNKNOWN", "reason": "unsupported"}})
        turn = app.process_text("Mở Youtube cho tôi.")
        self.assertIsInstance(turn.output, UnsupportedRequest)
        self.assertEqual(turn.state.mode, ConversationMode.IDLE)

    def test_fragment_is_context_sensitive(self):
        responses = {
            "Phòng A.": {"type": "CLARIFICATION_ANSWER", "field": "destination", "value": "Phòng A"},
        }
        idle_app = application(responses)
        idle = idle_app.process_text("Phòng A.")
        self.assertIsInstance(idle.output, UnsupportedRequest)

        pending_app = application({
            "Giao tài liệu này.": payload(None),
            **responses,
        })
        pending_app.process_text("Giao tài liệu này.")
        completed = pending_app.process_text("Phòng A.")
        self.assertIsInstance(completed.output, TaskReady)

    def test_wait_and_cancel_decisions(self):
        responses = {
            "Chờ đi.": {"type": "ROBOT_DECISION", "decision": "WAIT"},
            "Không cần giao nữa.": {"type": "ROBOT_DECISION", "decision": "CANCEL"},
        }
        for text, decision in (("Chờ đi.", Action.WAIT), ("Không cần giao nữa.", Action.CANCEL)):
            with self.subTest(text=text):
                app = application(responses)
                app.state = replace(
                    app.state,
                    mode=ConversationMode.AWAITING_ROBOT_DECISION,
                    activeTaskId="task_001",
                    allowedDecisions=[Action.WAIT, Action.CANCEL],
                )
                turn = app.process_text(text)
                self.assertEqual(turn.output, DecisionReady("task_001", decision))
                self.assertEqual(turn.state.mode, ConversationMode.IDLE)

    def test_disallowed_decision_is_rejected(self):
        app = TextConversationApplication(InvalidDecisionParser(), demo_map_catalog())
        app.state = replace(
            app.state,
            mode=ConversationMode.AWAITING_ROBOT_DECISION,
            activeTaskId="task_001",
            allowedDecisions=[Action.WAIT, Action.CANCEL],
        )
        turn = app.process_text("Thử lại.")
        self.assertIsInstance(turn.output, DialogueError)
        self.assertEqual(turn.state.mode, ConversationMode.AWAITING_ROBOT_DECISION)

    def test_provider_failure_preserves_pending_state(self):
        app = TextConversationApplication(FakeLanguageUnderstandingParser({
            "Giao tài liệu này.": payload(None),
        }), demo_map_catalog())
        first = app.process_text("Giao tài liệu này.")
        pending_state = first.state
        app.parser = FailingParser()
        failed = app.process_text("Phòng A.")
        self.assertIsInstance(failed.output, ProviderFailure)
        self.assertEqual(failed.state, pending_state)
        self.assertEqual(app.state.mode, ConversationMode.AWAITING_TASK_INFORMATION)

    def test_rendering_uses_existing_result_fields(self):
        app = application({"Đưa tài liệu tới phòng A.": payload("phòng A")})
        output = render_result(app.process_text("Đưa tài liệu tới phòng A.").output)
        self.assertIn("Task ready:", output)
        self.assertIn("meeting_room_A", output)
        self.assertNotIn("coordinates", output.casefold())


if __name__ == "__main__":
    unittest.main()