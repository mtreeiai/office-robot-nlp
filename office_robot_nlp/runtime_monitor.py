"""Local monitoring models and deterministic rendering, without robot control."""

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum

from office_robot_nlp.domain.map import MapCatalog
from office_robot_nlp.semantic_map import SemanticRoomRegistry
from office_robot_nlp.task_lifecycle import (
    DeliveryTaskLifecycle, TaskExecutionEvent, TaskLifecycleState, apply_execution_event,
)


class StateSource(str, Enum):
    NONE = "NONE"
    AUTOMATION = "AUTOMATION"
    SIMULATED = "SIMULATED"


class ConnectionState(str, Enum):
    NOT_CONNECTED = "NOT_CONNECTED"
    CONNECTED = "CONNECTED"
    UNKNOWN = "UNKNOWN"


class DocumentLoadState(str, Enum):
    UNCONFIRMED = "UNCONFIRMED"
    NOT_LOADED = "NOT_LOADED"
    LOADED = "LOADED"


class TaskHandoffState(str, Enum):
    NOT_SENT = "NOT_SENT"
    PENDING_ACK = "PENDING_ACK"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class RobotStateSnapshot:
    robotId: str | None = None
    connectionState: ConnectionState = ConnectionState.NOT_CONNECTED
    operatingState: str = "UNKNOWN"
    locationId: str | None = None
    currentTaskId: str | None = None
    carryingDocumentIds: tuple[str, ...] | None = None
    lastUpdatedAt: datetime | None = None
    source: StateSource = StateSource.NONE


class MonitoringEventType(str, Enum):
    DOCUMENT_LOADED = "DOCUMENT_LOADED"
    DOCUMENT_NOT_LOADED = "DOCUMENT_NOT_LOADED"
    HANDOFF_PENDING = "HANDOFF_PENDING"
    TASK_ACCEPTED = "TASK_ACCEPTED"
    TASK_REJECTED = "TASK_REJECTED"
    EXECUTION = "EXECUTION"


@dataclass(frozen=True)
class TaskMonitoringEvent:
    taskId: str
    type: MonitoringEventType
    source: StateSource
    executionEvent: TaskExecutionEvent | None = None
    occurredAt: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class TaskMonitoringRecord:
    lifecycle: DeliveryTaskLifecycle
    handoff: TaskHandoffState = TaskHandoffState.NOT_SENT
    documentLoad: DocumentLoadState = DocumentLoadState.UNCONFIRMED
    lifecycleSource: StateSource = StateSource.NONE
    handoffSource: StateSource = StateSource.NONE
    documentLoadSource: StateSource = StateSource.NONE
    lastEvent: TaskMonitoringEvent | None = None

    @property
    def taskId(self) -> str:
        return self.lifecycle.taskId


def require_event_source(source: StateSource) -> None:
    if not isinstance(source, StateSource) or source is StateSource.NONE:
        raise ValueError("An explicit AUTOMATION or SIMULATED source is required.")


def apply_monitoring_event(record: TaskMonitoringRecord, event: TaskMonitoringEvent) -> TaskMonitoringRecord:
    """Record only the named dimension. Lifecycle rules stay in task_lifecycle."""
    require_event_source(event.source)
    if event.taskId != record.taskId:
        raise ValueError("Monitoring event task ID does not match the record.")
    if not isinstance(event.type, MonitoringEventType):
        raise ValueError("Invalid monitoring event type.")
    if event.type is MonitoringEventType.EXECUTION:
        if not isinstance(event.executionEvent, TaskExecutionEvent):
            raise ValueError("An EXECUTION event requires a TaskExecutionEvent.")
        lifecycle = apply_execution_event(record.lifecycle, event.executionEvent)
        return replace(record, lifecycle=lifecycle, lifecycleSource=event.source, lastEvent=event)
    if event.executionEvent is not None:
        raise ValueError("Only EXECUTION events may contain a lifecycle event.")
    if event.type in {MonitoringEventType.DOCUMENT_LOADED, MonitoringEventType.DOCUMENT_NOT_LOADED}:
        loaded = (DocumentLoadState.LOADED if event.type is MonitoringEventType.DOCUMENT_LOADED
                  else DocumentLoadState.NOT_LOADED)
        return replace(record, documentLoad=loaded, documentLoadSource=event.source, lastEvent=event)
    handoff_states = {
        MonitoringEventType.HANDOFF_PENDING: TaskHandoffState.PENDING_ACK,
        MonitoringEventType.TASK_ACCEPTED: TaskHandoffState.ACCEPTED,
        MonitoringEventType.TASK_REJECTED: TaskHandoffState.REJECTED,
    }
    return replace(record, handoff=handoff_states[event.type], handoffSource=event.source, lastEvent=event)


def render_robot_state(snapshot: RobotStateSnapshot) -> str:
    carrying = ("UNKNOWN" if snapshot.carryingDocumentIds is None
                else ", ".join(snapshot.carryingDocumentIds) or "NONE (confirmed empty)")
    return "\n".join([
        "[ROBOT STATE]",
        f"Robot ID         : {snapshot.robotId or 'UNKNOWN'}",
        f"Connection       : {snapshot.connectionState.value}",
        f"Operating State  : {snapshot.operatingState}",
        f"Location         : {snapshot.locationId or 'UNKNOWN'}",
        f"Current Task     : {snapshot.currentTaskId or 'NONE'}",
        f"Carrying Docs    : {carrying}",
        f"Last Updated     : {snapshot.lastUpdatedAt.isoformat() if snapshot.lastUpdatedAt else 'UNKNOWN'}",
        f"State Source     : {snapshot.source.value}",
    ])


def _execution_confirmations(record: TaskMonitoringRecord, robot: RobotStateSnapshot,
                             room_registered: bool) -> str:
    """Describe missing evidence, not a scheduling or execution authorization."""
    missing = []
    if not room_registered:
        missing.append("registered room")
    if record.handoff is not TaskHandoffState.ACCEPTED or record.handoffSource is not StateSource.AUTOMATION:
        missing.append("automation acceptance")
    if record.documentLoad is not DocumentLoadState.LOADED or record.documentLoadSource is not StateSource.AUTOMATION:
        missing.append("document load")
    if robot.connectionState is not ConnectionState.CONNECTED or robot.source is not StateSource.AUTOMATION:
        missing.append("robot connection")
    if robot.currentTaskId != record.taskId or robot.source is not StateSource.AUTOMATION:
        missing.append("robot task association")
    if robot.operatingState == "UNKNOWN":
        missing.append("robot operating state")
    if missing:
        return "NOT_CONFIRMED; missing: " + ", ".join(missing)
    return "PREREQUISITES_RECORDED; execution still requires explicit lifecycle events"


def render_task_monitor(record: TaskMonitoringRecord, robot: RobotStateSnapshot,
                        registry: SemanticRoomRegistry | None, catalog: MapCatalog) -> str:
    task = record.lifecycle
    intent = task.resolvedIntent
    destination_id = intent.destination.locationId
    room_registered = any(location.id == destination_id for location in catalog.locations)
    pose = None
    door_id = "UNKNOWN"
    if registry is not None:
        room_registered = destination_id in registry.rooms
        if room_registered:
            room = registry.get_room(destination_id)
            if any(door.id == "main_door" for door in room.doors):
                door_id = "main_door"
                pose = registry.get_staging_pose(destination_id, door_id)
    if record.handoff is TaskHandoffState.ACCEPTED:
        accepted = f"YES ({record.handoffSource.value})"
    elif record.handoff is TaskHandoffState.PENDING_ACK:
        accepted = f"UNCONFIRMED (PENDING_ACK; {record.handoffSource.value})"
    else:
        accepted = f"NO ({record.handoff.value}; {record.handoffSource.value})"
    physical = ("NOT_STARTED" if task.state in {
        TaskLifecycleState.DRAFT, TaskLifecycleState.READY, TaskLifecycleState.QUEUED,
    } else task.state.value)
    last_event = record.lastEvent
    event_label = "NONE"
    if last_event:
        kind = last_event.executionEvent.value if last_event.executionEvent else last_event.type.value
        event_label = f"{kind} ({last_event.source.value}; {last_event.occurredAt.isoformat()})"
    return "\n".join([
        "=" * 50,
        "             ROBOT TASK MONITOR",
        "=" * 50, "",
        "[SEMANTIC TASK]",
        f"Task ID          : {task.taskId}",
        f"Intent           : {intent.intent.value}",
        f"Document Text    : {intent.document.originalText or 'UNSPECIFIED'}",
        f"Document ID      : {intent.document.documentId or 'UNKNOWN'}",
        f"Recipient Text   : {intent.recipientText or 'UNSPECIFIED'}",
        f"Destination Text : {intent.destination.originalText}",
        f"Destination ID   : {destination_id}",
        f"Priority         : {intent.priority.value}",
        f"If Busy          : {intent.constraints.ifBusy.value if intent.constraints.ifBusy else 'UNSPECIFIED'}",
        f"If Closed        : {intent.constraints.ifClosed.value if intent.constraints.ifClosed else 'UNSPECIFIED'}",
        "", "[MAP]",
        f"Map ID           : {registry.map_id if registry else 'UNKNOWN'}",
        f"Frame            : {registry.frame_id if registry else 'UNKNOWN'}",
        f"Door             : {door_id}",
        f"Staging X        : {pose.x:.3f}" if pose else "Staging X        : UNKNOWN",
        f"Staging Y        : {pose.y:.3f}" if pose else "Staging Y        : UNKNOWN",
        f"Staging Yaw      : {pose.yaw:.3f}" if pose else "Staging Yaw      : UNKNOWN",
        "", "[TASK EXECUTION]",
        f"Lifecycle        : {task.state.value}",
        f"Lifecycle Source : {record.lifecycleSource.value}",
        f"Handoff          : {record.handoff.value}",
        f"Handoff Source   : {record.handoffSource.value}",
        f"Document Loaded  : {record.documentLoad.value}",
        f"Load Source      : {record.documentLoadSource.value}",
        f"Last Event       : {event_label}",
        "", render_robot_state(robot),
        "", "[INTERPRETATION]",
        "Semantic request : VALID",
        f"Room registered  : {'YES' if room_registered else 'NO'}",
        f"Robot accepted   : {accepted}",
        f"Physical delivery: {physical} ({record.lifecycleSource.value})",
        f"Execution checks : {_execution_confirmations(record, robot, room_registered)}",
        "", "=" * 50,
    ])
