"""Trustworthy, opt-in evaluation helpers for live CLU providers."""

import os
import re
import argparse
from dataclasses import dataclass, field
from typing import Any

from office_robot_nlp.clu import (
    ClarificationAnswerResult,
    LanguageUnderstandingParser,
    RobotDecisionResult,
    TaskCommandResult,
    UnknownUtteranceResult,
)
from office_robot_nlp.domain.conversation import ConversationMode, ConversationState
from office_robot_nlp.domain.intent import Action
from office_robot_nlp.domain.robot_event import DecisionReason
from office_robot_nlp.gemini_adapter import GeminiAdapterError, GeminiLanguageUnderstandingParser
from office_robot_nlp.ollama_adapter import (
    OllamaAdapterError,
    OllamaConfig,
    OllamaLanguageUnderstandingParser,
    OllamaModelNotFoundError,
    check_ollama_health,
)
from office_robot_nlp.openai_adapter import RealLLMLanguageUnderstandingParser


CASES: list[dict[str, Any]] = [
    {"id": "basic-task", "group": "task", "input": "Đưa tài liệu tới phòng A.", "mode": ConversationMode.IDLE,
     "expected": "TASK_COMMAND", "intent": "DELIVER_DOCUMENT", "destinationText": "phòng A", "priority": "NORMAL"},
    {"id": "paraphrased-task", "group": "task", "input": "Phiền robot mang bộ hồ sơ này sang phòng họp A giúp tôi.", "mode": ConversationMode.IDLE,
     "expected": "TASK_COMMAND", "intent": "DELIVER_DOCUMENT"},
    {"id": "high-priority-task", "group": "task", "input": "Chuyển gấp tài liệu này sang phòng B.", "mode": ConversationMode.IDLE,
     "expected": "TASK_COMMAND", "intent": "DELIVER_DOCUMENT", "destinationText": "phòng B", "priority": "HIGH"},
    {"id": "busy-wait-policy", "group": "task", "input": "Đưa tài liệu tới phòng A, nếu phòng đang bận thì chờ.", "mode": ConversationMode.IDLE,
     "expected": "TASK_COMMAND", "intent": "DELIVER_DOCUMENT", "ifBusy": "WAIT"},
    {"id": "raw-destination-text", "group": "task", "input": "Đưa tài liệu tới phòng họp.", "mode": ConversationMode.IDLE,
     "expected": "TASK_COMMAND", "intent": "DELIVER_DOCUMENT", "destinationText": "phòng họp"},
    {"id": "missing-destination", "group": "task", "input": "Giao tài liệu này.", "mode": ConversationMode.IDLE,
     "expected": "TASK_COMMAND", "intent": "DELIVER_DOCUMENT", "destinationText": None,
     "missingFields": ["destination"]},
    {"id": "clarification-destination", "group": "clarification", "input": "Phòng A.", "mode": ConversationMode.AWAITING_TASK_INFORMATION,
     "expected": "CLARIFICATION_ANSWER", "field": "destination", "value": "Phòng A"},
    {"id": "idle-destination-fragment", "group": "safety", "input": "Phòng A.", "mode": ConversationMode.IDLE,
     "expected": "UNKNOWN"},
    {"id": "decision-wait-1", "group": "decision", "input": "Chờ đi.", "mode": ConversationMode.AWAITING_ROBOT_DECISION,
     "expected": "ROBOT_DECISION", "decision": "WAIT"},
    {"id": "decision-wait-2", "group": "decision", "input": "Cứ đợi ở đó.", "mode": ConversationMode.AWAITING_ROBOT_DECISION,
     "expected": "ROBOT_DECISION", "decision": "WAIT"},
    {"id": "decision-wait-3", "group": "decision", "input": "Đợi một lúc nhé.", "mode": ConversationMode.AWAITING_ROBOT_DECISION,
     "expected": "ROBOT_DECISION", "decision": "WAIT"},
    {"id": "decision-wait-4", "group": "decision", "input": "Thôi cứ chờ trước đi.", "mode": ConversationMode.AWAITING_ROBOT_DECISION,
     "expected": "ROBOT_DECISION", "decision": "WAIT"},
    {"id": "decision-cancel-1", "group": "decision", "input": "Hủy đi.", "mode": ConversationMode.AWAITING_ROBOT_DECISION,
     "expected": "ROBOT_DECISION", "decision": "CANCEL"},
    {"id": "decision-cancel-2", "group": "decision", "input": "Không cần giao nữa.", "mode": ConversationMode.AWAITING_ROBOT_DECISION,
     "expected": "ROBOT_DECISION", "decision": "CANCEL"},
    {"id": "decision-cancel-3", "group": "decision", "input": "Thôi bỏ nhiệm vụ này.", "mode": ConversationMode.AWAITING_ROBOT_DECISION,
     "expected": "ROBOT_DECISION", "decision": "CANCEL"},
    {"id": "decision-cancel-4", "group": "decision", "input": "Không cần nữa đâu.", "mode": ConversationMode.AWAITING_ROBOT_DECISION,
     "expected": "ROBOT_DECISION", "decision": "CANCEL"},
    {"id": "unsupported-youtube", "group": "safety", "input": "Mở Youtube cho tôi.", "mode": ConversationMode.IDLE,
     "expected": "UNKNOWN"},
]


@dataclass(frozen=True)
class ProviderRuntime:
    provider: str
    model: str
    base_url: str
    parser: LanguageUnderstandingParser


@dataclass
class Metric:
    passed: int = 0
    total: int = 0


@dataclass
class EvaluationMetrics:
    total_cases: int = 0
    provider_successes: int = 0
    provider_errors: int = 0
    evaluated_semantic_cases: int = 0
    semantic_passes: int = 0
    semantic_failures: int = 0
    invented_destination_count: int = 0
    unsupported_command_to_task_count: int = 0
    fields: dict[str, Metric] = field(default_factory=lambda: {
        "category": Metric(),
        "intent": Metric(),
        "destinationText": Metric(),
        "priority": Metric(),
        "ifBusy": Metric(),
        "missingFields": Metric(),
        "decision": Metric(),
        "field": Metric(),
        "value": Metric(),
    })


@dataclass(frozen=True)
class CaseEvaluation:
    passed: bool
    expected: dict[str, Any]
    actual: dict[str, Any]
    failures: tuple[str, ...]


def conversation_context(mode: ConversationMode) -> ConversationState:
    if mode is ConversationMode.AWAITING_TASK_INFORMATION:
        return ConversationState("live", mode, None, "destination", None, [])
    if mode is ConversationMode.AWAITING_ROBOT_DECISION:
        return ConversationState(
            "live", mode, None, None, "task_live", [Action.WAIT, Action.CANCEL],
            DecisionReason.ROOM_BUSY,
        )
    return ConversationState("live", ConversationMode.IDLE, None, None, None, [])


def select_cases(
    *,
    case_id: str | None = None,
    group: str | None = None,
    all_cases: bool = False,
) -> list[dict[str, Any]]:
    if sum(value is not None for value in (case_id, group)) + int(all_cases) > 1:
        raise ValueError("Choose only one of --case, --group, or --all.")
    if case_id:
        selected = [case for case in CASES if case["id"] == case_id]
        if not selected:
            raise ValueError(f"Unknown case id: {case_id}")
        return selected
    if group:
        selected = [case for case in CASES if case["group"] == group]
        if not selected:
            raise ValueError(f"Unknown case group: {group}")
        return selected
    if all_cases:
        return list(CASES)
    return [CASES[0]]


def list_cases() -> None:
    for case in CASES:
        print(f"{case['id']}\t{case['group']}\t{case['input']}")


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run sequential live CLU evaluation.")
    parser.add_argument("--list", action="store_true", help="List case IDs without contacting a provider.")
    parser.add_argument("--case", choices=[case["id"] for case in CASES])
    parser.add_argument("--group", choices=sorted({case["group"] for case in CASES}))
    parser.add_argument("--all", action="store_true", dest="all_cases")
    return parser.parse_args(argv)


def build_runtime(provider: str | None = None) -> ProviderRuntime:
    selected = (provider or os.environ.get("CLU_PROVIDER", "gemini")).casefold()
    if selected == "gemini":
        parser = GeminiLanguageUnderstandingParser()
        config = parser._config
        return ProviderRuntime(selected, config.model, config.endpoint.rsplit("/interactions", 1)[0], parser)
    if selected == "openai":
        parser = RealLLMLanguageUnderstandingParser()
        config = parser._config
        return ProviderRuntime(selected, config.model, config.endpoint.rsplit("/chat/completions", 1)[0], parser)
    if selected == "ollama":
        config = OllamaConfig.from_environment()
        check_ollama_health(config)
        return ProviderRuntime(selected, config.model, config.base_url, OllamaLanguageUnderstandingParser(config=config))
    raise ValueError("CLU_PROVIDER must be 'ollama', 'gemini', or 'openai'.")


def category(result: object) -> str:
    if isinstance(result, TaskCommandResult):
        return "TASK_COMMAND"
    if isinstance(result, ClarificationAnswerResult):
        return "CLARIFICATION_ANSWER"
    if isinstance(result, RobotDecisionResult):
        return "ROBOT_DECISION"
    if isinstance(result, UnknownUtteranceResult):
        return "UNKNOWN"
    return type(result).__name__


def normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    return " ".join(value.strip(" \t\r\n.,!?;:。！？，、").casefold().split())


def values_equal(expected: Any, actual: Any) -> bool:
    if isinstance(expected, str) or isinstance(actual, str):
        return normalize_text(expected) == normalize_text(actual)
    return expected == actual


def actual_fields(result: object) -> dict[str, Any]:
    if isinstance(result, TaskCommandResult):
        intent = result.parsedIntent
        return {
            "category": "TASK_COMMAND",
            "intent": intent.intent.value,
            "destinationText": intent.destinationText,
            "documentText": intent.documentText,
            "priority": intent.priority.value,
            "ifBusy": intent.constraints.ifBusy.value if intent.constraints.ifBusy else None,
            "ifClosed": intent.constraints.ifClosed.value if intent.constraints.ifClosed else None,
            "missingFields": intent.missingFields,
        }
    if isinstance(result, ClarificationAnswerResult):
        return {"category": "CLARIFICATION_ANSWER", "field": result.field, "value": result.value}
    if isinstance(result, RobotDecisionResult):
        return {"category": "ROBOT_DECISION", "decision": result.decision.value}
    if isinstance(result, UnknownUtteranceResult):
        return {"category": "UNKNOWN"}
    return {"category": category(result)}


def expected_fields(case: dict[str, Any]) -> dict[str, Any]:
    fields = {"category": case["expected"]}
    if case["expected"] == "TASK_COMMAND":
        for name in ("intent", "destinationText", "documentText", "priority", "ifBusy", "ifClosed", "missingFields"):
            if name in case:
                fields[name] = case[name]
    elif case["expected"] == "CLARIFICATION_ANSWER":
        fields.update({"field": case["field"], "value": case["value"]})
    elif case["expected"] == "ROBOT_DECISION":
        fields["decision"] = case["decision"]
    return fields


def evaluate_case(case: dict[str, Any], result: object, metrics: EvaluationMetrics) -> CaseEvaluation:
    expected = expected_fields(case)
    actual = actual_fields(result)
    failures: list[str] = []
    for name, expected_value in expected.items():
        metric = metrics.fields[name]
        metric.total += 1
        actual_value = actual.get(name)
        if values_equal(expected_value, actual_value):
            metric.passed += 1
        else:
            failures.append(f"{name}: expected {expected_value!r}, actual {actual_value!r}")

    if expected["category"] == "UNKNOWN" and isinstance(result, TaskCommandResult):
        metrics.unsupported_command_to_task_count += 1
        failures.append("unsupported command produced TASK_COMMAND")
    if "destinationText" in expected and expected["destinationText"] is None:
        actual_destination = actual.get("destinationText")
        normalized_input = normalize_text(case.get("input"))
        normalized_destination = normalize_text(actual_destination)
        if (
            isinstance(normalized_destination, str)
            and normalized_destination
            and (normalized_input is None or normalized_destination not in normalized_input)
        ):
            metrics.invented_destination_count += 1
            if "invented destination" not in " ".join(failures):
                failures.append("invented destination")

    if "destinationText" in expected and isinstance(expected["destinationText"], str):
        actual_destination = normalize_text(actual.get("destinationText"))
        expected_destination = normalize_text(expected["destinationText"])
        normalized_input = normalize_text(case.get("input")) or ""
        if (
            isinstance(actual_destination, str)
            and actual_destination
            and actual_destination not in normalized_input
            and actual_destination != expected_destination
        ):
            metrics.invented_destination_count += 1
            if "invented destination" not in " ".join(failures):
                failures.append("invented destination")

    metrics.evaluated_semantic_cases += 1
    if failures:
        metrics.semantic_failures += 1
    else:
        metrics.semantic_passes += 1
    return CaseEvaluation(not failures, expected, actual, tuple(failures))


def _provider_error_type(error: BaseException) -> str:
    if error.__class__.__name__.endswith("TimeoutError") or isinstance(error.__cause__, TimeoutError):
        return "TIMEOUT"
    if error.__class__.__name__.endswith("MalformedOutputError"):
        return "MALFORMED_OUTPUT"
    if error.__class__.__name__.endswith("ResultError"):
        return "INVALID_CLU_RESULT"
    return "NETWORK_ERROR"


def _provider_message(error: BaseException) -> str:
    message = str(error)
    for secret in (os.environ.get("GEMINI_API_KEY", ""), os.environ.get("OPENAI_API_KEY", "")):
        if secret:
            message = message.replace(secret, "<redacted>")
    return message


def print_provider_error(runtime: ProviderRuntime, error: BaseException) -> None:
    print("Provider error:")
    print(f"  provider: {runtime.provider}")
    print(f"  model: {runtime.model}")
    if hasattr(error, "status_code") and getattr(error, "status_code") is not None:
        print(f"  HTTP status: {error.status_code}")
        if getattr(error, "provider_code", None) is not None:
            print(f"  code: {error.provider_code}")
        if getattr(error, "provider_message", None):
            print(f"  message: {_provider_message(error.provider_message)}")
    else:
        print(f"  type: {_provider_error_type(error)}")
        if str(error):
            print(f"  message: {_provider_message(error)}")
    references = set(re.findall(
        r"\b(?:gemini|gpt|llama|qwen|mistral|claude)[\w.:-]*\d[\w.:-]*\b",
        _provider_message(error),
        re.I,
    ))
    mismatches = sorted(reference for reference in references if reference.casefold() != runtime.model.casefold())
    if mismatches:
        print("WARNING:")
        print(f"Configured model: {runtime.model}")
        print(f"Provider message references: {', '.join(mismatches)}")


def _format_fields(fields: dict[str, Any]) -> None:
    for name, value in fields.items():
        print(f"  {name}: {value}")


def run_evaluation(runtime: ProviderRuntime, cases: list[dict[str, Any]] = CASES) -> EvaluationMetrics:
    metrics = EvaluationMetrics(total_cases=len(cases))
    for case in cases:
        print(f"Input:\n{case['input']}")
        print(f"Context:\n{case['mode'].value}")
        print("Expected:")
        _format_fields(expected_fields(case))
        try:
            result = runtime.parser.parse(case["input"], conversation_context(case["mode"]))
        except (GeminiAdapterError, OllamaAdapterError, RuntimeError) as error:
            metrics.provider_errors += 1
            print("Actual:\n  provider: ERROR")
            print("Result: ERROR")
            print_provider_error(runtime, error)
            print()
            continue
        metrics.provider_successes += 1
        evaluation = evaluate_case(case, result, metrics)
        print("Actual:")
        _format_fields(evaluation.actual)
        print(f"Result: {'PASS' if evaluation.passed else 'FAIL'}")
        if evaluation.failures:
            print("  Field failures:")
            for failure in evaluation.failures:
                print(f"  - {failure}")
        print()
    return metrics


def _ratio(metric: Metric) -> str:
    return f"{metric.passed}/{metric.total}" if metric.total else "0/0"


def print_summary(runtime: ProviderRuntime, metrics: EvaluationMetrics) -> None:
    print("Summary")
    print(f"Provider: {runtime.provider}")
    print(f"Model: {runtime.model}")
    print(f"Base URL: {runtime.base_url}")
    print(f"Total Cases: {metrics.total_cases}")
    print(f"Provider Successes: {metrics.provider_successes}")
    print(f"Provider Errors: {metrics.provider_errors}")
    print(f"Provider Success Rate: {metrics.provider_successes}/{metrics.total_cases}")
    print(f"Evaluated Semantic Cases: {metrics.evaluated_semantic_cases}")
    print(f"Semantic Passes: {metrics.semantic_passes}")
    print(f"Semantic Failures: {metrics.semantic_failures}")
    print(f"Semantic Accuracy: {metrics.semantic_passes}/{metrics.evaluated_semantic_cases}")
    print(f"Category Accuracy: {_ratio(metrics.fields['category'])}")
    print(f"Intent Accuracy: {_ratio(metrics.fields['intent'])}")
    print(f"Destination Accuracy: {_ratio(metrics.fields['destinationText'])}")
    print(f"Priority Accuracy: {_ratio(metrics.fields['priority'])}")
    print(f"Busy Policy Accuracy: {_ratio(metrics.fields['ifBusy'])}")
    print(f"Decision Accuracy: {_ratio(metrics.fields['decision'])}")
    print(f"Invented Destination Count: {metrics.invented_destination_count}")
    print(f"Unsupported Command -> Task Count: {metrics.unsupported_command_to_task_count}")


def main(argv: list[str] | None = None) -> None:
    arguments = parse_arguments(argv)
    if arguments.list:
        list_cases()
        return
    try:
        selected_cases = select_cases(
            case_id=arguments.case,
            group=arguments.group,
            all_cases=arguments.all_cases,
        )
    except ValueError as error:
        print(f"ERROR: {error}")
        return
    try:
        runtime = build_runtime()
    except OllamaModelNotFoundError as error:
        print("Provider: ollama")
        print("OLLAMA MODEL NOT FOUND")
        print(_provider_message(error))
        return
    except OllamaAdapterError as error:
        print("Provider: ollama")
        if error.__class__.__name__.endswith("ConnectionError"):
            print("OLLAMA SERVER NOT AVAILABLE")
        else:
            print(f"ERROR: {_provider_message(error)}")
        return
    except (GeminiAdapterError, RuntimeError, ValueError) as error:
        print(f"ERROR: {_provider_message(error)}")
        return

    print(f"Provider: {runtime.provider}")
    print(f"Model: {runtime.model}")
    print(f"Base URL: {runtime.base_url}")
    metrics = run_evaluation(runtime, selected_cases)
    print_summary(runtime, metrics)


if __name__ == "__main__":
    main()