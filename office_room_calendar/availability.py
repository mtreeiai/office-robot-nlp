"""Calendar evidence and policy decisions; no Gemini, scheduler or robot control."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg

from office_room_calendar.errors import require
from office_room_calendar.service import room_id, utc, window


def merge_intervals(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def first_gap(intervals, start, end, duration):
    candidate = start
    for busy_start, busy_end in intervals:
        if busy_end <= candidate:
            continue
        if busy_start >= candidate + duration:
            break
        candidate = max(candidate, busy_end)
    return candidate if candidate + duration <= end else None


def business_windows(start, end, zone_name, hours):
    zone = ZoneInfo(zone_name)
    day = start.astimezone(zone).date()
    last = end.astimezone(zone).date()
    while day <= last:
        for opening, closing in hours.get(str(day.weekday()), []):
            left = datetime.combine(day, datetime.strptime(opening, "%H:%M").time(), zone)
            right = datetime.combine(day, datetime.strptime(closing, "%H:%M").time(), zone)
            # Do not grant an interval that contains an imaginary DST wall time.
            if any(value.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != value.replace(tzinfo=None) for value in (left, right)):
                continue
            left, right = max(start, utc(left)), min(end, utc(right))
            if left < right:
                yield left, right
        day += timedelta(days=1)


class AvailabilityService:
    def __init__(self, database, settings, clock=lambda: datetime.now(timezone.utc)):
        self.database, self.settings, self.clock = database, settings, clock

    def unknown(self, value, start, end, checked, preliminary, reason="DATABASE_UNAVAILABLE"):
        return {"roomId": value, "calendarStatus": "UNKNOWN", "deliveryDecision": "UNKNOWN", "deliveryAllowed": False,
                "reasonCode": reason, "deliveryPolicy": None, "nextAvailableFrom": None, "busyUntil": None,
                "checkedAt": checked.isoformat(), "validUntil": checked.isoformat(), "calendarVersion": None,
                "dataSource": "CALENDAR", "checkType": "PRELIMINARY" if preliminary else "ARRIVAL_WINDOW",
                "arrivalWindow": {"start": start.isoformat(), "end": end.isoformat()}, "calendarChanged": None}

    def check(self, value, arrival=None, known_version=None):
        room_id(value)
        checked = utc(self.clock())
        preliminary = arrival is None
        if preliminary:
            start = checked + timedelta(seconds=self.settings.travel_seconds)
            end = start + timedelta(seconds=self.settings.handoff_seconds)
        else:
            start, end = window(arrival["start"], arrival["end"], timedelta(hours=8))
        require(start <= checked + timedelta(days=30), "WINDOW_TOO_FAR", "Arrival must be within the next 30 days.", 422)
        horizon = max(checked, start) + timedelta(days=30)
        try:
            with self.database.transaction(readonly=True) as connection:
                room = connection.execute("""SELECT * FROM calendar_rooms JOIN calendar_room_policies USING(room_id)
                    JOIN calendar_room_versions USING(room_id) WHERE room_id=%s""", (value,)).fetchone()
                require(room is not None, "ROOM_NOT_FOUND", "Room is not configured.", 404)
                rows = connection.execute("""SELECT start_at,end_at FROM calendar_bookings WHERE room_id=%s AND status='CONFIRMED'
                    AND end_at>%s AND start_at<%s ORDER BY start_at LIMIT 10001""", (value, start, horizon)).fetchall()
            if len(rows) > 10000:
                return self.unknown(value, start, end, checked, preliminary, "CALENDAR_TOO_LARGE")
        except psycopg.Error:
            return self.unknown(value, start, end, checked, preliminary)
        merged = merge_intervals((row["start_at"], row["end_at"]) for row in rows)
        overlapping = [(left, right) for left, right in merged if left < end and right > start]
        status = "BUSY" if overlapping else "FREE"
        policy = room["policy"]
        next_slot = None
        decision, reason = "ALLOW", "CALENDAR_FREE"
        candidate_start = max(start, checked)
        if not room["enabled"]:
            status, decision, reason = "UNKNOWN", "DENY", "ROOM_DISABLED"
        elif policy == "DISABLED":
            decision, reason = "DENY", "DELIVERY_DISABLED"
        elif policy == "MANAGER_APPROVAL":
            decision, reason = "NEEDS_APPROVAL", "MANAGER_APPROVAL_REQUIRED"
        elif policy == "BUSINESS_HOURS_ONLY" and not room["working_hours"]:
            decision, reason = "UNKNOWN", "WORKING_HOURS_NOT_CONFIGURED"
        else:
            occupied = [] if policy == "ALLOW_DOOR_HANDOFF" else merged
            limits = (merge_intervals(business_windows(candidate_start, horizon, room["timezone"], room["working_hours"]))
                      if policy == "BUSINESS_HOURS_ONLY" else [(candidate_start, horizon)])
            slots = [slot for left, right in limits if (slot := first_gap(occupied, left, right, end-start)) is not None]
            next_slot = min(slots) if slots else None
            if policy == "ALLOW_DOOR_HANDOFF" and overlapping:
                reason = "DOOR_HANDOFF_ALLOWED"
            elif overlapping:
                decision, reason = "DEFER", "ROOM_BOOKED"
            elif policy == "BUSINESS_HOURS_ONLY" and next_slot != start:
                decision, reason = "DEFER", "OUTSIDE_BUSINESS_HOURS"
        if end <= checked:
            decision, reason = "UNKNOWN", "WINDOW_EXPIRED"
        elif preliminary and decision == "ALLOW":
            decision, reason = "UNKNOWN", "ETA_NOT_PROVIDED"
        valid_until = min(checked + timedelta(seconds=self.settings.evidence_seconds), end)
        if decision == "UNKNOWN":
            valid_until = checked
        return {"roomId": value, "calendarStatus": status, "deliveryDecision": decision, "deliveryAllowed": decision == "ALLOW",
                "reasonCode": reason, "deliveryPolicy": policy, "nextAvailableFrom": next_slot.isoformat() if next_slot else None,
                "busyUntil": max(right for _, right in overlapping).isoformat() if overlapping else None,
                "checkedAt": checked.isoformat(), "validUntil": valid_until.isoformat(), "calendarVersion": room["version"],
                "dataSource": "CALENDAR", "checkType": "PRELIMINARY" if preliminary else "ARRIVAL_WINDOW",
                "arrivalWindow": {"start": start.isoformat(), "end": end.isoformat()},
                "calendarChanged": known_version is not None and known_version != room["version"]}


def vietnamese_feedback(evidence, zone="Asia/Ho_Chi_Minh"):
    room = evidence["roomId"]
    status, decision = evidence["calendarStatus"], evidence["deliveryDecision"]
    if status == "UNKNOWN":
        return f"Chưa xác minh được lịch phòng {room}; không coi kết quả này là cho phép giao tài liệu."
    text = f"Phòng {room} {'có lịch bận' if status == 'BUSY' else 'không có lịch bận'} trong khoảng giao nhận đã kiểm tra."
    if evidence.get("busyUntil"):
        until = utc(evidence["busyUntil"]).astimezone(ZoneInfo(zone)).strftime("%H:%M %d/%m/%Y")
        text += f" Khoảng lịch bận kết thúc lúc {until}."
    if decision == "DEFER" and evidence.get("nextAvailableFrom"):
        next_at = utc(evidence["nextAvailableFrom"]).astimezone(ZoneInfo(zone)).strftime("%H:%M %d/%m/%Y")
        text += f" Thời điểm có thể xét giao tiếp theo theo lịch là {next_at}."
    elif decision == "NEEDS_APPROVAL":
        text += " Cần quản lý phê duyệt."
    elif decision == "DENY":
        text += " Chính sách phòng không cho phép giao."
    elif evidence["deliveryPolicy"] == "ALLOW_DOOR_HANDOFF" and decision == "ALLOW":
        text += " Chính sách cho phép bàn giao bên ngoài cửa."
    if evidence["checkType"] == "PRELIMINARY":
        text += " Đây là kiểm tra sơ bộ bằng thời gian di chuyển ước tính; cần kiểm tra lại với khoảng đến thực tế."
    text += " Lịch không xác nhận phòng trống người hay cấp quyền điều hướng."
    return text
