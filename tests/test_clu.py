import json
import unittest
from pathlib import Path

from office_robot_nlp.clu import (
    ClarificationAnswerResult,
    FakeLanguageUnderstandingParser,
    RobotDecisionResult,
    TaskCommandResult,
    UnknownUtteranceResult,
    decode_language_understanding_result,
)
from office_robot_nlp.domain.conversation import ConversationMode, ConversationState
from office_robot_nlp.domain.intent import Action, IntentType, Priority
from office_robot_nlp.domain.map import MapCatalog, MapLocation
from office_robot_nlp.entity_resolver import resolve_destination
from office_robot_nlp.validation.intent_validator import ValidationError, validate_intent


FIXTURES = Path(__file__).parent / "fixtures"


def map_catalog() -> MapCatalog:
    raw = json.loads((FIXTURES / "map_catalog.json").read_text(encoding="utf-8"))
    return MapCatalog(raw["version"], [MapLocation(**location) for location in raw["locations"]])


def task_command_payload(
    destination: str | None = "phòng A",
    document: str | None = "tài liệu",
    priority: str = "NORMAL",
    if_busy: str | None = None,
) -> dict[str, object]:
    return {
        "type": "TASK_COMMAND",
        "parsedIntent": {
            "intent": "DELIVER_DOCUMENT",
            "destinationText": destination,
            "documentText": document,
            "priority": priority,
            "constraints": {"ifBusy": if_busy, "ifClosed": None},
            "missingFields": ["destination"] if destination is None else [],
        },
    }


def idle_context() -> ConversationState:
    return ConversationState("conv_001", ConversationMode.IDLE, None, None, None, [])


class ConversationalLanguageUnderstandingTests(unittest.TestCase):
    def test_task_command_reuses_parsed_intent_decoder(self):
        payload = {
            "type": "TASK_COMMAND",
            "parsedIntent": {
                "intent": "DELIVER_DOCUMENT",
                "destinationText": "phòng A",
                "documentText": "tài liệu",
                "priority": "NORMAL",
                "constraints": {"ifBusy": None, "ifClosed": None},
                "missingFields": [],
            },
        }
        result = decode_language_understanding_result(payload, idle_context())
        self.assertIsInstance(result.result, TaskCommandResult)
        self.assertEqual(result.result.parsedIntent.intent, IntentType.DELIVER_DOCUMENT)
        self.assertEqual(result.result.parsedIntent.priority, Priority.NORMAL)

    def test_task_command_high_priority_fixture(self):
        result = decode_language_understanding_result(
            task_command_payload("phòng B", "hồ sơ này", "HIGH"), idle_context()
        )
        self.assertIsInstance(result.result, TaskCommandResult)
        self.assertEqual(result.result.type, "TASK_COMMAND")
        self.assertEqual(result.result.parsedIntent.intent, IntentType.DELIVER_DOCUMENT)
        self.assertEqual(result.result.parsedIntent.destinationText, "phòng B")
        self.assertEqual(result.result.parsedIntent.priority, Priority.HIGH)

    def test_task_command_busy_constraint_fixture(self):
        result = decode_language_understanding_result(
            task_command_payload(if_busy="WAIT"), idle_context()
        )
        self.assertIsInstance(result.result, TaskCommandResult)
        self.assertEqual(result.result.parsedIntent.constraints.ifBusy, Action.WAIT)

    def test_missing_destination_is_preserved_without_invention(self):
        result = decode_language_understanding_result(
            task_command_payload(destination=None), idle_context()
        )
        self.assertIsInstance(result.result, TaskCommandResult)
        self.assertIsNone(result.result.parsedIntent.destinationText)
        self.assertIn("destination", result.result.parsedIntent.missingFields)

    def test_task_command_passes_existing_validator_boundary(self):
        result = decode_language_understanding_result(
            task_command_payload("phòng B"), idle_context()
        )
        parsed_intent = result.result.parsedIntent
        catalog = map_catalog()
        resolution = resolve_destination(parsed_intent.destinationText, catalog)
        validation = validate_intent(parsed_intent, resolution, catalog)
        self.assertTrue(validation.valid)

        malformed = decode_language_understanding_result(
            task_command_payload(priority="URGENT"), idle_context()
        )
        self.assertIsNone(malformed.result)
        self.assertEqual(malformed.errors, (ValidationError.INVALID_PRIORITY.value,))

    def test_clarification_requires_pending_destination(self):
        context = ConversationState(
            "conv_001", ConversationMode.AWAITING_TASK_INFORMATION, None, "destination", None, []
        )
        result = decode_language_understanding_result(
            {"type": "CLARIFICATION_ANSWER", "field": "destination", "value": "Phòng A"},
            context,
        )
        self.assertEqual(result.result, ClarificationAnswerResult("destination", "Phòng A"))

        self.assertIsNone(
            decode_language_understanding_result(
                {"type": "CLARIFICATION_ANSWER", "field": "destination", "value": "Phòng A"},
                idle_context(),
            ).result
        )

    def test_robot_decision_requires_allowed_option(self):
        context = ConversationState(
            "conv_001", ConversationMode.AWAITING_ROBOT_DECISION, None, None,
            "task_001", [Action.WAIT, Action.CANCEL],
        )
        result = decode_language_understanding_result(
            {"type": "ROBOT_DECISION", "decision": "WAIT"}, context
        )
        self.assertEqual(result.result, RobotDecisionResult(Action.WAIT))

        rejected = decode_language_understanding_result(
            {"type": "ROBOT_DECISION", "decision": "RETRY"}, context
        )
        self.assertIsNone(rejected.result)
        self.assertEqual(rejected.errors, ("ROBOT_DECISION_NOT_ALLOWED",))

    def test_valid_cancel_robot_decision(self):
        context = ConversationState(
            "conv_001", ConversationMode.AWAITING_ROBOT_DECISION, None, None,
            "task_001", [Action.WAIT, Action.CANCEL],
        )
        result = decode_language_understanding_result(
            {"type": "ROBOT_DECISION", "decision": "CANCEL"}, context
        )
        self.assertEqual(result.result, RobotDecisionResult(Action.CANCEL))

    def test_robot_decision_is_rejected_in_idle_context(self):
        result = decode_language_understanding_result(
            {"type": "ROBOT_DECISION", "decision": "WAIT"}, idle_context()
        )
        self.assertIsNone(result.result)
        self.assertEqual(result.errors, ("ROBOT_DECISION_NOT_EXPECTED",))

    def test_clarification_is_rejected_in_idle_context(self):
        result = decode_language_understanding_result(
            {"type": "CLARIFICATION_ANSWER", "field": "destination", "value": "Phòng A"},
            idle_context(),
        )
        self.assertIsNone(result.result)
        self.assertEqual(result.errors, ("CLARIFICATION_NOT_EXPECTED",))

    def test_clarification_field_mismatch_is_rejected(self):
        context = ConversationState(
            "conv_001", ConversationMode.AWAITING_TASK_INFORMATION, None, "destination", None, []
        )
        result = decode_language_understanding_result(
            {"type": "CLARIFICATION_ANSWER", "field": "priority", "value": "HIGH"}, context
        )
        self.assertIsNone(result.result)
        self.assertEqual(result.errors, ("INVALID_CLARIFICATION_FIELD",))

    def test_unknown_utterance_emits_no_task_intent(self):
        result = decode_language_understanding_result(
            {"type": "UNKNOWN", "reason": "unsupported request"}, idle_context()
        )
        self.assertIsInstance(result.result, UnknownUtteranceResult)
        self.assertEqual(result.result.type, "UNKNOWN")
        self.assertFalse(hasattr(result.result, "parsedIntent"))

    def test_context_dependent_short_answers_are_unknown_when_idle(self):
        for text in ("Phòng A.", "Chờ đi.", "Ừ.", "Không.", "Mở Youtube."):
            parser = FakeLanguageUnderstandingParser({text: {"type": "UNKNOWN"}})
            self.assertIsInstance(parser.parse(text, idle_context()), UnknownUtteranceResult)

    def test_fake_parser_decodes_json_responses(self):
        text = "Phòng A."
        context = ConversationState(
            "conv_001", ConversationMode.AWAITING_TASK_INFORMATION, None, "destination", None, []
        )
        parser = FakeLanguageUnderstandingParser({
            text: json.dumps({"type": "CLARIFICATION_ANSWER", "field": "destination", "value": text})
        })
        self.assertEqual(parser.parse(text, context).value, text)


if __name__ == "__main__":
    unittest.main()