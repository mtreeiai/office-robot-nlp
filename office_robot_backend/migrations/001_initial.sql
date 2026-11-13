CREATE TABLE operator_accounts (
    id text PRIMARY KEY, token_hash text NOT NULL UNIQUE, enabled boolean NOT NULL DEFAULT true
);
CREATE TABLE map_versions (
    id text PRIMARY KEY, frame_id text NOT NULL CHECK (length(frame_id)>0), active boolean NOT NULL DEFAULT true
);
CREATE TABLE locations (
    id text NOT NULL, map_version_id text NOT NULL REFERENCES map_versions(id), name text NOT NULL,
    pickup_allowed boolean NOT NULL, delivery_allowed boolean NOT NULL, enabled boolean NOT NULL DEFAULT true,
    pose jsonb NOT NULL CHECK (jsonb_typeof(pose)='object'), PRIMARY KEY(map_version_id,id)
);
CREATE TABLE delivery_requests (
    id text PRIMARY KEY, operator_id text NOT NULL REFERENCES operator_accounts(id),
    idempotency_key text NOT NULL, fingerprint text NOT NULL, source jsonb NOT NULL,
    order_policy text NOT NULL CHECK (order_policy IN ('PRIORITY','STRICT_ORDER')),
    state text NOT NULL DEFAULT 'DRAFT' CHECK (state IN ('DRAFT','CONFIRMED','CANCELLED')),
    created_at timestamptz NOT NULL DEFAULT now(), confirmed_at timestamptz,
    UNIQUE(operator_id,idempotency_key)
);
CREATE SEQUENCE delivery_queue_order;
CREATE TABLE delivery_tasks (
    id text PRIMARY KEY, request_id text NOT NULL REFERENCES delivery_requests(id),
    source_task_id text NOT NULL UNIQUE, sequence_index integer NOT NULL CHECK(sequence_index>=0),
    document_text text NOT NULL CHECK(length(document_text)>0), package_count integer NOT NULL DEFAULT 1 CHECK(package_count=1),
    map_version_id text NOT NULL, pickup_location_id text NOT NULL, delivery_location_id text NOT NULL,
    priority integer NOT NULL CHECK(priority IN (0,1,2)),
    state text NOT NULL DEFAULT 'DRAFT' CHECK(state IN ('DRAFT','QUEUED','ASSIGNED','COMPLETED','FAILED','CANCELLED')),
    queue_order bigint UNIQUE, UNIQUE(request_id,sequence_index),
    FOREIGN KEY(map_version_id,pickup_location_id) REFERENCES locations(map_version_id,id),
    FOREIGN KEY(map_version_id,delivery_location_id) REFERENCES locations(map_version_id,id)
);
CREATE TABLE task_dependencies (
    task_id text NOT NULL REFERENCES delivery_tasks(id), predecessor_id text NOT NULL REFERENCES delivery_tasks(id),
    PRIMARY KEY(task_id,predecessor_id), CHECK(task_id<>predecessor_id)
);
CREATE TABLE robots (
    id text PRIMARY KEY, token_hash text NOT NULL UNIQUE, map_version_id text NOT NULL REFERENCES map_versions(id),
    state text NOT NULL DEFAULT 'UNKNOWN' CHECK(state IN ('UNKNOWN','IDLE','BUSY')),
    last_seen timestamptz, boot_id text, reported_assignment_id text, safe_stopped boolean NOT NULL DEFAULT false
);
CREATE UNIQUE INDEX one_robot_v1 ON robots ((true));
CREATE TABLE robot_assignments (
    id text PRIMARY KEY, robot_id text NOT NULL REFERENCES robots(id), task_id text NOT NULL UNIQUE REFERENCES delivery_tasks(id),
    state text NOT NULL CHECK(state IN ('OFFERED','ACKED','NAVIGATING_PICKUP','WAITING_PICKUP_QR',
      'NAVIGATING_DELIVERY','WAITING_DELIVERY_QR','COMPLETED','FAILED','UNKNOWN','CANCEL_REQUESTED','CANCELLED')),
    last_sequence bigint NOT NULL DEFAULT 0, picked_up boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(), resolved_at timestamptz
);
CREATE UNIQUE INDEX one_unresolved_assignment ON robot_assignments(robot_id) WHERE resolved_at IS NULL;
CREATE TABLE navigation_goals (
    id text PRIMARY KEY, assignment_id text NOT NULL REFERENCES robot_assignments(id),
    leg text NOT NULL CHECK(leg IN ('PICKUP','DELIVERY')), pose jsonb NOT NULL,
    state text NOT NULL CHECK(state IN ('BLOCKED','READY','STARTED','ARRIVED','FAILED')),
    UNIQUE(assignment_id,leg)
);
CREATE TABLE qr_challenges (
    id text PRIMARY KEY, assignment_id text NOT NULL REFERENCES robot_assignments(id),
    leg text NOT NULL CHECK(leg IN ('PICKUP','DELIVERY')), token_hash text NOT NULL,
    expires_at timestamptz NOT NULL, consumed_by text UNIQUE, issued_by text NOT NULL REFERENCES operator_accounts(id)
);
CREATE TABLE execution_events (
    id text PRIMARY KEY, assignment_id text NOT NULL REFERENCES robot_assignments(id),
    sequence bigint NOT NULL CHECK(sequence>0), fingerprint text NOT NULL, kind text NOT NULL,
    payload jsonb NOT NULL, received_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(assignment_id,sequence)
);
CREATE TABLE outbox_events (
    id text PRIMARY KEY, assignment_id text NOT NULL REFERENCES robot_assignments(id),
    kind text NOT NULL, payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), acknowledged_at timestamptz
);
CREATE INDEX queue_eligible ON delivery_tasks(priority DESC,queue_order) WHERE state='QUEUED';
