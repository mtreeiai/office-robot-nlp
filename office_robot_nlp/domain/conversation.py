"Conversation state contract for a future dialogue manager."

from dataclasses import dataclass
from enum import Enum
from typing import Literal, TypeAlias
from .intent import ParsedIntent, UserDecision
from .robot_event import DecisionReason
class ConversationMode(str, Enum):
    IDLE = "IDLE"
    AWAITING_TASK_INFORMATION = "AWAITING_TASK_INFORMATION"
    AWAITING_ROBOT_DECISION = "AWAITING_ROBOT_DECISION"

AwaitingField: TypeAlias = Literal["destination", "document"]

@dataclass
class ConversationState:
    conversationId: str
    mode: ConversationMode
    pendingIntent: ParsedIntent | None
    awaitingField: AwaitingField | None
    activeTaskId: str | None
    allowedDecisions: list[UserDecision]
    decisionReason: DecisionReason | None = None
