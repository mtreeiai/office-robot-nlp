'Robot-originated event contracts (not robot-side world-state logic).'

from dataclasses import dataclass
from enum import Enum
from typing import Literal, TypeAlias
from .intent import UserDecision
class RobotStatus(str, Enum):
    QUEUED = "QUEUED"
    MOVING = "MOVING"
    WAITING = "WAITING"
    DELIVERING = "DELIVERING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

class DecisionReason(str, Enum):
    ROOM_BUSY = "ROOM_BUSY"
    ROOM_CLOSED = "ROOM_CLOSED"
    OTHER = "OTHER"

@dataclass
class TaskStatusEvent:
    event: Literal["TASK_STATUS"]
    taskId: str
    status: RobotStatus
    reason: str | None = None
    locationId: str | None = None
@dataclass
class UserDecisionRequiredEvent:
    event: Literal["USER_DECISION_REQUIRED"]
    taskId: str
    reason: DecisionReason
    allowedOptions: list[UserDecision]
    locationId: str | None = None
RobotEvent: TypeAlias = TaskStatusEvent | UserDecisionRequiredEvent
