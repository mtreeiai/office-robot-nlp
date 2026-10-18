"Task contracts exchanged with a robot-side implementation."

from dataclasses import dataclass
from typing import Literal
from .intent import BusyPolicy, ClosedPolicy, IntentType, Priority, UserDecision
@dataclass
class TaskPayload:
    documentId: str | None
@dataclass
class TaskTarget:
    locationId: str
@dataclass
class TaskPolicies:
    onBusy: BusyPolicy | None
    onClosed: ClosedPolicy | None
@dataclass
class TaskRequest:
    requestId: str
    taskType: Literal[IntentType.DELIVER_DOCUMENT]
    payload: TaskPayload
    target: TaskTarget
    priority: Priority
    policies: TaskPolicies
@dataclass
class TaskAccepted:
    requestId: str
    taskId: str
    accepted: bool
    reason: str | None = None
@dataclass
class TaskUpdate:
    taskId: str
    decision: UserDecision
