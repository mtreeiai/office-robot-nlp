# Architecture and database

```mermaid
flowchart LR
    UI[Browser /calendar/] -->|user cookie + Origin/CSRF| C[Calendar API]
    C --> AUTH[USER / ADMIN authorization]
    AUTH --> B[Booking and room service]
    B -->|atomic writes, versions, audit| DB[(Dedicated Calendar PostgreSQL)]
    C --> V[Deterministic Availability Service]
    R[Robot / Fake Robot] -->|distinct read bearer| RA[Robot Availability API]
    RA --> V
    NLP[Existing NLP HTTP v1] --> BR[Session Bridge]
    BR --> G[Existing language understanding + room resolver]
    G -->|feature-flagged enrichment| V
    V -->|consistent read-only snapshot| DB
    V --> E[Structured evidence + Vietnamese feedback]
    E --> BR
    BR --> CACHE[Existing message replay cache]
```

Calendar sends no ROS/Nav2 commands, dispatches no tasks and changes no lifecycle status. NLP records valid tasks through the existing pipeline. Calendar provides evidence for a separately implemented robot decision gate; no calendar data is delegated to an LLM.

## Persistence

Dedicated database, default schema `office_calendar`. Migration: `office_room_calendar/migrations/001_calendar.sql`. PostgreSQL `btree_gist` support is required; verified on PostgreSQL 17. Migrations are explicit and checksummed, never run automatically at startup. Three-second connection and five-second SQL statement timeouts. No fallback database or in-memory calendar.

```mermaid
erDiagram
    calendar_users ||--o{ calendar_bookings : organizes
    calendar_users ||--o{ calendar_sessions : authenticates
    calendar_users ||--o{ calendar_idempotency : owns
    calendar_users o|--o{ calendar_audit_logs : actor
    calendar_rooms ||--|| calendar_room_policies : policy
    calendar_rooms ||--|| calendar_room_versions : version
    calendar_rooms ||--o{ calendar_bookings : hosts
    calendar_booking_series o|--o{ calendar_bookings : groups
    calendar_users {
        uuid user_id PK
        text email UK
        text password_hash
        text role "USER or ADMIN"
        boolean enabled
    }
    calendar_rooms {
        text room_id PK "01 through 07"
        text display_name
        boolean bookable "default false"
        boolean enabled
    }
    calendar_bookings {
        uuid booking_id PK
        text room_id FK
        uuid organizer_id FK
        text title
        timestamptz start_at
        timestamptz end_at
        text status "CONFIRMED or CANCELLED"
        bigint revision
        uuid series_id FK
    }
    calendar_booking_series {
        uuid series_id PK
        text timezone
        integer occurrences
    }
    calendar_room_policies {
        text room_id PK,FK
        text policy
        text timezone
        jsonb working_hours
    }
    calendar_room_versions {
        text room_id PK,FK
        bigint version
    }
    calendar_sessions {
        text token_hash PK
        uuid user_id FK
        timestamptz expires_at
    }
    calendar_robot_tokens {
        text token_hash PK
        text name UK
        boolean enabled
    }
    calendar_login_attempts {
        text bucket_key PK
        integer attempts
        timestamptz expires_at
    }
    calendar_idempotency {
        uuid user_id PK,FK
        text request_key PK
        text fingerprint
        jsonb response
    }
    calendar_audit_logs {
        bigint audit_id PK
        uuid actor_id FK
        text action
        text entity_id
        jsonb changes
        timestamptz created_at
    }
    calendar_migrations {
        text name PK
        text checksum
    }
```

Only semantic room IDs/names are seeded; no navigation poses. Availability reads policy, version and confirmed intervals in one REPEATABLE READ, READ ONLY transaction.

## Transaction guarantees

- PostgreSQL enforces `EXCLUDE USING gist (room_id WITH =, tstzrange(start_at,end_at,'[)') WITH &&) WHERE status='CONFIRMED'`. Half-open intervals allow adjacent bookings and reject overlap even if service checks are bypassed.
- Create holds an actor/request-key advisory lock and commits its replay response with the booking. Identical retries survive restart; different content returns 409. Failed creates leave no idempotency record.
- Room-version rows lock in sorted order. Expected booking revision / room version reject stale writes. Moving a booking versions both rooms.
- Booking, audit and version updates commit together. Weekly create is atomic; one occurrence conflict rolls back the whole series.
- Out-of-band SQL is not a supported booking/policy write interface: it would bypass service version/audit semantics.
- Evidence reserves no future slot. Version changes invalidate old evidence even within its short TTL.

## Credentials and website

Argon2id passwords are provisioned by CLI. Random session token digests persist for 12 hours; current enabled/role state is checked per request. Cookies are HttpOnly, SameSite=Strict, scoped to `/v1/calendar`, Secure on HTTPS. Writes check exact Origin and session-bound HMAC CSRF token; login checks Origin/JSON. Persistent login limits: eight attempts/email and 100/network per 15 minutes; password verification concurrency is bounded.

Robot token digests are stored separately and authenticate only the read API. Browser code contains no backend bearer, robot secret or localStorage credential. DOM text rendering protects meeting/room titles. Local scripts are enforced by CSP; generated FullCalendar layout needs inline styles. Runtime errors return safe messages without database credentials or raw internal errors.
