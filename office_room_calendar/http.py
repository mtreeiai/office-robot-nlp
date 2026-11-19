"""Separate website/robot credentials under the existing FastAPI server."""

from pathlib import Path
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, Request, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from office_room_calendar.auth import CalendarAuth
from office_room_calendar.errors import CalendarError, require


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Login(Input):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class Recurrence(Input):
    weeks: int = Field(ge=2, le=52)
    timezone: str = Field(min_length=1, max_length=100)


class BookingCreate(Input):
    roomId: str
    title: str = Field(min_length=1, max_length=160)
    startAt: str
    endAt: str
    idempotencyKey: str = Field(min_length=1, max_length=100)
    recurrence: Recurrence | None = None


class BookingPatch(Input):
    expectedRevision: int = Field(ge=1)
    roomId: str | None = None
    title: str | None = Field(default=None, min_length=1, max_length=160)
    startAt: str | None = None
    endAt: str | None = None


class RoomPatch(Input):
    expectedVersion: int = Field(ge=1)
    displayName: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    bookable: bool | None = None
    enabled: bool | None = None
    deliveryPolicy: str | None = None
    timezone: str | None = Field(default=None, max_length=100)
    workingHours: dict[str, list[list[str]]] | None = None


class ArrivalWindow(Input):
    start: str
    end: str


class AvailabilityInput(Input):
    roomId: str
    operation: str = "DOCUMENT_DELIVERY"
    arrivalWindow: ArrivalWindow | None = None
    knownCalendarVersion: int | None = Field(default=None, ge=1)


def error(status, code, message):
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def make_subapp():
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(CalendarError)
    async def calendar_error(request, problem):
        return error(problem.status, problem.code, problem.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, problem):
        return error(422, "INVALID_REQUEST", "Invalid calendar request payload or parameters.")

    @app.middleware("http")
    async def boundary(request, call_next):
        try:
            response = await call_next(request)
        except psycopg.Error:
            response = error(503, "CALENDAR_UNAVAILABLE", "Calendar database is unavailable. No availability permission can be inferred.")
        except Exception:
            response = error(500, "CALENDAR_INTERNAL_ERROR", "Calendar request could not be completed.")
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    return app


def install_calendar(root, service, availability, settings):
    from secrets import compare_digest
    auth = CalendarAuth(service.database, settings, service.clock)
    calendar = make_subapp()

    def origin(request):
        require(request.headers.get("origin") == settings.public_origin,
                "ORIGIN_REJECTED", "Use the configured calendar website origin.", 403)

    def user(request: Request):
        return auth.user(request.cookies.get("calendar_session", ""))

    def writer(request: Request, actor=Depends(user)):
        origin(request)
        token = request.cookies.get("calendar_session", "")
        require(compare_digest(request.headers.get("x-csrf-token", "").encode(), auth.csrf(token).encode()),
                "CSRF_REJECTED", "Calendar CSRF token is missing or invalid.", 403)
        return actor

    @calendar.post("/auth/login")
    def login(body: Login, request: Request):
        origin(request)
        require(request.headers.get("content-type", "").split(";")[0].strip() == "application/json",
                "INVALID_REQUEST", "Use JSON for calendar authentication.", 422)
        token, actor = auth.login(body.email, body.password, request.client.host if request.client else "unknown")
        response = JSONResponse({"user": actor, "csrfToken": auth.csrf(token), "timezone": settings.timezone})
        response.set_cookie("calendar_session", token, max_age=43200, httponly=True, secure=settings.secure_cookie,
                            samesite="strict", path="/v1/calendar")
        return response

    @calendar.get("/auth/session")
    def session(request: Request, actor=Depends(user)):
        return {"user": actor, "csrfToken": auth.csrf(request.cookies["calendar_session"]), "timezone": settings.timezone}

    @calendar.post("/auth/logout")
    def logout(request: Request, actor=Depends(writer)):
        auth.logout(request.cookies["calendar_session"], actor)
        response = JSONResponse({"status": "SIGNED_OUT"})
        response.delete_cookie("calendar_session", path="/v1/calendar", secure=settings.secure_cookie, httponly=True, samesite="strict")
        return response

    @calendar.get("/rooms")
    def rooms(actor=Depends(user)):
        return {"rooms": service.rooms()}

    @calendar.patch("/rooms/{value}")
    def patch_room(value: str, body: RoomPatch, actor=Depends(writer)):
        return service.configure_room(actor, value, body.model_dump(exclude={"expectedVersion"}, exclude_none=True), body.expectedVersion)

    @calendar.get("/bookings")
    def list_bookings(start: str, end: str, roomId: str | None = None, actor=Depends(user)):
        return {"bookings": service.list_bookings(start, end, roomId)}

    @calendar.post("/bookings", status_code=201)
    def create_booking(body: BookingCreate, actor=Depends(writer)):
        return service.create_booking(actor, body.model_dump())

    @calendar.patch("/bookings/{booking_id}")
    def patch_booking(booking_id: UUID, body: BookingPatch, actor=Depends(writer)):
        return service.change_booking(actor, booking_id, body.model_dump(exclude={"expectedRevision"}, exclude_none=True), body.expectedRevision)

    @calendar.delete("/bookings/{booking_id}")
    def cancel_booking(booking_id: UUID, expectedRevision: int | None = Query(default=None, ge=1), actor=Depends(writer)):
        return service.change_booking(actor, booking_id, {}, expectedRevision, cancel=True)

    @calendar.get("/availability")
    def check(roomId: str, start: str | None = None, end: str | None = None, actor=Depends(user)):
        require((start is None) == (end is None), "INVALID_WINDOW", "Provide both interval endpoints.", 422)
        return availability.check(roomId, {"start": start, "end": end} if start else None)

    @calendar.get("/free-slots")
    def free_slots(roomId: str, start: str, durationSeconds: int = Query(default=180, ge=1, le=28800), actor=Depends(user)):
        from office_room_calendar.service import utc
        from datetime import timedelta
        point = utc(start)
        evidence = availability.check(roomId, {"start": point.isoformat(), "end": (point+timedelta(seconds=durationSeconds)).isoformat()})
        return {"nextAvailableFrom": evidence["nextAvailableFrom"], "durationSeconds": durationSeconds, "availability": evidence}

    @calendar.get("/audit")
    def audits(limit: int = Query(default=100, ge=1, le=500), actor=Depends(user)):
        from office_room_calendar.service import admin
        admin(actor)
        with service.database.transaction(readonly=True) as connection:
            rows = connection.execute("SELECT * FROM calendar_audit_logs ORDER BY audit_id DESC LIMIT %s", (limit,)).fetchall()
        return {"entries": rows}

    robot = make_subapp()

    def robot_identity(request: Request):
        header = request.headers.get("authorization", "")
        require(header.startswith("Bearer "), "UNAUTHORIZED", "Robot read token required.", 401)
        return auth.robot(header[7:])

    @robot.post("/check")
    def robot_check(body: AvailabilityInput, name=Depends(robot_identity)):
        require(body.operation == "DOCUMENT_DELIVERY", "INVALID_OPERATION", "Only DOCUMENT_DELIVERY is supported.", 422)
        return availability.check(body.roomId, body.arrivalWindow.model_dump() if body.arrivalWindow else None, body.knownCalendarVersion)

    root.mount("/v1/calendar", calendar)
    root.mount("/v1/robot/availability", robot)
    website = make_subapp()

    @website.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; object-src 'none'; form-action 'self'"
        return response

    website.mount("/", StaticFiles(directory=Path(__file__).parent / "web", html=True))
    root.mount("/calendar", website)
    return auth
