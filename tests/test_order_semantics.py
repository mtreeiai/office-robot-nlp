"""Gemini request guidance and end-to-end semantics using scripted transports."""

import json
import unittest
from unittest.mock import patch

from office_robot_nlp.clu import decode_language_understanding_result
from office_robot_nlp.dialogue_manager import BatchClarificationRequired, BatchReady, TaskReady
from office_robot_nlp.domain.batch import OrderPolicy
from office_robot_nlp.domain.intent import Action
from office_robot_nlp.gemini_adapter import GeminiConfig, GeminiLanguageUnderstandingParser
from office_robot_nlp.semantic_map import PROJECT_ROOT, load_semantic_registry
from text_conversation import TextConversationApplication


REQUEST = "Giao hợp đồng đến phòng 2, báo cáo đến phòng 5 và hồ sơ đến phòng 7."
PRIORITY_ONLY = REQUEST + " Ưu tiên hợp đồng trước."
STRICT_ONLY = REQUEST + " Giao lần lượt theo đúng thứ tự đã nêu."
CONFLICT = REQUEST + " Bắt buộc giữ đúng toàn bộ thứ tự này, nhưng ưu tiên hồ sơ trước."


def candidate(policy="PRIORITY", priorities=("HIGH", "NORMAL", "NORMAL")):
    return {
        "type": "BATCH_COMMAND", "orderPolicy": policy, "allocationStatus": "CLEAR",
        "tasks": [{"documentText": document, "recipientText": None, "destinationText": destination,
                   "priority": priority, "constraints": {"ifBusy": None, "ifClosed": None}}
                  for document, destination, priority in zip(
                      ("hợp đồng", "báo cáo", "hồ sơ"), ("phòng 2", "phòng 5", "phòng 7"), priorities)],
    }


class ScriptedGemini:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, endpoint, headers, body, timeout):
        request = json.loads(body)
        self.calls.append(request)
        response = self.responses[request["input"]]
        return {"steps": [{"type": "model_output", "content": [
            {"type": "text", "text": json.dumps(response, ensure_ascii=False)},
        ]}]}


class OrderSemanticsTests(unittest.TestCase):
    def setUp(self):
        blocked = patch("urllib.request.urlopen", side_effect=AssertionError("Real providers forbidden"))
        blocked.start()
        self.addCleanup(blocked.stop)

    def app(self, responses):
        provider = ScriptedGemini(responses)
        parser = GeminiLanguageUnderstandingParser(
            GeminiConfig("fake-key", endpoint="https://fake.invalid/interactions"), request=provider,
        )
        registry = load_semantic_registry(PROJECT_ROOT / "data/office_rooms.json",
                                           PROJECT_ROOT / "data/office_room_aliases.json")
        return TextConversationApplication(parser, registry.map_catalog, room_registry=registry), provider

    def assert_sequence(self, app):
        batch = app.batches["batch_001"]
        self.assertEqual([member.sequenceIndex for member in batch.tasks], [0, 1, 2])
        intents = [app.tasks[member.taskId].lifecycle.resolvedIntent for member in batch.tasks]
        self.assertEqual([intent.document.originalText for intent in intents], ["hợp đồng", "báo cáo", "hồ sơ"])
        self.assertEqual([intent.destination.locationId for intent in intents], ["02", "05", "07"])
        return intents

    def test_reported_priority_only_utterance_retains_priority_policy_and_sequence(self):
        app, _ = self.app({PRIORITY_ONLY: candidate()})
        turn = app.process_text(PRIORITY_ONLY)
        self.assertIsInstance(turn.output, BatchReady)
        self.assertEqual(app.batches["batch_001"].orderPolicy, OrderPolicy.PRIORITY)
        intents = self.assert_sequence(app)
        self.assertEqual([intent.priority.value for intent in intents], ["HIGH", "NORMAL", "NORMAL"])
        self.assertFalse(turn.output.draft.orderConfirmed)
        self.assertTrue(all(intent.constraints.ifBusy is None and intent.constraints.ifClosed is None for intent in intents))

    def test_prioritizing_a_later_item_does_not_reorder_the_array(self):
        text = REQUEST + " Ưu tiên báo cáo trước."
        app, _ = self.app({text: candidate(priorities=("NORMAL", "HIGH", "NORMAL"))})
        turn = app.process_text(text)
        self.assertIsInstance(turn.output, BatchReady)
        intents = self.assert_sequence(app)
        self.assertEqual([intent.priority.value for intent in intents], ["NORMAL", "HIGH", "NORMAL"])
        self.assertEqual(app.batches["batch_001"].orderPolicy, OrderPolicy.PRIORITY)

    def test_explicit_whole_batch_strict_order_does_not_invent_priority(self):
        app, _ = self.app({STRICT_ONLY: candidate("STRICT_ORDER", ("NORMAL",) * 3)})
        turn = app.process_text(STRICT_ONLY)
        self.assertIsInstance(turn.output, BatchReady)
        self.assertEqual(app.batches["batch_001"].orderPolicy, OrderPolicy.STRICT_ORDER)
        intents = self.assert_sequence(app)
        self.assertEqual([intent.priority.value for intent in intents], ["NORMAL"] * 3)
        # A successful strict batch needs no conflict acknowledgement if no conflict exists.
        self.assertFalse(turn.output.draft.orderConfirmed)

    def test_legitimate_strict_instruction_with_priority_phrase_is_not_rewritten(self):
        text = STRICT_ONLY + " Ưu tiên hợp đồng trước."
        app, _ = self.app({text: candidate("STRICT_ORDER")})
        turn = app.process_text(text)
        self.assertIsInstance(turn.output, BatchReady)
        self.assertEqual(app.batches["batch_001"].orderPolicy, OrderPolicy.STRICT_ORDER)
        self.assertEqual(self.assert_sequence(app)[0].priority.value, "HIGH")

    def test_conflicting_instructions_wait_for_user_and_only_then_confirm_choice(self):
        for choice in ("STRICT_ORDER", "PRIORITY"):
            with self.subTest(choice=choice):
                answer = "Giữ đúng thứ tự đã nêu." if choice == "STRICT_ORDER" else "Theo ưu tiên nhé."
                app, _ = self.app({CONFLICT: candidate("STRICT_ORDER", ("NORMAL", "NORMAL", "HIGH")),
                                   answer: {"type": "BATCH_ORDER", "orderPolicy": choice}})
                first = app.process_text(CONFLICT)
                self.assertIsInstance(first.output, BatchClarificationRequired)
                self.assertEqual(len(app.tasks), 0)
                self.assertEqual(len(app.batches), 0)
                self.assertEqual(app.state.pendingBatch.awaitingField, "order")
                self.assertFalse(app.state.pendingBatch.draft.orderConfirmed)
                ready = app.process_text(answer)
                self.assertIsInstance(ready.output, BatchReady)
                self.assertTrue(ready.output.draft.orderConfirmed)
                self.assertEqual(app.batches["batch_001"].orderPolicy.value, choice)
                self.assertEqual([intent.priority.value for intent in self.assert_sequence(app)], ["NORMAL", "NORMAL", "HIGH"])

    def test_gemini_receives_explicit_distinction_examples_and_schema_guidance(self):
        app, provider = self.app({PRIORITY_ONLY: candidate()})
        app.process_text(PRIORITY_ONLY)
        request = provider.calls[0]
        self.assertEqual(request["input"], PRIORITY_ONLY)
        prompt = request["system_instruction"]
        for example in (PRIORITY_ONLY, STRICT_ONLY, CONFLICT):
            self.assertIn(example, prompt)
        self.assertIn("ENTIRE delivery batch", prompt)
        self.assertIn("do NOT fix the order of all deliveries", prompt)
        self.assertIn("preserve BOTH in the candidate", prompt)
        properties = request["response_format"]["schema"]["properties"]
        self.assertIn("entire batch", properties["orderPolicy"]["description"])
        self.assertIn("Ưu tiên hợp đồng trước", properties["orderPolicy"]["description"])
        self.assertIn("original mention order", properties["tasks"]["description"])
        self.assertIn("Strict sequence", properties["tasks"]["items"]["properties"]["priority"]["description"])

    def test_omitted_batch_policies_are_deterministically_none_not_ask_user(self):
        payload = candidate()
        for task in payload["tasks"]:
            task.pop("constraints")
        app, _ = self.app({PRIORITY_ONLY: payload})
        app.process_text(PRIORITY_ONLY)
        for intent in self.assert_sequence(app):
            self.assertIsNone(intent.constraints.ifBusy)
            self.assertIsNone(intent.constraints.ifClosed)

    def test_explicit_policies_are_preserved_and_not_silently_cleared(self):
        for busy, closed, text in (
            ("ASK_USER", "ASK_USER", REQUEST + " Nếu phòng bận hoặc đóng thì hỏi tôi trước."),
            ("WAIT", "RETRY", REQUEST + " Nếu phòng bận thì chờ, nếu đóng thì thử lại."),
        ):
            with self.subTest(busy=busy, closed=closed):
                payload = candidate(priorities=("NORMAL",) * 3)
                for task in payload["tasks"]:
                    task["constraints"] = {"ifBusy": busy, "ifClosed": closed}
                app, _ = self.app({text: payload})
                app.process_text(text)
                self.assertEqual(app.batches["batch_001"].orderPolicy, OrderPolicy.PRIORITY)
                for intent in self.assert_sequence(app):
                    self.assertEqual(intent.constraints.ifBusy, Action(busy))
                    self.assertEqual(intent.constraints.ifClosed, Action(closed))

    def test_batch_and_single_schemas_guide_null_policies_without_changing_single_flow(self):
        text = "Giao gấp hợp đồng đến phòng 2."
        payload = {"type": "TASK_COMMAND", "parsedIntent": {
            "intent": "DELIVER_DOCUMENT", "documentText": "hợp đồng", "recipientText": None,
            "destinationText": "phòng 2", "priority": "HIGH",
            "constraints": {"ifBusy": None, "ifClosed": None}, "missingFields": [],
        }}
        app, provider = self.app({text: payload})
        turn = app.process_text(text)
        self.assertIsInstance(turn.output, TaskReady)
        self.assertEqual(turn.output.resolvedIntent.priority.value, "HIGH")
        self.assertEqual(len(app.batches), 0)
        properties = provider.calls[0]["response_format"]["schema"]["properties"]
        for item in (properties["parsedIntent"], properties["tasks"]["items"]):
            for name in ("ifBusy", "ifClosed"):
                description = item["properties"]["constraints"]["properties"][name]["description"]
                self.assertIn("Return null when unspecified", description)
                self.assertIn("never an inferred default", description)

    def test_provider_cannot_self_confirm_a_conflicting_order(self):
        app, _ = self.app({})
        payload = candidate("STRICT_ORDER", ("NORMAL", "NORMAL", "HIGH"))
        payload["orderConfirmed"] = True
        decoded = decode_language_understanding_result(payload, app.state)
        self.assertIsNone(decoded.result)
        self.assertEqual(len(app.tasks), 0)


if __name__ == "__main__":
    unittest.main()
