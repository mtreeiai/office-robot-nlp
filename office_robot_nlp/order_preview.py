"""Pure delivery order preview for validated, registered batches. No dispatch."""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping

from office_robot_nlp.domain.batch import BatchTaskReference, DeliveryBatch, OrderPolicy, OrderPreviewItem
from office_robot_nlp.domain.intent import Priority

if TYPE_CHECKING:
    from office_robot_nlp.runtime_monitor import TaskMonitoringRecord


def build_order_preview(
    batch: DeliveryBatch, task_records: Mapping[str, TaskMonitoringRecord],
) -> tuple[OrderPreviewItem, ...]:
    """Read registered task records without modifying membership or runtime state.

    Registration already enforces semantic validity, including strict-order
    conflict resolution. Positions here are one-based; sequenceIndex remains the
    zero-based original request index. This result carries no execution authority.
    """
    ranks = {Priority.HIGH: 0, Priority.NORMAL: 1, Priority.LOW: 2}

    def order_key(member: BatchTaskReference) -> tuple[int, ...]:
        if batch.orderPolicy is OrderPolicy.STRICT_ORDER:
            return (member.sequenceIndex,)
        priority = task_records[member.taskId].lifecycle.resolvedIntent.priority
        return (ranks[priority], member.sequenceIndex)

    ordered = sorted(batch.tasks, key=order_key)
    preview = []
    for position, member in enumerate(ordered, 1):
        record = task_records[member.taskId]
        if record.taskId != member.taskId:
            raise ValueError("Preview task record does not match batch membership.")
        intent = record.lifecycle.resolvedIntent
        preview.append(OrderPreviewItem(
            taskId=member.taskId, sequenceIndex=member.sequenceIndex, previewPosition=position,
            documentText=intent.document.originalText, destinationId=intent.destination.locationId,
            destinationText=intent.destination.originalText, priority=intent.priority,
        ))
    return tuple(preview)
