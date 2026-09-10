from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

from ..config import settings
from ..database import db
from . import action_policy, app_scopes, knowledge_collection_policy, local_files
from .knowledge import KnowledgeImportError, extract_text
from . import knowledge_sources


FILE_ACTION_VERSION = "v0.39"
EDITABLE_TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".json", ".csv", ".html", ".htm"}


class LocalFileActionError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _app_identity_for_execution(source_app_key: str, tool_key: str) -> tuple[dict[str, Any] | None, bool]:
    source = str(source_app_key or "").strip()
    if source == "owner":
        return None, True
    if not source.startswith("app:"):
        raise LocalFileActionError("Connected application identity is unavailable.", 403)
    app_key = source[4:].strip()
    if not app_key:
        raise LocalFileActionError("Connected application identity is unavailable.", 403)

    with db() as connection:
        app = connection.execute(
            "SELECT id, app_key, status FROM paired_apps WHERE app_key=? LIMIT 1",
            (app_key,),
        ).fetchone()
        if app is None or str(app["status"] or "") != "active":
            raise LocalFileActionError("Connected application is no longer active.", 403)
        permissions = {
            str(row["permission"])
            for row in connection.execute(
                "SELECT permission FROM app_permissions WHERE paired_app_id=? AND allowed=1",
                (int(app["id"]),),
            ).fetchall()
        }

    required = {"files.write", "tools.execute"}
    missing = sorted(required - permissions)
    if missing:
        raise LocalFileActionError(
            f"Connected application no longer has required permissions: {', '.join(missing)}.",
            403,
        )

    app_id = int(app["id"])
    scope = app_scopes.get_scope(app_id)
    if not app_scopes.tool_allowed(scope, tool_key):
        raise LocalFileActionError("File action is outside this application's current tool scope.", 403)

    policy = action_policy.resolve_policy(app_id, str(app["app_key"]), tool_key)
    if str(policy.get("policy_mode") or "") == action_policy.SENSITIVE_HIGH_IMPACT:
        raise LocalFileActionError("File action is currently blocked as sensitive/high-impact.", 403)

    return {"id": app_id, "scope": scope}, False


def _load_target(source_app_key: str, tool_key: str, file_ref: str) -> dict[str, Any]:
    identity, owner = _app_identity_for_execution(source_app_key, tool_key)
    try:
        visible = local_files.read_file(identity, file_ref, max_chars=1, owner=owner)
        file_id, requested_version = local_files._parse_file_ref(file_ref)
    except local_files.LocalFileError as exc:
        raise LocalFileActionError(str(exc), exc.status_code) from exc

    with db() as connection:
        row = connection.execute(
            """
            SELECT ksf.id AS file_id, ksf.source_id, ksf.relative_path,
                   ksf.content_hash, ksf.size_bytes, ksf.status,
                   ksf.knowledge_item_id, ks.path AS source_path, ks.enabled,
                   ks.recursive, ks.exclude_json
            FROM knowledge_source_files ksf
            JOIN knowledge_sources ks ON ks.id=ksf.source_id
            WHERE ksf.id=? LIMIT 1
            """,
            (file_id,),
        ).fetchone()
    if row is None or not bool(row["enabled"]) or str(row["status"] or "") != "indexed":
        raise LocalFileActionError("File is unavailable for mutation.", 404)
    if requested_version != local_files._version(row["content_hash"]):
        raise LocalFileActionError("File reference is stale. Discover the file again before changing it.", 409)

    relative = str(row["relative_path"] or "").replace("\\", "/")
    rel_path = Path(relative)
    if not relative or rel_path.is_absolute() or ".." in rel_path.parts:
        raise LocalFileActionError("Tracked file path is not safe for mutation.", 409)

    try:
        root = Path(str(row["source_path"] or "")).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise LocalFileActionError("The approved local file source is unavailable.", 409) from exc
    if not root.is_dir():
        raise LocalFileActionError("The approved local file source is unavailable.", 409)

    candidate = root.joinpath(*rel_path.parts)
    cursor = root
    for part in rel_path.parts:
        cursor = cursor / part
        try:
            if cursor.is_symlink():
                raise LocalFileActionError("Symlinked file paths cannot be mutated.", 403)
        except OSError as exc:
            raise LocalFileActionError("Tracked file path could not be inspected.", 409) from exc

    try:
        target = candidate.resolve(strict=True)
        target.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise LocalFileActionError("Tracked file is unavailable inside its approved source.", 409) from exc
    if not target.is_file():
        raise LocalFileActionError("Tracked file is unavailable inside its approved source.", 409)
    try:
        private_root = settings.data_dir.resolve(strict=False)
        target.relative_to(private_root)
        raise LocalFileActionError("HomeServer private data files cannot be mutated through file actions.", 403)
    except ValueError:
        pass

    suffix = target.suffix.lower()
    if suffix not in knowledge_sources.SUPPORTED_EXTENSIONS:
        raise LocalFileActionError("Tracked file type is no longer supported.", 409)

    try:
        before_bytes = target.read_bytes()
    except OSError as exc:
        raise LocalFileActionError("Tracked file could not be read before mutation.", 409) from exc
    disk_hash = hashlib.sha256(before_bytes).hexdigest()
    if disk_hash != str(row["content_hash"] or ""):
        raise LocalFileActionError(
            "The local file changed after it was indexed. Rescan and review the new version before changing it.",
            409,
        )

    return {
        "row": dict(row),
        "root": root,
        "target": target,
        "before_bytes": before_bytes,
        "metadata": visible["file"],
    }


def _atomic_replace(target: Path, data: bytes, mode: int | None = None) -> None:
    temporary = target.with_name(f".{target.name}.homeserver-{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, target)
    finally:
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass


def update_file(source_app_key: str, file_ref: str, content: str) -> dict[str, Any]:
    text = str(content if content is not None else "")
    data = text.encode("utf-8")
    if not text.strip():
        raise LocalFileActionError("files.update requires non-empty indexed text.")
    if len(data) > settings.max_upload_bytes:
        raise LocalFileActionError(
            f"Updated file exceeds the {settings.max_upload_bytes // (1024 * 1024)} MB indexing limit.",
            413,
        )

    if not knowledge_sources._SCAN_LOCK.acquire(blocking=False):
        raise LocalFileActionError("Another knowledge-source operation is already running.", 409)
    try:
        target_info = _load_target(source_app_key, "files.update", file_ref)
        row = target_info["row"]
        target: Path = target_info["target"]
        if target.suffix.lower() not in EDITABLE_TEXT_EXTENSIONS:
            raise LocalFileActionError(
                "files.update only supports editable text knowledge files; binary document formats are read-only.",
                422,
            )
        try:
            extracted = extract_text(target.name, data)
        except KnowledgeImportError as exc:
            raise LocalFileActionError(str(exc), getattr(exc, "status_code", 422)) from exc
        if not extracted.strip():
            raise LocalFileActionError("Updated file must contain readable indexed text.")

        try:
            original_mode = target.stat().st_mode
        except OSError as exc:
            raise LocalFileActionError("Tracked file changed before mutation could begin.", 409) from exc

        new_hash = hashlib.sha256(data).hexdigest()
        if new_hash == str(row["content_hash"] or ""):
            return {
                "updated": False,
                "unchanged": True,
                "file": target_info["metadata"],
                "capability_version": FILE_ACTION_VERSION,
            }

        _atomic_replace(target, data, original_mode)
        try:
            stat = target.stat()
            knowledge_sources._write_knowledge_item(
                item_id=int(row["knowledge_item_id"]),
                source_id=int(row["source_id"]),
                absolute_path=target,
                relative_path=str(row["relative_path"]),
                data=data,
                file_hash=new_hash,
            )
            with db() as connection:
                connection.execute(
                    """
                    UPDATE knowledge_source_files
                    SET content_hash=?, size_bytes=?, modified_ns=?, status='indexed',
                        last_error=NULL, updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (new_hash, len(data), int(stat.st_mtime_ns), int(row["file_id"])),
                )
        except Exception as exc:
            try:
                _atomic_replace(target, target_info["before_bytes"], original_mode)
            except Exception:
                pass
            raise LocalFileActionError("File update could not be reconciled with the local Knowledge index.", 500) from exc

        identity, owner = _app_identity_for_execution(source_app_key, "files.update")
        new_ref = local_files._file_ref(int(row["file_id"]), new_hash)
        try:
            refreshed = local_files.read_file(identity, new_ref, max_chars=1, owner=owner)["file"]
        except local_files.LocalFileError as exc:
            raise LocalFileActionError("Updated file is no longer visible under the current app scope.", 403) from exc
        return {
            "updated": True,
            "unchanged": False,
            "file": refreshed,
            "bytes_written": len(data),
            "capability_version": FILE_ACTION_VERSION,
        }
    finally:
        knowledge_sources._SCAN_LOCK.release()


def delete_file(source_app_key: str, file_ref: str) -> dict[str, Any]:
    if not knowledge_sources._SCAN_LOCK.acquire(blocking=False):
        raise LocalFileActionError("Another knowledge-source operation is already running.", 409)
    try:
        target_info = _load_target(source_app_key, "files.delete", file_ref)
        row = target_info["row"]
        target: Path = target_info["target"]
        tombstone = target.with_name(f".{target.name}.homeserver-delete-{uuid.uuid4().hex}.tmp")
        try:
            os.replace(target, tombstone)
        except OSError as exc:
            raise LocalFileActionError("Tracked file could not be reserved for deletion.", 409) from exc

        try:
            with db() as connection:
                if row["knowledge_item_id"] is not None:
                    connection.execute(
                        "DELETE FROM knowledge_items WHERE id=?",
                        (int(row["knowledge_item_id"]),),
                    )
                connection.execute(
                    "DELETE FROM knowledge_source_files WHERE id=?",
                    (int(row["file_id"]),),
                )
        except Exception as exc:
            try:
                os.replace(tombstone, target)
            except OSError:
                pass
            raise LocalFileActionError("File deletion could not be reconciled with the local Knowledge index.", 500) from exc

        try:
            tombstone.unlink()
        except OSError:
            pass
        metadata = dict(target_info["metadata"])
        return {
            "deleted": True,
            "file": metadata,
            "capability_version": FILE_ACTION_VERSION,
        }
    finally:
        knowledge_sources._SCAN_LOCK.release()
