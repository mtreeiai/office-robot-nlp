"""Execution-safe delivery task lifecycle, independent from conversation state."""

from dataclasses import dataclass, replace
from enum import Enum

from office_robot_nlp.domain.intent import ResolvedIntent


class TaskLifecycleState(str, Enum):
    DRAFT = "DRAFT"
    READY = "READY"
    QUEUED = "QUEUED"
    STARTED = "STARTED"
    NAVIGATING = "NAVIGATING"
    ARRIVED = "ARRIVED"
    WAITING_FOR_RECIPIENT = "WAITING_FOR_RECIPIENT"
    VERIFYING_RECIPIENT = "VERIFYING_RECIPIENT"
    DELIVERING = "DELIVERING"
    COMPLETED = "COMPLETED"
    DEFERRED = "DEFERRED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    RETURNING = "RETURNING"
    RETURNED = "RETURNED"


class TaskExecutionEvent(str, Enum):
    TASK_QUEUED = "TASK_QUEUED"
    TASK_STARTED = "TASK_STARTED"
    NAVIGATION_STARTED = "NAVIGATION_STARTED"
    ARRIVED_AT_DESTINATION = "ARRIVED_AT_DESTINATION"
    WAITING_FOR_RECIPIENT = "WAITING_FOR_RECIPIENT"
    RECIPIENT_VERIFICATION_STARTED = "RECIPIENT_VERIFICATION_STARTED"
    DELIVERY_STARTED = "DELIVERY_STARTED"
    DELIVERY_COMPLETED = "DELIVERY_COMPLETED"
    TASK_FAILED = "TASK_FAILED"
    TASK_CANCELLED = "TASK_CANCELLED"
    RETURN_STARTED = "RETURN_STARTED"
    RETURN_COMPLETED = "RETURN_COMPLETED"


class InvalidTaskTransitionError(ValueError):
    """Raised when an execution event skips or reverses lifecycle stages."""


@dataclass(frozen=True)
class DeliveryTaskLifecycle:
    taskId: str
    state: TaskLifecycleState
    resolvedIntent: ResolvedIntent


_TRANSITIONS: dict[tuple[TaskLifecycleState, TaskExecutionEvent], TaskLifecycleState] = {
    (TaskLifecycleState.READY, TaskExecutionEvent.TASK_QUEUED): TaskLifecycleState.QUEUED,
    (TaskLifecycleState.QUEUED, TaskExecutionEvent.TASK_STARTED): TaskLifecycleState.STARTED,
    (TaskLifecycleState.STARTED, TaskExecutionEvent.NAVIGATION_STARTED): TaskLifecycleState.NAVIGATING,
    (TaskLifecycleState.NAVIGATING, TaskExecutionEvent.ARRIVED_AT_DESTINATION): TaskLifecycleState.ARRIVED,
    (TaskLifecycleState.ARRIVED, TaskExecutionEvent.WAITING_FOR_RECIPIENT): TaskLifecycleState.WAITING_FOR_RECIPIENT,
    (TaskLifecycleState.WAITING_FOR_RECIPIENT, TaskExecutionEvent.RECIPIENT_VERIFICATION_STARTED): TaskLifecycleState.VERIFYING_RECIPIENT,
    (TaskLifecycleState.VERIFYING_RECIPIENT, TaskExecutionEvent.DELIVERY_STARTED): TaskLifecycleState.DELIVERING,
    (TaskLifecycleState.DELIVERING, TaskExecutionEvent.DELIVERY_COMPLETED): TaskLifecycleState.COMPLETED,
    (TaskLifecycleState.RETURNING, TaskExecutionEvent.RETURN_COMPLETED): TaskLifecycleState.RETURNED,
}


def create_ready_task(task_id: str, resolved_intent: ResolvedIntent) -> DeliveryTaskLifecycle:
    return DeliveryTaskLifecycle(task_id, TaskLifecycleState.READY, resolved_intent)


def apply_execution_event(
    task: DeliveryTaskLifecycle,
    event: TaskExecutionEvent,
) -> DeliveryTaskLifecycle:
    if event is TaskExecutionEvent.TASK_CANCELLED:
        if task.state in {TaskLifecycleState.COMPLETED, TaskLifecycleState.CANCELLED, TaskLifecycleState.RETURNED}:
            raise InvalidTaskTransitionError(f"Cannot cancel task in {task.state.value}.")
        return replace(task, state=TaskLifecycleState.CANCELLED)
    if event is TaskExecutionEvent.TASK_FAILED:
        if task.state in {TaskLifecycleState.COMPLETED, TaskLifecycleState.CANCELLED, TaskLifecycleState.RETURNED}:
            raise InvalidTaskTransitionError(f"Cannot fail task in {task.state.value}.")
        return replace(task, state=TaskLifecycleState.FAILED)
    if event is TaskExecutionEvent.RETURN_STARTED:
        if task.state not in {TaskLifecycleState.FAILED, TaskLifecycleState.DEFERRED}:
            raise InvalidTaskTransitionError(f"Cannot return task in {task.state.value}.")
        return replace(task, state=TaskLifecycleState.RETURNING)
    next_state = _TRANSITIONS.get((task.state, event))
    if next_state is None:
        raise InvalidTaskTransitionError(
            f"Event {event.value} is invalid for state {task.state.value}."
        )
    return replace(task, state=next_state)


__all__ = [
    "DeliveryTaskLifecycle",
    "InvalidTaskTransitionError",
    "TaskExecutionEvent",
    "TaskLifecycleState",
    "apply_execution_event",
    "create_ready_task",
]