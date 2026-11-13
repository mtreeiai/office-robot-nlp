"""Local SQLite mission state and atomic, ordered event outbox."""

import json
import hashlib
import sqlite3
from contextlib import contextmanager
from threading import RLock
from uuid import uuid4


class EdgeStore:
    def __init__(self, path):
        self._lock = RLock()
        self.connection = sqlite3.connect(str(path), check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS assignments (
                id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
                next_sequence INTEGER NOT NULL DEFAULT 0
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_assignment ON assignments(active) WHERE active=1;
            CREATE TABLE IF NOT EXISTS goals (
                id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL REFERENCES assignments(id),
                state TEXT NOT NULL, snapshot TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS outbox (
                event_id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL REFERENCES assignments(id),
                sequence INTEGER NOT NULL, payload TEXT NOT NULL, UNIQUE(assignment_id,sequence)
            );
            CREATE TABLE IF NOT EXISTS qr_scans (
                challenge_id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL REFERENCES assignments(id),
                token_hash TEXT NOT NULL
            );
        """)

    @contextmanager
    def transaction(self):
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self.connection
            except Exception:
                self.connection.rollback()
                raise
            else:
                self.connection.commit()

    def emit(self, connection, assignment_id, kind, payload=None):
        sequence = connection.execute("UPDATE assignments SET next_sequence=next_sequence+1 WHERE id=? RETURNING next_sequence",
                                      (assignment_id,)).fetchone()[0]
        event = {"eventId": str(uuid4()), "assignmentId": assignment_id, "sequence": sequence,
                 "kind": kind, "payload": payload or {}}
        connection.execute("INSERT INTO outbox VALUES (?,?,?,?)", (event["eventId"], assignment_id, sequence, json.dumps(event)))
        return event

    def accept(self, assignment):
        with self.transaction() as connection:
            previous = connection.execute("SELECT snapshot FROM assignments WHERE id=?", (assignment["assignmentId"],)).fetchone()
            if previous:
                def immutable(mission):
                    return (mission["robotId"], mission["taskId"],
                            sorted((goal["id"], goal["leg"], json.dumps(goal["pose"], sort_keys=True)) for goal in mission["goals"]))
                if immutable(json.loads(previous[0])) != immutable(assignment):
                    raise ValueError("Assignment ID reused with changed immutable mission data")
                return False
            if assignment["state"] != "OFFERED" or assignment["resolvedAt"] is not None:
                raise ValueError("A new edge may accept only a fresh OFFERED assignment")
            connection.execute("INSERT INTO assignments(id,snapshot) VALUES (?,?)", (assignment["assignmentId"], json.dumps(assignment)))
            for goal in assignment["goals"]:
                connection.execute("INSERT INTO goals VALUES (?,?,?,?)", (goal["id"], assignment["assignmentId"], "RECEIVED", json.dumps(goal)))
            self.emit(connection, assignment["assignmentId"], "ACK")
            return True

    def active(self):
        with self._lock:
            row = self.connection.execute("SELECT id FROM assignments WHERE active=1").fetchone()
            return row[0] if row else None

    def pending(self):
        with self._lock:
            return [json.loads(row[0]) for row in self.connection.execute("SELECT payload FROM outbox ORDER BY rowid")]

    def acknowledge(self, event_id):
        with self.transaction() as connection:
            connection.execute("DELETE FROM outbox WHERE event_id=?", (event_id,))

    def state(self, goal_id):
        with self._lock:
            row = self.connection.execute("SELECT state FROM goals WHERE id=?", (goal_id,)).fetchone()
            return row[0] if row else None

    def begin_navigation(self, goal):
        with self.transaction() as connection:
            changed = connection.execute("UPDATE goals SET state='START_REQUESTED' WHERE id=? AND state='RECEIVED'", (goal["id"],)).rowcount
            if changed:
                self.emit(connection, goal["assignment_id"], "NAV_STARTED", {"goalId": goal["id"]})
            return bool(changed)

    def navigation_result(self, goal_id, success):
        with self.transaction() as connection:
            goal = connection.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
            if goal is None or goal["state"] not in {"START_REQUESTED", "NAVIGATING"}:
                return False
            connection.execute("UPDATE goals SET state=? WHERE id=?", ("ARRIVED" if success else "FAILED", goal_id))
            self.emit(connection, goal["assignment_id"], "ARRIVED" if success else "FAILED", {"goalId": goal_id})
            return True

    def scan(self, assignment_id, challenge):
        token_hash = hashlib.sha256(challenge["token"].encode()).hexdigest()
        with self.transaction() as connection:
            previous = connection.execute("SELECT * FROM qr_scans WHERE challenge_id=?", (challenge["challengeId"],)).fetchone()
            if previous:
                if previous["assignment_id"] != assignment_id or previous["token_hash"] != token_hash:
                    raise ValueError("QR challenge reused with changed content")
                return False
            self.emit(connection, assignment_id, "QR_SCANNED", {"challengeId": challenge["challengeId"], "token": challenge["token"]})
            connection.execute("INSERT INTO qr_scans VALUES (?,?,?)", (challenge["challengeId"], assignment_id, token_hash))
            return True

    def recover(self):
        """A process crash never proves that a previously requested goal stopped."""
        with self.transaction() as connection:
            uncertain = connection.execute("SELECT DISTINCT assignment_id FROM goals WHERE state IN ('START_REQUESTED','NAVIGATING')").fetchall()
            connection.execute("UPDATE goals SET state='UNKNOWN' WHERE state IN ('START_REQUESTED','NAVIGATING')")
            for row in uncertain:
                self.emit(connection, row[0], "EXECUTION_UNKNOWN")

    def close(self):
        self.connection.close()
