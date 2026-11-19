import hashlib
import hmac
import secrets
import os
import threading
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from psycopg.types.json import Jsonb

from office_room_calendar.errors import CalendarError, require


HASHER = PasswordHasher()
DUMMY_HASH = HASHER.hash("not-a-real-calendar-account")
PASSWORD_WORKERS = threading.BoundedSemaphore(2)


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def audit(connection, actor, action, entity=None, changes=None):
    connection.execute("INSERT INTO calendar_audit_logs(actor_id,action,entity_id,changes) VALUES (%s,%s,%s,%s)",
                       (actor, action, str(entity) if entity else None, Jsonb(changes or {})))


class CalendarAuth:
    def __init__(self, database, settings, clock=lambda: datetime.now(timezone.utc)):
        self.database, self.settings, self.clock = database, settings, clock

    def create_user(self, email, name, password, role="USER"):
        email = email.strip().lower()
        require("@" in email and len(email) <= 254 and name.strip() and len(name) <= 120 and 15 <= len(password) <= 256
                and role in {"USER", "ADMIN"}, "INVALID_USER", "Valid account fields and a 15–256 character password are required.", 422)
        user_id = uuid4()
        with self.database.transaction() as connection:
            connection.execute("INSERT INTO calendar_users(user_id,email,display_name,password_hash,role) VALUES (%s,%s,%s,%s,%s)",
                               (user_id, email, name.strip(), HASHER.hash(password), role))
            audit(connection, user_id, "USER_PROVISIONED", user_id, {"role": role})
        return str(user_id)

    def create_robot(self, name, token):
        require(bool(name.strip()) and len(token) >= 32 and not any(c.isspace() for c in token),
                "INVALID_TOKEN", "Use a distinct random robot token of at least 32 characters.", 422)
        require(token not in {self.settings.session_secret, os.environ.get("API_ACCESS_TOKEN", "")}, "INVALID_TOKEN", "Robot token must be distinct.", 422)
        with self.database.transaction() as connection:
            connection.execute("INSERT INTO calendar_robot_tokens(token_hash,name) VALUES (%s,%s)", (digest(token), name))
            audit(connection, None, "ROBOT_TOKEN_PROVISIONED", changes={"name": name})

    def csrf(self, token):
        return hmac.new(self.settings.session_secret.encode(), ("calendar-csrf:" + token).encode(), hashlib.sha256).hexdigest()

    def login(self, email, password, remote_address):
        email = email.strip().lower()
        now, failure, result = self.clock(), None, None
        with self.database.transaction() as connection:
            for identity, limit in (("email:" + email, 8), ("network:" + remote_address, 100)):
                bucket = connection.execute("""INSERT INTO calendar_login_attempts(bucket_key,attempts,expires_at) VALUES (%s,1,%s)
                    ON CONFLICT(bucket_key) DO UPDATE SET attempts=CASE WHEN calendar_login_attempts.expires_at<=%s THEN 1 ELSE calendar_login_attempts.attempts+1 END,
                    expires_at=CASE WHEN calendar_login_attempts.expires_at<=%s THEN EXCLUDED.expires_at ELSE calendar_login_attempts.expires_at END RETURNING attempts""",
                    (digest(identity), now + timedelta(minutes=15), now, now)).fetchone()
                if bucket["attempts"] > limit:
                    failure = CalendarError("LOGIN_THROTTLED", "Too many sign-in attempts. Try again later.", 429)
            user = connection.execute("SELECT * FROM calendar_users WHERE email=%s", (email,)).fetchone()
            valid = False
            if failure is None:
                try:
                    with PASSWORD_WORKERS:
                        valid = HASHER.verify(user["password_hash"] if user else DUMMY_HASH, password)
                except VerificationError:
                    pass
            if failure is None and valid and user and user["enabled"]:
                token = secrets.token_urlsafe(32)
                connection.execute("INSERT INTO calendar_sessions(token_hash,user_id,expires_at) VALUES (%s,%s,%s)",
                                   (digest(token), user["user_id"], now + timedelta(hours=12)))
                audit(connection, user["user_id"], "LOGIN", user["user_id"])
                result = token, {"userId": str(user["user_id"]), "displayName": user["display_name"], "role": user["role"]}
            else:
                audit(connection, None, "LOGIN_REJECTED", changes={"reason": "THROTTLED" if failure else "INVALID_CREDENTIALS"})
                failure = failure or CalendarError("INVALID_CREDENTIALS", "Sign-in failed.", 401)
        if failure:
            raise failure  # Commit throttle/audit first; never roll them back on rejection.
        return result

    def user(self, token):
        require(bool(token), "UNAUTHORIZED", "Calendar sign-in required.", 401)
        with self.database.transaction(readonly=True) as connection:
            user = connection.execute("""SELECT u.user_id,u.display_name,u.role FROM calendar_sessions s JOIN calendar_users u USING(user_id)
                WHERE s.token_hash=%s AND s.expires_at>%s AND u.enabled""", (digest(token), self.clock())).fetchone()
            require(user is not None, "UNAUTHORIZED", "Calendar session is invalid or expired.", 401)
            return {"userId": str(user["user_id"]), "displayName": user["display_name"], "role": user["role"]}

    def robot(self, token):
        require(bool(token), "UNAUTHORIZED", "Robot read token required.", 401)
        with self.database.transaction(readonly=True) as connection:
            row = connection.execute("SELECT name FROM calendar_robot_tokens WHERE token_hash=%s AND enabled", (digest(token),)).fetchone()
            require(row is not None, "UNAUTHORIZED", "Robot read token rejected.", 401)
            return row["name"]

    def logout(self, token, user):
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM calendar_sessions WHERE token_hash=%s", (digest(token),))
            audit(connection, user["userId"], "LOGOUT", user["userId"])
