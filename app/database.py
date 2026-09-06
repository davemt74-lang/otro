from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import settings

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "database" / "schema.sql"


def connect() -> sqlite3.Connection:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(settings.db_path, timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    connection = connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database() -> None:
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    with db() as connection:
        connection.executescript(schema)
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations(version) VALUES (1)"
        )
        existing = connection.execute("SELECT id FROM agents WHERE is_primary = 1 LIMIT 1").fetchone()
        if existing is None:
            connection.execute(
                "INSERT INTO agents(name, instructions, is_primary) VALUES (?, ?, 1)",
                ("HomeServer Agent", "Primary local HomeServer agent."),
            )
