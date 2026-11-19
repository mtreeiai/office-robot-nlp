"""Transactional calendar management; no language model or navigation dependencies."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg
from psycopg.types.json import Jsonb

from office_room_calendar.auth import audit
from office_room_calendar.errors import CalendarError, require


UTC = timezone.utc
POLICIES = {"BLOCK_WHEN_BUSY", "ALLOW_DOOR_HANDOFF", "BUSINESS_HOURS_ONLY", "MANAGER_APPROVAL", "DISABLED"}


def utc(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise CalendarError("INVALID_TIME", "Use an ISO-8601 datetime with an explicit offset.", 422) from None
    require(isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None,
            "INVALID_TIME", "Timezone-aware datetimes are required.", 422)
    return value.astimezone(UTC)


def window(start, end, maximum=timedelta(hours=24)):
    start, end = utc(start), utc(end)
    require(timedelta(0) < end - start <= maximum, "INVALID_WINDOW", "Invalid or excessive time interval.", 422)
    return start, end


def room_id(value):
    require(value in {f"{index:02d}" for index in range(1, 8)}, "INVALID_ROOM", "Use a canonical room ID from 01 to 07.", 422)
    return value


def admin(actor):
    require(actor["role"] == "ADMIN", "FORBIDDEN", "Administrator permission required.", 403)


def booking_json(row):
    return {"bookingId": str(row["booking_id"]), "roomId": row["room_id"], "organizerId": str(row["organizer_id"]),
            "title": row["title"], "startAt": row["start_at"].isoformat(), "endAt": row["end_at"].isoformat(),
            "status": row["status"], "revision": row["revision"], "seriesId": str(row["series_id"]) if row["series_id"] else None}


class CalendarService:
    def __init__(self, database, settings, clock=lambda: datetime.now(UTC)):
        self.database, self.settings, self.clock = database, settings, clock

    def _lock_rooms(self, connection, room_ids):
        for value in sorted(set(room_ids)):
            row = connection.execute("SELECT version FROM calendar_room_versions WHERE room_id=%s FOR UPDATE", (room_id(value),)).fetchone()
            require(row is not None, "ROOM_NOT_FOUND", "Room is not configured.", 404)

    def _version(self, connection, room_ids):
        for value in sorted(set(room_ids)):
            connection.execute("UPDATE calendar_room_versions SET version=version+1 WHERE room_id=%s", (value,))

    def rooms(self):
        with self.database.transaction(readonly=True) as connection:
            rows = connection.execute("SELECT * FROM calendar_rooms JOIN calendar_room_policies USING(room_id) JOIN calendar_room_versions USING(room_id) ORDER BY room_id").fetchall()
        return [{"roomId": row["room_id"], "displayName": row["display_name"], "description": row["description"],
                 "bookable": row["bookable"], "enabled": row["enabled"], "deliveryPolicy": row["policy"],
                 "timezone": row["timezone"], "workingHours": row["working_hours"], "calendarVersion": row["version"]} for row in rows]

    def configure_room(self, actor, value, changes, expected_version):
        admin(actor)
        room_id(value)
        require(set(changes) <= {"displayName", "description", "bookable", "enabled", "deliveryPolicy", "timezone", "workingHours"},
                "INVALID_ROOM", "Unsupported room configuration.", 422)
        if "deliveryPolicy" in changes:
            require(changes["deliveryPolicy"] in POLICIES, "INVALID_POLICY", "Invalid delivery policy.", 422)
        if "timezone" in changes:
            try:
                ZoneInfo(changes["timezone"])
            except (ZoneInfoNotFoundError, ValueError):
                raise CalendarError("INVALID_TIMEZONE", "Use a valid IANA timezone.", 422) from None
        if "workingHours" in changes:
            hours = changes["workingHours"]
            require(isinstance(hours, dict), "INVALID_POLICY", "Working hours must map weekdays 0–6 to time intervals.", 422)
            for day, intervals in hours.items():
                require(day in list("0123456") and isinstance(intervals, list), "INVALID_POLICY", "Invalid working weekday.", 422)
                for interval in intervals:
                    require(isinstance(interval, list) and len(interval) == 2 and all(isinstance(t, str) and len(t) == 5 for t in interval), "INVALID_POLICY", "Invalid working interval.", 422)
                    try:
                        times = [datetime.strptime(t, "%H:%M").time() for t in interval]
                    except ValueError:
                        raise CalendarError("INVALID_POLICY", "Use HH:MM working times.", 422) from None
                    require(times[0] < times[1], "INVALID_POLICY", "Split overnight hours into separate weekdays.", 422)
        with self.database.transaction() as connection:
            self._lock_rooms(connection, [value])
            current = connection.execute("SELECT version FROM calendar_room_versions WHERE room_id=%s", (value,)).fetchone()
            require(current["version"] == expected_version, "STALE_VERSION", "Room changed; reload before saving.", 409)
            from psycopg import sql
            for public, field in {"displayName": "display_name", "description": "description", "bookable": "bookable", "enabled": "enabled"}.items():
                if public in changes:
                    connection.execute(sql.SQL("UPDATE calendar_rooms SET {}=%s WHERE room_id=%s").format(sql.Identifier(field)), (changes[public], value))
            for public, field in {"deliveryPolicy": "policy", "timezone": "timezone", "workingHours": "working_hours"}.items():
                if public in changes:
                    data = Jsonb(changes[public]) if field == "working_hours" else changes[public]
                    connection.execute(sql.SQL("UPDATE calendar_room_policies SET {}=%s WHERE room_id=%s").format(sql.Identifier(field)), (data, value))
            self._version(connection, [value])
            audit(connection, actor["userId"], "ROOM_UPDATED", value, changes)
        return next(room for room in self.rooms() if room["roomId"] == value)

    def list_bookings(self, start, end, room=None):
        start, end = window(start, end, timedelta(days=93))
        if room:
            room_id(room)
        with self.database.transaction(readonly=True) as connection:
            rows = connection.execute("""SELECT * FROM calendar_bookings WHERE start_at<%s AND end_at>%s
                AND (%s::text IS NULL OR room_id=%s) ORDER BY start_at LIMIT 5001""", (end, start, room, room)).fetchall()
        require(len(rows) <= 5000, "RANGE_TOO_LARGE", "Choose a smaller date range.", 422)
        return [booking_json(row) for row in rows]

    def create_booking(self, actor, body):
        value = room_id(body["roomId"])
        start, end = window(body["startAt"], body["endAt"])
        title = body["title"].strip()
        require(1 <= len(title) <= 160, "INVALID_TITLE", "Title must have 1–160 characters.", 422)
        occurrence_windows = [(start, end)]
        recurrence = body.get("recurrence")
        if recurrence:
            count = recurrence["weeks"]
            require(type(count) is int and 2 <= count <= 52, "INVALID_RECURRENCE", "Weekly series must contain 2–52 occurrences.", 422)
            try:
                zone = ZoneInfo(recurrence["timezone"])
            except (ZoneInfoNotFoundError, ValueError):
                raise CalendarError("INVALID_TIMEZONE", "Invalid recurrence timezone.", 422) from None
            for offset in range(1, count):
                pair = [instant.astimezone(zone) + timedelta(weeks=offset) for instant in (start, end)]
                require(all(instant.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == instant.replace(tzinfo=None) for instant in pair),
                        "INVALID_RECURRENCE", "A repeated local time does not exist because of a timezone transition.", 422)
                occurrence_windows.append(window(*pair))
        normalized = {**body, "startAt": start.isoformat(), "endAt": end.isoformat(), "title": title}
        content_hash = hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()
        try:
            with self.database.transaction() as connection:
                connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,8119))", (actor["userId"] + ":" + body["idempotencyKey"],))
                previous = connection.execute("SELECT * FROM calendar_idempotency WHERE user_id=%s AND request_key=%s", (actor["userId"], body["idempotencyKey"])).fetchone()
                if previous:
                    require(previous["fingerprint"] == content_hash, "IDEMPOTENCY_CONFLICT", "Request ID reused with different content.", 409)
                    return previous["response"]
                self._lock_rooms(connection, [value])
                room = connection.execute("SELECT * FROM calendar_rooms WHERE room_id=%s", (value,)).fetchone()
                require(room["enabled"] and room["bookable"], "ROOM_NOT_BOOKABLE", "This room is not enabled for booking.", 409)
                series_id = uuid4() if recurrence else None
                if recurrence:
                    connection.execute("INSERT INTO calendar_booking_series VALUES (%s,%s,%s)", (series_id, recurrence["timezone"], recurrence["weeks"]))
                bookings = []
                for occurrence_start, occurrence_end in occurrence_windows:
                    row = connection.execute("""INSERT INTO calendar_bookings(booking_id,room_id,organizer_id,title,start_at,end_at,series_id)
                        VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *""", (uuid4(), value, actor["userId"], title, occurrence_start, occurrence_end, series_id)).fetchone()
                    bookings.append(booking_json(row))
                self._version(connection, [value])
                result = {"bookings": bookings, "seriesId": str(series_id) if series_id else None}
                audit(connection, actor["userId"], "BOOKING_CREATED", bookings[0]["bookingId"], {"roomId": value, "occurrences": len(bookings)})
                connection.execute("INSERT INTO calendar_idempotency VALUES (%s,%s,%s,%s)", (actor["userId"], body["idempotencyKey"], content_hash, Jsonb(result)))
                return result
        except psycopg.errors.ExclusionViolation:
            raise CalendarError("BOOKING_CONFLICT", "Confirmed bookings overlap in this room.", 409) from None

    def change_booking(self, actor, booking_id, changes, expected_revision, cancel=False):
        try:
            with self.database.transaction() as connection:
                row = connection.execute("SELECT * FROM calendar_bookings WHERE booking_id=%s", (booking_id,)).fetchone()
                require(row is not None, "BOOKING_NOT_FOUND", "Booking not found.", 404)
                require(actor["role"] == "ADMIN" or str(row["organizer_id"]) == actor["userId"], "FORBIDDEN", "Only the owner or an administrator can change this booking.", 403)
                rooms = [row["room_id"], room_id(changes.get("roomId", row["room_id"]))]
                self._lock_rooms(connection, rooms)
                current = connection.execute("SELECT * FROM calendar_bookings WHERE booking_id=%s FOR UPDATE", (booking_id,)).fetchone()
                require(current["revision"] == row["revision"] and (expected_revision is None or expected_revision == current["revision"]),
                        "STALE_VERSION", "Booking changed; reload before saving.", 409)
                if cancel and current["status"] == "CANCELLED":
                    return booking_json(current)
                require(current["status"] == "CONFIRMED", "BOOKING_CANCELLED", "Cancelled bookings cannot be edited.", 409)
                before = booking_json(current)
                if cancel:
                    row = connection.execute("UPDATE calendar_bookings SET status='CANCELLED',revision=revision+1,updated_at=%s WHERE booking_id=%s RETURNING *", (self.clock(), booking_id)).fetchone()
                else:
                    start, end = window(changes.get("startAt", current["start_at"]), changes.get("endAt", current["end_at"]))
                    title = changes.get("title", current["title"]).strip()
                    require(1 <= len(title) <= 160, "INVALID_TITLE", "Invalid booking title.", 422)
                    room = connection.execute("SELECT * FROM calendar_rooms WHERE room_id=%s", (rooms[1],)).fetchone()
                    require(room["bookable"] and room["enabled"], "ROOM_NOT_BOOKABLE", "Room is not enabled for booking.", 409)
                    row = connection.execute("""UPDATE calendar_bookings SET room_id=%s,title=%s,start_at=%s,end_at=%s,
                        revision=revision+1,updated_at=%s WHERE booking_id=%s RETURNING *""", (rooms[1], title, start, end, self.clock(), booking_id)).fetchone()
                self._version(connection, rooms)
                audit(connection, actor["userId"], "BOOKING_CANCELLED" if cancel else "BOOKING_UPDATED", booking_id, {"before": before, "after": booking_json(row)})
                return booking_json(row)
        except psycopg.errors.ExclusionViolation:
            raise CalendarError("BOOKING_CONFLICT", "Confirmed bookings overlap in this room.", 409) from None
