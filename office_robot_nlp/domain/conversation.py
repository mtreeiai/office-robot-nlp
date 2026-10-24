"Conversation state contract for a future dialogue manager."

from dataclasses import dataclass
from enum import Enum
from typing import Literal, TypeAlias
from .intent import ParsedIntent, UserDecision
from .robot_event import DecisionReason
from .batch import PendingBatch
class ConversationMode(str, Enum):
    IDLE = "IDLE"
    AWAITING_TASK_INFORMATION = "AWAITING_TASK_INFORMATION"
    AWAITING_ROBOT_DECISION = "AWAITING_ROBOT_DECISION"
    AWAITING_BATCH_INFORMATION = "AWAITING_BATCH_INFORMATION"

AwaitingField: TypeAlias = Literal["destination", "document", "allocation", "order", "confirmation"]

@dataclass
class ConversationState:
    conversationId: str
    mode: ConversationMode
    pendingIntent: ParsedIntent | None
    awaitingField: AwaitingField | None
    activeTaskId: str | None
    allowedDecisions: list[UserDecision]
    decisionReason: DecisionReason | None = None
    pendingBatch: PendingBatch | None = None
