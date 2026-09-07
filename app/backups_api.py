from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .database import db
from .services import backups

router = APIRouter()


def _audit(action: str, resource_key: str | None = None, metadata: dict | None = None) -> None:
    with db() as connection:
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES ('owner', 'control-center', ?, 'backup', ?, ?)
            """,
            (
                action,
                resource_key,
                json.dumps(metadata or {}, separators=(",", ":")),
            ),
        )


def _backup_error(exc: backups.BackupError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


def _revalidate_created_backup(path: Path) -> None:
    """Prove the completed archive is restorable before exposing it to the owner."""
    with tempfile.TemporaryDirectory(prefix="homeserver-backup-check-", dir=backups.settings.data_dir) as temp_name:
        backups._extract_and_validate_archive(path, Path(temp_name))


@router.get("/api/v1/control/backups")
def control_backups() -> dict:
    return {
        "items": backups.list_backups(),
        "pending_restore": backups.pending_restore_info(),
        "last_restore": backups.last_restore_result(),
    }


@router.post("/api/v1/control/backups/create")
def control_backup_create() -> dict:
    item: dict | None = None
    try:
        item = backups.create_backup("manual")
        _revalidate_created_backup(Path(item["path"]))
    except backups.BackupError as exc:
        if item is not None:
            Path(item["path"]).unlink(missing_ok=True)
        raise _backup_error(exc) from exc
    _audit(
        "backup.created",
        item["name"],
        {
            "schema_version": item["schema_version"],
            "file_count": item["file_count"],
            "size_bytes": item["size_bytes"],
        },
    )
    return {"created": True, "backup": item}


@router.get("/api/v1/control/backups/download/{backup_name}")
def control_backup_download(backup_name: str):
    try:
        path = backups.backup_path(backup_name)
    except backups.BackupError as exc:
        raise _backup_error(exc) from exc
    _audit("backup.downloaded", path.name, {"size_bytes": path.stat().st_size})
    response = FileResponse(
        path,
        media_type="application/zip",
        filename=path.name,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@router.delete("/api/v1/control/backups/{backup_name}")
def control_backup_delete(backup_name: str) -> dict:
    try:
        path = backups.backup_path(backup_name)
        name = path.name
        size = path.stat().st_size
        backups.delete_backup(name)
    except backups.BackupError as exc:
        raise _backup_error(exc) from exc
    _audit("backup.deleted", name, {"size_bytes": size})
    return {"deleted": True, "name": name}


@router.post("/api/v1/control/restore/stage")
def control_restore_stage(file: UploadFile = File(...)) -> dict:
    try:
        file.file.seek(0)
        state = backups.stage_restore(file.file, file.filename or "backup.zip")
    except backups.BackupError as exc:
        raise _backup_error(exc) from exc
    finally:
        try:
            file.file.seek(0)
        except Exception:
            pass
    _audit(
        "backup.restore_staged",
        Path(file.filename or "backup.zip").name[:240],
        {
            "schema_version": state["schema_version"],
            "file_count": state["file_count"],
            "upload_size_bytes": state["upload_size_bytes"],
        },
    )
    return {
        "staged": True,
        "restore": state,
        "restart_required": True,
        "message": "Restore validated and staged. Quit and reopen HomeServer to apply it safely.",
    }


@router.delete("/api/v1/control/restore/pending")
def control_restore_cancel() -> dict:
    cancelled = backups.cancel_pending_restore()
    if not cancelled:
        raise HTTPException(status_code=404, detail="No pending restore found")
    _audit("backup.restore_cancelled")
    return {"cancelled": True}
