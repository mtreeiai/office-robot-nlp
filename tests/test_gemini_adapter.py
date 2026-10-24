import json
import io
import os
import unittest
import urllib.error
from unittest.mock import patch

from office_robot_nlp.clu import (
    ClarificationAnswerResult,
    RobotDecisionResult,
    TaskCommandResult,
    UnknownUtteranceResult,
)
from office_robot_nlp.domain.conversation import ConversationMode, ConversationState
from office_robot_nlp.domain.intent import Action
from office_robot_nlp.domain.robot_event import DecisionReason
from office_robot_nlp.gemini_adapter import (
    GEMINI_CLU_RESPONSE_SCHEMA,
    GeminiConfig,
    GeminiLanguageUnderstandingParser,
    GeminiProviderRequestError,
    InvalidGeminiConfigurationError,
    InvalidGeminiResultError,
    MalformedGeminiOutputError,
    MissingGeminiAPIKeyError,
    _http_request,
)


def idle_context() -> ConversationState:
    return ConversationState("conv_001", ConversationMode.IDLE, None, None, None, [])


def task_payload(destination="phòng A", priority="NORMAL", if_busy=None):
    return {
        "type": "TASK_COMMAND",
        "parsedIntent": {
            "intent": "DELIVER_DOCUMENT",
            "destinationText": destination,
            "documentText": "tài liệu",
            "priority": priority,
            "constraints": {"ifBusy": if_busy, "ifClosed": None},
            "missingFields": [] if destination else ["destination"],
        },
    }


def gemini_response(content, *, status="completed"):
    return {
        "status": status,
        "steps": [{"type": "model_output", "content": [{"type": "text", "text": json.dumps(content)}]}],
    }


class FakeGeminiProvider:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, endpoint, headers, body, timeout):
        self.calls.append((endpoint, headers, json.loads(body), timeout))
        return self.response


class GeminiAdapterTests(unittest.TestCase):
    def parser(self, response):
        provider = FakeGeminiProvider(response)
        parser = GeminiLanguageUnderstandingParser(
            GeminiConfig("test-key", model="test-model", endpoint="https://test.invalid/interactions"),
            request=provider,
        )
        return parser, provider

    def test_missing_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(MissingGeminiAPIKeyError):
                GeminiConfig.from_environment()

    def test_default_model(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=True):
            self.assertEqual(GeminiConfig.from_environment().model, "gemini-3.8-flash")

    def test_base_url_and_interactions_endpoint(self):
        for base_url in (
            "https://generativelanguage.googleapis.com/v1",
            "https://generativelanguage.googleapis.com/v1/",
        ):
            with self.subTest(base_url=base_url), patch.dict(
                os.environ,
                {"GEMINI_API_KEY": "test-key", "GEMINI_BASE_URL": base_url},
                clear=True,
            ):
                config = GeminiConfig.from_environment()
                self.assertEqual(
                    config.endpoint,
                    "https://generativelanguage.googleapis.com/v1/interactions",
                )

    def test_headers_and_key_are_not_in_json_body(self):
        parser, provider = self.parser(gemini_response({"type": "UNKNOWN", "reason": None}))
        parser.parse("Mở Youtube cho tôi.", idle_context())
        endpoint, headers, body, _ = provider.calls[0]
        self.assertEqual(endpoint, "https://test.invalid/interactions")
        self.assertEqual(headers["x-goog-api-key"], "test-key")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertNotIn("test-key", json.dumps(body))

    def test_idle_context_is_explicit(self):
        parser, provider = self.parser(gemini_response({"type": "UNKNOWN", "reason": None}))
        parser.parse("Phòng A.", idle_context())
        self.assertIn('"state": "IDLE"', provider.calls[0][2]["system_instruction"])

    def test_clarification_context_is_explicit(self):
        context = ConversationState(
            "conv_001", ConversationMode.AWAITING_TASK_INFORMATION,
            None, "destination", None, [],
        )
        parser, provider = self.parser(
            gemini_response({"type": "CLARIFICATION_ANSWER", "field": "destination", "value": "Phòng A"})
        )
        result = parser.parse("Phòng A.", context)
        self.assertEqual(result, ClarificationAnswerResult("destination", "Phòng A"))
        self.assertIn('"awaitingField": "destination"', provider.calls[0][2]["system_instruction"])

    def test_robot_decision_context_is_explicit(self):
        context = ConversationState(
            "conv_001", ConversationMode.AWAITING_ROBOT_DECISION,
            None, None, "task_001", [Action.WAIT, Action.CANCEL], DecisionReason.ROOM_BUSY,
        )
        parser, provider = self.parser(gemini_response({"type": "ROBOT_DECISION", "decision": "WAIT"}))
        result = parser.parse("Chờ đi.", context)
        self.assertEqual(result, RobotDecisionResult(Action.WAIT))
        system_instruction = provider.calls[0][2]["system_instruction"]
        self.assertIn('"reason": "ROOM_BUSY"', system_instruction)
        self.assertIn('"allowedDecisions": ["WAIT", "CANCEL"]', system_instruction)

    def test_request_uses_gemini_json_response_format_and_schema(self):
        parser, provider = self.parser(gemini_response({"type": "UNKNOWN", "reason": None}))
        parser.parse("test", idle_context())
        request = provider.calls[0][2]
        self.assertEqual(request["response_format"]["type"], "text")
        self.assertEqual(request["response_format"]["mime_type"], "application/json")
        schema = request["response_format"]["schema"]
        self.assertEqual(schema, GEMINI_CLU_RESPONSE_SCHEMA)
        self.assertEqual(
            set(schema["properties"]["type"]["enum"]),
            {"TASK_COMMAND", "TASK_UPDATE", "CLARIFICATION_ANSWER", "ROBOT_DECISION", "SOCIAL", "UNKNOWN",
             "BATCH_COMMAND", "BATCH_ORDER", "BATCH_CONFIRM", "CANCEL_PENDING"},
        )
        self.assertEqual(
            set(schema["properties"]["parsedIntent"]["properties"]),
            {"intent", "destinationText", "documentText", "recipientText", "priority", "constraints", "missingFields"},
        )

    def test_valid_task_command(self):
        parser, _ = self.parser(gemini_response(task_payload("phòng B", "HIGH", "WAIT")))
        result = parser.parse("Chuyển gấp hồ sơ này sang phòng B.", idle_context())
        self.assertIsInstance(result, TaskCommandResult)
        self.assertEqual(result.parsedIntent.destinationText, "phòng B")
        self.assertEqual(result.parsedIntent.priority.value, "HIGH")
        self.assertEqual(result.parsedIntent.constraints.ifBusy, Action.WAIT)

    def test_valid_clarification(self):
        context = ConversationState(
            "conv_001", ConversationMode.AWAITING_TASK_INFORMATION,
            None, "destination", None, [],
        )
        parser, _ = self.parser(
            gemini_response({"type": "CLARIFICATION_ANSWER", "field": "destination", "value": "Phòng A"})
        )
        self.assertEqual(
            parser.parse("Phòng A.", context),
            ClarificationAnswerResult("destination", "Phòng A"),
        )

    def test_valid_wait_and_cancel_decisions(self):
        context = ConversationState(
            "conv_001", ConversationMode.AWAITING_ROBOT_DECISION,
            None, None, "task_001", [Action.WAIT, Action.CANCEL], DecisionReason.ROOM_BUSY,
        )
        for decision in ("WAIT", "CANCEL"):
            with self.subTest(decision=decision):
                parser, _ = self.parser(gemini_response({"type": "ROBOT_DECISION", "decision": decision}))
                self.assertEqual(parser.parse("decision", context), RobotDecisionResult(Action(decision)))

    def test_valid_unknown(self):
        parser, _ = self.parser(gemini_response({"type": "UNKNOWN", "reason": "unsupported"}))
        self.assertEqual(
            parser.parse("Mở Youtube cho tôi.", idle_context()),
            UnknownUtteranceResult("unsupported"),
        )

    def test_disallowed_decision_is_rejected_by_clu_decoder(self):
        context = ConversationState(
            "conv_001", ConversationMode.AWAITING_ROBOT_DECISION,
            None, None, "task_001", [Action.WAIT, Action.CANCEL], DecisionReason.ROOM_BUSY,
        )
        parser, _ = self.parser(gemini_response({"type": "ROBOT_DECISION", "decision": "RETRY"}))
        with self.assertRaises(InvalidGeminiResultError) as raised:
            parser.parse("Thử lại.", context)
        self.assertEqual(raised.exception.errors, ("ROBOT_DECISION_NOT_ALLOWED",))

    def test_malformed_http_payload(self):
        parser = GeminiLanguageUnderstandingParser(
            GeminiConfig("test-key"), request=lambda *_: b"not-json"
        )
        with self.assertRaises(MalformedGeminiOutputError):
            parser.parse("test", idle_context())

    def test_missing_model_output(self):
        parser, _ = self.parser({"status": "completed", "steps": []})
        with self.assertRaises(MalformedGeminiOutputError):
            parser.parse("test", idle_context())

    def test_missing_text_content(self):
        parser, _ = self.parser({
            "status": "completed",
            "steps": [{"type": "model_output", "content": [{"type": "json"}]}],
        })
        with self.assertRaises(MalformedGeminiOutputError):
            parser.parse("test", idle_context())

    def test_malformed_structured_clu_json(self):
        parser, _ = self.parser({
            "status": "completed",
            "steps": [{"type": "model_output", "content": [{"type": "text", "text": "not-json"}]}],
        })
        with self.assertRaises(InvalidGeminiResultError):
            parser.parse("test", idle_context())

    def test_provider_failure_and_429_details(self):
        parser = GeminiLanguageUnderstandingParser(
            GeminiConfig("test-key"), request=lambda *_: (_ for _ in ()).throw(OSError("offline"))
        )
        with self.assertRaises(GeminiProviderRequestError):
            parser.parse("test", idle_context())

        error = urllib.error.HTTPError(
            "https://test.invalid/interactions",
            429,
            "rate limited",
            {},
            io.BytesIO(json.dumps({
                "error": {
                    "status": "RESOURCE_EXHAUSTED",
                    "code": 429,
                    "message": "quota exceeded",
                }
            }).encode("utf-8")),
        )
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(GeminiProviderRequestError) as raised:
                _http_request("https://test.invalid/interactions", {}, b"{}", 1)
        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(raised.exception.provider_status, "RESOURCE_EXHAUSTED")

    def test_invalid_configuration_and_timeout(self):
        with self.assertRaises(InvalidGeminiConfigurationError):
            GeminiConfig("test-key", timeout=0)
        with self.assertRaises(InvalidGeminiConfigurationError):
            GeminiConfig("test-key", endpoint="not-a-url")
        with patch.dict(
            os.environ,
            {"GEMINI_API_KEY": "test-key", "GEMINI_TIMEOUT": "bad"},
            clear=True,
        ):
            with self.assertRaises(InvalidGeminiConfigurationError):
                GeminiConfig.from_environment()


if __name__ == "__main__":
    unittest.main()
