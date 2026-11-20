"""Add preliminary evidence and execution stage; never authorize or schedule a task."""

from typing import Literal

from office_robot_nlp.api_dto import DTO, MessageResponse
from office_room_calendar.availability import vietnamese_feedback


class ExecutionDecisionDTO(DTO):
    taskId: str
    roomId: str
    status: Literal["PENDING_AVAILABILITY_CHECK"] = "PENDING_AVAILABILITY_CHECK"
    allowed: Literal[False] = False
    reasonCode: Literal["ARRIVAL_WINDOW_REQUIRED"] = "ARRIVAL_WINDOW_REQUIRED"


class CalendarMessageResponse(MessageResponse):
    roomAvailability: list[dict]
    executionDecisions: list[ExecutionDecisionDTO]


def enrich_message(availability, timezone):
    def enrich(response):
        evidence = []
        decisions = []
        if response.batch:
            for task in response.batch.tasks:
                evidence.append({"taskId": task.taskId, **availability.check(task.destinationId)})
                # Stage metadata is independent of the advisory Calendar decision.
                decisions.append(ExecutionDecisionDTO(taskId=task.taskId, roomId=task.destinationId))
        reply = response.reply
        if evidence:
            reply += "\n" + "\n".join(vietnamese_feedback(item, timezone) for item in evidence)
        return CalendarMessageResponse(**{**response.model_dump(), "reply": reply},
                                       roomAvailability=evidence, executionDecisions=decisions)
    return enrich
