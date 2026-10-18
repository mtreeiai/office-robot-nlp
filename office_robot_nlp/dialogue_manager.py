"""Deterministic multi-turn orchestration for validated CLU results."""

from dataclasses import dataclass, replace
from enum import Enum
from typing import TypeAlias

from office_robot_nlp.clu import (
    ClarificationAnswerResult,
    LanguageUnderstandingResult,
    RobotDecisionResult,
    SocialResult,
    TaskCommandResult,
    TaskUpdateResult,
    UnknownUtteranceResult,
)
from office_robot_nlp.domain.conversation import ConversationMode, ConversationState
from office_robot_nlp.domain.intent import (
    Action,
    Constraints,
    IntentType,
    ParsedIntent,
    ResolvedDestination,
    ResolvedDocument,
    ResolvedIntent,
)
from office_robot_nlp.domain.map import MapCatalog
from office_robot_nlp.entity_resolver import resolve_destination
from office_robot_nlp.validation.intent_validator import (
    AmbiguousLocation,
    UnknownLocation,
    ValidationError,
    validate_intent,
)


MISSING_DESTINATION_RESPONSE = "Bạn muốn giao tài liệu tới phòng nào?"
UNKNOWN_LOCATION_RESPONSE = "Mình không tìm thấy phòng đó trong danh sách địa điểm hiện tại. Bạn muốn giao tới phòng nào khác?"
AMBIGUOUS_LOCATION_RESPONSE = "Có nhiều phòng phù hợp. Vui lòng chọn một phòng cụ thể."
UNKNOWN_REQUEST_RESPONSE = "Tôi chưa hiểu yêu cầu đó."


class DialogueErrorCode(str, Enum):
    CONTEXT_MISMATCH = "CONTEXT_MISMATCH"
    MISSING_PENDING_INTENT = "MISSING_PENDING_INTENT"
    INVALID_INTENT = "INVALID_INTENT"
    UNKNOWN_LOCATION = "UNKNOWN_LOCATION"
    AMBIGUOUS_LOCATION = "AMBIGUOUS_LOCATION"


@dataclass(frozen=True)
class ClarificationRequired:
    responseText: str
    awaitingField: str
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskReady:
    resolvedIntent: ResolvedIntent


@dataclass(frozen=True)
class DecisionReady:
    taskId: str
    decision: Action


@dataclass(frozen=True)
class UnsupportedRequest:
    responseText: str


@dataclass(frozen=True)
class DialogueError:
    responseText: str
    reason: DialogueErrorCode


@dataclass(frozen=True)
class SocialAcknowledged:
    pendingField: str | None = None


@dataclass(frozen=True)
class TaskUpdateAccepted:
    update: TaskUpdateResult


DialogueResult: TypeAlias = (
    ClarificationRequired
    | TaskReady
    | DecisionReady
    | UnsupportedRequest
    | DialogueError
    | SocialAcknowledged
    | TaskUpdateAccepted
)


@dataclass(frozen=True)
class DialogueHandleResult:
    state: ConversationState
    result: DialogueResult


def _idle_state(state: ConversationState) -> ConversationState:
    return replace(
        state,
        mode=ConversationMode.IDLE,
        pendingIntent=None,
        awaitingField=None,
        activeTaskId=None,
        allowedDecisions=[],
        decisionReason=None,
    )


class DialogueManager:
    """Turn validated CLU results into state transitions and application outcomes."""

    def handle(
        self,
        state: ConversationState,
        result: LanguageUnderstandingResult,
        map_catalog: MapCatalog,
    ) -> DialogueHandleResult:
        if isinstance(result, TaskCommandResult):
            return self._handle_task_command(state, result.parsedIntent, map_catalog)
        if isinstance(result, TaskUpdateResult):
            return self._handle_task_update(state, result, map_catalog)
        if isinstance(result, ClarificationAnswerResult):
            return self._handle_clarification(state, result, map_catalog)
        if isinstance(result, RobotDecisionResult):
            return self._handle_robot_decision(state, result)
        if isinstance(result, SocialResult):
            return self._handle_social(state)
        if isinstance(result, UnknownUtteranceResult):
            return self._handle_unknown(state)
        return self._error(state, DialogueErrorCode.INVALID_INTENT, "Kết quả hội thoại không hợp lệ.")

    def _handle_task_command(
        self,
        state: ConversationState,
        intent: ParsedIntent,
        map_catalog: MapCatalog,
    ) -> DialogueHandleResult:
        if state.mode is not ConversationMode.IDLE:
            return self._error(state, DialogueErrorCode.CONTEXT_MISMATCH,
                               "Đang chờ thông tin cho yêu cầu hiện tại.")
        return self._process_intent(state, intent, map_catalog)

    def _process_intent(
        self,
        state: ConversationState,
        intent: ParsedIntent,
        map_catalog: MapCatalog,
    ) -> DialogueHandleResult:
        if not intent.destinationText or not intent.destinationText.strip():
            pending_state = replace(
                state,
                mode=ConversationMode.AWAITING_TASK_INFORMATION,
                pendingIntent=intent,
                awaitingField="destination",
            )
            return DialogueHandleResult(
                pending_state,
                ClarificationRequired(MISSING_DESTINATION_RESPONSE, "destination"),
            )

        resolution = resolve_destination(intent.destinationText, map_catalog)
        validation = validate_intent(intent, resolution, map_catalog)
        if not validation.valid:
            if isinstance(resolution, UnknownLocation):
                return self._await_destination(state, intent, UNKNOWN_LOCATION_RESPONSE)
            if isinstance(resolution, AmbiguousLocation):
                return self._await_destination(
                    state, intent, AMBIGUOUS_LOCATION_RESPONSE, tuple(resolution.candidates)
                )
            return self._error(state, DialogueErrorCode.INVALID_INTENT,
                               "; ".join(error.value for error in validation.errors))

        if not hasattr(resolution, "locationId"):
            return self._error(state, DialogueErrorCode.INVALID_INTENT, "Không thể phân giải phòng đích.")

        resolved = ResolvedIntent(
            intent=IntentType.DELIVER_DOCUMENT,
            destination=ResolvedDestination(resolution.locationId, intent.destinationText),
            document=ResolvedDocument(None, intent.documentText),
            priority=intent.priority,
            constraints=Constraints(intent.constraints.ifBusy, intent.constraints.ifClosed),
            recipientText=intent.recipientText,
        )
        return DialogueHandleResult(_idle_state(state), TaskReady(resolved))

    def _handle_clarification(
        self,
        state: ConversationState,
        answer: ClarificationAnswerResult,
        map_catalog: MapCatalog,
    ) -> DialogueHandleResult:
        if state.mode is not ConversationMode.AWAITING_TASK_INFORMATION:
            return self._error(state, DialogueErrorCode.CONTEXT_MISMATCH,
                               "Hiện không có thông tin nào đang cần làm rõ.")
        if state.pendingIntent is None:
            return self._error(state, DialogueErrorCode.MISSING_PENDING_INTENT,
                               "Không tìm thấy yêu cầu đang chờ.")
        if state.awaitingField != answer.field:
            return self._error(state, DialogueErrorCode.CONTEXT_MISMATCH,
                               "Thông tin trả lời không khớp với câu hỏi hiện tại.")

        updated_intent = replace(
            state.pendingIntent,
            # recipientText is a compatibility property, not a dataclass field;
            # dataclasses.replace would otherwise reset it to its default.
            recipientText=state.pendingIntent.recipientText,
            destinationText=answer.value
            if answer.field == "destination" else state.pendingIntent.destinationText,
            documentText=answer.value
            if answer.field == "document" else state.pendingIntent.documentText,
            missingFields=[
                field for field in state.pendingIntent.missingFields if field != answer.field
            ],
        )
        return self._process_intent(state, updated_intent, map_catalog)

    def _handle_task_update(
        self,
        state: ConversationState,
        update: TaskUpdateResult,
        map_catalog: MapCatalog,
    ) -> DialogueHandleResult:
        if state.mode is not ConversationMode.AWAITING_TASK_INFORMATION:
            return self._error(state, DialogueErrorCode.CONTEXT_MISMATCH,
                               "Hiện không có nhiệm vụ nào đang chờ cập nhật.")
        if state.pendingIntent is None:
            return self._error(state, DialogueErrorCode.MISSING_PENDING_INTENT,
                               "Không tìm thấy nhiệm vụ đang chờ.")
        pending = state.pendingIntent
        missing = list(pending.missingFields)
        if update.destinationText is not None:
            missing = [field for field in missing if field != "destination"]
        if update.documentText is not None:
            missing = [field for field in missing if field != "document"]
        merged = replace(
            pending,
            recipientText=update.recipientText if update.recipientText is not None else pending.recipientText,
            destinationText=update.destinationText if update.destinationText is not None else pending.destinationText,
            documentText=update.documentText if update.documentText is not None else pending.documentText,
            priority=update.priority if update.priority is not None else pending.priority,
            constraints=update.constraints if update.constraints is not None else pending.constraints,
            missingFields=missing,
        )
        if update.destinationText is not None:
            return self._process_intent(state, merged, map_catalog)
        waiting_state = replace(
            state,
            pendingIntent=merged,
            mode=ConversationMode.AWAITING_TASK_INFORMATION,
            awaitingField="destination",
        )
        return DialogueHandleResult(waiting_state, TaskUpdateAccepted(update))

    @staticmethod
    def _handle_social(state: ConversationState) -> DialogueHandleResult:
        pending_field = state.awaitingField if state.mode is ConversationMode.AWAITING_TASK_INFORMATION else None
        return DialogueHandleResult(state, SocialAcknowledged(pending_field))

    def _handle_robot_decision(
        self,
        state: ConversationState,
        decision: RobotDecisionResult,
    ) -> DialogueHandleResult:
        if state.mode is not ConversationMode.AWAITING_ROBOT_DECISION:
            return self._error(state, DialogueErrorCode.CONTEXT_MISMATCH,
                               "Hiện không có quyết định robot nào đang chờ.")
        if state.activeTaskId is None or decision.decision not in state.allowedDecisions:
            return self._error(state, DialogueErrorCode.CONTEXT_MISMATCH,
                               "Quyết định không hợp lệ trong ngữ cảnh hiện tại.")
        return DialogueHandleResult(
            _idle_state(state), DecisionReady(state.activeTaskId, decision.decision)
        )

    def _handle_unknown(self, state: ConversationState) -> DialogueHandleResult:
        if state.mode is ConversationMode.AWAITING_TASK_INFORMATION:
            if state.awaitingField is None or state.pendingIntent is None:
                return self._error(state, DialogueErrorCode.MISSING_PENDING_INTENT,
                                   "Không tìm thấy yêu cầu đang chờ.")
            return DialogueHandleResult(
                state,
                ClarificationRequired(
                    MISSING_DESTINATION_RESPONSE
                    if state.awaitingField == "destination"
                    else "Vui lòng cung cấp thông tin còn thiếu.",
                    state.awaitingField,
                ),
            )
        if state.mode is ConversationMode.IDLE:
            return DialogueHandleResult(state, UnsupportedRequest(UNKNOWN_REQUEST_RESPONSE))
        return self._error(state, DialogueErrorCode.CONTEXT_MISMATCH, UNKNOWN_REQUEST_RESPONSE)

    def _await_destination(
        self,
        state: ConversationState,
        intent: ParsedIntent,
        response: str,
        candidates: tuple[str, ...] = (),
    ) -> DialogueHandleResult:
        waiting_state = replace(
            state,
            mode=ConversationMode.AWAITING_TASK_INFORMATION,
            pendingIntent=intent,
            awaitingField="destination",
        )
        return DialogueHandleResult(
            waiting_state, ClarificationRequired(response, "destination", candidates)
        )

    @staticmethod
    def _error(
        state: ConversationState,
        reason: DialogueErrorCode,
        response: str,
    ) -> DialogueHandleResult:
        return DialogueHandleResult(state, DialogueError(response, reason))


__all__ = [
    "AMBIGUOUS_LOCATION_RESPONSE",
    "ClarificationRequired",
    "DecisionReady",
    "DialogueError",
    "DialogueErrorCode",
    "DialogueHandleResult",
    "DialogueManager",
    "DialogueResult",
    "MISSING_DESTINATION_RESPONSE",
    "TaskReady",
    "SocialAcknowledged",
    "TaskUpdateAccepted",
    "UNKNOWN_LOCATION_RESPONSE",
    "UNKNOWN_REQUEST_RESPONSE",
    "UnsupportedRequest",
]
