"""PostgreSQL transactions and parameterized repository primitives; no SQLite fallback."""

import hashlib
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


class UnitOfWork:
    def __init__(self, connection):
        self.connection = connection

    def all(self, statement, values=()):
        return self.connection.execute(statement, values).fetchall()

    def one(self, statement, values=()):
        return self.connection.execute(statement, values).fetchone()

    def execute(self, statement, values=()):
        return self.connection.execute(statement, values)

    def insert(self, table, **values):
        statement = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
            sql.Identifier(table), sql.SQL(",").join(map(sql.Identifier, values)),
            sql.SQL(",").join(sql.Placeholder() for _ in values))
        return self.one(statement, tuple(values.values()))

    def update(self, table, record_id, **values):
        statement = sql.SQL("UPDATE {} SET {} WHERE id=%s RETURNING *").format(
            sql.Identifier(table), sql.SQL(",").join(sql.SQL("{}=%s").format(sql.Identifier(key)) for key in values))
        return self.one(statement, (*values.values(), record_id))


class PostgresRepository:
    def __init__(self, dsn):
        self._dsn = dsn

    @contextmanager
    def transaction(self):
        with psycopg.connect(self._dsn, row_factory=dict_row, connect_timeout=5) as connection:
            # V1 deliberately serializes operational writes across server processes.
            # No provider, network dispatch or robot callback occurs under this lock.
            connection.execute("SELECT pg_advisory_xact_lock(8131101)")
            yield UnitOfWork(connection)

    def migrate(self):
        with self.transaction() as tx:
            tx.execute("CREATE TABLE IF NOT EXISTS operational_migrations (version text PRIMARY KEY, checksum text NOT NULL)")
            for path in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                body = path.read_text(encoding="utf-8")
                checksum = hashlib.sha256(body.encode()).hexdigest()
                previous = tx.one("SELECT checksum FROM operational_migrations WHERE version=%s", (path.name,))
                if previous:
                    if previous["checksum"] != checksum:
                        raise ValueError("Applied operational migration checksum changed.")
                    continue
                tx.execute(body)
                tx.execute("INSERT INTO operational_migrations VALUES (%s,%s)", (path.name, checksum))


def json_value(value):
    return Jsonb(value)
