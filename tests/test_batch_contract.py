import json
import unittest

from office_robot_nlp.batch_schema import BATCH_TYPES
from office_robot_nlp.clu import (
    BatchCommandResult, BatchOrderResult, batch_semantic_context,
    decode_language_understanding_result,
)
from office_robot_nlp.dialogue_manager import DialogueManager
from office_robot_nlp.domain.batch import AllocationStatus, BatchDraft, BatchTaskReference, DeliveryBatch, OrderPolicy
from office_robot_nlp.batch_validation import validate_batch
from office_robot_nlp.domain.conversation import ConversationMode, ConversationState
from office_robot_nlp.domain.intent import Priority
from office_robot_nlp.gemini_adapter import GEMINI_CLU_RESPONSE_SCHEMA, GeminiConfig, GeminiLanguageUnderstandingParser
from office_robot_nlp.ollama_adapter import OLLAMA_CLU_RESPONSE_SCHEMA, OllamaConfig, OllamaLanguageUnderstandingParser
from office_robot_nlp.openai_adapter import OPENAI_CLU_RESPONSE_SCHEMA, OpenAIConfig, RealLLMLanguageUnderstandingParser
from office_robot_nlp.semantic_map import load_semantic_registry


def item(document="hợp đồng", destination="phòng 2", **changes):
    return {"documentText": document, "destinationText": destination, **changes}


def batch(*items, **changes):
    return {"type": "BATCH_COMMAND", "tasks": list(items or (item(), item("báo cáo", "phòng 5"))), **changes}


def idle():
    return ConversationState("test-session", ConversationMode.IDLE, None, None, None, [])


class BatchContractTests(unittest.TestCase):
    def decode(self, payload, context=None):
        return decode_language_understanding_result(payload, context or idle())

    def test_decodes_document_recipient_associations_with_normal_defaults(self):
        result = self.decode(batch(item(recipientText="anh Phong"), item("báo cáo", "phòng 5", recipientText="chị Lan")))
        self.assertIsInstance(result.result, BatchCommandResult)
        first, second = result.result.draft.tasks
        self.assertEqual((first.documentText, first.destinationText, first.recipientText), ("hợp đồng", "phòng 2", "anh Phong"))
        self.assertEqual((second.documentText, second.destinationText, second.recipientText), ("báo cáo", "phòng 5", "chị Lan"))
        self.assertEqual(first.priority, Priority.NORMAL)
        self.assertEqual(result.result.draft.orderPolicy, OrderPolicy.PRIORITY)
        self.assertEqual(result.result.draft.allocationStatus, AllocationStatus.CLEAR)
        self.assertEqual(self.decode(json.dumps(batch())).errors, ())

    def test_invalid_batch_structures_and_unknown_fields_are_rejected_atomically(self):
        payloads = [
            {"type": "BATCH_COMMAND"}, {"type": "BATCH_COMMAND", "tasks": []},
            {"type": "BATCH_COMMAND", "tasks": {}}, {"type": "BATCH_COMMAND", "tasks": [None]},
            batch(item(), {"documentText": "báo cáo"}),
            batch(item(), item(destinationId="05")), batch(item(), item(documentId="invented")),
            batch(item(), item(taskId="model-generated")), batch(item(), item(compartmentId="1")),
            batch(item(), item(staging_pose={"x": 1})), batch(orderPolicy="DISTANCE"),
            batch(allocationStatus=True), batch(orderConfirmed=True), batch(roomId="02"),
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                self.assertIsNone(self.decode(payload).result)

    def test_invalid_per_item_values_reuse_existing_schema_validation(self):
        for changes in ({"priority": "URGENT"}, {"documentText": []}, {"destinationText": 2},
                        {"recipientText": True}, {"constraints": {"ifBusy": "FLY", "ifClosed": None}}):
            with self.subTest(changes=changes):
                payload = batch(item(), {**item("báo cáo", "phòng 5"), **changes})
                self.assertIsNone(self.decode(payload).result)

    def test_duplicate_generic_documents_require_allocation_clarification(self):
        result = self.decode(batch(item("tài liệu", "phòng 2"), item("tài liệu", "phòng 5"))).result
        self.assertEqual(result.draft.allocationStatus, AllocationStatus.AMBIGUOUS)
        explicit = self.decode(batch(item("bản 1", "phòng 2"), item("bản 2", "phòng 5"), allocationStatus="CLEAR")).result
        self.assertEqual(explicit.draft.allocationStatus, AllocationStatus.CLEAR)

    def test_direct_drafts_cannot_bypass_type_or_per_item_validation(self):
        for tasks in ((), [], (None,)):
            with self.subTest(tasks=tasks), self.assertRaises(ValueError):
                BatchDraft(tasks)
        result = self.decode(batch()).result
        result.draft.tasks[0].priority = "URGENT"
        with self.assertRaises(ValueError):
            validate_batch(result.draft, load_semantic_registry().map_catalog)

    def test_empty_batch_duplicate_ids_invalid_ids_and_sequence_are_rejected(self):
        cases = [(), (BatchTaskReference("t", 0), BatchTaskReference("t", 1)),
                 (BatchTaskReference("t", 1),), (BatchTaskReference(1, 0),),
                 (BatchTaskReference("t", False),)]
        for refs in cases:
            with self.subTest(refs=refs), self.assertRaises(ValueError):
                DeliveryBatch("batch", "session", refs, OrderPolicy.PRIORITY)
        with self.assertRaises(ValueError):
            DeliveryBatch("", "session", (BatchTaskReference("t", 0),), OrderPolicy.PRIORITY)
        one = DeliveryBatch("batch", "session", (BatchTaskReference("t", 0),), OrderPolicy.PRIORITY)
        self.assertEqual(len(one.tasks), 1)

    def test_batch_clarification_context_contains_only_semantic_fields(self):
        result = self.decode(batch(item(), item("báo cáo", "phòng 8", recipientText="chị Lan"))).result
        state = DialogueManager().handle(idle(), result, load_semantic_registry().map_catalog).state
        context = batch_semantic_context(state)
        self.assertEqual(context["awaitingItemIndex"], 1)
        self.assertEqual(context["awaitingField"], "destination")
        self.assertEqual(context["pendingBatch"]["tasks"][0]["documentText"], "hợp đồng")
        self.assertNotIn("destinationId", json.dumps(context))
        self.assertNotIn("staging_pose", json.dumps(context))
        self.assertIsNotNone(self.decode({"type": "CLARIFICATION_ANSWER", "field": "destination", "value": "phòng 5"}, state).result)
        self.assertIsNone(self.decode({"type": "CLARIFICATION_ANSWER", "field": "document", "value": "khác"}, state).result)

    def test_order_confirm_and_cancel_are_context_gated(self):
        for payload in ({"type": "BATCH_ORDER", "orderPolicy": "STRICT_ORDER"},
                        {"type": "BATCH_CONFIRM"}, {"type": "CANCEL_PENDING"}):
            self.assertIsNone(self.decode(payload).result)
        result = self.decode(batch(item(priority="NORMAL"), item("báo cáo", "phòng 5", priority="HIGH"),
                                   orderPolicy="STRICT_ORDER")).result
        state = DialogueManager().handle(idle(), result, load_semantic_registry().map_catalog).state
        self.assertEqual(state.pendingBatch.awaitingField, "order")
        chosen = self.decode({"type": "BATCH_ORDER", "orderPolicy": "STRICT_ORDER"}, state).result
        self.assertEqual(chosen, BatchOrderResult(OrderPolicy.STRICT_ORDER))
        self.assertIsNone(self.decode({"type": "TASK_UPDATE", "recipientText": "khác"}, state).result)
        self.assertIsNotNone(self.decode({"type": "CANCEL_PENDING"}, state).result)

    def test_provider_schemas_include_batch_types_and_no_ids_or_capacity_fields(self):
        for schema in (GEMINI_CLU_RESPONSE_SCHEMA, OLLAMA_CLU_RESPONSE_SCHEMA):
            self.assertTrue(set(BATCH_TYPES).issubset(schema["properties"]["type"]["enum"]))
            tasks = schema["properties"]["tasks"]
            self.assertEqual(tasks["minItems"], 1)
            self.assertNotIn("maxItems", tasks)
            self.assertEqual(set(tasks["items"]["properties"]),
                             {"documentText", "recipientText", "destinationText", "priority", "constraints"})
        branches = OPENAI_CLU_RESPONSE_SCHEMA["schema"]["oneOf"]
        names = {branch["properties"]["type"]["enum"][0] for branch in branches}
        self.assertTrue(set(BATCH_TYPES).issubset(names))

    def test_gemini_fake_transport_decodes_batch_and_sends_pending_item_context(self):
        requests = []
        content = batch(item(), item("báo cáo", "phòng 8"))
        def request(endpoint, headers, body, timeout):
            requests.append(json.loads(body))
            return {"steps": [{"type": "model_output", "content": [{"type": "text", "text": json.dumps(content)}]}]}
        parser = GeminiLanguageUnderstandingParser(GeminiConfig("fake-key"), request=request)
        result = parser.parse("a multi-document request", idle())
        self.assertIsInstance(result, BatchCommandResult)
        state = DialogueManager().handle(idle(), result, load_semantic_registry().map_catalog).state
        content = {"type": "CLARIFICATION_ANSWER", "field": "destination", "value": "phòng 5"}
        parser.parse("Phòng 5", state)
        self.assertIn('"awaitingItemIndex": 1', requests[-1]["system_instruction"])
        self.assertIn("pendingBatch", requests[-1]["system_instruction"])
        self.assertEqual(requests[0]["response_format"]["schema"], GEMINI_CLU_RESPONSE_SCHEMA)

    def test_openai_and_ollama_fake_transports_keep_shared_batch_contract(self):
        payload = batch()
        openai = RealLLMLanguageUnderstandingParser(OpenAIConfig("fake-key"), request=lambda *_: {
            "choices": [{"message": {"content": json.dumps(payload)}}],
        })
        ollama = OllamaLanguageUnderstandingParser(OllamaConfig(model="fake-model"), request=lambda *_: {
            "message": {"content": json.dumps(payload)},
        })
        self.assertIsInstance(openai.parse("request", idle()), BatchCommandResult)
        self.assertIsInstance(ollama.parse("request", idle()), BatchCommandResult)


if __name__ == "__main__":
    unittest.main()
