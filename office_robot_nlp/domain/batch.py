"""Delivery semantics and batch membership; no scheduling or physical capacity."""

from dataclasses import dataclass
from enum import Enum

from .intent import ParsedIntent, Priority, ResolvedIntent


class OrderPolicy(str, Enum):
    PRIORITY = "PRIORITY"
    STRICT_ORDER = "STRICT_ORDER"


class AllocationStatus(str, Enum):
    CLEAR = "CLEAR"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class BatchDraft:
    """Semantic candidates; orderConfirmed acknowledges a conflict-choice only.

    False is normal for a valid PRIORITY or nonconflicting STRICT_ORDER batch.
    It does not indicate missing semantic validation, robot acceptance, or loading.
    Only dialogue handling BATCH_ORDER sets it after the user resolves a conflict.
    """

    tasks: tuple[ParsedIntent, ...]
    orderPolicy: OrderPolicy = OrderPolicy.PRIORITY
    allocationStatus: AllocationStatus = AllocationStatus.CLEAR
    orderConfirmed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.tasks, tuple) or not self.tasks or any(not isinstance(task, ParsedIntent) for task in self.tasks):
            raise ValueError("A batch draft must contain one or more parsed delivery candidates.")
        if (not isinstance(self.orderPolicy, OrderPolicy) or not isinstance(self.allocationStatus, AllocationStatus)
                or type(self.orderConfirmed) is not bool):
            raise ValueError("Invalid batch draft semantics.")


@dataclass(frozen=True)
class BatchIssue:
    itemIndex: int | None
    field: str
    code: str
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class PendingBatch:
    draft: BatchDraft
    resolved: tuple[ResolvedIntent | None, ...]
    issues: tuple[BatchIssue, ...]

    @property
    def awaitingIndex(self) -> int | None:
        return self.issues[0].itemIndex

    @property
    def awaitingField(self) -> str:
        return self.issues[0].field


@dataclass(frozen=True)
class BatchTaskReference:
    taskId: str
    sequenceIndex: int


@dataclass(frozen=True)
class OrderPreviewItem:
    """Read-only presentation of an existing task, never an execution schedule."""

    taskId: str
    sequenceIndex: int
    previewPosition: int
    documentText: str | None
    destinationId: str
    destinationText: str
    priority: Priority


@dataclass(frozen=True)
class DeliveryBatch:
    batchId: str
    sessionId: str
    tasks: tuple[BatchTaskReference, ...]
    orderPolicy: OrderPolicy

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (self.batchId, self.sessionId)):
            raise ValueError("Batch and session IDs must be non-empty strings.")
        if not isinstance(self.orderPolicy, OrderPolicy):
            raise ValueError("Invalid batch order policy.")
        if not isinstance(self.tasks, tuple) or not self.tasks:
            raise ValueError("A batch must contain one or more task references.")
        if any(not isinstance(task, BatchTaskReference) or not isinstance(task.taskId, str)
               or not task.taskId.strip() or type(task.sequenceIndex) is not int for task in self.tasks):
            raise ValueError("Invalid batch task reference.")
        if len({task.taskId for task in self.tasks}) != len(self.tasks):
            raise ValueError("Duplicate task IDs in batch.")
        if tuple(task.sequenceIndex for task in self.tasks) != tuple(range(len(self.tasks))):
            raise ValueError("Sequence indexes must preserve contiguous input order, starting at zero.")
