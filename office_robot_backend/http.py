"""Additive operational endpoints, with separate operator and robot credentials."""

from typing import Literal

from fastapi import Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from office_robot_nlp.api_dto import DTO
from office_robot_backend.service import OperationError


class DraftInput(DTO):
    idempotencyKey: str = Field(min_length=1)
    sessionId: str = Field(min_length=1)
    messageId: str = Field(min_length=1)
    mapId: str = Field(min_length=1)
    pickups: dict[str, str]
    dependencies: list[list[int]] = Field(default_factory=list)


class HeartbeatInput(DTO):
    bootId: str = Field(min_length=1)
    activeAssignmentId: str | None
    safeStopped: bool


class EventInput(DTO):
    eventId: str = Field(min_length=1)
    assignmentId: str = Field(min_length=1)
    sequence: int = Field(gt=0)
    kind: Literal["ACK", "NAV_STARTED", "ARRIVED", "QR_SCANNED", "FAILED", "EXECUTION_UNKNOWN", "CANCELLED_SAFE"]
    payload: dict = Field(default_factory=dict)


class QRInput(DTO):
    leg: Literal["PICKUP", "DELIVERY"]


class ReconcileInput(DTO):
    packageAccountedFor: bool


def install_operations(api, bridge, service):
    @api.exception_handler(OperationError)
    async def operation_error(request: Request, error: OperationError):
        return JSONResponse(status_code=error.status, content={"error": {"code": error.code, "message": error.message}})

    def operator(x_operator_token: str = Header(default="")):
        return service.authenticate(x_operator_token, "operator")

    def robot(x_robot_token: str = Header(default="")):
        return service.authenticate(x_robot_token, "robot")

    @api.post("/v1/operations/requests", status_code=201)
    def draft(body: DraftInput, actor=Depends(operator)):
        source = body.model_dump(exclude={"idempotencyKey"})
        previous = service.find_request(actor, body.idempotencyKey, source)
        if previous:
            return previous
        semantic = bridge.get_message_result(body.sessionId, body.messageId).model_dump(mode="json")
        return service.draft(actor, body.idempotencyKey, semantic, body.mapId, body.pickups, body.dependencies)

    @api.get("/v1/operations/requests/{request_id}")
    def request_snapshot(request_id: str, actor=Depends(operator)):
        return service.request(actor, request_id)

    @api.post("/v1/operations/requests/{request_id}/confirm")
    def confirm(request_id: str, actor=Depends(operator)):
        return service.confirm(actor, request_id)

    @api.post("/v1/operations/requests/{request_id}/cancel")
    def cancel(request_id: str, actor=Depends(operator)):
        return service.cancel(actor, request_id)

    @api.post("/v1/operations/assignments/{assignment_id}/qr")
    def issue_qr(assignment_id: str, body: QRInput, actor=Depends(operator)):
        return service.issue_qr(actor, assignment_id, body.leg)

    @api.post("/v1/operations/assignments/{assignment_id}/reconcile")
    def reconcile(assignment_id: str, body: ReconcileInput, actor=Depends(operator)):
        return service.reconcile(actor, assignment_id, body.packageAccountedFor)

    @api.post("/v1/gateway/heartbeat")
    def heartbeat(body: HeartbeatInput, actor=Depends(robot)):
        return service.heartbeat(actor, body.bootId, body.activeAssignmentId, body.safeStopped)

    @api.get("/v1/gateway/assignment")
    def assignment(actor=Depends(robot)):
        return {"assignment": service.schedule(actor)}

    @api.get("/v1/gateway/assignments/{assignment_id}")
    def assignment_snapshot(assignment_id: str, actor=Depends(robot)):
        return service.assignment(actor, assignment_id)

    @api.post("/v1/gateway/events")
    def event(body: EventInput, actor=Depends(robot)):
        return service.ingest(actor, body.model_dump())
