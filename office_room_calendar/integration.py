from office_robot_nlp.api_dto import MessageResponse
from office_room_calendar.availability import vietnamese_feedback


class CalendarMessageResponse(MessageResponse):
    roomAvailability: list[dict]


def enrich_message(availability, timezone):
    def enrich(response):
        evidence = []
        if response.batch:
            for task in response.batch.tasks:
                evidence.append({"taskId": task.taskId, **availability.check(task.destinationId)})
        reply = response.reply
        if evidence:
            reply += "\n" + "\n".join(vietnamese_feedback(item, timezone) for item in evidence)
        return CalendarMessageResponse(**{**response.model_dump(), "reply": reply}, roomAvailability=evidence)
    return enrich
