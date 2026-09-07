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


def _write_snapshot_metadata(target: Path, payload: dict[str, Any]) -> None:
    target.mkdir(parents=True, exist_ok=True)
    (target / "README.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


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
    quarantine.mkdir(parents=True, exist_ok=False)
    moved_database: list[str] = []
    knowledge_copied = False
    try:
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
        result["unreadable_live_snapshot"] = str(quarantine)
        return result
    except Exception:
        _restore_database_family(quarantine)
        raise
