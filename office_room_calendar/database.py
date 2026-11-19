"""Independent calendar PostgreSQL schema. No Integration v1 code or fallback DB."""

import hashlib
import re
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.rows import dict_row


class CalendarDatabase:
    def __init__(self, url, schema="office_calendar"):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,60}", schema):
            raise ValueError("Invalid calendar schema name")
        self._url, self.schema = url, schema

    @contextmanager
    def transaction(self, readonly=False):
        with psycopg.connect(self._url, row_factory=dict_row, connect_timeout=3) as connection:
            if readonly:
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            connection.execute(sql.SQL("SET LOCAL search_path TO {},public").format(sql.Identifier(self.schema)))
            connection.execute("SET LOCAL statement_timeout='5s'")
            yield connection

    def migrate(self, room_names):
        with self.transaction() as connection:
            name = connection.execute("SELECT current_database() AS name").fetchone()["name"]
            if "calendar" not in name.lower():
                raise ValueError("Calendar migrations require a dedicated database whose name contains calendar")
            connection.execute("SELECT pg_advisory_xact_lock(8191101)")
            connection.execute("CREATE EXTENSION IF NOT EXISTS btree_gist WITH SCHEMA public")
            connection.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self.schema)))
            connection.execute("CREATE TABLE IF NOT EXISTS calendar_migrations (name text PRIMARY KEY, checksum text NOT NULL)")
            for path in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                body = path.read_text(encoding="utf-8")
                checksum = hashlib.sha256(body.encode()).hexdigest()
                saved = connection.execute("SELECT checksum FROM calendar_migrations WHERE name=%s", (path.name,)).fetchone()
                if saved:
                    if saved["checksum"] != checksum:
                        raise ValueError("Applied calendar migration changed")
                    continue
                connection.execute(body)
                connection.execute("INSERT INTO calendar_migrations VALUES (%s,%s)", (path.name, checksum))
            for room_id, display_name in room_names.items():
                connection.execute("INSERT INTO calendar_rooms(room_id,display_name) VALUES (%s,%s) ON CONFLICT DO NOTHING", (room_id, display_name))
                connection.execute("INSERT INTO calendar_room_policies(room_id) VALUES (%s) ON CONFLICT DO NOTHING", (room_id,))
                connection.execute("INSERT INTO calendar_room_versions(room_id) VALUES (%s) ON CONFLICT DO NOTHING", (room_id,))
