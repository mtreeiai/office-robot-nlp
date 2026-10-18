"""Conversational language-understanding boundary for the office robot."""

import json
from dataclasses import dataclass
from enum import Enum
from typing import Literal, Mapping, Protocol, TypeAlias

from office_robot_nlp.domain.conversation import ConversationMode, ConversationState
from office_robot_nlp.domain.intent import Action, IntentType, ParsedIntent, Priority, UserDecision
from office_robot_nlp.validation.intent_validator import decode_parsed_intent


class UnderstandingType(str, Enum):
    TASK_COMMAND = "TASK_COMMAND"
    TASK_UPDATE = "TASK_UPDATE"
    CLARIFICATION_ANSWER = "CLARIFICATION_ANSWER"
    ROBOT_DECISION = "ROBOT_DECISION"
    SOCIAL = "SOCIAL"
    UNKNOWN = "UNKNOWN"


ClarificationField: TypeAlias = Literal["destination", "document"]


@dataclass(frozen=True)
class TaskCommandResult:
    parsedIntent: ParsedIntent
    type: Literal["TASK_COMMAND"] = UnderstandingType.TASK_COMMAND.value


@dataclass(frozen=True)
class TaskUpdateResult:
    recipientText: str | None = None
    destinationText: str | None = None
    documentText: str | None = None
    priority: Priority | None = None
    constraints: object | None = None
    type: Literal["TASK_UPDATE"] = UnderstandingType.TASK_UPDATE.value


@dataclass(frozen=True)
class ClarificationAnswerResult:
    field: ClarificationField
    value: str
    type: Literal["CLARIFICATION_ANSWER"] = UnderstandingType.CLARIFICATION_ANSWER.value


@dataclass(frozen=True)
class RobotDecisionResult:
    decision: UserDecision
    type: Literal["ROBOT_DECISION"] = UnderstandingType.ROBOT_DECISION.value


@dataclass(frozen=True)
class SocialResult:
    reason: str | None = None
    type: Literal["SOCIAL"] = UnderstandingType.SOCIAL.value


@dataclass(frozen=True)
class UnknownUtteranceResult:
    reason: str | None = None
    type: Literal["UNKNOWN"] = UnderstandingType.UNKNOWN.value


LanguageUnderstandingResult: TypeAlias = (
    TaskCommandResult
    | TaskUpdateResult
    | ClarificationAnswerResult
    | RobotDecisionResult
    | SocialResult
    | UnknownUtteranceResult
)
ConversationContext: TypeAlias = ConversationState


@dataclass(frozen=True)
class CLUDecodeResult:
    result: LanguageUnderstandingResult | None
    errors: tuple[str, ...] = ()


class LanguageUnderstandingParser(Protocol):
    def parse(
        self,
        text: str,
        context: ConversationState,
    ) -> LanguageUnderstandingResult:
        """Interpret text using only the explicitly supplied context."""


CLU_SYSTEM_INSTRUCTIONS = """You are a language-understanding component for an office delivery robot.

Your responsibility is to interpret what the user means.
You do not control the robot.
You do not plan paths.
You do not know physical world state unless it is explicitly supplied as conversation context.
You must not infer that a room is busy, free, open, or closed.

Classify each utterance as exactly one of:
TASK_COMMAND
TASK_UPDATE
CLARIFICATION_ANSWER
ROBOT_DECISION
SOCIAL
UNKNOWN

Use conversation context when interpreting short answers.
Do not invent missing information, map IDs, robot actions, coordinates, or free-form reasoning.
Do not partially execute multi-step or compound requests; classify unsupported sequences as UNKNOWN.
Only single-destination delivery is supported. "Giao tài liệu tới phòng 1 và phòng 2"
must be UNKNOWN; never truncate it to the first room or create multiple tasks.
Extract destinationText verbatim even for unregistered rooms; deterministic code resolves it.
Keep recipientText separate from destinationText. "anh Phong" is a recipient, not a room.
Use SOCIAL for greetings. Use TASK_UPDATE for changes to an existing pending task.
Return structured output only.

"Phòng 5" during AWAITING_TASK_INFORMATION(destination) is a clarification answer.
"Chờ đi" during AWAITING_ROBOT_DECISION([WAIT, CANCEL]) is a robot decision with decision WAIT.
"""


def build_clu_prompt(text: str, context: ConversationState) -> str:
    """Build explicit context for a future provider adapter."""
    context_data = {
        "state": context.mode.value,
        "awaitingField": context.awaitingField,
        "pendingIntent": context.pendingIntent,
        "taskId": context.activeTaskId,
        "reason": context.decisionReason.value if context.decisionReason else None,
        "allowedDecisions": [decision.value for decision in context.allowedDecisions],
    }
    return f"{CLU_SYSTEM_INSTRUCTIONS}\n\nContext:\n{context_data!r}\n\nUser text:\n{text}"


def _decode_payload(payload: object) -> object | None:
    if not isinstance(payload, str):
        return payload
    try:
        return json.loads(payload)
    except (TypeError, ValueError):
        return None


def _invalid(*errors: str) -> CLUDecodeResult:
    return CLUDecodeResult(None, tuple(dict.fromkeys(errors)))


def _decode_constraints(value: object) -> tuple[object | None, tuple[str, ...]]:
    if value is None:
        return None, ()
    if not isinstance(value, dict) or set(value) != {"ifBusy", "ifClosed"}:
        return None, ("INVALID_POLICY",)
    busy = value["ifBusy"]
    closed = value["ifClosed"]
    if busy is not None and busy not in {Action.WAIT.value, Action.ASK_USER.value}:
        return None, ("INVALID_POLICY",)
    if closed is not None and closed not in {
        Action.WAIT.value, Action.RETRY.value, Action.ASK_USER.value
    }:
        return None, ("INVALID_POLICY",)
    from office_robot_nlp.domain.intent import Constraints
    return Constraints(
        Action(busy) if busy is not None else None,
        Action(closed) if closed is not None else None,
    ), ()


def decode_language_understanding_result(
    payload: object,
    context: ConversationState,
) -> CLUDecodeResult:
    """Decode provider output and enforce context-dependent CLU safeguards."""
    payload = _decode_payload(payload)
    if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
        return _invalid("INVALID_CLU_OUTPUT")

    result_type = payload["type"]
    if result_type == UnderstandingType.TASK_COMMAND.value:
        if set(payload) != {"type", "parsedIntent"}:
            return _invalid("INVALID_CLU_OUTPUT")
        decoded = decode_parsed_intent(payload["parsedIntent"])
        if not decoded.validation.valid or decoded.intent is None:
            return _invalid(*(error.value for error in decoded.validation.errors))
        if decoded.intent.intent is not IntentType.DELIVER_DOCUMENT:
            return _invalid("UNSUPPORTED_INTENT")
        return CLUDecodeResult(TaskCommandResult(decoded.intent))

    if result_type == UnderstandingType.TASK_UPDATE.value:
        allowed = {
            "type", "recipientText", "destinationText", "documentText", "priority", "constraints"
        }
        if not set(payload).issubset(allowed) or set(payload) == {"type"}:
            return _invalid("INVALID_TASK_UPDATE")
        text_fields = [payload.get(name) for name in ("recipientText", "destinationText", "documentText")]
        if any(value is not None and (not isinstance(value, str) or not value.strip()) for value in text_fields):
            return _invalid("INVALID_TASK_UPDATE")
        priority_value = payload.get("priority")
        priority = None
        if priority_value is not None:
            try:
                priority = Priority(priority_value)
            except (TypeError, ValueError):
                return _invalid("INVALID_PRIORITY")
        constraints, constraint_errors = _decode_constraints(payload.get("constraints"))
        if constraint_errors:
            return _invalid(*constraint_errors)
        meaningful = any(value is not None for value in text_fields) or priority is not None
        meaningful = meaningful or constraints is not None and (
            constraints.ifBusy is not None or constraints.ifClosed is not None
        )
        if not meaningful:
            return _invalid("EMPTY_TASK_UPDATE")
        if context.mode is not ConversationMode.AWAITING_TASK_INFORMATION:
            return _invalid("TASK_UPDATE_NOT_EXPECTED")
        return CLUDecodeResult(TaskUpdateResult(
            recipientText=payload.get("recipientText"),
            destinationText=payload.get("destinationText"),
            documentText=payload.get("documentText"),
            priority=priority,
            constraints=constraints,
        ))

    if result_type == UnderstandingType.CLARIFICATION_ANSWER.value:
        if set(payload) != {"type", "field", "value"}:
            return _invalid("INVALID_CLU_OUTPUT")
        field = payload["field"]
        value = payload["value"]
        if context.mode is not ConversationMode.AWAITING_TASK_INFORMATION:
            return _invalid("CLARIFICATION_NOT_EXPECTED")
        if field != context.awaitingField or field not in {"destination", "document"}:
            return _invalid("INVALID_CLARIFICATION_FIELD")
        if not isinstance(value, str) or not value.strip():
            return _invalid("INVALID_CLARIFICATION_VALUE")
        return CLUDecodeResult(ClarificationAnswerResult(field, value))

    if result_type == UnderstandingType.ROBOT_DECISION.value:
        if set(payload) != {"type", "decision"}:
            return _invalid("INVALID_CLU_OUTPUT")
        decision = payload["decision"]
        if context.mode is not ConversationMode.AWAITING_ROBOT_DECISION:
            return _invalid("ROBOT_DECISION_NOT_EXPECTED")
        if not isinstance(decision, str) or decision not in {
            Action.WAIT.value, Action.RETRY.value, Action.CANCEL.value
        }:
            return _invalid("INVALID_ROBOT_DECISION")
        if Action(decision) not in context.allowedDecisions:
            return _invalid("ROBOT_DECISION_NOT_ALLOWED")
        return CLUDecodeResult(RobotDecisionResult(Action(decision)))

    if result_type == UnderstandingType.SOCIAL.value:
        if set(payload) - {"type", "reason"}:
            return _invalid("INVALID_CLU_OUTPUT")
        reason = payload.get("reason")
        if reason is not None and not isinstance(reason, str):
            return _invalid("INVALID_CLU_OUTPUT")
        return CLUDecodeResult(SocialResult(reason))

    if result_type == UnderstandingType.UNKNOWN.value:
        if set(payload) - {"type", "reason"}:
            return _invalid("INVALID_CLU_OUTPUT")
        reason = payload.get("reason")
        if reason is not None and not isinstance(reason, str):
            return _invalid("INVALID_CLU_OUTPUT")
        return CLUDecodeResult(UnknownUtteranceResult(reason))

    return _invalid("INVALID_CLU_TYPE")


class FakeLanguageUnderstandingParser:
    """Test double that decodes predefined provider-shaped responses."""

    def __init__(self, responses: Mapping[str, object]):
        self._responses = responses

    def parse(self, text: str, context: ConversationState) -> LanguageUnderstandingResult:
        decoded = decode_language_understanding_result(self._responses.get(text), context)
        if decoded.result is None:
            reason = ", ".join(decoded.errors) or "UNKNOWN"
            return UnknownUtteranceResult(reason)
        return decoded.result


__all__ = [
    "CLUDecodeResult",
    "CLU_SYSTEM_INSTRUCTIONS",
    "ClarificationAnswerResult",
    "ConversationContext",
    "FakeLanguageUnderstandingParser",
    "LanguageUnderstandingParser",
    "LanguageUnderstandingResult",
    "RobotDecisionResult",
    "SocialResult",
    "TaskUpdateResult",
    "TaskCommandResult",
    "UnderstandingType",
    "UnknownUtteranceResult",
    "build_clu_prompt",
    "decode_language_understanding_result",
]
