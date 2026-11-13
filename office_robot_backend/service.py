"""Single-robot business transactions. No LLM, navigation or HTTP calls here."""

import hashlib
import json
import math
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from office_robot_backend.repository import json_value


class OperationError(Exception):
    def __init__(self, code, message, status=409):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def require(condition, message, code="INVALID_TRANSITION", status=409):
    if not condition:
        raise OperationError(code, message, status)


def identifier():
    return str(uuid4())


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def fingerprint(value):
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def utcnow():
    return datetime.now(timezone.utc)


def get(tx, table, record_id):
    # Only internal constant table names reach this function.
    from psycopg import sql
    row = tx.one(sql.SQL("SELECT * FROM {} WHERE id=%s").format(sql.Identifier(table)), (record_id,))
    require(row is not None, "Operational resource not found.", "NOT_FOUND", 404)
    return row


class DeliveryService:
    def __init__(self, repository, clock=utcnow, heartbeat_seconds=60):
        self.repository, self.clock, self.heartbeat_seconds = repository, clock, heartbeat_seconds

    def authenticate(self, token, kind):
        require(bool(token), "Operational credentials required.", "UNAUTHORIZED", 401)
        with self.repository.transaction() as tx:
            if kind == "operator":
                row = tx.one("SELECT id FROM operator_accounts WHERE token_hash=%s AND enabled", (digest(token),))
            else:
                row = tx.one("SELECT id FROM robots WHERE token_hash=%s", (digest(token),))
            require(row is not None, "Operational credentials rejected.", "UNAUTHORIZED", 401)
            return row["id"]

    def provision(self, operator_id, operator_token, robot_id, robot_token, map_id, locations, frame_id="map"):
        """Explicit administrative bootstrap; immutable map versions and geometry."""
        require(len(operator_token) >= 24 and len(robot_token) >= 24 and operator_token != robot_token,
                "Use distinct random operational tokens of at least 24 characters.", "INVALID_CONFIGURATION", 422)
        require(bool(locations) and bool(frame_id), "Map locations and frame are required.", "INVALID_CONFIGURATION", 422)
        for location in locations:
            pose = location["pose"]
            require(set(pose) == {"x", "y", "yaw", "frameId"} and pose["frameId"] == frame_id
                    and all(type(pose[key]) in (int, float) and math.isfinite(pose[key]) for key in ("x", "y", "yaw")),
                    "Every location requires a finite pose in the configured map frame.", "INVALID_LOCATION", 422)
            require(type(location["pickupAllowed"]) is bool and type(location["deliveryAllowed"]) is bool,
                    "Location roles must be booleans.", "INVALID_LOCATION", 422)
        require(len({location["id"] for location in locations}) == len(locations), "Duplicate map location.", "INVALID_LOCATION", 422)
        with self.repository.transaction() as tx:
            tx.execute("INSERT INTO operator_accounts(id,token_hash) VALUES (%s,%s) ON CONFLICT(id) DO NOTHING", (operator_id, digest(operator_token)))
            require(get(tx, "operator_accounts", operator_id)["token_hash"] == digest(operator_token), "Operator token differs from provisioned token.")
            tx.execute("INSERT INTO map_versions(id,frame_id) VALUES (%s,%s) ON CONFLICT(id) DO NOTHING", (map_id, frame_id))
            require(get(tx, "map_versions", map_id)["frame_id"] == frame_id, "Map version is immutable.")
            for location in locations:
                values = (location["id"], map_id, location["name"], location["pickupAllowed"], location["deliveryAllowed"], json_value(location["pose"]))
                tx.execute("INSERT INTO locations(id,map_version_id,name,pickup_allowed,delivery_allowed,pose) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", values)
                saved = tx.one("SELECT * FROM locations WHERE id=%s AND map_version_id=%s", (location["id"], map_id))
                require((saved["name"], saved["pickup_allowed"], saved["delivery_allowed"], saved["pose"]) ==
                        (location["name"], location["pickupAllowed"], location["deliveryAllowed"], location["pose"]), "Map version is immutable; create a new version.")
            tx.execute("INSERT INTO robots(id,token_hash,map_version_id) VALUES (%s,%s,%s) ON CONFLICT(id) DO NOTHING", (robot_id, digest(robot_token), map_id))
            robot = get(tx, "robots", robot_id)
            require(robot["token_hash"] == digest(robot_token) and robot["map_version_id"] == map_id, "Robot provisioning differs from existing configuration.")

    def _locations(self, tx, task):
        map_record = get(tx, "map_versions", task["map_version_id"])
        require(map_record["active"], "Map version is inactive.", "INVALID_LOCATION")
        result = []
        for field, role in (("pickup_location_id", "pickup_allowed"), ("delivery_location_id", "delivery_allowed")):
            location = tx.one("SELECT * FROM locations WHERE map_version_id=%s AND id=%s", (task["map_version_id"], task[field]))
            require(location is not None and location["enabled"] and location[role], "Pickup or delivery location is not eligible.", "INVALID_LOCATION")
            result.append(location)
        return result

    def draft(self, operator_id, key, semantic, map_id, pickups, dependencies=()):
        require(semantic.get("dialogueStatus") in {"TASK_READY", "BATCH_READY"} and semantic.get("batch"),
                "A validated NLP result is required.", "INVALID_DRAFT", 422)
        source = semantic["batch"]
        tasks = source["tasks"]
        require(bool(tasks) and set(pickups) == {task["taskId"] for task in tasks},
                "Select one pickup location for every task; no default pickup is inferred.", "INVALID_DRAFT", 422)
        edges = {tuple(edge) for edge in dependencies}
        if source["orderPolicy"] == "STRICT_ORDER":
            edges.update((i, i + 1) for i in range(len(tasks) - 1))
        require(all(len(edge) == 2 and all(type(i) is int and 0 <= i < len(tasks) for i in edge) and edge[0] != edge[1]
                    for edge in edges), "Invalid mandatory dependency.", "INVALID_DEPENDENCY", 422)
        remaining = set(range(len(tasks)))
        while remaining:
            roots = {i for i in remaining if not any(target == i and predecessor in remaining for predecessor, target in edges)}
            require(bool(roots), "Mandatory dependencies contain a cycle.", "INVALID_DEPENDENCY", 422)
            remaining -= roots
        content_hash = fingerprint({"sessionId": semantic["sessionId"], "messageId": semantic["messageId"],
                                    "mapId": map_id, "pickups": pickups, "dependencies": sorted(map(tuple, dependencies))})
        with self.repository.transaction() as tx:
            require(get(tx, "operator_accounts", operator_id)["enabled"], "Operator is disabled.", "UNAUTHORIZED", 401)
            previous = tx.one("SELECT * FROM delivery_requests WHERE operator_id=%s AND idempotency_key=%s", (operator_id, key))
            if previous:
                require(previous["fingerprint"] == content_hash, "Request key reused with different content.", "IDEMPOTENCY_CONFLICT")
                return self._request(tx, previous["id"], operator_id)
            for task in tasks:
                require(not tx.one("SELECT id FROM delivery_tasks WHERE source_task_id=%s", (task["taskId"],)),
                        "This NLP task already belongs to an operational request.", "DUPLICATE_SOURCE")
                require(task["documentText"] and task["sequenceIndex"] == tasks.index(task), "Invalid NLP task snapshot.", "INVALID_DRAFT", 422)
                self._locations(tx, {"map_version_id": map_id, "pickup_location_id": pickups[task["taskId"]], "delivery_location_id": task["destinationId"]})
            request_id = identifier()
            tx.insert("delivery_requests", id=request_id, operator_id=operator_id, idempotency_key=key,
                      fingerprint=content_hash, source=json_value(semantic), order_policy=source["orderPolicy"] or "PRIORITY")
            task_ids = []
            for task in tasks:
                task_id = identifier()
                task_ids.append(task_id)
                tx.insert("delivery_tasks", id=task_id, request_id=request_id, source_task_id=task["taskId"],
                          sequence_index=task["sequenceIndex"], document_text=task["documentText"], map_version_id=map_id,
                          pickup_location_id=pickups[task["taskId"]], delivery_location_id=task["destinationId"],
                          priority={"LOW": 0, "NORMAL": 1, "HIGH": 2}[task["priority"]])
            for predecessor, target in edges:
                tx.insert("task_dependencies", task_id=task_ids[target], predecessor_id=task_ids[predecessor])
            return self._request(tx, request_id, operator_id)

    def find_request(self, operator_id, key, source_input):
        """Retry a durable Draft creation even after its NLP session has expired."""
        source_input = {**source_input, "dependencies": sorted(map(tuple, source_input["dependencies"]))}
        with self.repository.transaction() as tx:
            previous = tx.one("SELECT * FROM delivery_requests WHERE operator_id=%s AND idempotency_key=%s", (operator_id, key))
            if previous:
                require(previous["fingerprint"] == fingerprint(source_input), "Request key reused with changed content.", "IDEMPOTENCY_CONFLICT")
                return self._request(tx, previous["id"], operator_id)
            return None

    def _request(self, tx, request_id, operator_id):
        request = get(tx, "delivery_requests", request_id)
        require(request["operator_id"] == operator_id, "Request belongs to another operator.", "FORBIDDEN", 403)
        tasks = tx.all("SELECT * FROM delivery_tasks WHERE request_id=%s ORDER BY sequence_index", (request_id,))
        return {"requestId": request_id, "state": request["state"], "orderPolicy": request["order_policy"],
                "tasks": tasks, "createdAt": request["created_at"], "confirmedAt": request["confirmed_at"]}

    def request(self, operator_id, request_id):
        with self.repository.transaction() as tx:
            return self._request(tx, request_id, operator_id)

    def confirm(self, operator_id, request_id):
        with self.repository.transaction() as tx:
            request = self._request(tx, request_id, operator_id)
            if request["state"] == "CONFIRMED":
                return request
            require(request["state"] == "DRAFT", "Only a draft can be confirmed.")
            for task in request["tasks"]:
                self._locations(tx, task)
            # All-or-nothing validation and original-order FIFO allocation.
            for task in request["tasks"]:
                tx.execute("UPDATE delivery_tasks SET state='QUEUED',queue_order=nextval('delivery_queue_order') WHERE id=%s", (task["id"],))
            tx.update("delivery_requests", request_id, state="CONFIRMED", confirmed_at=self.clock())
            return self._request(tx, request_id, operator_id)

    def _active(self, tx, robot_id):
        return tx.one("SELECT * FROM robot_assignments WHERE robot_id=%s AND resolved_at IS NULL", (robot_id,))

    def heartbeat(self, robot_id, boot_id, active_id, safe_stopped):
        with self.repository.transaction() as tx:
            robot = get(tx, "robots", robot_id)
            assignment = self._active(tx, robot_id)
            if assignment:
                consistent = active_id == assignment["id"] and robot["boot_id"] in {None, boot_id}
                if not consistent and assignment["state"] != "OFFERED":
                    tx.update("robot_assignments", assignment["id"], state="UNKNOWN")
                state = "BUSY" if consistent and assignment["state"] not in {"UNKNOWN", "FAILED", "CANCELLED", "CANCEL_REQUESTED"} else "UNKNOWN"
            else:
                state = "IDLE" if active_id is None and safe_stopped else "UNKNOWN"
            tx.update("robots", robot_id, state=state, boot_id=boot_id, reported_assignment_id=active_id,
                      safe_stopped=safe_stopped, last_seen=self.clock())
            return {"state": state}

    def schedule(self, robot_id):
        with self.repository.transaction() as tx:
            robot = get(tx, "robots", robot_id)
            active = self._active(tx, robot_id)
            if active:
                return self._assignment(tx, active["id"])
            if (robot["state"] != "IDLE" or robot["last_seen"] is None
                    or robot["last_seen"] < self.clock() - timedelta(seconds=self.heartbeat_seconds)):
                tx.update("robots", robot_id, state="UNKNOWN")
                return None
            candidates = tx.all("""SELECT t.* FROM delivery_tasks t JOIN delivery_requests r ON r.id=t.request_id
                WHERE t.state='QUEUED' AND r.state='CONFIRMED' AND t.map_version_id=%s
                AND NOT EXISTS (SELECT 1 FROM task_dependencies d JOIN delivery_tasks p ON p.id=d.predecessor_id
                                WHERE d.task_id=t.id AND p.state<>'COMPLETED')
                ORDER BY t.priority DESC,t.queue_order FOR UPDATE OF t""", (robot["map_version_id"],))
            for task in candidates:
                try:
                    pickup, delivery = self._locations(tx, task)
                except OperationError:
                    continue
                assignment_id = identifier()
                tx.insert("robot_assignments", id=assignment_id, robot_id=robot_id, task_id=task["id"], state="OFFERED")
                for leg, location in (("PICKUP", pickup), ("DELIVERY", delivery)):
                    tx.insert("navigation_goals", id=identifier(), assignment_id=assignment_id, leg=leg,
                              pose=json_value(location["pose"]), state="BLOCKED")
                tx.update("delivery_tasks", task["id"], state="ASSIGNED")
                tx.update("robots", robot_id, state="BUSY")
                self._notify(tx, assignment_id, "ASSIGNMENT_AVAILABLE")
                return self._assignment(tx, assignment_id)
            return None

    def _assignment(self, tx, assignment_id):
        assignment = get(tx, "robot_assignments", assignment_id)
        goals = tx.all("SELECT * FROM navigation_goals WHERE assignment_id=%s ORDER BY leg DESC", (assignment_id,))
        return {"assignmentId": assignment_id, "robotId": assignment["robot_id"], "taskId": assignment["task_id"],
                "state": assignment["state"], "resolvedAt": assignment["resolved_at"], "lastSequence": assignment["last_sequence"],
                "goals": goals}

    def assignment(self, robot_id, assignment_id):
        with self.repository.transaction() as tx:
            require(get(tx, "robot_assignments", assignment_id)["robot_id"] == robot_id, "Assignment belongs to another robot.", "FORBIDDEN", 403)
            return self._assignment(tx, assignment_id)

    def _notify(self, tx, assignment_id, kind):
        tx.insert("outbox_events", id=identifier(), assignment_id=assignment_id, kind=kind,
                  payload=json_value({"assignmentId": assignment_id}))

    def issue_qr(self, operator_id, assignment_id, leg):
        with self.repository.transaction() as tx:
            assignment = get(tx, "robot_assignments", assignment_id)
            task = get(tx, "delivery_tasks", assignment["task_id"])
            self._request(tx, task["request_id"], operator_id)
            require(leg in {"PICKUP", "DELIVERY"} and assignment["state"] == f"WAITING_{leg}_QR", "QR is available only after arrival at that stage.")
            token, challenge_id = secrets.token_urlsafe(32), identifier()
            # Rotation expires earlier unconsumed challenges for this stage.
            tx.execute("UPDATE qr_challenges SET expires_at=%s WHERE assignment_id=%s AND leg=%s AND consumed_by IS NULL", (self.clock(), assignment_id, leg))
            tx.insert("qr_challenges", id=challenge_id, assignment_id=assignment_id, leg=leg, token_hash=digest(token),
                      issued_by=operator_id, expires_at=self.clock() + timedelta(minutes=10))
            return {"challengeId": challenge_id, "token": token, "assignmentId": assignment_id, "leg": leg}

    def ingest(self, robot_id, event):
        event_hash = fingerprint(event)
        with self.repository.transaction() as tx:
            assignment = get(tx, "robot_assignments", event["assignmentId"])
            require(assignment["robot_id"] == robot_id, "Assignment belongs to another robot.", "FORBIDDEN", 403)
            duplicate = tx.one("SELECT * FROM execution_events WHERE id=%s", (event["eventId"],))
            if duplicate:
                require(duplicate["fingerprint"] == event_hash, "Event ID reused with changed content.", "IDEMPOTENCY_CONFLICT")
                return self._assignment(tx, assignment["id"])
            require(event["sequence"] == assignment["last_sequence"] + 1, "Event sequence must be contiguous.", "EVENT_SEQUENCE_CONFLICT")
            require(assignment["resolved_at"] is None, "Assignment is already resolved.")
            kind, payload = event["kind"], event["payload"]
            if kind == "ACK":
                require(assignment["state"] == "OFFERED", "Assignment cannot be acknowledged in this state.")
                tx.update("robot_assignments", assignment["id"], state="ACKED")
                tx.execute("UPDATE navigation_goals SET state='READY' WHERE assignment_id=%s AND leg='PICKUP'", (assignment["id"],))
                tx.execute("UPDATE outbox_events SET acknowledged_at=%s WHERE assignment_id=%s AND acknowledged_at IS NULL", (self.clock(), assignment["id"]))
            elif kind in {"NAV_STARTED", "ARRIVED"}:
                goal = get(tx, "navigation_goals", payload.get("goalId"))
                leg = "DELIVERY" if assignment["picked_up"] else "PICKUP"
                require(goal["assignment_id"] == assignment["id"] and goal["leg"] == leg, "Wrong navigation goal/leg.")
                expected = "ACKED" if kind == "NAV_STARTED" else f"NAVIGATING_{leg}"
                require(assignment["state"] == expected and goal["state"] == ("READY" if kind == "NAV_STARTED" else "STARTED"), "Invalid navigation transition.")
                tx.update("navigation_goals", goal["id"], state="STARTED" if kind == "NAV_STARTED" else "ARRIVED")
                tx.update("robot_assignments", assignment["id"], state=f"NAVIGATING_{leg}" if kind == "NAV_STARTED" else f"WAITING_{leg}_QR")
            elif kind == "QR_SCANNED":
                challenge = get(tx, "qr_challenges", payload.get("challengeId"))
                require(challenge["assignment_id"] == assignment["id"] and challenge["consumed_by"] is None
                        and challenge["expires_at"] > self.clock() and secrets.compare_digest(challenge["token_hash"], digest(payload.get("token", ""))),
                        "QR challenge is invalid, expired or already consumed.", "QR_REJECTED")
                leg = challenge["leg"]
                require(assignment["state"] == f"WAITING_{leg}_QR", "QR stage does not match the assignment.", "QR_REJECTED")
                tx.update("qr_challenges", challenge["id"], consumed_by=event["eventId"])
                if leg == "PICKUP":
                    tx.update("robot_assignments", assignment["id"], picked_up=True, state="ACKED")
                    tx.execute("UPDATE navigation_goals SET state='READY' WHERE assignment_id=%s AND leg='DELIVERY'", (assignment["id"],))
                    self._notify(tx, assignment["id"], "DELIVERY_LEG_RELEASED")
                else:
                    tx.update("robot_assignments", assignment["id"], state="COMPLETED", resolved_at=self.clock())
                    tx.update("delivery_tasks", assignment["task_id"], state="COMPLETED")
                    tx.update("robots", robot_id, state="UNKNOWN")  # Wait for fresh edge-idle reconciliation.
            elif kind in {"EXECUTION_UNKNOWN", "FAILED", "CANCELLED_SAFE"}:
                require(kind != "CANCELLED_SAFE" or assignment["state"] == "CANCEL_REQUESTED", "Cancellation was not requested.")
                state = {"EXECUTION_UNKNOWN": "UNKNOWN", "FAILED": "FAILED", "CANCELLED_SAFE": "CANCELLED"}[kind]
                tx.update("robot_assignments", assignment["id"], state=state)
                tx.update("robots", robot_id, state="UNKNOWN")
                if kind == "FAILED" and payload.get("goalId"):
                    goal = get(tx, "navigation_goals", payload["goalId"])
                    require(goal["assignment_id"] == assignment["id"], "Wrong failed navigation goal.")
                    tx.update("navigation_goals", goal["id"], state="FAILED")
                # Even FAILED/CANCELLED holds the robot until explicit reconciliation.
            else:
                raise OperationError("INVALID_EVENT", "Unsupported execution event.", 422)
            safe_payload = {key: value for key, value in payload.items() if key in {"goalId", "challengeId"}}
            tx.insert("execution_events", id=event["eventId"], assignment_id=assignment["id"], sequence=event["sequence"],
                      fingerprint=event_hash, kind=kind, payload=json_value(safe_payload))
            tx.update("robot_assignments", assignment["id"], last_sequence=event["sequence"])
            tx.update("robots", robot_id, last_seen=self.clock())
            return self._assignment(tx, assignment["id"])

    def cancel(self, operator_id, request_id):
        with self.repository.transaction() as tx:
            request = self._request(tx, request_id, operator_id)
            for task in request["tasks"]:
                if task["state"] in {"DRAFT", "QUEUED"}:
                    tx.update("delivery_tasks", task["id"], state="CANCELLED")
                elif task["state"] == "ASSIGNED":
                    assignment = tx.one("SELECT * FROM robot_assignments WHERE task_id=%s AND resolved_at IS NULL", (task["id"],))
                    tx.update("robot_assignments", assignment["id"], state="CANCEL_REQUESTED")
                    self._notify(tx, assignment["id"], "CANCEL_REQUESTED")
            tx.update("delivery_requests", request_id, state="CANCELLED")
            return self._request(tx, request_id, operator_id)

    def reconcile(self, operator_id, assignment_id, package_accounted_for):
        with self.repository.transaction() as tx:
            assignment = get(tx, "robot_assignments", assignment_id)
            task = get(tx, "delivery_tasks", assignment["task_id"])
            self._request(tx, task["request_id"], operator_id)
            if assignment["resolved_at"] is not None:
                return self._assignment(tx, assignment_id)
            robot = get(tx, "robots", assignment["robot_id"])
            require(assignment["state"] in {"UNKNOWN", "FAILED", "CANCELLED"} and package_accounted_for
                    and robot["safe_stopped"] and robot["reported_assignment_id"] == assignment_id
                    and robot["last_seen"] is not None and robot["last_seen"] > self.clock() - timedelta(seconds=self.heartbeat_seconds),
                    "Fresh stopped-edge evidence and explicit package accounting are required.")
            terminal = "CANCELLED" if assignment["state"] == "CANCELLED" else "FAILED"
            tx.update("robot_assignments", assignment_id, state=terminal, resolved_at=self.clock())
            tx.update("delivery_tasks", task["id"], state=terminal)
            tx.update("robots", robot["id"], state="UNKNOWN")
            self._notify(tx, assignment_id, "OPERATOR_RECONCILED")
            return self._assignment(tx, assignment_id)
