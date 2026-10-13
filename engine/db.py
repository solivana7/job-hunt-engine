"""Postgres access. All state lives in the database; nothing transient on disk."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from .config import database_url

SCHEMA_FILES = ("db/schema.sql", "db/migration_v2.sql", "db/migration_v3_digest.sql")


def connect() -> psycopg.Connection:
    return psycopg.connect(database_url(), row_factory=dict_row, autocommit=False)


@contextmanager
def tx():
    conn = connect()
    try:
        with conn:  # commits on clean exit, rolls back on exception
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Apply schema.sql then migration_v2.sql (idempotent)."""
    from .config import ROOT

    with connect() as conn:
        for rel in SCHEMA_FILES:
            sql = (ROOT / rel).read_text(encoding="utf-8")
            # statements are simple DDL separated by semicolons; no functions/triggers
            for stmt in sql.split(";\n"):
                stmt = stmt.strip()
                if not stmt:
                    continue
                try:
                    conn.execute(stmt)
                except psycopg.errors.DuplicateObject:
                    conn.rollback()  # aborted tx blocks subsequent stmts; already applied
        conn.commit()


def log_event(conn, application_id: int, kind: str, payload: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO events (application_id, kind, payload) VALUES (%s, %s, %s::jsonb)",
        (application_id, kind, __import__("json").dumps(payload or {})),
    )
