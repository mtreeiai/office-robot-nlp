"""Version-one JSON DTOs and explicit adapters; no HTTP or Qt dependency."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from office_robot_nlp.order_preview import build_order_preview


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CreateSessionRequest(DTO):
    pass


class MessageRequest(DTO):
    messageId: str
    text: str

    @field_validator("messageId", "text")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Must not be blank")
        return value  # Preserve exact content for idempotency.


class SessionResponse(DTO):
    sessionId: str
    status: Literal["ACTIVE"] = "ACTIVE"


class ConstraintDTO(DTO):
    ifBusy: str | None
    ifClosed: str | None


class TaskDTO(DTO):
    taskId: str
    sequenceIndex: int
    documentText: str | None
    documentId: str | None
    recipientText: str | None
    destinationText: str
    destinationId: str
    priority: str
    constraints: ConstraintDTO
    lifecycle: str
    handoff: str
    documentLoad: str


class PreviewDTO(DTO):
    position: int
    taskId: str


class BatchDTO(DTO):
    batchId: str | None
    orderPolicy: str | None
    tasks: list[TaskDTO]
    orderPreview: list[PreviewDTO]


DialogueStatus = Literal[
    "TASK_READY", "BATCH_READY", "CLARIFICATION_REQUIRED", "DECISION_READY",
    "UNSUPPORTED", "DIALOGUE_ERROR", "SOCIAL", "TASK_UPDATE_ACCEPTED", "PENDING_CANCELLED",
]


class MessageResponse(DTO):
    sessionId: str
    messageId: str
    dialogueStatus: DialogueStatus
    reply: str
    batch: BatchDTO | None


class RobotDTO(DTO):
    robotId: str | None
    connection: str
    operatingState: str
    locationId: str | None
    currentTaskId: str | None
    carryingDocumentIds: list[str] | None
    lastUpdatedAt: str | None
    source: str


class PoseDTO(DTO):
    x: float
    y: float
    yaw: float
    yawDegrees: float


class SemanticDTO(DTO):
    mapId: str
    frameId: str
    doorId: str
    stagingPose: PoseDTO


class EventDTO(DTO):
    type: str
    source: str
    executionEvent: str | None
    occurredAt: str


class TaskSnapshot(TaskDTO):
    sessionId: str
    batchId: str | None
    lifecycleSource: str
    handoffSource: str
    documentLoadSource: str
    lastEvent: EventDTO | None
    semantic: SemanticDTO | None
    robotState: RobotDTO


class BatchSnapshot(DTO):
    batchId: str
    sessionId: str
    orderPolicy: str
    tasks: list[TaskSnapshot]
    orderPreview: list[PreviewDTO]
    robotState: RobotDTO


class HealthResponse(DTO):
    status: Literal["ok"] = "ok"
    service: Literal["office-robot-nlp"] = "office-robot-nlp"
    apiVersion: Literal["v1"] = "v1"


class ErrorDetail(DTO):
    code: str
    message: str


class ErrorResponse(DTO):
    error: ErrorDetail


def task_dto(record) -> TaskDTO:
    intent = record.lifecycle.resolvedIntent
    return TaskDTO(
        taskId=record.taskId, sequenceIndex=record.sequenceIndex if record.sequenceIndex is not None else 0,
        documentText=intent.document.originalText, documentId=intent.document.documentId,
        recipientText=intent.recipientText, destinationText=intent.destination.originalText,
        destinationId=intent.destination.locationId, priority=intent.priority.value,
        constraints=ConstraintDTO(ifBusy=intent.constraints.ifBusy.value if intent.constraints.ifBusy else None,
                                  ifClosed=intent.constraints.ifClosed.value if intent.constraints.ifClosed else None),
        lifecycle=record.lifecycle.state.value, handoff=record.handoff.value,
        documentLoad=record.documentLoad.value,
    )


def preview_dtos(batch, records) -> list[PreviewDTO]:
    # The domain function owns ordering. Only the API's index convention changes.
    return [PreviewDTO(position=item.previewPosition - 1, taskId=item.taskId)
            for item in build_order_preview(batch, records)]


def batch_dto(batch, records) -> BatchDTO:
    return BatchDTO(batchId=batch.batchId, orderPolicy=batch.orderPolicy.value,
                    tasks=[task_dto(records[member.taskId]) for member in batch.tasks],
                    orderPreview=preview_dtos(batch, records))


def robot_dto(robot) -> RobotDTO:
    return RobotDTO(
        robotId=robot.robotId, connection=robot.connectionState.value,
        operatingState=robot.operatingState, locationId=robot.locationId, currentTaskId=robot.currentTaskId,
        carryingDocumentIds=list(robot.carryingDocumentIds) if robot.carryingDocumentIds is not None else None,
        lastUpdatedAt=robot.lastUpdatedAt.isoformat() if robot.lastUpdatedAt else None, source=robot.source.value,
    )


def task_snapshot(app, record) -> TaskSnapshot:
    semantic = None
    if app.room_registry is not None:
        room_id = record.lifecycle.resolvedIntent.destination.locationId
        try:
            pose = app.room_registry.get_staging_pose(room_id)
        except KeyError:
            pass  # Optional metadata must never be guessed from a room anchor.
        else:
            semantic = SemanticDTO(mapId=app.room_registry.map_id, frameId=pose.frame_id, doorId="main_door",
                                   stagingPose=PoseDTO(x=pose.x, y=pose.y, yaw=pose.yaw, yawDegrees=pose.yaw_deg))
    event = record.lastEvent
    return TaskSnapshot(
        **task_dto(record).model_dump(), sessionId=app.state.conversationId, batchId=record.batchId,
        lifecycleSource=record.lifecycleSource.value, handoffSource=record.handoffSource.value,
        documentLoadSource=record.documentLoadSource.value,
        lastEvent=EventDTO(type=event.type.value, source=event.source.value,
                           executionEvent=event.executionEvent.value if event.executionEvent else None,
                           occurredAt=event.occurredAt.isoformat()) if event else None,
        semantic=semantic, robotState=robot_dto(app.robot_state),
    )
