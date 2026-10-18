"""Safe response planning and natural-language generation for Phase 7B."""

import json
import urllib.request
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Callable, Mapping, Protocol

from office_robot_nlp.clu import ConversationContext
from office_robot_nlp.dialogue_manager import (
    ClarificationRequired,
    DecisionReady,
    DialogueError,
    DialogueResult,
    SocialAcknowledged,
    TaskReady,
    TaskUpdateAccepted,
    UnsupportedRequest,
)
from office_robot_nlp.gemini_adapter import GeminiConfig
from office_robot_nlp.task_lifecycle import DeliveryTaskLifecycle, TaskLifecycleState


class ResponseAct(str, Enum):
    GREETING = "GREETING"
    GREETING_WITH_PENDING_REQUEST = "GREETING_WITH_PENDING_REQUEST"
    ASK_DESTINATION = "ASK_DESTINATION"
    UNKNOWN_LOCATION = "UNKNOWN_LOCATION"
    AMBIGUOUS_LOCATION = "AMBIGUOUS_LOCATION"
    ACK_TASK_UPDATE = "ACK_TASK_UPDATE"
    TASK_ACCEPTED = "TASK_ACCEPTED"
    TASK_RECORDED = "TASK_RECORDED"
    TASK_QUEUED = "TASK_QUEUED"
    TASK_STARTED = "TASK_STARTED"
    TASK_NAVIGATING = "TASK_NAVIGATING"
    TASK_ARRIVED = "TASK_ARRIVED"
    WAITING_FOR_RECIPIENT = "WAITING_FOR_RECIPIENT"
    VERIFYING_RECIPIENT = "VERIFYING_RECIPIENT"
    DELIVERING = "DELIVERING"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_DEFERRED = "TASK_DEFERRED"
    TASK_FAILED = "TASK_FAILED"
    TASK_CANCELLED = "TASK_CANCELLED"
    TASK_RETURNING = "TASK_RETURNING"
    TASK_RETURNED = "TASK_RETURNED"
    DECISION_ACCEPTED = "DECISION_ACCEPTED"
    UNSUPPORTED_REQUEST = "UNSUPPORTED_REQUEST"
    PROVIDER_TEMPORARILY_UNAVAILABLE = "PROVIDER_TEMPORARILY_UNAVAILABLE"


@dataclass(frozen=True)
class ResponsePlan:
    act: ResponseAct
    facts: dict[str, Any]
    pendingField: str | None = None


class NaturalResponseGenerator(Protocol):
    def generate(self, plan: ResponsePlan, context: ConversationContext) -> str:
        """Verbalize only the already-decided response plan."""


def response_plan_for(result: DialogueResult) -> ResponsePlan:
    if isinstance(result, SocialAcknowledged):
        if result.pendingField:
            return ResponsePlan(
                ResponseAct.GREETING_WITH_PENDING_REQUEST,
                {},
                result.pendingField,
            )
        return ResponsePlan(ResponseAct.GREETING, {})
    if isinstance(result, ClarificationRequired):
        if result.candidates:
            return ResponsePlan(
                ResponseAct.AMBIGUOUS_LOCATION,
                {"candidates": result.candidates},
                result.awaitingField,
            )
        if "không tìm thấy" in result.responseText.casefold():
            return ResponsePlan(
                ResponseAct.UNKNOWN_LOCATION,
                {},
                result.awaitingField,
            )
        return ResponsePlan(ResponseAct.ASK_DESTINATION, {}, result.awaitingField)
    if isinstance(result, TaskUpdateAccepted):
        update = result.update
        facts = {
            name: value for name, value in {
                "recipientText": update.recipientText,
                "destinationText": update.destinationText,
                "documentText": update.documentText,
                "priority": update.priority.value if update.priority else None,
            }.items() if value is not None
        }
        return ResponsePlan(ResponseAct.ACK_TASK_UPDATE, facts, "destination")
    if isinstance(result, TaskReady):
        resolved = result.resolvedIntent
        return ResponsePlan(
            ResponseAct.TASK_RECORDED,
            {
                "intent": resolved.intent.value,
                "locationId": resolved.destination.locationId,
                "destinationText": resolved.destination.originalText,
                "documentText": resolved.document.originalText,
                "recipientText": resolved.recipientText,
                "priority": resolved.priority.value,
                "ifBusy": resolved.constraints.ifBusy.value if resolved.constraints.ifBusy else None,
                "ifClosed": resolved.constraints.ifClosed.value if resolved.constraints.ifClosed else None,
                "lifecycleState": TaskLifecycleState.READY.value,
            },
        )
    if isinstance(result, DecisionReady):
        return ResponsePlan(
            ResponseAct.DECISION_ACCEPTED,
            {"taskId": result.taskId, "decision": result.decision.value},
        )
    if isinstance(result, UnsupportedRequest):
        return ResponsePlan(ResponseAct.UNSUPPORTED_REQUEST, {})
    if isinstance(result, DialogueError):
        return ResponsePlan(ResponseAct.UNSUPPORTED_REQUEST, {})
    return ResponsePlan(ResponseAct.UNSUPPORTED_REQUEST, {})


def response_plan_for_lifecycle(task: DeliveryTaskLifecycle) -> ResponsePlan:
    facts = {
        "taskId": task.taskId,
        "lifecycleState": task.state.value,
        "destinationText": task.resolvedIntent.destination.originalText,
        "recipientText": task.resolvedIntent.recipientText,
    }
    acts = {
        TaskLifecycleState.READY: ResponseAct.TASK_RECORDED,
        TaskLifecycleState.QUEUED: ResponseAct.TASK_QUEUED,
        TaskLifecycleState.STARTED: ResponseAct.TASK_STARTED,
        TaskLifecycleState.NAVIGATING: ResponseAct.TASK_NAVIGATING,
        TaskLifecycleState.ARRIVED: ResponseAct.TASK_ARRIVED,
        TaskLifecycleState.WAITING_FOR_RECIPIENT: ResponseAct.WAITING_FOR_RECIPIENT,
        TaskLifecycleState.VERIFYING_RECIPIENT: ResponseAct.VERIFYING_RECIPIENT,
        TaskLifecycleState.DELIVERING: ResponseAct.DELIVERING,
        TaskLifecycleState.COMPLETED: ResponseAct.TASK_COMPLETED,
        TaskLifecycleState.DEFERRED: ResponseAct.TASK_DEFERRED,
        TaskLifecycleState.FAILED: ResponseAct.TASK_FAILED,
        TaskLifecycleState.CANCELLED: ResponseAct.TASK_CANCELLED,
        TaskLifecycleState.RETURNING: ResponseAct.TASK_RETURNING,
        TaskLifecycleState.RETURNED: ResponseAct.TASK_RETURNED,
    }
    return ResponsePlan(acts[task.state], facts)


class TemplateNaturalResponseGenerator:
    """Deterministic Vietnamese fallback that never invents facts."""

    def generate(self, plan: ResponsePlan, context: ConversationContext) -> str:
        if plan.act is ResponseAct.GREETING:
            return "Chào bạn! Tôi có thể hỗ trợ giao tài liệu trong văn phòng."
        if plan.act is ResponseAct.GREETING_WITH_PENDING_REQUEST:
            return "Chào bạn! Mình vẫn đang chờ địa điểm giao tài liệu nhé.\nBạn muốn mình mang tới phòng nào?"
        if plan.act is ResponseAct.ASK_DESTINATION:
            return "Bạn muốn giao tài liệu tới phòng nào?"
        if plan.act is ResponseAct.UNKNOWN_LOCATION:
            return "Mình chưa tìm thấy phòng đó trong danh sách địa điểm hiện tại. Bạn muốn giao tới phòng nào khác?"
        if plan.act is ResponseAct.AMBIGUOUS_LOCATION:
            return "Mình chưa xác định được chính xác phòng. Bạn có thể nói rõ hơn không?"
        if plan.act is ResponseAct.ACK_TASK_UPDATE:
            if "recipientText" in plan.facts:
                return (
                    f"Được, mình đã ghi nhận người nhận là {plan.facts['recipientText']}.\n"
                    "Bạn muốn giao tài liệu tới phòng nào?"
                )
            if plan.facts.get("priority") == "HIGH":
                return "Được, mình đã ghi nhận tài liệu này cần giao gấp. Bạn muốn giao tới phòng nào?"
            return "Được, mình đã cập nhật thông tin. Bạn muốn giao tài liệu tới phòng nào?"
        if plan.act in {ResponseAct.TASK_ACCEPTED, ResponseAct.TASK_RECORDED}:
            recipient = plan.facts.get("recipientText")
            recipient_text = f" cho {recipient}" if recipient else ""
            return f"Được rồi, mình đã ghi nhận nhiệm vụ giao tài liệu{recipient_text} tới {plan.facts['destinationText']}."
        if plan.act is ResponseAct.TASK_QUEUED:
            return "Nhiệm vụ đã được thêm vào hàng chờ."
        if plan.act is ResponseAct.TASK_STARTED:
            return "Mình đã bắt đầu xử lý nhiệm vụ giao tài liệu."
        if plan.act is ResponseAct.TASK_NAVIGATING:
            return f"Mình đang di chuyển tới {plan.facts['destinationText']} để giao tài liệu."
        if plan.act is ResponseAct.TASK_ARRIVED:
            return f"Mình đã tới {plan.facts['destinationText']}."
        if plan.act is ResponseAct.WAITING_FOR_RECIPIENT:
            recipient = plan.facts.get("recipientText")
            return f"Mình đã tới {plan.facts['destinationText']} và đang chờ {recipient or 'người nhận'} tài liệu."
        if plan.act is ResponseAct.VERIFYING_RECIPIENT:
            return "Mình đang xác nhận người nhận tài liệu."
        if plan.act is ResponseAct.DELIVERING:
            return "Mình đang thực hiện bàn giao tài liệu."
        if plan.act is ResponseAct.TASK_COMPLETED:
            return "Tài liệu đã được giao thành công."
        if plan.act is ResponseAct.TASK_DEFERRED:
            return "Nhiệm vụ này đang được tạm hoãn."
        if plan.act is ResponseAct.TASK_FAILED:
            return "Hiện tại mình chưa thể hoàn thành nhiệm vụ này."
        if plan.act is ResponseAct.TASK_CANCELLED:
            return "Nhiệm vụ đã được hủy."
        if plan.act is ResponseAct.TASK_RETURNING:
            return "Mình đang mang tài liệu chưa giao được trở lại."
        if plan.act is ResponseAct.TASK_RETURNED:
            return "Tài liệu chưa giao được đã được mang trở lại."
        if plan.act is ResponseAct.DECISION_ACCEPTED:
            return f"Mình đã ghi nhận quyết định {plan.facts['decision'].casefold()} cho nhiệm vụ này."
        if plan.act is ResponseAct.PROVIDER_TEMPORARILY_UNAVAILABLE:
            return "Mình đang gặp sự cố kết nối. Bạn có thể thử lại tin nhắn này sau nhé."
        return "Hiện tại mình chưa hỗ trợ yêu cầu này."


NLG_SYSTEM_INSTRUCTIONS = """You verbalize a safe response plan for an office delivery assistant.
Speak naturally and concisely in Vietnamese. Use only the supplied facts.
Do not invent destinations, recipient identities, actions, success, or robot state.
READY does not mean STARTED. QUEUED does not mean NAVIGATING.
ARRIVED does not mean COMPLETED. WAITING_FOR_RECIPIENT does not mean verified.
DELIVERING does not mean completed. Never claim movement unless state is NAVIGATING.
Never claim delivery success unless lifecycle state is COMPLETED.
Do not expose enum names, JSON, technical errors, or reasoning.
Do not claim execution unless the response plan explicitly says the task is accepted.
Return only the user-facing response text.
"""


class GeminiNaturalResponseGenerator:
    """Optional Gemini NLG; callers must fall back to templates on any failure."""

    def __init__(
        self,
        config: GeminiConfig | None = None,
        request: Callable[[str, Mapping[str, str], bytes, float], object] | None = None,
    ):
        self.config = config or GeminiConfig.from_environment()
        self.request = request or self._request

    def _request(self, url: str, headers: Mapping[str, str], body: bytes, timeout: float) -> object:
        request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()

    def generate(self, plan: ResponsePlan, context: ConversationContext) -> str:
        body = json.dumps({
            "model": self.config.model,
            "system_instruction": NLG_SYSTEM_INSTRUCTIONS,
            "input": json.dumps({"act": plan.act.value, "facts": plan.facts}, ensure_ascii=False),
            "response_format": {"type": "text"},
        }, ensure_ascii=False).encode("utf-8")
        raw = self.request(
            self.config.endpoint,
            {"x-goog-api-key": self.config.api_key, "Content-Type": "application/json"},
            body,
            self.config.timeout,
        )
        payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        blocks = []
        for step in payload.get("steps", []):
            if step.get("type") == "model_output":
                blocks.extend(
                    block.get("text", "") for block in step.get("content", [])
                    if block.get("type") == "text"
                )
        text = "".join(blocks).strip()
        if not text:
            raise ValueError("Gemini NLG returned no text")
        return text


def generate_with_fallback(
    plan: ResponsePlan,
    context: ConversationContext,
    generator: NaturalResponseGenerator,
    fallback: NaturalResponseGenerator | None = None,
) -> str:
    fallback = fallback or TemplateNaturalResponseGenerator()
    try:
        text = generator.generate(plan, context)
        if not _is_execution_safe(plan, text):
            raise ValueError("NLG response exceeded confirmed lifecycle state")
        return text
    except Exception:
        return fallback.generate(plan, context)


def _is_execution_safe(plan: ResponsePlan, text: str) -> bool:
    lowered = text.casefold()
    state = plan.facts.get("lifecycleState")
    forbidden_by_state = {
        "READY": ("bắt đầu", "đang giao", "đang di chuyển", "trên đường", "đã giao thành công"),
        "QUEUED": ("đang di chuyển", "đang giao", "đã giao thành công"),
        "STARTED": ("đang di chuyển", "đã tới", "đã giao thành công"),
        "NAVIGATING": ("đã tới", "đã giao thành công"),
        "ARRIVED": ("đã giao thành công",),
        "WAITING_FOR_RECIPIENT": ("đã giao thành công",),
        "VERIFYING_RECIPIENT": ("đã xác nhận đúng", "đã giao thành công"),
        "DELIVERING": ("đã giao thành công",),
    }
    return not any(marker in lowered for marker in forbidden_by_state.get(state, ()))


__all__ = [
    "GeminiNaturalResponseGenerator",
    "NaturalResponseGenerator",
    "ResponseAct",
    "ResponsePlan",
    "TemplateNaturalResponseGenerator",
    "generate_with_fallback",
    "response_plan_for",
    "response_plan_for_lifecycle",
]
