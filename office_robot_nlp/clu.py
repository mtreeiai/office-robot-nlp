"""Conversational language-understanding boundary for the office robot."""

import json
from dataclasses import dataclass
from enum import Enum
from typing import Literal, Mapping, Protocol, TypeAlias

from office_robot_nlp.domain.conversation import ConversationMode, ConversationState
from office_robot_nlp.domain.intent import Action, IntentType, ParsedIntent, Priority, UserDecision
from office_robot_nlp.domain.batch import AllocationStatus, BatchDraft, OrderPolicy
from office_robot_nlp.validation.intent_validator import decode_parsed_intent


class UnderstandingType(str, Enum):
    TASK_COMMAND = "TASK_COMMAND"
    BATCH_COMMAND = "BATCH_COMMAND"
    BATCH_ORDER = "BATCH_ORDER"
    BATCH_CONFIRM = "BATCH_CONFIRM"
    CANCEL_PENDING = "CANCEL_PENDING"
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
class BatchCommandResult:
    draft: BatchDraft
    type: Literal["BATCH_COMMAND"] = UnderstandingType.BATCH_COMMAND.value


@dataclass(frozen=True)
class BatchOrderResult:
    orderPolicy: OrderPolicy
    type: Literal["BATCH_ORDER"] = UnderstandingType.BATCH_ORDER.value


@dataclass(frozen=True)
class BatchConfirmResult:
    type: Literal["BATCH_CONFIRM"] = UnderstandingType.BATCH_CONFIRM.value


@dataclass(frozen=True)
class CancelPendingResult:
    type: Literal["CANCEL_PENDING"] = UnderstandingType.CANCEL_PENDING.value


@dataclass(frozen=True)
class TaskUpdateResult:
    recipientText: str | None = None
    destinationText: str | None = None
    documentText: str | None = None
    priority: Priority | None = None
    constraints: object | None = None
    type: Literal["TASK_UPDATE"] = UnderstandingType.TASK_UPDATE.value
    itemIndex: int | None = None


@dataclass(frozen=True)
class ClarificationAnswerResult:
    field: ClarificationField
    value: str
    type: Literal["CLARIFICATION_ANSWER"] = UnderstandingType.CLARIFICATION_ANSWER.value
    itemIndex: int | None = None


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
    | BatchCommandResult
    | BatchOrderResult
    | BatchConfirmResult
    | CancelPendingResult
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
BATCH_COMMAND
BATCH_ORDER
BATCH_CONFIRM
CANCEL_PENDING
TASK_UPDATE
CLARIFICATION_ANSWER
ROBOT_DECISION
SOCIAL
UNKNOWN

Use conversation context when interpreting short answers.
Do not invent missing information, map IDs, robot actions, coordinates, or free-form reasoning.
Do not partially execute multi-step or compound requests; classify unsupported sequences as UNKNOWN.
Use TASK_COMMAND for one delivery, preserving its existing parsedIntent contract.
Use BATCH_COMMAND for multiple physical documents or separate explicitly requested copies.
Its tasks array contains one object per item in the original utterance order:
documentText, recipientText, destinationText, priority, constraints (ifBusy, ifClosed).
Never return task IDs, document IDs, compartment assignments, room IDs or coordinates.
Preserve each document/recipient/destination association, including different documents for the same room.
Do not merge distinct physical documents into a single item. Do not truncate a list.
Use NORMAL unless urgency is explicit for that item; "Ưu tiên hợp đồng" makes only that item HIGH.
Task priority and batch ordering are independent semantic fields.
orderPolicy is PRIORITY by default. STRICT_ORDER requires an explicit requirement
for a fixed sequence covering the ENTIRE delivery batch, not just one favored task.
"ưu tiên hợp đồng trước", "giao hợp đồng trước", "cái này gấp", and "ưu tiên báo cáo"
express per-task priority; by themselves they do NOT fix the order of all deliveries.
Set only the favored task HIGH; leave the other tasks NORMAL unless separately specified.
A list of deliveries, mention order, or the word "trước" alone is not a strict-order mandate.
Always keep the tasks array in original mention order, even if a later item is HIGH.
Use STRICT_ORDER for an explicit whole-batch mandate such as "giao lần lượt theo đúng
thứ tự đã nêu" or "bắt buộc giữ nguyên toàn bộ thứ tự giao, không được đổi thứ tự".
Strict ordering alone does not make the first task HIGH. Do not discard a legitimate
whole-batch STRICT_ORDER mandate just because the utterance also mentions priority.
If strict sequence and task priorities conflict, preserve BOTH in the candidate;
do not resolve the conflict, silently change orderPolicy, or reorder tasks.
Deterministic dialogue will ask the user which policy to keep.

Priority-only example:
User: "Giao hợp đồng đến phòng 2, báo cáo đến phòng 5 và hồ sơ đến phòng 7. Ưu tiên hợp đồng trước."
Expected: BATCH_COMMAND, orderPolicy=PRIORITY; tasks in mention order:
hợp đồng -> phòng 2 -> HIGH; báo cáo -> phòng 5 -> NORMAL; hồ sơ -> phòng 7 -> NORMAL.
Explicit whole-batch strict-order example:
User: "Giao hợp đồng đến phòng 2, báo cáo đến phòng 5 và hồ sơ đến phòng 7. Giao lần lượt theo đúng thứ tự đã nêu."
Expected: BATCH_COMMAND, orderPolicy=STRICT_ORDER; all priorities NORMAL; original task order.
Conflicting example:
User: "Giao hợp đồng đến phòng 2, báo cáo đến phòng 5 và hồ sơ đến phòng 7. Bắt buộc giữ đúng toàn bộ thứ tự này, nhưng ưu tiên hồ sơ trước."
Expected: BATCH_COMMAND, orderPolicy=STRICT_ORDER; priorities NORMAL, NORMAL, HIGH;
original task order. Keep this conflict for clarification instead of silently overriding either instruction.

Extract ifBusy/ifClosed only from an explicit user policy for that condition.
Without such a policy return null for the corresponding field, including all three examples above.
ASK_USER is an explicit policy (e.g. "nếu phòng bận thì hỏi tôi"), not a default.
Do not infer ASK_USER from a missing condition, unknown room state, urgency, or strict order.
Keep explicit WAIT, RETRY, or ASK_USER choices; never replace them with an invented preference.
allocationStatus is CLEAR only when separate items/copies and their destinations are unambiguous.
"Giao tài liệu đến phòng 2 và phòng 5" is BATCH_COMMAND with allocationStatus AMBIGUOUS:
do not invent copies, different documents or sequential delivery of one physical document.
For unclear allocation retain known destinations and document text in the candidate and ask for clarification.
"Giao hợp đồng đến phòng 2 và báo cáo đến phòng 5" is CLEAR with two items.
"Giao hợp đồng và báo cáo đến phòng 2" is CLEAR only if two separate documents are explicitly meant.
Unsupported actions such as delivering and then bringing the document back are UNKNOWN;
never discard the unsupported action while extracting supported deliveries.
While awaiting a batch item, CLARIFICATION_ANSWER or TASK_UPDATE changes only that item.
For an explicitly identified batch item include its zero-based itemIndex. If the user
targets a different item than awaitingItemIndex, return UNKNOWN instead of changing the wrong item.
While awaiting allocation, use BATCH_COMMAND to restate each document/copy and its destination
in the existing order. Do not rewrite already known destinations or other task details.
While awaiting order, BATCH_ORDER with orderPolicy PRIORITY or STRICT_ORDER is an explicit user choice.
Use BATCH_CONFIRM only to retry registration when awaiting confirmation.
Use CANCEL_PENDING for explicit cancellation of a pending request/draft, never for an executing task.
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
        **batch_semantic_context(context),
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


def batch_semantic_context(context: ConversationState) -> dict[str, object]:
    if context.pendingBatch is None:
        return {}
    pending = context.pendingBatch
    return {
        "awaitingField": pending.awaitingField,
        "awaitingItemIndex": pending.awaitingIndex,
        "pendingBatch": {
            "orderPolicy": pending.draft.orderPolicy.value,
            "allocationStatus": pending.draft.allocationStatus.value,
            "tasks": [
                {"sequenceIndex": index, "documentText": task.documentText,
                 "recipientText": task.recipientText, "destinationText": task.destinationText,
                 "priority": task.priority.value,
                 "constraints": {"ifBusy": task.constraints.ifBusy.value if task.constraints.ifBusy else None,
                                 "ifClosed": task.constraints.ifClosed.value if task.constraints.ifClosed else None}}
                for index, task in enumerate(pending.draft.tasks)
            ],
        },
    }


def _decode_batch_command(payload: dict) -> CLUDecodeResult:
    if (set(payload) - {"type", "tasks", "orderPolicy", "allocationStatus"}
            or not isinstance(payload.get("tasks"), list) or not payload["tasks"]):
        return _invalid("INVALID_BATCH_COMMAND")
    tasks = []
    for index, item in enumerate(payload["tasks"]):
        if (not isinstance(item, dict) or not {"documentText", "destinationText"}.issubset(item)
                or set(item) - {"documentText", "destinationText", "recipientText", "priority", "constraints"}):
            return _invalid(f"INVALID_BATCH_ITEM_{index}")
        decoded = decode_parsed_intent({
            "intent": "DELIVER_DOCUMENT", "documentText": item["documentText"],
            "destinationText": item["destinationText"], "recipientText": item.get("recipientText"),
            "priority": item.get("priority", "NORMAL"),
            "constraints": item.get("constraints", {"ifBusy": None, "ifClosed": None}),
            "missingFields": [],
        })
        if not decoded.validation.valid or decoded.intent is None:
            return _invalid(f"INVALID_BATCH_ITEM_{index}", *(error.value for error in decoded.validation.errors))
        tasks.append(decoded.intent)
    try:
        order = OrderPolicy(payload.get("orderPolicy", "PRIORITY"))
        documents = [" ".join((task.documentText or "").casefold().split()) for task in tasks]
        default_allocation = "AMBIGUOUS" if len(documents) != len(set(documents)) else "CLEAR"
        allocation = AllocationStatus(payload.get("allocationStatus", default_allocation))
    except (ValueError, TypeError):
        return _invalid("INVALID_BATCH_POLICY")
    return CLUDecodeResult(BatchCommandResult(BatchDraft(tuple(tasks), order, allocation)))


def _decode_constraints(value: object) -> tuple[object | None, tuple[str, ...]]:
    if value is None:
        return None, ()
    if not isinstance(value, dict) or set(value) != {"ifBusy", "ifClosed"}:
        return None, ("INVALID_POLICY",)
    busy = value["ifBusy"]
    closed = value["ifClosed"]
    if busy is not None and (not isinstance(busy, str) or busy not in {Action.WAIT.value, Action.ASK_USER.value}):
        return None, ("INVALID_POLICY",)
    if closed is not None and (not isinstance(closed, str) or closed not in {
        Action.WAIT.value, Action.RETRY.value, Action.ASK_USER.value
    }):
        return None, ("INVALID_POLICY",)
    from office_robot_nlp.domain.intent import Constraints
    return Constraints(
        Action(busy) if busy is not None else None,
        Action(closed) if closed is not None else None,
    ), ()


def _item_target_matches(index: object, context: ConversationState) -> bool:
    return index is None or (type(index) is int and context.pendingBatch is not None
                             and index == context.pendingBatch.awaitingIndex)


def decode_language_understanding_result(
    payload: object,
    context: ConversationState,
) -> CLUDecodeResult:
    """Decode provider output and enforce context-dependent CLU safeguards."""
    payload = _decode_payload(payload)
    if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
        return _invalid("INVALID_CLU_OUTPUT")

    result_type = payload["type"]
    if result_type == UnderstandingType.BATCH_COMMAND.value:
        return _decode_batch_command(payload)
    if result_type == UnderstandingType.CANCEL_PENDING.value:
        if set(payload) != {"type"} or context.mode not in {
            ConversationMode.AWAITING_TASK_INFORMATION, ConversationMode.AWAITING_BATCH_INFORMATION,
        }:
            return _invalid("CANCELLATION_NOT_EXPECTED")
        return CLUDecodeResult(CancelPendingResult())
    if result_type in {UnderstandingType.BATCH_ORDER.value, UnderstandingType.BATCH_CONFIRM.value}:
        expected_field = "order" if result_type == UnderstandingType.BATCH_ORDER.value else "confirmation"
        if context.pendingBatch is None or context.pendingBatch.awaitingField != expected_field:
            return _invalid("BATCH_ANSWER_NOT_EXPECTED")
        if expected_field == "confirmation":
            return CLUDecodeResult(BatchConfirmResult()) if set(payload) == {"type"} else _invalid("INVALID_BATCH_ANSWER")
        if set(payload) != {"type", "orderPolicy"}:
            return _invalid("INVALID_BATCH_ANSWER")
        try:
            return CLUDecodeResult(BatchOrderResult(OrderPolicy(payload["orderPolicy"])))
        except (ValueError, TypeError):
            return _invalid("INVALID_BATCH_POLICY")
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
            "type", "recipientText", "destinationText", "documentText", "priority", "constraints", "itemIndex"
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
        if context.mode not in {ConversationMode.AWAITING_TASK_INFORMATION, ConversationMode.AWAITING_BATCH_INFORMATION}:
            return _invalid("TASK_UPDATE_NOT_EXPECTED")
        if context.pendingBatch is not None and context.pendingBatch.awaitingIndex is None:
            return _invalid("BATCH_ITEM_NOT_SELECTED")
        if not _item_target_matches(payload.get("itemIndex"), context):
            return _invalid("INVALID_BATCH_ITEM_TARGET")
        return CLUDecodeResult(TaskUpdateResult(
            recipientText=payload.get("recipientText"),
            destinationText=payload.get("destinationText"),
            documentText=payload.get("documentText"),
            priority=priority,
            constraints=constraints,
            itemIndex=payload.get("itemIndex"),
        ))

    if result_type == UnderstandingType.CLARIFICATION_ANSWER.value:
        if not {"type", "field", "value"}.issubset(payload) or set(payload) - {"type", "field", "value", "itemIndex"}:
            return _invalid("INVALID_CLU_OUTPUT")
        field = payload["field"]
        value = payload["value"]
        if context.mode not in {ConversationMode.AWAITING_TASK_INFORMATION, ConversationMode.AWAITING_BATCH_INFORMATION}:
            return _invalid("CLARIFICATION_NOT_EXPECTED")
        if field != context.awaitingField or field not in {"destination", "document"}:
            return _invalid("INVALID_CLARIFICATION_FIELD")
        if not isinstance(value, str) or not value.strip():
            return _invalid("INVALID_CLARIFICATION_VALUE")
        if not _item_target_matches(payload.get("itemIndex"), context):
            return _invalid("INVALID_BATCH_ITEM_TARGET")
        return CLUDecodeResult(ClarificationAnswerResult(field, value, itemIndex=payload.get("itemIndex")))

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
    "BatchCommandResult",
    "BatchOrderResult",
    "BatchConfirmResult",
    "CancelPendingResult",
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
    "batch_semantic_context",
    "decode_language_understanding_result",
]
