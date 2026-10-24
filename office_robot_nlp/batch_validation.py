"""Atomic semantic validation using the existing resolver and intent validator."""

from office_robot_nlp.domain.batch import AllocationStatus, BatchDraft, BatchIssue, OrderPolicy, PendingBatch
from office_robot_nlp.domain.intent import Constraints, IntentType, Priority, ResolvedDestination, ResolvedDocument, ResolvedIntent
from office_robot_nlp.domain.map import MapCatalog
from office_robot_nlp.entity_resolver import resolve_destination
from office_robot_nlp.validation.intent_validator import (
    AmbiguousLocation, ResolvedLocation, UnknownLocation, ValidationError, validate_intent,
)


def validate_batch(draft: BatchDraft, catalog: MapCatalog) -> PendingBatch:
    """Resolve every item before any task is eligible for registration."""
    if not isinstance(draft, BatchDraft) or not draft.tasks:
        raise ValueError("A batch needs at least one task candidate.")
    issues = []
    resolved = []
    if draft.allocationStatus is AllocationStatus.AMBIGUOUS:
        issues.append(BatchIssue(None, "allocation", "AMBIGUOUS_DOCUMENT_ALLOCATION"))
    for index, intent in enumerate(draft.tasks):
        resolution = resolve_destination(intent.destinationText, catalog)
        validation = validate_intent(intent, resolution, catalog)
        clarifiable = {ValidationError.MISSING_DESTINATION, ValidationError.UNKNOWN_LOCATION,
                       ValidationError.AMBIGUOUS_LOCATION}
        if any(error not in clarifiable for error in validation.errors):
            raise ValueError("Invalid batch candidate: " + ", ".join(error.value for error in validation.errors))
        document_present = isinstance(intent.documentText, str) and bool(intent.documentText.strip())
        if not document_present:
            issues.append(BatchIssue(index, "document", "MISSING_DOCUMENT"))
        if not validation.valid:
            if not intent.destinationText or not intent.destinationText.strip():
                issues.append(BatchIssue(index, "destination", "MISSING_DESTINATION"))
            elif isinstance(resolution, UnknownLocation):
                issues.append(BatchIssue(index, "destination", "UNKNOWN_LOCATION"))
            elif isinstance(resolution, AmbiguousLocation):
                issues.append(BatchIssue(index, "destination", "AMBIGUOUS_LOCATION", tuple(resolution.candidates)))
            else:
                raise ValueError("Invalid batch candidate: " + ", ".join(error.value for error in validation.errors))
        if validation.valid and document_present and isinstance(resolution, ResolvedLocation):
            resolved.append(ResolvedIntent(
                IntentType.DELIVER_DOCUMENT,
                ResolvedDestination(resolution.locationId, intent.destinationText),
                ResolvedDocument(None, intent.documentText), intent.priority,
                Constraints(intent.constraints.ifBusy, intent.constraints.ifClosed),
                recipientText=intent.recipientText,
            ))
        else:
            resolved.append(None)
    # orderConfirmed is a conflict-choice acknowledgement, not batch validity.
    # A conflict-free batch is ready without asking for a redundant confirmation.
    if draft.orderPolicy is OrderPolicy.STRICT_ORDER and not draft.orderConfirmed:
        ranks = {Priority.LOW: 0, Priority.NORMAL: 1, Priority.HIGH: 2}
        priorities = [ranks[intent.priority] for intent in draft.tasks]
        if any(later > earlier for earlier, later in zip(priorities, priorities[1:])):
            issues.append(BatchIssue(None, "order", "CONFLICTING_ORDER_AND_PRIORITY"))
    return PendingBatch(draft, tuple(resolved), tuple(issues))


def batch_question(pending: PendingBatch) -> str:
    issue = pending.issues[0]
    if issue.field == "allocation":
        return ("Mình chưa rõ mỗi tài liệu tương ứng với phòng nào, hay bạn muốn các bản riêng. "
                "Bạn hãy nói rõ từng tài liệu hoặc từng bản và phòng nhận theo thứ tự đã nêu nhé.")
    if issue.field == "order":
        return ("Thứ tự giao cố định đang khác với mức ưu tiên của các tài liệu. "
                "Bạn muốn giữ đúng thứ tự đã nêu hay giao theo mức ưu tiên?")
    if issue.field == "confirmation":
        return "Mình chưa đăng ký được lô nhiệm vụ. Bạn muốn thử ghi nhận lại lô này hay hủy yêu cầu?"
    item = pending.draft.tasks[issue.itemIndex]
    label = f"mục {issue.itemIndex + 1} ({item.documentText})" if item.documentText else f"mục {issue.itemIndex + 1}"
    if issue.field == "document":
        return f"Bạn muốn giao tài liệu hoặc bản nào cho {label} tới {item.destinationText or 'phòng đang chờ xác định'}?"
    if issue.code == "UNKNOWN_LOCATION":
        return (f"Mình chưa tìm thấy {item.destinationText} trong danh sách phòng đã đăng ký. "
                f"Bạn muốn giao tài liệu ở {label} tới phòng nào khác?")
    if issue.code == "AMBIGUOUS_LOCATION":
        return f"Có nhiều phòng phù hợp cho {label}. Bạn muốn giao tới phòng cụ thể nào?"
    return f"Bạn muốn giao tài liệu ở {label} tới phòng nào?"
