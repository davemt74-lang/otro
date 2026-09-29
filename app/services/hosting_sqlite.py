from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

from . import hosting_runtime

CONTRACT = "vp3.hosting.sqlite.v1"
MAX_MIGRATION_BYTES = 2 * 1024 * 1024
MAX_MIGRATIONS_PER_RELEASE = 200
_MIGRATION_NAME = re.compile(r"^[0-9]{1,8}_[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\.sql$")
_FORBIDDEN_SQL = (
    re.compile(r"\bATTACH\s+(?:DATABASE\s+)?", re.IGNORECASE),
    re.compile(r"\bDETACH\s+(?:DATABASE\s+)?", re.IGNORECASE),
    re.compile(r"\bVACUUM\s+INTO\b", re.IGNORECASE),
    re.compile(r"\bload_extension\s*\(", re.IGNORECASE),
    re.compile(r"\bPRAGMA\s+(?:writable_schema|temp_store_directory|data_store_directory)\b", re.IGNORECASE),
)
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class SQLiteRuntimeError(hosting_runtime.HostingError):
    pass


def _lock_for(site_id: str) -> threading.RLock:
    site_id = hosting_runtime._safe_site_id(site_id)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(site_id)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[site_id] = lock
        return lock


def _ensure_runtime_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS vp3_site_app_migrations (
          migration_name TEXT PRIMARY KEY,
          sha256 TEXT NOT NULL,
          release_id TEXT,
          applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _logical_bytes(connection: sqlite3.Connection) -> int:
    page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
    page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
    return page_count * page_size


def _migration_statements(sql: str) -> list[str]:
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                statements.append(statement)
            buffer = ""
    if buffer.strip():
        raise SQLiteRuntimeError("SQLite migration contains an incomplete statement.")
    return statements


def _validate_sql(name: str, sql: str) -> None:
    encoded = sql.encode("utf-8")
    if not encoded or len(encoded) > MAX_MIGRATION_BYTES:
        raise SQLiteRuntimeError("SQLite migration is empty or exceeds the migration size limit.", 413)
    for pattern in _FORBIDDEN_SQL:
        if pattern.search(sql):
            raise SQLiteRuntimeError(f"SQLite migration {name} contains a filesystem-capable statement.", 409)
    _migration_statements(sql)


def schema_status(site_id: str) -> dict[str, Any]:
    site = hosting_runtime.get_site(site_id)
    with _lock_for(site_id):
        connection = hosting_runtime.connect_site_db(site_id)
        try:
            _ensure_runtime_tables(connection)
            connection.commit()
            integrity = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            journal = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            rows = connection.execute(
                "SELECT migration_name,sha256,release_id,applied_at "
                "FROM vp3_site_app_migrations ORDER BY migration_name"
            ).fetchall()
            logical_bytes = _logical_bytes(connection)
        finally:
            connection.close()
    return {
        "contract": CONTRACT,
        "site_id": site_id,
        "healthy": integrity == "ok" and journal == "wal",
        "integrity": integrity,
        "journal_mode": journal,
        "logical_bytes": logical_bytes,
        "sqlite_limit_bytes": site.get("sqlite_limit_bytes"),
        "migration_count": len(rows),
        "latest_migration": rows[-1]["migration_name"] if rows else None,
        "latest_release_id": rows[-1]["release_id"] if rows else None,
    }


def apply_migrations(
    site_id: str,
    migrations: list[dict[str, str]],
    *,
    release_id: str | None = None,
) -> dict[str, Any]:
    site = hosting_runtime.get_site(site_id)
    if not isinstance(migrations, list) or len(migrations) > MAX_MIGRATIONS_PER_RELEASE:
        raise SQLiteRuntimeError("SQLite migration set exceeds the per-release limit.", 413)

    normalized: list[dict[str, str]] = []
    names: set[str] = set()
    for item in migrations:
        if not isinstance(item, dict):
            raise SQLiteRuntimeError("SQLite migration metadata is invalid.")
        name = str(item.get("name") or "").strip()
        sql = str(item.get("sql") or "")
        if not _MIGRATION_NAME.fullmatch(name):
            raise SQLiteRuntimeError("SQLite migration name must use N_name.sql ordering.")
        if name in names:
            raise SQLiteRuntimeError("SQLite migration names must be unique.")
        names.add(name)
        _validate_sql(name, sql)
        normalized.append({
            "name": name,
            "sql": sql,
            "sha256": hashlib.sha256(sql.encode("utf-8")).hexdigest(),
        })
    normalized.sort(key=lambda item: item["name"])

    if not normalized:
        status = schema_status(site_id)
        return {"site_id": site_id, "applied": [], "skipped": [], "recovery_id": None, "status": status}

    with _lock_for(site_id):
        connection = hosting_runtime.connect_site_db(site_id)
        try:
            _ensure_runtime_tables(connection)
            connection.commit()
            applied_rows = {
                row["migration_name"]: row["sha256"]
                for row in connection.execute(
                    "SELECT migration_name,sha256 FROM vp3_site_app_migrations"
                ).fetchall()
            }
        finally:
            connection.close()

        pending: list[dict[str, str]] = []
        skipped: list[str] = []
        for item in normalized:
            existing = applied_rows.get(item["name"])
            if existing is None:
                pending.append(item)
            elif existing == item["sha256"]:
                skipped.append(item["name"])
            else:
                raise SQLiteRuntimeError(
                    f"SQLite migration {item['name']} was previously applied with different contents.",
                    409,
                )

        if not pending:
            return {
                "site_id": site_id,
                "applied": [],
                "skipped": skipped,
                "recovery_id": None,
                "status": schema_status(site_id),
            }

        from . import hosting_recovery

        recovery = hosting_recovery.create_recovery_point(
            site_id,
            reason=f"pre-sqlite-migration:{release_id or pending[-1]['name']}",
        )

        connection = hosting_runtime.connect_site_db(site_id)
        applied: list[str] = []
        try:
            _ensure_runtime_tables(connection)
            connection.commit()
            connection.execute("BEGIN IMMEDIATE")
            for item in pending:
                for statement in _migration_statements(item["sql"]):
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO vp3_site_app_migrations(migration_name,sha256,release_id) VALUES (?,?,?)",
                    (item["name"], item["sha256"], release_id),
                )
                applied.append(item["name"])

            limit = site.get("sqlite_limit_bytes")
            logical_bytes = _logical_bytes(connection)
            if limit is not None and logical_bytes > int(limit):
                raise SQLiteRuntimeError("SQLite migration would exceed the site database quota.", 409)
            integrity = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            if integrity != "ok":
                raise SQLiteRuntimeError("SQLite migration failed post-migration integrity verification.", 409)
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except Exception:
            try:
                connection.rollback()
            except Exception:
                pass
            raise
        finally:
            connection.close()

        hosting_runtime.sample_usage(site_id)
        return {
            "site_id": site_id,
            "applied": applied,
            "skipped": skipped,
            "recovery_id": recovery["recovery_id"],
            "status": schema_status(site_id),
        }


def apply_release_migrations(
    site_id: str,
    manifest: dict[str, Any],
    content_root: Path,
    *,
    release_id: str,
) -> dict[str, Any]:
    database = manifest.get("database")
    if database is None:
        return apply_migrations(site_id, [], release_id=release_id)
    if not isinstance(database, dict):
        raise SQLiteRuntimeError("Deployment database manifest must be an object.")
    paths = database.get("migrations", [])
    if not isinstance(paths, list) or len(paths) > MAX_MIGRATIONS_PER_RELEASE:
        raise SQLiteRuntimeError("Deployment database migrations must be a bounded list.", 413)

    root = content_root.resolve()
    items: list[dict[str, str]] = []
    for raw_path in paths:
        relative = str(raw_path or "").replace("\\", "/").strip("/")
        parts = Path(relative).parts
        if (
            not relative
            or len(parts) < 3
            or parts[0] != "database"
            or parts[1] != "migrations"
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise SQLiteRuntimeError("SQLite migration paths must live under database/migrations/.")
        path = (root / Path(*parts)).resolve()
        if path != root and root not in path.parents:
            raise SQLiteRuntimeError("SQLite migration path escaped the deployment package.")
        if not path.is_file() or path.is_symlink():
            raise SQLiteRuntimeError("SQLite migration file is missing or unsafe.")
        if path.stat().st_size > MAX_MIGRATION_BYTES:
            raise SQLiteRuntimeError("SQLite migration file exceeds the size limit.", 413)
        try:
            sql = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise SQLiteRuntimeError("SQLite migration file is unreadable.") from exc
        items.append({"name": path.name, "sql": sql})
    return apply_migrations(site_id, items, release_id=release_id)


def public_capability() -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "per_site_database": True,
        "wal": True,
        "foreign_keys": True,
        "serialized_migrations": True,
        "migration_checksum_pinning": True,
        "pre_migration_recovery_point": True,
        "post_migration_integrity_check": True,
        "quota_guard": True,
        "filesystem_capable_migration_sql": False,
        "cloud_raw_sql": False,
        "cloud_database_path": False,
        "automatic_down_migrations": False,
    }
