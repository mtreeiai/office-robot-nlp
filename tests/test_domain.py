"Tests for transport-independent domain contracts."

import json
import unittest
from dataclasses import asdict
from typing import get_args
from office_robot_nlp.domain.conversation import ConversationMode, ConversationState
from office_robot_nlp.domain.intent import (
    Action, BusyPolicy, ClosedPolicy, Constraints, IntentType, ParsedIntent,
    Priority, ResolvedDestination, ResolvedDocument, ResolvedIntent, UserDecision,
)
from office_robot_nlp.domain.map import MapCatalog, MapLocation
from office_robot_nlp.domain.robot_event import (
    DecisionReason, RobotEvent, RobotStatus, TaskStatusEvent,
    UserDecisionRequiredEvent,
)
from office_robot_nlp.domain.task import (
    TaskAccepted, TaskPayload, TaskPolicies, TaskRequest, TaskTarget, TaskUpdate,
)

class DomainSerializationTests(unittest.TestCase):
    def test_intent_map_and_task_json(self):
        intent = ParsedIntent(
            intent=IntentType.DELIVER_DOCUMENT,
            destinationText="phòng A",
            documentText="tài liệu này",
            priority=Priority.NORMAL,
            constraints=Constraints(ifBusy=Action.WAIT, ifClosed=None),
            missingFields=[],
        )
        catalog = MapCatalog("1.0", [MapLocation("meeting_room_A", "Phòng họp A", ["phòng A"])])
        resolved = ResolvedIntent(
            IntentType.DELIVER_DOCUMENT,
            ResolvedDestination("meeting_room_A", "phòng A"),
            ResolvedDocument(None, "tài liệu này"),
            Priority.NORMAL, intent.constraints,
        )
        task = TaskRequest(
            "req_001", IntentType.DELIVER_DOCUMENT,
            TaskPayload(None), TaskTarget(resolved.destination.locationId),
            resolved.priority, TaskPolicies(Action.WAIT, None),
        )
        self.assertEqual(json.loads(json.dumps(asdict(intent)))["constraints"]["ifBusy"], "WAIT")
        self.assertEqual(json.loads(json.dumps(asdict(catalog)))["locations"][0]["id"], "meeting_room_A")
        self.assertEqual(json.loads(json.dumps(asdict(resolved)))["intent"], "DELIVER_DOCUMENT")
        self.assertEqual(json.loads(json.dumps(asdict(task)))["target"], {"locationId": "meeting_room_A"})
        self.assertEqual(json.loads(json.dumps(asdict(TaskAccepted("req_001", "task_001", True)))),
                         {"requestId": "req_001", "taskId": "task_001", "accepted": True, "reason": None})

    def test_robot_event_decision_and_conversation_json(self):
        events: list[RobotEvent] = [
            TaskStatusEvent("TASK_STATUS", "task_001", RobotStatus.MOVING),
            UserDecisionRequiredEvent("USER_DECISION_REQUIRED", "task_001",
                                      DecisionReason.ROOM_BUSY, [Action.WAIT, Action.CANCEL]),
        ]
        self.assertEqual(json.loads(json.dumps([asdict(event) for event in events]))[1]["allowedOptions"],
                         ["WAIT", "CANCEL"])
        self.assertEqual(json.loads(json.dumps(asdict(TaskUpdate("task_001", Action.WAIT))))["decision"],
                         "WAIT")
        state = ConversationState("conv_001", ConversationMode.AWAITING_ROBOT_DECISION,
                                  None, None, "task_001", [Action.WAIT])
        self.assertEqual(json.loads(json.dumps(asdict(state)))["mode"], "AWAITING_ROBOT_DECISION")

    def test_policy_and_decision_type_sets_are_distinct(self):
        self.assertEqual(set(get_args(BusyPolicy)), {Action.WAIT, Action.ASK_USER})
        self.assertEqual(set(get_args(ClosedPolicy)), {Action.WAIT, Action.RETRY, Action.ASK_USER})
        self.assertEqual(set(get_args(UserDecision)), {Action.WAIT, Action.RETRY, Action.CANCEL})
        self.assertEqual({item.value for item in IntentType}, {"DELIVER_DOCUMENT", "UNKNOWN"})

if __name__ == "__main__":
    unittest.main()
