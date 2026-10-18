import json
import unittest
from dataclasses import replace
from pathlib import Path

from office_robot_nlp.clu import (
    ClarificationAnswerResult,
    RobotDecisionResult,
    TaskCommandResult,
    UnknownUtteranceResult,
)
from office_robot_nlp.dialogue_manager import (
    ClarificationRequired,
    DecisionReady,
    DialogueError,
    DialogueErrorCode,
    DialogueManager,
    TaskReady,
    UnsupportedRequest,
)
from office_robot_nlp.domain.conversation import ConversationMode, ConversationState
from office_robot_nlp.domain.intent import Action, Constraints, IntentType, ParsedIntent, Priority
from office_robot_nlp.domain.map import MapCatalog, MapLocation


FIXTURES = Path(__file__).parent / "fixtures"


def map_catalog() -> MapCatalog:
    raw = json.loads((FIXTURES / "map_catalog.json").read_text(encoding="utf-8"))
    return MapCatalog(raw["version"], [MapLocation(**location) for location in raw["locations"]])


def idle_state() -> ConversationState:
    return ConversationState("conv_001", ConversationMode.IDLE, None, None, None, [])


def intent(destination: str | None, priority: Priority = Priority.NORMAL,
           if_busy: Action | None = None) -> ParsedIntent:
    return ParsedIntent(
        IntentType.DELIVER_DOCUMENT,
        destination,
        "tài liệu này",
        priority,
        Constraints(if_busy, None),
        ["destination"] if destination is None else [],
    )


class DialogueManagerTests(unittest.TestCase):
    def setUp(self):
        self.manager = DialogueManager()
        self.catalog = map_catalog()

    def test_complete_command_becomes_task_ready(self):
        handled = self.manager.handle(
            idle_state(), TaskCommandResult(intent("phòng A")), self.catalog
        )
        self.assertIsInstance(handled.result, TaskReady)
        self.assertEqual(handled.result.resolvedIntent.destination.locationId, "meeting_room_A")
        self.assertEqual(handled.state.mode, ConversationMode.IDLE)

    def test_missing_destination_stores_pending_intent(self):
        pending = intent(None)
        handled = self.manager.handle(idle_state(), TaskCommandResult(pending), self.catalog)
        self.assertIsInstance(handled.result, ClarificationRequired)
        self.assertEqual(handled.state.mode, ConversationMode.AWAITING_TASK_INFORMATION)
        self.assertEqual(handled.state.awaitingField, "destination")
        self.assertEqual(handled.state.pendingIntent, pending)

    def test_clarification_completes_pending_destination(self):
        state = ConversationState(
            "conv_001", ConversationMode.AWAITING_TASK_INFORMATION,
            intent(None), "destination", None, [],
        )
        handled = self.manager.handle(
            state, ClarificationAnswerResult("destination", "Phòng A"), self.catalog
        )
        self.assertIsInstance(handled.result, TaskReady)
        self.assertEqual(handled.result.resolvedIntent.destination.locationId, "meeting_room_A")
        self.assertEqual(handled.state.mode, ConversationMode.IDLE)
        self.assertIsNone(handled.state.pendingIntent)
        self.assertIsNone(handled.state.awaitingField)

    def test_clarification_preserves_priority_and_busy_policy(self):
        pending = intent(None, Priority.HIGH, Action.WAIT)
        state = ConversationState(
            "conv_001", ConversationMode.AWAITING_TASK_INFORMATION,
            pending, "destination", None, [],
        )
        handled = self.manager.handle(
            state, ClarificationAnswerResult("destination", "Phòng A"), self.catalog
        )
        self.assertIsInstance(handled.result, TaskReady)
        resolved = handled.result.resolvedIntent
        self.assertEqual(resolved.priority, Priority.HIGH)
        self.assertEqual(resolved.constraints.ifBusy, Action.WAIT)

    def test_unknown_destination_preserves_pending_task(self):
        pending = intent(None)
        state = ConversationState(
            "conv_001", ConversationMode.AWAITING_TASK_INFORMATION,
            pending, "destination", None, [],
        )
        handled = self.manager.handle(
            state, ClarificationAnswerResult("destination", "Phòng Z"), self.catalog
        )
        self.assertIsInstance(handled.result, ClarificationRequired)
        self.assertEqual(handled.state.mode, ConversationMode.AWAITING_TASK_INFORMATION)
        self.assertEqual(handled.state.awaitingField, "destination")
        self.assertIsNotNone(handled.state.pendingIntent)
        self.assertEqual(handled.state.pendingIntent.destinationText, "Phòng Z")

    def test_ambiguous_destination_does_not_choose_candidate(self):
        raw = json.loads((FIXTURES / "ambiguous_map_catalog.json").read_text(encoding="utf-8"))
        ambiguous = MapCatalog(raw["version"], [MapLocation(**location) for location in raw["locations"]])
        state = ConversationState(
            "conv_001", ConversationMode.AWAITING_TASK_INFORMATION,
            intent(None), "destination", None, [],
        )
        handled = self.manager.handle(
            state, ClarificationAnswerResult("destination", "Phòng chung"), ambiguous
        )
        self.assertIsInstance(handled.result, ClarificationRequired)
        self.assertEqual(handled.result.candidates,
                         ("meeting_room_A", "meeting_room_B"))
        self.assertEqual(handled.state.mode, ConversationMode.AWAITING_TASK_INFORMATION)

    def test_unknown_idle_request_does_not_change_state(self):
        state = idle_state()
        handled = self.manager.handle(state, UnknownUtteranceResult("unsupported"), self.catalog)
        self.assertIsInstance(handled.result, UnsupportedRequest)
        self.assertEqual(handled.state, state)

    def test_unknown_during_clarification_preserves_pending_task(self):
        state = ConversationState(
            "conv_001", ConversationMode.AWAITING_TASK_INFORMATION,
            intent(None), "destination", None, [],
        )
        handled = self.manager.handle(state, UnknownUtteranceResult(), self.catalog)
        self.assertIsInstance(handled.result, ClarificationRequired)
        self.assertEqual(handled.state, state)

    def test_wait_decision_is_ready_and_clears_decision_context(self):
        state = ConversationState(
            "conv_001", ConversationMode.AWAITING_ROBOT_DECISION,
            None, None, "task_001", [Action.WAIT, Action.CANCEL],
        )
        handled = self.manager.handle(state, RobotDecisionResult(Action.WAIT), self.catalog)
        self.assertEqual(handled.result, DecisionReady("task_001", Action.WAIT))
        self.assertEqual(handled.state.mode, ConversationMode.IDLE)
        self.assertIsNone(handled.state.activeTaskId)
        self.assertEqual(handled.state.allowedDecisions, [])

    def test_cancel_decision_is_ready(self):
        state = ConversationState(
            "conv_001", ConversationMode.AWAITING_ROBOT_DECISION,
            None, None, "task_001", [Action.WAIT, Action.CANCEL],
        )
        handled = self.manager.handle(state, RobotDecisionResult(Action.CANCEL), self.catalog)
        self.assertEqual(handled.result, DecisionReady("task_001", Action.CANCEL))
        self.assertEqual(handled.state.mode, ConversationMode.IDLE)

    def test_context_mismatch_is_rejected_defensively(self):
        clarification = self.manager.handle(
            idle_state(), ClarificationAnswerResult("destination", "Phòng A"), self.catalog
        )
        decision = self.manager.handle(
            idle_state(), RobotDecisionResult(Action.WAIT), self.catalog
        )
        self.assertIsInstance(clarification.result, DialogueError)
        self.assertEqual(clarification.result.reason, DialogueErrorCode.CONTEXT_MISMATCH)
        self.assertIsInstance(decision.result, DialogueError)
        self.assertEqual(decision.result.reason, DialogueErrorCode.CONTEXT_MISMATCH)


if __name__ == "__main__":
    unittest.main()