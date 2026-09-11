from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import settings

ROOT_DIR = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT_DIR / "database" / "schema.sql"
MIGRATIONS_DIR = ROOT_DIR / "database" / "migrations"
FEATURE_SCHEMA_PATHS = (
    ROOT_DIR / "database" / "knowledge_collections.sql",
    ROOT_DIR / "database" / "agent_voice_profiles.sql",
    ROOT_DIR / "database" / "agent_routing.sql",
    ROOT_DIR / "database" / "agent_delegation_workflows.sql",
)
MIGRATION_PATTERN = re.compile(r"^(?P<version>\d{3})_.+\.sql$")
SQLITE_BUSY_TIMEOUT_SECONDS = 30
SQLITE_BUSY_TIMEOUT_MS = SQLITE_BUSY_TIMEOUT_SECONDS * 1000


def connect() -> sqlite3.Connection:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        settings.db_path,
        timeout=SQLITE_BUSY_TIMEOUT_SECONDS,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    # sqlite3.connect(timeout=...) installs a busy handler, but setting
    # PRAGMA busy_timeout replaces it. Keep both values aligned so transient
    # background writes cannot shorten the audit-write wait to five seconds.
    connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
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


def migration_files() -> list[tuple[int, Path]]:
    if not MIGRATIONS_DIR.exists():
        return []
    migrations: list[tuple[int, Path]] = []
    seen: set[int] = set()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        match = MIGRATION_PATTERN.match(path.name)
        if match is None:
            continue
        version = int(match.group("version"))
        if version in seen:
            raise RuntimeError(f"Duplicate database migration version: {version}")
        seen.add(version)
        migrations.append((version, path))
    return migrations


def _apply_migration(connection: sqlite3.Connection, version: int, path: Path) -> None:
    sql = path.read_text(encoding="utf-8").strip()
    # sqlite3.executescript() commits any open transaction before running.
    # Put BEGIN/COMMIT inside the script so the schema change and migration
    # record succeed or fail together.
    script = (
        "BEGIN IMMEDIATE;\n"
        + sql
        + "\n"
        + f"INSERT INTO schema_migrations(version) VALUES ({int(version)});\n"
        + "COMMIT;\n"
    )
    try:
        connection.executescript(script)
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise


def _ensure_schema_extensions() -> None:
    for path in FEATURE_SCHEMA_PATHS:
        if not path.exists():
            continue
        sql = path.read_text(encoding="utf-8").strip()
        if not sql:
            continue
        with db() as connection:
            connection.executescript(sql)


def initialize_database() -> None:
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    with db() as connection:
        connection.executescript(schema)
        connection.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES (1)")

    for version, path in migration_files():
        with db() as connection:
            applied = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version=?",
                (version,),
            ).fetchone()
            if applied is not None:
                continue
            _apply_migration(connection, version, path)

    # Feature schemas that do not change the canonical migration version are
    # idempotent and run after numbered migrations so their foreign keys always
    # target tables already present on both fresh installs and upgrades.
    _ensure_schema_extensions()
