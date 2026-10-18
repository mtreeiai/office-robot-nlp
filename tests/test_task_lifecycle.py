import json
import unittest
from dataclasses import replace

from office_robot_nlp.clu import FakeLanguageUnderstandingParser
from office_robot_nlp.demo_catalog import demo_map_catalog
from office_robot_nlp.dialogue_manager import TaskReady
from office_robot_nlp.domain.conversation import ConversationMode, ConversationState
from office_robot_nlp.domain.intent import Constraints, IntentType, ParsedIntent, Priority
from office_robot_nlp.response_plan import (
    ResponseAct,
    ResponsePlan,
    TemplateNaturalResponseGenerator,
    generate_with_fallback,
)
from office_robot_nlp.task_lifecycle import (
    InvalidTaskTransitionError,
    TaskExecutionEvent,
    TaskLifecycleState,
    apply_execution_event,
    create_ready_task,
)
from text_conversation import TextConversationApplication


def resolved_intent():
    from office_robot_nlp.domain.intent import ResolvedDestination, ResolvedDocument, ResolvedIntent
    return ResolvedIntent(
        IntentType.DELIVER_DOCUMENT,
        ResolvedDestination("meeting_room_A", "phòng A"),
        ResolvedDocument(None, "tài liệu"),
        Priority.NORMAL,
        Constraints(None, None),
    )


def task():
    return create_ready_task("task_001", resolved_intent())


class TaskLifecycleTests(unittest.TestCase):
    def test_ready_is_initial_state(self):
        self.assertEqual(task().state, TaskLifecycleState.READY)

    def test_full_valid_transition_path(self):
        current = task()
        for event, expected in (
            (TaskExecutionEvent.TASK_QUEUED, TaskLifecycleState.QUEUED),
            (TaskExecutionEvent.TASK_STARTED, TaskLifecycleState.STARTED),
            (TaskExecutionEvent.NAVIGATION_STARTED, TaskLifecycleState.NAVIGATING),
            (TaskExecutionEvent.ARRIVED_AT_DESTINATION, TaskLifecycleState.ARRIVED),
            (TaskExecutionEvent.WAITING_FOR_RECIPIENT, TaskLifecycleState.WAITING_FOR_RECIPIENT),
            (TaskExecutionEvent.RECIPIENT_VERIFICATION_STARTED, TaskLifecycleState.VERIFYING_RECIPIENT),
            (TaskExecutionEvent.DELIVERY_STARTED, TaskLifecycleState.DELIVERING),
            (TaskExecutionEvent.DELIVERY_COMPLETED, TaskLifecycleState.COMPLETED),
        ):
            current = apply_execution_event(current, event)
            self.assertEqual(current.state, expected)

    def test_invalid_skipped_transitions_are_rejected(self):
        for state, event in (
            (TaskLifecycleState.READY, TaskExecutionEvent.DELIVERY_COMPLETED),
            (TaskLifecycleState.QUEUED, TaskExecutionEvent.ARRIVED_AT_DESTINATION),
            (TaskLifecycleState.NAVIGATING, TaskExecutionEvent.DELIVERY_COMPLETED),
            (TaskLifecycleState.COMPLETED, TaskExecutionEvent.TASK_STARTED),
            (TaskLifecycleState.RETURNED, TaskExecutionEvent.NAVIGATION_STARTED),
        ):
            with self.subTest(state=state, event=event):
                with self.assertRaises(InvalidTaskTransitionError):
                    apply_execution_event(replace(task(), state=state), event)

    def test_cancel_and_return_terminal_paths(self):
        cancelled = apply_execution_event(task(), TaskExecutionEvent.TASK_CANCELLED)
        self.assertEqual(cancelled.state, TaskLifecycleState.CANCELLED)
        returning = apply_execution_event(
            replace(task(), state=TaskLifecycleState.FAILED), TaskExecutionEvent.RETURN_STARTED
        )
        returned = apply_execution_event(returning, TaskExecutionEvent.RETURN_COMPLETED)
        self.assertEqual(returned.state, TaskLifecycleState.RETURNED)

    def test_task_ready_is_recorded_not_started(self):
        app = TextConversationApplication(
            FakeLanguageUnderstandingParser({
                "Đưa tài liệu tới phòng A.": {
                    "type": "TASK_COMMAND",
                    "parsedIntent": {
                        "intent": "DELIVER_DOCUMENT", "destinationText": "phòng A",
                        "documentText": "tài liệu", "priority": "NORMAL",
                        "constraints": {"ifBusy": None, "ifClosed": None}, "missingFields": [],
                    },
                }
            }),
            demo_map_catalog(),
        )
        turn = app.process_text("Đưa tài liệu tới phòng A.")
        self.assertIsInstance(turn.output, TaskReady)
        self.assertEqual(app.active_task.state, TaskLifecycleState.READY)
        self.assertEqual(turn.responsePlan.act, ResponseAct.TASK_RECORDED)
        self.assertIn("ghi nhận", turn.responseText.casefold())
        self.assertNotIn("đang di chuyển", turn.responseText.casefold())
        self.assertNotIn("bắt đầu", turn.responseText.casefold())

    def test_events_drive_execution_safe_responses(self):
        app = TextConversationApplication(
            FakeLanguageUnderstandingParser({
                "task": {"type": "UNKNOWN"},
            }),
            demo_map_catalog(),
        )
        app.active_task = task()
        app.active_task = apply_execution_event(app.active_task, TaskExecutionEvent.TASK_QUEUED)
        self.assertEqual(app.process_execution_event(TaskExecutionEvent.TASK_STARTED).responsePlan.act, ResponseAct.TASK_STARTED)
        navigating = app.process_execution_event(TaskExecutionEvent.NAVIGATION_STARTED)
        self.assertEqual(navigating.responsePlan.act, ResponseAct.TASK_NAVIGATING)
        self.assertIn("di chuyển", navigating.responseText.casefold())
        arrived = app.process_execution_event(TaskExecutionEvent.ARRIVED_AT_DESTINATION)
        self.assertNotIn("giao thành công", arrived.responseText.casefold())

    def test_ready_nlg_overclaim_falls_back_to_safe_template(self):
        class OverclaimingGenerator:
            def generate(self, plan, context):
                return "Tôi đang di chuyển và sẽ giao ngay."

        plan = ResponsePlan(
            ResponseAct.TASK_RECORDED,
            {"lifecycleState": "READY", "destinationText": "phòng A"},
        )
        response = generate_with_fallback(
            plan,
            ConversationState("test", ConversationMode.IDLE, None, None, None, []),
            OverclaimingGenerator(),
        )
        self.assertIn("ghi nhận", response.casefold())
        self.assertNotIn("đang di chuyển", response.casefold())


if __name__ == "__main__":
    unittest.main()