CREATE TABLE calendar_users (
    user_id uuid PRIMARY KEY, email text NOT NULL UNIQUE, display_name text NOT NULL,
    password_hash text NOT NULL, role text NOT NULL CHECK(role IN ('USER','ADMIN')),
    enabled boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE calendar_rooms (
    room_id text PRIMARY KEY CHECK(room_id IN ('01','02','03','04','05','06','07')),
    display_name text NOT NULL, description text NOT NULL DEFAULT '',
    bookable boolean NOT NULL DEFAULT false, enabled boolean NOT NULL DEFAULT true
);
CREATE TABLE calendar_room_policies (
    room_id text PRIMARY KEY REFERENCES calendar_rooms(room_id),
    policy text NOT NULL DEFAULT 'BLOCK_WHEN_BUSY' CHECK(policy IN
      ('BLOCK_WHEN_BUSY','ALLOW_DOOR_HANDOFF','BUSINESS_HOURS_ONLY','MANAGER_APPROVAL','DISABLED')),
    timezone text NOT NULL DEFAULT 'Asia/Ho_Chi_Minh', working_hours jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE calendar_room_versions (
    room_id text PRIMARY KEY REFERENCES calendar_rooms(room_id), version bigint NOT NULL DEFAULT 1 CHECK(version>0)
);
CREATE TABLE calendar_booking_series (
    series_id uuid PRIMARY KEY, timezone text NOT NULL, occurrences integer NOT NULL CHECK(occurrences BETWEEN 2 AND 52)
);
CREATE TABLE calendar_bookings (
    booking_id uuid PRIMARY KEY, room_id text NOT NULL REFERENCES calendar_rooms(room_id),
    organizer_id uuid NOT NULL REFERENCES calendar_users(user_id), title text NOT NULL CHECK(length(title) BETWEEN 1 AND 160),
    start_at timestamptz NOT NULL, end_at timestamptz NOT NULL, status text NOT NULL DEFAULT 'CONFIRMED'
      CHECK(status IN ('CONFIRMED','CANCELLED')),
    series_id uuid REFERENCES calendar_booking_series(series_id), revision bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK(end_at>start_at), CHECK(end_at-start_at<=interval '24 hours'),
    CONSTRAINT confirmed_booking_no_overlap EXCLUDE USING gist
      (room_id WITH =, tstzrange(start_at,end_at,'[)') WITH &&) WHERE (status='CONFIRMED')
);
CREATE INDEX calendar_booking_window ON calendar_bookings(room_id,start_at,end_at) WHERE status='CONFIRMED';
CREATE TABLE calendar_audit_logs (
    audit_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, actor_id uuid REFERENCES calendar_users(user_id),
    action text NOT NULL, entity_id text, changes jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE calendar_sessions (
    token_hash text PRIMARY KEY, user_id uuid NOT NULL REFERENCES calendar_users(user_id),
    expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE calendar_robot_tokens (
    token_hash text PRIMARY KEY, name text NOT NULL UNIQUE, enabled boolean NOT NULL DEFAULT true
);
CREATE TABLE calendar_login_attempts (
    bucket_key text PRIMARY KEY, attempts integer NOT NULL, expires_at timestamptz NOT NULL
);
CREATE TABLE calendar_idempotency (
    user_id uuid NOT NULL REFERENCES calendar_users(user_id), request_key text NOT NULL,
    fingerprint text NOT NULL, response jsonb NOT NULL, PRIMARY KEY(user_id,request_key)
);
