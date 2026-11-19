# Calendar API contract

Enabled by `CALENDAR_ENABLED=true`. Calendar credentials are separate from the existing NLP backend bearer. Responses are no-store. Requests use strict JSON; unknown fields/type errors return 422. All times require ISO-8601 explicit offsets and are returned in UTC.

## Browser / operator routes

Every `/v1/calendar/*` route except login requires a Calendar user session. Writes additionally require exact `Origin: <CALENDAR_PUBLIC_ORIGIN>` and `X-CSRF-Token` from login/session. USER may create in enabled/bookable rooms and change own bookings. ADMIN may change any booking/room/policy and read audit. Shared users see calendar titles/organizer IDs; robot evidence omits them.

| Method | Path | Inputs | Output |
| --- | --- | --- | --- |
| POST | `/v1/calendar/auth/login` | email/password; Origin, JSON | user, csrfToken, timezone; cookie |
| GET | `/v1/calendar/auth/session` | cookie | user, csrfToken, timezone |
| POST | `/v1/calendar/auth/logout` | write credentials | `{"status":"SIGNED_OUT"}` |
| GET | `/v1/calendar/rooms` | cookie | `{"rooms":[...]}` |
| PATCH | `/v1/calendar/rooms/{roomId}` | ADMIN, expectedVersion + room fields | updated room |
| GET | `/v1/calendar/bookings` | start/end, optional roomId | bookings including cancellations |
| POST | `/v1/calendar/bookings` | booking create | 201, bookings + seriesId |
| PATCH | `/v1/calendar/bookings/{bookingId}` | expectedRevision + changes | updated booking |
| DELETE | `/v1/calendar/bookings/{bookingId}` | optional expectedRevision query | cancelled booking |
| GET | `/v1/calendar/availability` | roomId, optional start AND end | evidence |
| GET | `/v1/calendar/free-slots` | roomId/start/durationSeconds (default 180, max 28800) | nextAvailableFrom, durationSeconds, availability |
| GET | `/v1/calendar/audit` | ADMIN, limit 1–500 (default 100) | entries |

Login input/output:

```json
{"email":"operator@example.org","password":"<privately supplied password>"}
```

```json
{"user":{"userId":"<uuid>","displayName":"Operator","role":"USER"},"csrfToken":"<session-bound token>","timezone":"Asia/Ho_Chi_Minh"}
```

Create input/output (duration >0 and ≤24 hours):

```json
{"roomId":"02","title":"Họp dự án","startAt":"2026-11-20T14:00:00+07:00","endAt":"2026-11-20T16:00:00+07:00","idempotencyKey":"operator-unique-create-001"}
```

```json
{"bookings":[{"bookingId":"<uuid>","roomId":"02","organizerId":"<uuid>","title":"Họp dự án","startAt":"2026-11-20T07:00:00+00:00","endAt":"2026-11-20T09:00:00+00:00","status":"CONFIRMED","revision":1,"seriesId":null}],"seriesId":null}
```

Optional `"recurrence":{"weeks":4,"timezone":"Asia/Ho_Chi_Minh"}` creates 2–52 weekly occurrences, including the first. Local wall time is preserved across DST; imaginary repeated times are rejected. Every occurrence must succeed atomically. Edit/cancel affects only the selected occurrence; no series-wide edit/cancel.

Update: `{"expectedRevision":1,"title":"Tên mới","endAt":"2026-11-20T17:00:00+07:00"}`. Changed fields: roomId/title/startAt/endAt. Cancel is soft deletion; repeating without revision is idempotent, while a stale explicit revision returns 409. Confirmed intervals are half-open, so adjacent bookings are allowed.

List interval: >0 and ≤93 days, max 5000 records (422 requires a smaller range). Create retries with same actor/key/content return the original response; changed content with that key returns 409.

Room example (read current calendarVersion first):

```json
{"expectedVersion":1,"bookable":true,"enabled":true,"deliveryPolicy":"BUSINESS_HOURS_ONLY","timezone":"Asia/Ho_Chi_Minh","workingHours":{"0":[["08:00","12:00"],["13:00","17:00"]],"1":[["08:00","17:00"]]}}
```

Other room fields: displayName/description. Weekdays: `0` Monday to `6` Sunday; increasing local HH:MM intervals. Split overnight spans across days. Adjacent/overlapping opening intervals are merged. Missing business-hours configuration gives UNKNOWN delivery decision. All seven rooms seed unbookable, default BLOCK_WHEN_BUSY; admin approval is required to open booking.

## Robot read-only

`POST /v1/robot/availability/check`, `Authorization: Bearer <distinct CALENDAR_ROBOT_TOKEN>`. Browser cookie/backend bearer cannot authenticate this endpoint. Robot token cannot manage bookings or create NLP sessions.

```json
{"roomId":"02","operation":"DOCUMENT_DELIVERY","arrivalWindow":{"start":"2026-11-20T14:30:00+07:00","end":"2026-11-20T14:35:00+07:00"},"knownCalendarVersion":7}
```

Illustration: booking 14:00–16:00, checked at 14:25:

```json
{
  "roomId":"02", "calendarStatus":"BUSY", "deliveryDecision":"DEFER", "deliveryAllowed":false,
  "deliveryPolicy":"BLOCK_WHEN_BUSY", "reasonCode":"ROOM_BOOKED",
  "busyUntil":"2026-11-20T09:00:00+00:00", "nextAvailableFrom":"2026-11-20T09:00:00+00:00",
  "checkedAt":"2026-11-20T07:25:00+00:00", "validUntil":"2026-11-20T07:25:30+00:00",
  "calendarVersion":8, "calendarChanged":true, "dataSource":"CALENDAR", "checkType":"ARRIVAL_WINDOW",
  "arrivalWindow":{"start":"2026-11-20T07:30:00+00:00","end":"2026-11-20T07:35:00+00:00"}
}
```

Window: >0 and ≤8 hours, start within the next 30 days. Omit arrivalWindow for PRELIMINARY using configured travel/handoff estimates; preliminary never grants ALLOW. Expired windows yield UNKNOWN. Next-slot search covers 30 days from later of check/start; no fitting slot returns null. DB outage during availability yields UNKNOWN; outage during credential validation returns structured 503.

| Policy / condition | Calendar status | Delivery decision |
| --- | --- | --- |
| BLOCK_WHEN_BUSY | FREE / BUSY | ALLOW / DEFER |
| ALLOW_DOOR_HANDOFF | FREE / BUSY unchanged | ALLOW for explicit valid window, including BUSY |
| BUSINESS_HOURS_ONLY | FREE / BUSY unchanged | ALLOW only if full window inside working hours and unbooked; else DEFER; missing hours UNKNOWN |
| MANAGER_APPROVAL | FREE / BUSY unchanged | NEEDS_APPROVAL (no approval workflow here) |
| DISABLED | FREE / BUSY unchanged | DENY |
| Disabled room | UNKNOWN | DENY |
| DB failure | UNKNOWN | UNKNOWN; deliveryAllowed=false, immediate expiry |

TTL defaults to 30 seconds (config range 1–60), capped by window end; UNKNOWN expires immediately. Supported booking/room/policy writes increment the room version. `calendarChanged` compares known version with the current snapshot; it cannot detect a future write. No reservation, push invalidation or navigation authorization.

## Optional NLP integration

`CALENDAR_NLP_ENABLED=false` by default: exact original message envelope. Enabled with Calendar: adds `roomAvailability`, an array with one evidence object per ready-envelope task plus taskId. Existing task/batch IDs/fields/constraints/priority/order/sequence/registration/dialogueStatus stay unchanged. Clarification/other turns without a ready envelope return an empty array.

Vietnamese feedback is appended deterministically after resolution, with busy end/next slot and estimate caveat. No live calendar data is sent to Gemini. Message retries replay exactly the original response, including expired evidence; use the separate API to refresh. Existing task/batch snapshot DTOs remain unchanged.

## Errors

```json
{"error":{"code":"BOOKING_CONFLICT","message":"Confirmed bookings overlap in this room."}}
```

401 UNAUTHORIZED/INVALID_CREDENTIALS; 403 FORBIDDEN/ORIGIN_REJECTED/CSRF_REJECTED; 404 ROOM_NOT_FOUND/BOOKING_NOT_FOUND; 409 BOOKING_CONFLICT/STALE_VERSION/IDEMPOTENCY_CONFLICT/ROOM_NOT_BOOKABLE/BOOKING_CANCELLED; 422 invalid request/time/window/policy/recurrence/operation/range; 429 LOGIN_THROTTLED; 503 CALENDAR_UNAVAILABLE; 500 CALENDAR_INTERNAL_ERROR. No raw database errors/credentials. Unknown never becomes free.
