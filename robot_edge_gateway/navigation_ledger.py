"""Durable Nav2 admission IDs: a manager restart must not re-drive an old goal."""

import hashlib
import json
import sqlite3
import threading


class DeliveryGoalLedger:
    def __init__(self, path, acquire_owner=False):
        self.lock = threading.RLock()
        self._owner = None
        if acquire_owner:
            import fcntl  # ROS deployment is Linux; offline Windows tests need no owner lock.
            self._owner = open(str(path) + ".owner", "a")
            try:
                fcntl.flock(self._owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self._owner.close()
                self._owner = None
                raise RuntimeError("Another navigation manager owns the local ledger") from None
        self.connection = sqlite3.connect(str(path), check_same_thread=False)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("CREATE TABLE IF NOT EXISTS delivery_goal_admissions (goal_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL)")
        self.connection.commit()

    def claim(self, goal_id, payload):
        content_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with self.lock, self.connection:
            try:
                self.connection.execute("INSERT INTO delivery_goal_admissions VALUES (?,?)", (goal_id, content_hash))
            except sqlite3.IntegrityError:
                return False  # Same or changed replay never causes physical execution.
            return True

    def close(self):
        self.connection.close()
        if self._owner is not None:
            self._owner.close()
            self._owner = None
