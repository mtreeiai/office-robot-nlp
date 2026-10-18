import io
import json
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
from office_robot_nlp.ollama_adapter import (
    OLLAMA_CLU_RESPONSE_SCHEMA,
    InvalidOllamaConfigurationError,
    InvalidOllamaResultError,
    MalformedOllamaOutputError,
    OllamaConfig,
    OllamaConnectionError,
    OllamaLanguageUnderstandingParser,
    OllamaModelNotFoundError,
    OllamaTimeoutError,
    _http_request,
    check_ollama_health,
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


def ollama_response(content):
    return {"message": {"content": json.dumps(content)}}


class FakeOllamaProvider:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, endpoint, headers, body, timeout):
        self.calls.append((endpoint, headers, json.loads(body), timeout))
        return self.response


class OllamaAdapterTests(unittest.TestCase):
    def parser(self, response):
        provider = FakeOllamaProvider(response)
        parser = OllamaLanguageUnderstandingParser(
            OllamaConfig("test-model", base_url="http://test.invalid/"),
            request=provider,
        )
        return parser, provider

    def test_base_url_and_configured_model(self):
        config = OllamaConfig("llama3.2", base_url="http://localhost:11434/")
        self.assertEqual(config.chat_endpoint, "http://localhost:11434/api/chat")
        with patch.dict(
            os.environ,
            {"OLLAMA_MODEL": "qwen2.5", "OLLAMA_BASE_URL": "http://host:11434/"},
            clear=True,
        ):
            configured = OllamaConfig.from_environment()
        self.assertEqual(configured.model, "qwen2.5")
        self.assertEqual(configured.tags_endpoint, "http://host:11434/api/tags")

    def test_explicit_idle_context_and_request_schema(self):
        parser, provider = self.parser(ollama_response({"type": "UNKNOWN", "reason": None}))
        parser.parse("Phòng A.", idle_context())
        endpoint, headers, body, timeout = provider.calls[0]
        self.assertEqual(endpoint, "http://test.invalid/api/chat")
        self.assertEqual(headers, {"Content-Type": "application/json"})
        self.assertEqual(body["model"], "test-model")
        self.assertFalse(body["stream"])
        self.assertEqual(body["format"], OLLAMA_CLU_RESPONSE_SCHEMA)
        self.assertIn('"state": "IDLE"', body["messages"][0]["content"])
        self.assertEqual(body["messages"][1]["content"], "Phòng A.")
        self.assertEqual(timeout, 30.0)

    def test_clarification_context(self):
        context = ConversationState(
            "conv_001", ConversationMode.AWAITING_TASK_INFORMATION,
            None, "destination", None, [],
        )
        parser, provider = self.parser(
            ollama_response({"type": "CLARIFICATION_ANSWER", "field": "destination", "value": "Phòng A"})
        )
        result = parser.parse("Phòng A.", context)
        self.assertEqual(result, ClarificationAnswerResult("destination", "Phòng A"))
        self.assertIn('"awaitingField": "destination"', provider.calls[0][2]["messages"][0]["content"])

    def test_robot_decision_context(self):
        context = ConversationState(
            "conv_001", ConversationMode.AWAITING_ROBOT_DECISION,
            None, None, "task_001", [Action.WAIT, Action.CANCEL], DecisionReason.ROOM_BUSY,
        )
        parser, provider = self.parser(ollama_response({"type": "ROBOT_DECISION", "decision": "WAIT"}))
        self.assertEqual(parser.parse("Chờ đi.", context), RobotDecisionResult(Action.WAIT))
        prompt = provider.calls[0][2]["messages"][0]["content"]
        self.assertIn('"reason": "ROOM_BUSY"', prompt)
        self.assertIn('"allowedDecisions": ["WAIT", "CANCEL"]', prompt)

    def test_valid_task_clarification_wait_cancel_and_unknown(self):
        parser, _ = self.parser(ollama_response(task_payload("phòng B", "HIGH", "WAIT")))
        result = parser.parse("Chuyển gấp hồ sơ sang phòng B.", idle_context())
        self.assertIsInstance(result, TaskCommandResult)
        self.assertEqual(result.parsedIntent.priority.value, "HIGH")

        context = ConversationState(
            "conv_001", ConversationMode.AWAITING_ROBOT_DECISION,
            None, None, "task_001", [Action.WAIT, Action.CANCEL], DecisionReason.ROOM_BUSY,
        )
        for decision in ("WAIT", "CANCEL"):
            with self.subTest(decision=decision):
                parser, _ = self.parser(ollama_response({"type": "ROBOT_DECISION", "decision": decision}))
                self.assertEqual(parser.parse("quyết định", context), RobotDecisionResult(Action(decision)))

        parser, _ = self.parser(ollama_response({"type": "UNKNOWN", "reason": "unsupported"}))
        self.assertEqual(parser.parse("Mở Youtube", idle_context()), UnknownUtteranceResult("unsupported"))

    def test_disallowed_decision_uses_existing_decoder(self):
        context = ConversationState(
            "conv_001", ConversationMode.AWAITING_ROBOT_DECISION,
            None, None, "task_001", [Action.WAIT, Action.CANCEL], DecisionReason.ROOM_BUSY,
        )
        parser, _ = self.parser(ollama_response({"type": "ROBOT_DECISION", "decision": "RETRY"}))
        with self.assertRaises(InvalidOllamaResultError) as raised:
            parser.parse("Thử lại", context)
        self.assertEqual(raised.exception.errors, ("ROBOT_DECISION_NOT_ALLOWED",))

    def test_malformed_provider_response_and_invalid_clu(self):
        parser = OllamaLanguageUnderstandingParser(
            OllamaConfig("test-model"), request=lambda *_: b"not-json"
        )
        with self.assertRaises(MalformedOllamaOutputError):
            parser.parse("test", idle_context())

        parser, _ = self.parser({"message": {"content": "not-json"}})
        with self.assertRaises(InvalidOllamaResultError):
            parser.parse("test", idle_context())

    def test_connection_and_timeout_failures(self):
        def fail(*_):
            raise ConnectionError("offline")

        parser = OllamaLanguageUnderstandingParser(OllamaConfig("test-model"), request=fail)
        with self.assertRaises(OllamaConnectionError):
            parser.parse("test", idle_context())

        def timeout(*_):
            raise TimeoutError("timed out")

        parser = OllamaLanguageUnderstandingParser(OllamaConfig("test-model"), request=timeout)
        with self.assertRaises(OllamaTimeoutError):
            parser.parse("test", idle_context())

    def test_invalid_configuration(self):
        with self.assertRaises(InvalidOllamaConfigurationError):
            OllamaConfig("", base_url="http://localhost:11434")
        with self.assertRaises(InvalidOllamaConfigurationError):
            OllamaConfig("model", timeout=0)
        with patch.dict(os.environ, {"OLLAMA_MODEL": "", "OLLAMA_TIMEOUT": "bad"}, clear=True):
            with self.assertRaises(InvalidOllamaConfigurationError):
                OllamaConfig.from_environment()

    def test_health_check_model_presence(self):
        config = OllamaConfig("llama3.2", base_url="http://localhost:11434")
        check_ollama_health(config, lambda *_: json.dumps({"models": [{"name": "llama3.2:latest"}]}))
        with self.assertRaises(OllamaModelNotFoundError):
            check_ollama_health(config, lambda *_: json.dumps({"models": [{"name": "other"}]}))

    def test_http_model_not_found_is_distinct(self):
        error = urllib.error.HTTPError(
            "http://localhost:11434/api/chat", 404, "not found", {},
            io.BytesIO(json.dumps({"error": "model 'missing' not found"}).encode("utf-8")),
        )
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(OllamaModelNotFoundError):
                _http_request("http://localhost:11434/api/chat", {}, b"{}", 1)


if __name__ == "__main__":
    unittest.main()