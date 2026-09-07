from __future__ import annotations

import json
import os
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import settings
from . import backups


LATEST_UNREADABLE_STATE = "unreadable-live-latest.json"


def _healthy_live_database() -> bool:
    if not settings.db_path.is_file():
        return True
    try:
        connection = sqlite3.connect(settings.db_path, timeout=3)
        try:
            rows = connection.execute("PRAGMA quick_check").fetchall()
            if [str(row[0]) for row in rows] != ["ok"]:
                return False
            connection.execute("SELECT 1 FROM schema_migrations LIMIT 1").fetchone()
            return True
        finally:
            connection.close()
    except (sqlite3.Error, OSError):
        return False


def _recovery_snapshot_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    return settings.restore_dir / f"unreadable-live-{stamp}-{uuid.uuid4().hex[:8]}"


def _move_database_family(target: Path) -> list[str]:
    target.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    for suffix in ("", "-wal", "-shm"):
        source = Path(f"{settings.db_path}{suffix}")
        if not source.exists():
            continue
        destination = target / f"homeserver.db{suffix}"
        os.replace(source, destination)
        moved.append(destination.name)
    return moved


def _restore_database_family(source_dir: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        Path(f"{settings.db_path}{suffix}").unlink(missing_ok=True)
        source = source_dir / f"homeserver.db{suffix}"
        if source.exists():
            os.replace(source, Path(f"{settings.db_path}{suffix}"))


def _copy_knowledge_snapshot(target: Path) -> bool:
    if not settings.knowledge_files_dir.is_dir():
        return False
    destination = target / "knowledge" / "files"
    shutil.copytree(settings.knowledge_files_dir, destination)
    return True


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _write_snapshot_metadata(target: Path, payload: dict[str, Any]) -> None:
    target.mkdir(parents=True, exist_ok=True)
    _write_json(target / "README.json", payload)


def _record_latest_quarantine(target: Path, *, status: str, error: str | None = None) -> None:
    _write_json(
        settings.restore_dir / LATEST_UNREADABLE_STATE,
        {
            "status": status,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "path": str(target),
            "error": error,
            "warning": "This path contains raw pre-restore data from an unreadable HomeServer database and is not a validated backup archive.",
        },
    )


def latest_unreadable_snapshot() -> dict[str, Any] | None:
    path = settings.restore_dir / LATEST_UNREADABLE_STATE
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def apply_pending_restore_for_startup() -> dict[str, Any] | None:
    """Apply a staged restore, preserving unreadable live data when necessary.

    The normal backup service intentionally refuses to create a validated backup
    from a corrupt SQLite database. Recovery startup therefore quarantines the
    unreadable database family first, takes a raw copy of retained knowledge
    files, and applies the already-validated staged restore with no live DB in
    place. The quarantine is never advertised as a normal restorable backup.
    """
    if not settings.pending_restore_dir.is_dir():
        return None
    if _healthy_live_database():
        return backups.apply_pending_restore()

    quarantine = _recovery_snapshot_dir()
    try:
        quarantine.mkdir(parents=True, exist_ok=False)
        moved_database = _move_database_family(quarantine)
        knowledge_copied = _copy_knowledge_snapshot(quarantine)
        _write_snapshot_metadata(
            quarantine,
            {
                "kind": "unreadable-live-data",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "database_files": moved_database,
                "knowledge_copied": knowledge_copied,
                "warning": "This is a raw safety snapshot of unreadable pre-restore data, not a validated HomeServer backup archive.",
            },
        )
        result = backups.apply_pending_restore()
        if result is None:
            raise backups.BackupError("The staged restore disappeared before recovery application.")
        _record_latest_quarantine(quarantine, status="preserved")
        result["unreadable_live_snapshot"] = str(quarantine)
        return result
    except Exception as exc:
        rollback_error: Exception | None = None
        try:
            _restore_database_family(quarantine)
        except Exception as restore_exc:  # pragma: no cover - catastrophic filesystem failure
            rollback_error = restore_exc
        try:
            _record_latest_quarantine(
                quarantine,
                status="restore_failed",
                error=f"{type(exc).__name__}: {str(exc)[:500]}",
            )
        except OSError:
            pass
        if rollback_error is not None:
            raise backups.BackupError(
                "Recovery restore failed and the unreadable live database could not be moved back automatically. "
                f"Preserved data remains under {quarantine}."
            ) from rollback_error
        if isinstance(exc, backups.BackupError):
            raise
        raise backups.BackupError(f"Recovery restore preparation failed: {type(exc).__name__}") from exc
