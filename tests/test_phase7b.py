import json
import unittest
from dataclasses import replace

from office_robot_nlp.clu import (
    FakeLanguageUnderstandingParser,
    SocialResult,
    TaskUpdateResult,
    decode_language_understanding_result,
)
from office_robot_nlp.demo_catalog import demo_map_catalog
from office_robot_nlp.dialogue_manager import (
    SocialAcknowledged,
    TaskReady,
    TaskUpdateAccepted,
)
from office_robot_nlp.domain.conversation import ConversationMode, ConversationState
from office_robot_nlp.domain.intent import Action, Constraints, IntentType, ParsedIntent, Priority
from office_robot_nlp.response_plan import (
    GeminiNaturalResponseGenerator,
    ResponseAct,
    ResponsePlan,
    TemplateNaturalResponseGenerator,
    generate_with_fallback,
    response_plan_for,
)
from text_conversation import TextConversationApplication


def task_payload(destination=None, recipient=None, priority="NORMAL"):
    parsed = {
        "intent": "DELIVER_DOCUMENT",
        "destinationText": destination,
        "documentText": "tài liệu này",
        "priority": priority,
        "constraints": {"ifBusy": None, "ifClosed": None},
        "missingFields": ["destination"] if destination is None else [],
    }
    if recipient is not None:
        parsed["recipientText"] = recipient
    return {"type": "TASK_COMMAND", "parsedIntent": parsed}


def pending_state() -> ConversationState:
    return ConversationState(
        "test", ConversationMode.AWAITING_TASK_INFORMATION,
        ParsedIntent(
            IntentType.DELIVER_DOCUMENT,
            None,
            "tài liệu này",
            Priority.NORMAL,
            Constraints(None, None),
            ["destination"],
        ),
        "destination", None, [],
    )


class FailingGenerator:
    def generate(self, plan, context):
        raise RuntimeError("quota")


class Phase7BTests(unittest.TestCase):
    def test_social_decode_and_idle_response(self):
        result = decode_language_understanding_result(
            {"type": "SOCIAL", "reason": "greeting"},
            ConversationState("test", ConversationMode.IDLE, None, None, None, []),
        )
        self.assertEqual(result.result, SocialResult("greeting"))
        app = TextConversationApplication(
            FakeLanguageUnderstandingParser({"Chào bạn": {"type": "SOCIAL"}}),
            demo_map_catalog(),
        )
        turn = app.process_text("Chào bạn")
        self.assertIsInstance(turn.output, SocialAcknowledged)
        self.assertEqual(turn.state.mode, ConversationMode.IDLE)
        self.assertEqual(turn.responsePlan.act, ResponseAct.GREETING)

    def test_social_pending_preserves_task_and_asks_destination(self):
        app = TextConversationApplication(
            FakeLanguageUnderstandingParser({
                "Chào bạn": {"type": "SOCIAL"},
            }),
            demo_map_catalog(),
        )
        app.state = pending_state()
        turn = app.process_text("Chào bạn")
        self.assertIsInstance(turn.output, SocialAcknowledged)
        self.assertEqual(turn.state, app.state)
        self.assertEqual(turn.state.mode, ConversationMode.AWAITING_TASK_INFORMATION)
        self.assertEqual(turn.responsePlan.act, ResponseAct.GREETING_WITH_PENDING_REQUEST)
        self.assertIn("phòng nào", turn.responseText)

    def test_task_update_recipient_does_not_resolve_as_room(self):
        app = TextConversationApplication(
            FakeLanguageUnderstandingParser({
                "Hãy giao cho anh Phong nhé": {
                    "type": "TASK_UPDATE", "recipientText": "anh Phong"
                }
            }),
            demo_map_catalog(),
        )
        app.state = pending_state()
        turn = app.process_text("Hãy giao cho anh Phong nhé")
        self.assertIsInstance(turn.output, TaskUpdateAccepted)
        self.assertEqual(turn.state.pendingIntent.recipientText, "anh Phong")
        self.assertEqual(turn.state.mode, ConversationMode.AWAITING_TASK_INFORMATION)
        self.assertEqual(turn.responsePlan.act, ResponseAct.ACK_TASK_UPDATE)

    def test_task_update_priority_is_preserved(self):
        app = TextConversationApplication(
            FakeLanguageUnderstandingParser({
                "Cái này gấp nhé": {"type": "TASK_UPDATE", "priority": "HIGH"}
            }),
            demo_map_catalog(),
        )
        app.state = pending_state()
        turn = app.process_text("Cái này gấp nhé")
        self.assertEqual(turn.state.pendingIntent.priority, Priority.HIGH)
        self.assertIsInstance(turn.output, TaskUpdateAccepted)

    def test_task_update_destination_completes_and_preserves_recipient(self):
        app = TextConversationApplication(
            FakeLanguageUnderstandingParser({
                "Đổi sang phòng A": {
                    "type": "TASK_UPDATE", "destinationText": "phòng A"
                }
            }),
            demo_map_catalog(),
        )
        app.state = pending_state()
        # Construct the pending intent through the accepted CLU decoder instead of inventing IDs.
        decoded = decode_language_understanding_result(
            task_payload(None, "anh Phong", "HIGH"), pending_state()
        )
        pending = decoded.result.parsedIntent
        app.state = replace(app.state, pendingIntent=pending)
        turn = app.process_text("Đổi sang phòng A")
        self.assertIsInstance(turn.output, TaskReady)
        self.assertEqual(turn.output.resolvedIntent.destination.locationId, "meeting_room_A")
        self.assertEqual(turn.output.resolvedIntent.recipientText, "anh Phong")
        self.assertEqual(turn.output.resolvedIntent.priority, Priority.HIGH)

    def test_compound_unsupported_result_never_becomes_task_ready(self):
        app = TextConversationApplication(
            FakeLanguageUnderstandingParser({
                "Đem tài liệu đến phòng A rồi mang về": {"type": "UNKNOWN"}
            }),
            demo_map_catalog(),
        )
        turn = app.process_text("Đem tài liệu đến phòng A rồi mang về")
        self.assertEqual(turn.responsePlan.act, ResponseAct.UNSUPPORTED_REQUEST)
        self.assertNotIsInstance(turn.output, TaskReady)
        self.assertNotIn("INVALID_CLU_OUTPUT", turn.responseText)

    def test_template_fallback_after_nlg_failure(self):
        plan = ResponsePlan(ResponseAct.ASK_DESTINATION, {})
        text = generate_with_fallback(
            plan,
            ConversationState("test", ConversationMode.IDLE, None, None, None, []),
            FailingGenerator(),
        )
        self.assertEqual(text, "Bạn muốn giao tài liệu tới phòng nào?")

    def test_gemini_nlg_receives_only_response_plan_facts(self):
        captured = {}

        def request(endpoint, headers, body, timeout):
            captured.update({"endpoint": endpoint, "headers": headers, "body": json.loads(body)})
            return json.dumps({
                "steps": [{"type": "model_output", "content": [{"type": "text", "text": "Được rồi."}]}]
            }).encode("utf-8")

        from office_robot_nlp.gemini_adapter import GeminiConfig
        generator = GeminiNaturalResponseGenerator(
            GeminiConfig("secret", model="test-model", endpoint="https://test.invalid/interactions"),
            request=request,
        )
        text = generator.generate(
            ResponsePlan(ResponseAct.TASK_ACCEPTED, {"destinationText": "phòng A"}),
            ConversationState("test", ConversationMode.IDLE, None, None, None, []),
        )
        self.assertEqual(text, "Được rồi.")
        self.assertNotIn("secret", json.dumps(captured["body"]))
        self.assertIn("phòng A", json.dumps(captured["body"], ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()