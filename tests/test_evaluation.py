import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from office_robot_nlp.clu import (
    ClarificationAnswerResult,
    RobotDecisionResult,
    TaskCommandResult,
)
from office_robot_nlp.domain.conversation import ConversationMode
from office_robot_nlp.domain.intent import Action, Constraints, IntentType, ParsedIntent, Priority
from office_robot_nlp.evaluation import (
    CASES,
    EvaluationMetrics,
    ProviderRuntime,
    actual_fields,
    evaluate_case,
    list_cases,
    normalize_text,
    parse_arguments,
    print_provider_error,
    print_summary,
    run_evaluation,
    select_cases,
)
from office_robot_nlp.gemini_adapter import GeminiProviderRequestError


class StaticParser:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def parse(self, text, context):
        if self.error:
            raise self.error
        return self.result


def command(destination="phòng B", priority=Priority.HIGH):
    return TaskCommandResult(ParsedIntent(
        IntentType.DELIVER_DOCUMENT,
        destination,
        "hồ sơ này",
        priority,
        Constraints(None, None),
        [],
    ))


def command_with_fields(destination="phòng A", priority=Priority.NORMAL):
    return TaskCommandResult(ParsedIntent(
        IntentType.DELIVER_DOCUMENT,
        destination,
        "tài liệu này",
        priority,
        Constraints(None, None),
        [],
    ))


class EvaluationHelperTests(unittest.TestCase):
    def test_complete_task_contributes_each_expected_field_metric(self):
        metrics = EvaluationMetrics()
        result = evaluate_case(
            {
                "expected": "TASK_COMMAND",
                "intent": "DELIVER_DOCUMENT",
                "destinationText": "phòng A",
                "priority": "NORMAL",
            },
            command_with_fields(),
            metrics,
        )
        self.assertTrue(result.passed)
        for name in ("category", "intent", "destinationText", "priority"):
            self.assertEqual(metrics.fields[name].passed, 1)
            self.assertEqual(metrics.fields[name].total, 1)

    def test_wrong_priority_fails_despite_correct_category(self):
        metrics = EvaluationMetrics()
        case = {"expected": "TASK_COMMAND", "priority": "HIGH"}
        result = evaluate_case(case, command(priority=Priority.NORMAL), metrics)
        self.assertFalse(result.passed)
        self.assertEqual(metrics.fields["priority"].passed, 0)

    def test_wrong_destination_fails_despite_correct_category(self):
        metrics = EvaluationMetrics()
        case = {"expected": "TASK_COMMAND", "destinationText": "phòng A"}
        result = evaluate_case(case, command(destination="phòng B"), metrics)
        self.assertFalse(result.passed)
        self.assertEqual(metrics.fields["destinationText"].passed, 0)

    def test_invented_destination_fails_and_is_counted(self):
        metrics = EvaluationMetrics()
        case = {"expected": "TASK_COMMAND", "destinationText": None}
        result = evaluate_case(case, command(destination="phòng A"), metrics)
        self.assertFalse(result.passed)
        self.assertEqual(metrics.invented_destination_count, 1)

    def test_raw_destination_phrase_is_not_invented(self):
        metrics = EvaluationMetrics()
        result = evaluate_case(
            {
                "input": "Đưa tài liệu tới phòng họp.",
                "expected": "TASK_COMMAND",
                "intent": "DELIVER_DOCUMENT",
                "destinationText": "phòng họp",
            },
            command(destination="phòng họp", priority=Priority.NORMAL),
            metrics,
        )
        self.assertTrue(result.passed)
        self.assertEqual(metrics.invented_destination_count, 0)

    def test_missing_destination_case_detects_hallucinated_room(self):
        metrics = EvaluationMetrics()
        result = evaluate_case(
            {
                "input": "Giao tài liệu này.",
                "expected": "TASK_COMMAND",
                "intent": "DELIVER_DOCUMENT",
                "destinationText": None,
                "missingFields": ["destination"],
            },
            command(destination="phòng A"),
            metrics,
        )
        self.assertFalse(result.passed)
        self.assertEqual(metrics.invented_destination_count, 1)

    def test_raw_phrase_with_wrong_room_is_invented(self):
        metrics = EvaluationMetrics()
        result = evaluate_case(
            {
                "input": "Đưa tài liệu tới phòng họp.",
                "expected": "TASK_COMMAND",
                "intent": "DELIVER_DOCUMENT",
                "destinationText": "phòng họp",
            },
            command(destination="phòng A"),
            metrics,
        )
        self.assertFalse(result.passed)
        self.assertEqual(metrics.invented_destination_count, 1)

    def test_unsupported_command_to_task_fails_and_is_counted(self):
        metrics = EvaluationMetrics()
        result = evaluate_case(
            {"expected": "UNKNOWN"}, command(), metrics
        )
        self.assertFalse(result.passed)
        self.assertEqual(metrics.unsupported_command_to_task_count, 1)

    def test_provider_error_is_excluded_from_semantic_metrics(self):
        runtime = ProviderRuntime("gemini", "gemini-3.1-flash-lite", "https://test", StaticParser(error=RuntimeError("offline")))
        metrics = run_evaluation(runtime, [{"input": "test", "mode": ConversationMode.IDLE, "expected": "UNKNOWN"}])
        self.assertEqual(metrics.total_cases, 1)
        self.assertEqual(metrics.provider_successes, 0)
        self.assertEqual(metrics.provider_errors, 1)
        self.assertEqual(metrics.evaluated_semantic_cases, 0)
        self.assertEqual(metrics.semantic_passes, 0)

    def test_text_normalization_is_conservative(self):
        self.assertEqual(normalize_text("Phòng A"), normalize_text(" phòng   a "))
        self.assertNotEqual(normalize_text("phòng A"), normalize_text("phòng B"))

    def test_decision_wait_comparison(self):
        metrics = EvaluationMetrics()
        result = evaluate_case(
            {"expected": "ROBOT_DECISION", "decision": "WAIT"},
            RobotDecisionResult(Action.WAIT), metrics,
        )
        self.assertTrue(result.passed)

    def test_clarification_field_and_value_comparison(self):
        metrics = EvaluationMetrics()
        result = evaluate_case(
            {"expected": "CLARIFICATION_ANSWER", "field": "destination", "value": "Phòng A"},
            ClarificationAnswerResult("destination", " phòng   a "), metrics,
        )
        self.assertTrue(result.passed)

    def test_runtime_provider_model_and_base_url_are_explicit(self):
        runtime = ProviderRuntime("gemini", "gemini-3.1-flash-lite", "https://configured", StaticParser())
        output = io.StringIO()
        with redirect_stdout(output):
            print_summary(runtime, EvaluationMetrics())
        text = output.getvalue()
        self.assertIn("Provider: gemini", text)
        self.assertIn("Model: gemini-3.1-flash-lite", text)
        self.assertIn("Base URL: https://configured", text)

    def test_provider_error_output_never_prints_api_key(self):
        secret = "test-secret-key"
        error = GeminiProviderRequestError(
            "request failed",
            status_code=503,
            provider_status="UNAVAILABLE",
            provider_code=503,
            provider_message=f"model failed with {secret}",
        )
        runtime = ProviderRuntime("gemini", "gemini-3.1-flash-lite", "https://configured", StaticParser())
        output = io.StringIO()
        with patch.dict(os.environ, {"GEMINI_API_KEY": secret}, clear=True), redirect_stdout(output):
            print_provider_error(runtime, error)
        text = output.getvalue()
        self.assertNotIn(secret, text)
        self.assertIn("HTTP status: 503", text)
        self.assertIn("Provider error:", text)

    def test_case_selection_by_id_and_group(self):
        selected = select_cases(case_id="missing-destination")
        self.assertEqual([case["id"] for case in selected], ["missing-destination"])
        decision_cases = select_cases(group="decision")
        self.assertEqual(len(decision_cases), 8)
        self.assertTrue(all(case["group"] == "decision" for case in decision_cases))

    def test_cli_list_does_not_construct_provider(self):
        arguments = parse_arguments(["--list"])
        self.assertTrue(arguments.list)
        listed_ids = {case["id"] for case in CASES}
        self.assertIn("basic-task", listed_ids)
        self.assertIn("missing-destination", listed_ids)

        output = io.StringIO()
        with redirect_stdout(output):
            list_cases()
        self.assertIn("basic-task", output.getvalue())
        self.assertIn("unsupported-youtube", output.getvalue())


if __name__ == "__main__":
    unittest.main()