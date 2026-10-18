import json
import os
import unittest
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
from office_robot_nlp.openai_adapter import (
    InvalidConfigurationError,
    InvalidProviderResultError,
    MalformedProviderOutputError,
    MissingAPIKeyError,
    OpenAIConfig,
    ProviderRequestError,
    RealLLMLanguageUnderstandingParser,
)


def idle_context() -> ConversationState:
    return ConversationState("conv_001", ConversationMode.IDLE, None, None, None, [])


def task_content(destination="phòng A", priority="NORMAL", if_busy=None):
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


class FakeProvider:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def __call__(self, endpoint, headers, body, timeout):
        self.calls.append((endpoint, headers, json.loads(body), timeout))
        return {"choices": [{"message": {"content": json.dumps(self.content)}}]}


class OpenAIAdapterTests(unittest.TestCase):
    def parser(self, content):
        provider = FakeProvider(content)
        parser = RealLLMLanguageUnderstandingParser(
            OpenAIConfig("test-key", model="test-model", endpoint="https://test.invalid"),
            request=provider,
        )
        return parser, provider

    def test_task_command_and_explicit_request_context(self):
        parser, provider = self.parser(task_content("phòng B", "HIGH", "WAIT"))
        result = parser.parse("Chuyển gấp hồ sơ này sang phòng B.", idle_context())
        self.assertIsInstance(result, TaskCommandResult)
        self.assertEqual(result.parsedIntent.destinationText, "phòng B")
        self.assertEqual(result.parsedIntent.priority.value, "HIGH")
        self.assertEqual(result.parsedIntent.constraints.ifBusy.value, "WAIT")
        request = provider.calls[0][2]
        self.assertEqual(request["messages"][1]["content"], "Chuyển gấp hồ sơ này sang phòng B.")
        self.assertIn('"state": "IDLE"', request["messages"][0]["content"])
        response_format = request["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        schema = response_format["json_schema"]
        self.assertTrue(schema["strict"])
        branches = schema["schema"]["oneOf"]
        branch_types = {
            branch["properties"]["type"]["enum"][0]: branch for branch in branches
        }
        self.assertEqual(
            set(branch_types),
            {"TASK_COMMAND", "TASK_UPDATE", "CLARIFICATION_ANSWER", "ROBOT_DECISION", "SOCIAL", "UNKNOWN"},
        )
        self.assertIn("parsedIntent", branch_types["TASK_COMMAND"]["properties"])
        self.assertIn("constraints", branch_types["TASK_COMMAND"]["properties"]["parsedIntent"]["properties"])
        self.assertIn("field", branch_types["CLARIFICATION_ANSWER"]["properties"])
        self.assertIn("decision", branch_types["ROBOT_DECISION"]["properties"])
        self.assertIn("reason", branch_types["UNKNOWN"]["properties"])

    def test_clarification_context_is_sent_and_decoded(self):
        context = ConversationState(
            "conv_001", ConversationMode.AWAITING_TASK_INFORMATION,
            None, "destination", None, [],
        )
        parser, provider = self.parser({
            "type": "CLARIFICATION_ANSWER", "field": "destination", "value": "Phòng A"
        })
        result = parser.parse("Phòng A.", context)
        self.assertEqual(result, ClarificationAnswerResult("destination", "Phòng A"))
        self.assertIn('"awaitingField": "destination"', provider.calls[0][2]["messages"][0]["content"])

    def test_robot_decision_context_is_sent_and_decoded(self):
        context = ConversationState(
            "conv_001", ConversationMode.AWAITING_ROBOT_DECISION,
            None, None, "task_001", [Action.WAIT, Action.CANCEL], DecisionReason.ROOM_BUSY,
        )
        parser, provider = self.parser({"type": "ROBOT_DECISION", "decision": "WAIT"})
        result = parser.parse("Chờ đi.", context)
        self.assertEqual(result, RobotDecisionResult(Action.WAIT))
        system_message = provider.calls[0][2]["messages"][0]["content"]
        self.assertIn('"allowedDecisions": ["WAIT", "CANCEL"]', system_message)
        self.assertIn('"reason": "ROOM_BUSY"', system_message)

    def test_unknown_is_decoded(self):
        parser, _ = self.parser({"type": "UNKNOWN", "reason": "unsupported"})
        self.assertEqual(
            parser.parse("Mở Youtube cho tôi.", idle_context()),
            UnknownUtteranceResult("unsupported"),
        )

    def test_malformed_provider_response_fails_safely(self):
        parser = RealLLMLanguageUnderstandingParser(
            OpenAIConfig("test-key"), request=lambda *_: b"not-json"
        )
        with self.assertRaises(MalformedProviderOutputError):
            parser.parse("test", idle_context())

    def test_invalid_structured_output_uses_existing_decoder(self):
        parser, _ = self.parser(task_content(priority="URGENT"))
        with self.assertRaises(InvalidProviderResultError) as raised:
            parser.parse("test", idle_context())
        self.assertEqual(raised.exception.errors, ("INVALID_PRIORITY",))

    def test_context_invalid_output_is_rejected(self):
        parser, _ = self.parser({"type": "ROBOT_DECISION", "decision": "WAIT"})
        with self.assertRaises(InvalidProviderResultError) as raised:
            parser.parse("Chờ đi.", idle_context())
        self.assertEqual(raised.exception.errors, ("ROBOT_DECISION_NOT_EXPECTED",))

    def test_provider_request_failure_is_wrapped(self):
        def fail(*_):
            raise OSError("offline")

        parser = RealLLMLanguageUnderstandingParser(OpenAIConfig("test-key"), request=fail)
        with self.assertRaises(ProviderRequestError):
            parser.parse("test", idle_context())

    def test_missing_configuration_is_clear(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(MissingAPIKeyError, "OPENAI_API_KEY"):
                OpenAIConfig.from_environment()

    def test_base_url_builds_chat_completions_endpoint(self):
        for base_url in ("https://api.example.test/v1", "https://api.example.test/v1/"):
            with self.subTest(base_url=base_url), patch.dict(
                os.environ,
                {"OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": base_url},
                clear=True,
            ):
                self.assertEqual(
                    OpenAIConfig.from_environment().endpoint,
                    "https://api.example.test/v1/chat/completions",
                )

    def test_invalid_configuration_has_distinct_error(self):
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "test-key", "OPENAI_MODEL": "", "OPENAI_BASE_URL": "https://api.example.test/v1"},
            clear=True,
        ):
            with self.assertRaises(InvalidConfigurationError):
                OpenAIConfig.from_environment()

        with self.assertRaises(InvalidConfigurationError):
            OpenAIConfig("test-key", timeout=0)


if __name__ == "__main__":
    unittest.main()