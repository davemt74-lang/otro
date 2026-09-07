from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from ..config import settings
from ..database import connect, db, initialize_database, migration_files

BACKUP_FORMAT = "homeserver-backup-v1"
BACKUP_FORMAT_VERSION = 1
BACKUP_PREFIX = "HomeServer-Backup-"
BACKUP_SUFFIX = ".zip"
MANIFEST_NAME = "manifest.json"
RESTORE_STATE_NAME = "restore-state.json"
RESTORE_RESULT_NAME = "restore-result.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
WINDOWS_FORBIDDEN_CHARS = set('<>:"|?*')


class BackupError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat()


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_stream_to_file(source: BinaryIO, target: Path, max_bytes: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as output:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise BackupError(
                    f"Backup upload exceeds the {max_bytes // (1024 * 1024)} MB limit.",
                    status_code=413,
                )
            digest.update(chunk)
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    return total, digest.hexdigest()


def _supported_schema_version() -> int:
    versions = [1, *(version for version, _ in migration_files())]
    return max(versions)


def _sqlite_schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT COALESCE(MAX(version), 1) FROM schema_migrations").fetchone()
    return int(row[0] if row else 1)


def _validate_sqlite_database(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise BackupError("Backup database is missing.")
    try:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        quick = [row[0] for row in connection.execute("PRAGMA quick_check").fetchall()]
        if quick != ["ok"]:
            raise BackupError("Backup database failed SQLite integrity validation.")
        foreign = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign:
            raise BackupError("Backup database contains foreign-key violations.")
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        required = {"schema_migrations", "agents", "knowledge_items", "paired_apps", "agent_memory"}
        if not required.issubset(tables):
            raise BackupError("Backup database is not a compatible HomeServer database.")
        schema_version = _sqlite_schema_version(connection)
        if schema_version > _supported_schema_version():
            raise BackupError(
                f"Backup schema v{schema_version} is newer than this HomeServer supports "
                f"(v{_supported_schema_version()})."
            )
        referenced_files: list[str] = []
        if "knowledge_documents" in tables:
            for row in connection.execute("SELECT stored_name FROM knowledge_documents ORDER BY stored_name").fetchall():
                stored = str(row[0] or "").strip()
                if not stored or Path(stored).name != stored or "/" in stored or "\\" in stored:
                    raise BackupError("Backup contains an invalid stored knowledge-file name.")
                referenced_files.append(stored)
        return {"schema_version": schema_version, "referenced_files": referenced_files}
    except BackupError:
        raise
    except sqlite3.DatabaseError as exc:
        raise BackupError("Backup database could not be opened as SQLite.") from exc
    finally:
        try:
            connection.close()  # type: ignore[possibly-undefined]
        except Exception:
            pass


def _snapshot_database(target: Path) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    source = connect()
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source.close()
    return int(_validate_sqlite_database(target)["schema_version"])


def _copy_knowledge_files(target_root: Path) -> list[Path]:
    copied: list[Path] = []
    source_root = settings.knowledge_files_dir
    if not source_root.exists():
        return copied
    for source in sorted(source_root.rglob("*")):
        if source.is_symlink():
            raise BackupError("Knowledge storage contains a symbolic link; backup was stopped for safety.")
        if not source.is_file():
            continue
        relative = source.relative_to(source_root)
        target = target_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(target)
    return copied


def _manifest_file_entry(root: Path, relative_path: str) -> dict[str, Any]:
    path = root / PurePosixPath(relative_path)
    return {
        "path": relative_path,
        "size": path.stat().st_size,
        "sha256": _sha256_path(path),
    }


def create_backup(reason: str = "manual") -> dict[str, Any]:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    if not settings.db_path.exists():
        initialize_database()

    reason_text = str(reason or "manual").strip().lower()[:80] or "manual"
    timestamp = _utc_now().strftime("%Y%m%d-%H%M%SZ")
    filename = f"{BACKUP_PREFIX}{timestamp}-{uuid.uuid4().hex[:8]}{BACKUP_SUFFIX}"
    final_path = settings.backups_dir / filename
    temporary_archive = settings.backups_dir / f".{filename}.part"

    with tempfile.TemporaryDirectory(prefix="homeserver-backup-", dir=settings.data_dir) as temp_name:
        root = Path(temp_name)
        database_path = root / "database" / "homeserver.db"
        schema_version = _snapshot_database(database_path)
        knowledge_root = root / "knowledge" / "files"
        copied_files = _copy_knowledge_files(knowledge_root)

        archive_paths = ["database/homeserver.db"]
        archive_paths.extend(
            f"knowledge/files/{path.relative_to(knowledge_root).as_posix()}"
            for path in copied_files
        )
        archive_paths = [_safe_member_name(path) for path in archive_paths]
        if len({path.casefold() for path in archive_paths}) != len(archive_paths):
            raise BackupError("Knowledge storage contains file names that collide on Windows.")
        file_entries = [_manifest_file_entry(root, path) for path in sorted(archive_paths)]
        manifest = {
            "format": BACKUP_FORMAT,
            "format_version": BACKUP_FORMAT_VERSION,
            "created_at": _iso_now(),
            "app_version": settings.version,
            "schema_version": schema_version,
            "reason": reason_text,
            "files": file_entries,
        }
        (root / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

        try:
            with zipfile.ZipFile(
                temporary_archive,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
                allowZip64=True,
            ) as archive:
                archive.write(root / MANIFEST_NAME, MANIFEST_NAME)
                for relative in sorted(archive_paths):
                    archive.write(root / PurePosixPath(relative), relative)
            os.replace(temporary_archive, final_path)
        finally:
            temporary_archive.unlink(missing_ok=True)

    result = {
        "name": filename,
        "path": str(final_path),
        "created_at": manifest["created_at"],
        "app_version": manifest["app_version"],
        "schema_version": manifest["schema_version"],
        "reason": manifest["reason"],
        "file_count": len(file_entries),
        "size_bytes": final_path.stat().st_size,
        "sha256": _sha256_path(final_path),
    }
    return result


def _safe_backup_name(name: str) -> str:
    value = str(name or "").strip()
    if not value or "/" in value or "\\" in value or Path(value).name != value:
        raise BackupError("Invalid backup name.", 404)
    if not value.startswith(BACKUP_PREFIX) or not value.endswith(BACKUP_SUFFIX):
        raise BackupError("Invalid backup name.", 404)
    return value


def backup_path(name: str) -> Path:
    value = _safe_backup_name(name)
    path = settings.backups_dir / value
    if not path.is_file():
        raise BackupError("Backup not found.", 404)
    return path


def _read_manifest_from_archive(path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path, "r") as archive:
            info = archive.getinfo(MANIFEST_NAME)
            if info.file_size > 1024 * 1024:
                raise BackupError("Backup manifest is unexpectedly large.")
            return json.loads(archive.read(info).decode("utf-8"))
    except BackupError:
        raise
    except (KeyError, zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("Backup archive does not contain a valid HomeServer manifest.") from exc


def _backup_summary(path: Path) -> dict[str, Any]:
    manifest = _read_manifest_from_archive(path)
    return {
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "created_at": manifest.get("created_at"),
        "app_version": manifest.get("app_version"),
        "schema_version": manifest.get("schema_version"),
        "reason": manifest.get("reason", "manual"),
        "file_count": len(manifest.get("files") or []),
    }


def list_backups() -> list[dict[str, Any]]:
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    for path in sorted(settings.backups_dir.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"), reverse=True):
        try:
            items.append(_backup_summary(path))
        except BackupError:
            items.append(
                {
                    "name": path.name,
                    "size_bytes": path.stat().st_size,
                    "created_at": None,
                    "app_version": None,
                    "schema_version": None,
                    "reason": "invalid",
                    "file_count": 0,
                    "invalid": True,
                }
            )
    return items


def delete_backup(name: str) -> bool:
    path = backup_path(name)
    path.unlink()
    return True


def _safe_member_name(name: str) -> str:
    if not name or "\\" in name or len(name) > 1024:
        raise BackupError("Backup contains an unsafe archive path.")
    pure = PurePosixPath(name)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise BackupError("Backup contains an unsafe archive path.")
    for part in pure.parts:
        if len(part) > 255 or part != part.rstrip(" ."):
            raise BackupError("Backup contains a path that is not portable to Windows.")
        if any(ord(char) < 32 or char in WINDOWS_FORBIDDEN_CHARS for char in part):
            raise BackupError("Backup contains a path that is not portable to Windows.")
        stem = part.split(".", 1)[0].upper()
        if stem in WINDOWS_RESERVED_NAMES:
            raise BackupError("Backup contains a Windows-reserved file name.")
    return pure.as_posix()


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode == stat.S_IFLNK


def _validate_manifest_shape(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    if manifest.get("format") != BACKUP_FORMAT or manifest.get("format_version") != BACKUP_FORMAT_VERSION:
        raise BackupError("Unsupported HomeServer backup format.")
    try:
        manifest_schema = int(manifest.get("schema_version"))
    except (TypeError, ValueError) as exc:
        raise BackupError("Backup manifest has an invalid schema version.") from exc
    if manifest_schema > _supported_schema_version():
        raise BackupError(
            f"Backup schema v{manifest_schema} is newer than this HomeServer supports "
            f"(v{_supported_schema_version()})."
        )
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise BackupError("Backup manifest does not contain a file inventory.")
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    seen_casefold: set[str] = set()
    for raw in raw_files:
        if not isinstance(raw, dict):
            raise BackupError("Backup manifest contains an invalid file entry.")
        path = _safe_member_name(str(raw.get("path") or ""))
        folded = path.casefold()
        if path in seen or folded in seen_casefold:
            raise BackupError("Backup manifest contains duplicate or Windows-colliding file paths.")
        seen.add(path)
        seen_casefold.add(folded)
        if path != "database/homeserver.db" and not path.startswith("knowledge/files/"):
            raise BackupError("Backup manifest contains a file outside the HomeServer backup allowlist.")
        try:
            size = int(raw.get("size"))
        except (TypeError, ValueError) as exc:
            raise BackupError("Backup manifest contains an invalid file size.") from exc
        digest = str(raw.get("sha256") or "").lower()
        if size < 0 or not SHA256_RE.fullmatch(digest):
            raise BackupError("Backup manifest contains invalid integrity metadata.")
        entries.append({"path": path, "size": size, "sha256": digest})
    if "database/homeserver.db" not in seen:
        raise BackupError("Backup manifest does not include the HomeServer database.")
    return entries


def _extract_and_validate_archive(archive_path: Path, target_root: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            infos = archive.infolist()
            if len(infos) > settings.max_backup_entries:
                raise BackupError("Backup archive contains too many entries.")
            seen_names: set[str] = set()
            seen_casefold: set[str] = set()
            total_uncompressed = 0
            info_by_name: dict[str, zipfile.ZipInfo] = {}
            for info in infos:
                raw_name = info.filename.rstrip("/") if info.filename.endswith("/") else info.filename
                name = _safe_member_name(raw_name)
                folded = name.casefold()
                if name in seen_names or folded in seen_casefold:
                    raise BackupError("Backup archive contains duplicate or Windows-colliding paths.")
                seen_names.add(name)
                seen_casefold.add(folded)
                if _is_symlink(info):
                    raise BackupError("Backup archive contains a symbolic link.")
                if info.flag_bits & 0x1:
                    raise BackupError("Encrypted ZIP entries are not supported by this backup format.")
                if info.is_dir():
                    continue
                total_uncompressed += int(info.file_size)
                if total_uncompressed > settings.max_backup_uncompressed_bytes:
                    raise BackupError("Backup archive expands beyond the configured safety limit.")
                info_by_name[name] = info

            manifest_info = info_by_name.get(MANIFEST_NAME)
            if manifest_info is None or manifest_info.file_size > 1024 * 1024:
                raise BackupError("Backup archive is missing a valid manifest.")
            try:
                manifest = json.loads(archive.read(manifest_info).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BackupError("Backup manifest is not valid UTF-8 JSON.") from exc
            entries = _validate_manifest_shape(manifest)
            expected = {entry["path"] for entry in entries}
            actual = set(info_by_name) - {MANIFEST_NAME}
            if actual != expected:
                raise BackupError("Backup archive contents do not match its integrity manifest.")

            target_root.mkdir(parents=True, exist_ok=True)
            (target_root / MANIFEST_NAME).write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            for entry in entries:
                info = info_by_name[entry["path"]]
                if int(info.file_size) != entry["size"]:
                    raise BackupError("Backup file size does not match its integrity manifest.")
                target = target_root.joinpath(*PurePosixPath(entry["path"]).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                written = 0
                with archive.open(info, "r") as source, target.open("wb") as output:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > entry["size"]:
                            raise BackupError("Backup file expanded beyond its declared size.")
                        digest.update(chunk)
                        output.write(chunk)
                if written != entry["size"] or digest.hexdigest() != entry["sha256"]:
                    raise BackupError("Backup file failed SHA-256 integrity validation.")
    except BackupError:
        raise
    except zipfile.BadZipFile as exc:
        raise BackupError("Uploaded file is not a valid ZIP backup.") from exc

    database_summary = _validate_sqlite_database(target_root / "database" / "homeserver.db")
    if int(manifest["schema_version"]) != int(database_summary["schema_version"]):
        raise BackupError("Backup manifest schema version does not match its database.")
    for stored_name in database_summary["referenced_files"]:
        if not (target_root / "knowledge" / "files" / stored_name).is_file():
            raise BackupError("Backup is missing a knowledge file referenced by its database.")
    return {
        "manifest": manifest,
        "database": database_summary,
        "file_count": len(entries),
    }


def stage_restore(source: BinaryIO, original_name: str = "backup.zip") -> dict[str, Any]:
    settings.restore_dir.mkdir(parents=True, exist_ok=True)
    upload_path = settings.restore_dir / f".upload-{uuid.uuid4().hex}.zip"
    candidate = settings.restore_dir / f".candidate-{uuid.uuid4().hex}"
    previous = settings.restore_dir / f".previous-{uuid.uuid4().hex}"
    try:
        upload_size, upload_sha256 = _sha256_stream_to_file(
            source,
            upload_path,
            settings.max_backup_upload_bytes,
        )
        validated = _extract_and_validate_archive(upload_path, candidate)
        state = {
            "status": "pending_restart",
            "staged_at": _iso_now(),
            "original_name": Path(str(original_name or "backup.zip")).name[:240],
            "upload_size_bytes": upload_size,
            "upload_sha256": upload_sha256,
            "backup_created_at": validated["manifest"].get("created_at"),
            "backup_app_version": validated["manifest"].get("app_version"),
            "schema_version": validated["database"]["schema_version"],
            "file_count": validated["file_count"],
        }
        (candidate / RESTORE_STATE_NAME).write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

        pending = settings.pending_restore_dir
        if pending.exists():
            os.replace(pending, previous)
        try:
            os.replace(candidate, pending)
        except Exception:
            if previous.exists() and not pending.exists():
                os.replace(previous, pending)
            raise
        shutil.rmtree(previous, ignore_errors=True)
        return state
    finally:
        upload_path.unlink(missing_ok=True)
        shutil.rmtree(candidate, ignore_errors=True)
        shutil.rmtree(previous, ignore_errors=True)


def _validate_staged_directory(root: Path) -> dict[str, Any]:
    manifest_path = root / MANIFEST_NAME
    state_path = root / RESTORE_STATE_NAME
    if not manifest_path.is_file() or not state_path.is_file():
        raise BackupError("Pending restore is incomplete.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("Pending restore metadata is invalid.") from exc
    entries = _validate_manifest_shape(manifest)
    for entry in entries:
        path = root.joinpath(*PurePosixPath(entry["path"]).parts)
        if not path.is_file() or path.stat().st_size != entry["size"] or _sha256_path(path) != entry["sha256"]:
            raise BackupError("Pending restore failed integrity re-validation.")
    database = _validate_sqlite_database(root / "database" / "homeserver.db")
    if int(manifest["schema_version"]) != int(database["schema_version"]):
        raise BackupError("Pending restore schema metadata no longer matches its database.")
    for stored_name in database["referenced_files"]:
        if not (root / "knowledge" / "files" / stored_name).is_file():
            raise BackupError("Pending restore is missing a referenced knowledge file.")
    return {"manifest": manifest, "state": state, "database": database, "file_count": len(entries)}


def pending_restore_info() -> dict[str, Any] | None:
    pending = settings.pending_restore_dir
    if not pending.is_dir():
        return None
    try:
        validated = _validate_staged_directory(pending)
        return {**validated["state"], "valid": True}
    except BackupError as exc:
        return {"status": "invalid", "valid": False, "error": str(exc)}


def cancel_pending_restore() -> bool:
    pending = settings.pending_restore_dir
    if not pending.exists():
        return False
    shutil.rmtree(pending)
    return True


def _write_restore_result(payload: dict[str, Any]) -> None:
    settings.restore_dir.mkdir(parents=True, exist_ok=True)
    target = settings.restore_dir / RESTORE_RESULT_NAME
    temporary = settings.restore_dir / f".{RESTORE_RESULT_NAME}.tmp"
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, target)


def last_restore_result() -> dict[str, Any] | None:
    path = settings.restore_dir / RESTORE_RESULT_NAME
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"status": "unknown", "error": "Restore result metadata is unreadable."}


def _checkpoint_live_database() -> None:
    if not settings.db_path.is_file():
        return
    connection = sqlite3.connect(settings.db_path, timeout=30)
    try:
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()


def apply_pending_restore() -> dict[str, Any] | None:
    pending = settings.pending_restore_dir
    if not pending.is_dir():
        return None

    token = uuid.uuid4().hex
    knowledge_parent = settings.knowledge_files_dir.parent
    knowledge_parent.mkdir(parents=True, exist_ok=True)
    new_database = settings.data_dir / f".restore-new-{token}.db"
    old_database = settings.data_dir / f".restore-old-{token}.db"
    new_knowledge = knowledge_parent / f".files-restore-new-{token}"
    old_knowledge = knowledge_parent / f".files-restore-old-{token}"
    pre_restore_backup: dict[str, Any] | None = None
    swapped_database = False
    swapped_knowledge = False
    had_database = settings.db_path.exists()
    had_knowledge = settings.knowledge_files_dir.exists()

    try:
        validated = _validate_staged_directory(pending)
        if had_database:
            pre_restore_backup = create_backup("pre-restore")
            _checkpoint_live_database()

        shutil.copy2(pending / "database" / "homeserver.db", new_database)
        staged_knowledge = pending / "knowledge" / "files"
        if staged_knowledge.is_dir():
            shutil.copytree(staged_knowledge, new_knowledge)
        else:
            new_knowledge.mkdir(parents=True, exist_ok=True)

        if had_knowledge:
            os.replace(settings.knowledge_files_dir, old_knowledge)
        os.replace(new_knowledge, settings.knowledge_files_dir)
        swapped_knowledge = True

        if had_database:
            os.replace(settings.db_path, old_database)
        os.replace(new_database, settings.db_path)
        swapped_database = True
        Path(f"{settings.db_path}-wal").unlink(missing_ok=True)
        Path(f"{settings.db_path}-shm").unlink(missing_ok=True)

        initialize_database()
        from .knowledge import ensure_knowledge_index

        ensure_knowledge_index()
        final_database = _validate_sqlite_database(settings.db_path)
        with db() as connection:
            connection.execute(
                """
                INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
                VALUES ('system', 'restore', 'backup.restored', 'backup', ?, ?)
                """,
                (
                    str(validated["state"].get("original_name") or "backup"),
                    json.dumps(
                        {
                            "schema_version": final_database["schema_version"],
                            "pre_restore_backup": pre_restore_backup["name"] if pre_restore_backup else None,
                        },
                        separators=(",", ":"),
                    ),
                ),
            )

        shutil.rmtree(pending)
        old_database.unlink(missing_ok=True)
        shutil.rmtree(old_knowledge, ignore_errors=True)
        result = {
            "status": "applied",
            "applied_at": _iso_now(),
            "backup_created_at": validated["manifest"].get("created_at"),
            "backup_app_version": validated["manifest"].get("app_version"),
            "schema_version": final_database["schema_version"],
            "pre_restore_backup": pre_restore_backup["name"] if pre_restore_backup else None,
        }
        _write_restore_result(result)
        return result
    except Exception as exc:
        try:
            if swapped_database:
                settings.db_path.unlink(missing_ok=True)
                Path(f"{settings.db_path}-wal").unlink(missing_ok=True)
                Path(f"{settings.db_path}-shm").unlink(missing_ok=True)
            if old_database.exists():
                os.replace(old_database, settings.db_path)
            if swapped_knowledge:
                shutil.rmtree(settings.knowledge_files_dir, ignore_errors=True)
            if old_knowledge.exists():
                os.replace(old_knowledge, settings.knowledge_files_dir)
            elif not had_knowledge:
                shutil.rmtree(settings.knowledge_files_dir, ignore_errors=True)
        finally:
            shutil.rmtree(pending, ignore_errors=True)
            new_database.unlink(missing_ok=True)
            shutil.rmtree(new_knowledge, ignore_errors=True)
        _write_restore_result(
            {
                "status": "failed",
                "failed_at": _iso_now(),
                "error": str(exc)[:1000],
                "pre_restore_backup": pre_restore_backup["name"] if pre_restore_backup else None,
            }
        )
        raise BackupError(f"Pending restore could not be applied: {exc}") from exc
    finally:
        new_database.unlink(missing_ok=True)
        shutil.rmtree(new_knowledge, ignore_errors=True)
        old_database.unlink(missing_ok=True)
        shutil.rmtree(old_knowledge, ignore_errors=True)
