from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from ..config import settings
from ..database import db
from . import app_scopes
from .knowledge import _normalize_text, _replace_chunks

_SOURCE_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,160}$")
_ASSET_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,160}$")
_UPLOAD_ID = re.compile(r"^[A-Za-z0-9_-]{24,96}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_APP_KEY = re.compile(r"^[A-Za-z0-9._:-]{2,80}$")
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._ -]+")
_TRANSFER_LOCK = threading.RLock()

MAX_ASSET_BYTES = min(250 * 1024 * 1024, settings.max_backup_upload_bytes)
# 96 KiB becomes ~128 KiB after base64, leaving ample room below the bridge's
# 256 KiB JSON-message ceiling for operation metadata.
MAX_CHUNK_BYTES = 96 * 1024
UPLOAD_TTL_SECONDS = 24 * 60 * 60

_ALLOWED_MEDIA = {
    "audio/webm": ".webm",
    "video/webm": ".webm",
    "audio/ogg": ".ogg",
    "application/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "video/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}


class KnowledgeBackupError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _clean_key(value: Any, pattern: re.Pattern[str], label: str) -> str:
    clean = str(value or "").strip()
    if not pattern.fullmatch(clean):
        raise KnowledgeBackupError(f"Invalid {label}.")
    return clean


def _safe_name(value: Any, fallback: str = "recording") -> str:
    raw = Path(str(value or "")).name.strip()
    raw = _SAFE_FILENAME.sub("_", raw)[:180].strip(" .")
    return raw or fallback


def _source_path(app_key: str, source_key: str) -> str:
    return f"external://{app_key}/{source_key}"


def _incoming_dir() -> Path:
    path = settings.knowledge_files_dir / ".incoming"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _asset_dir() -> Path:
    path = settings.knowledge_files_dir / "attachments"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _upload_paths(upload_id: str) -> tuple[Path, Path]:
    clean = _clean_key(upload_id, _UPLOAD_ID, "upload id")
    root = _incoming_dir()
    return root / f"{clean}.part", root / f"{clean}.json"


def _cleanup_stale_uploads() -> None:
    root = _incoming_dir()
    cutoff = time.time() - UPLOAD_TTL_SECONDS
    for sidecar in root.glob("*.json"):
        try:
            if sidecar.stat().st_mtime >= cutoff:
                continue
            part = sidecar.with_suffix(".part")
            sidecar.unlink(missing_ok=True)
            part.unlink(missing_ok=True)
        except OSError:
            continue


def _load_metadata(row: Any) -> dict[str, Any]:
    try:
        decoded = json.loads(str(row["metadata_json"] or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        decoded = {}
    return decoded if isinstance(decoded, dict) else {}


def _safe_backup_metadata(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    out: dict[str, Any] = {}
    scalar_keys = {
        "cloud_knowledge_id",
        "session_id",
        "track_id",
        "duration_ms",
        "started_at",
        "recording_count",
        "source",
    }
    for key in scalar_keys:
        item = raw.get(key)
        if isinstance(item, bool) or item is None:
            continue
        if isinstance(item, (int, float)):
            out[key] = item
        elif isinstance(item, str):
            out[key] = item[:500]
    tags = raw.get("tags")
    if isinstance(tags, list):
        out["tags"] = [str(item)[:100] for item in tags[:32] if str(item).strip()]
    return out


def _safe_asset(asset: dict[str, Any]) -> dict[str, Any]:
    return {
        "asset_key": str(asset.get("asset_key") or ""),
        "original_name": str(asset.get("original_name") or ""),
        "media_type": str(asset.get("media_type") or ""),
        "size_bytes": int(asset.get("size_bytes") or 0),
        "sha256": str(asset.get("sha256") or ""),
        "created_at": str(asset.get("created_at") or ""),
    }


def _row_for_source(connection, app_key: str, source_key: str):
    return connection.execute(
        "SELECT id,title,kind,source_path,content,metadata_json FROM knowledge_items WHERE source_path=? ORDER BY id ASC LIMIT 1",
        (_source_path(app_key, source_key),),
    ).fetchone()


def _row_for_item(connection, item_id: int):
    return connection.execute(
        "SELECT id,title,kind,source_path,content,metadata_json FROM knowledge_items WHERE id=? LIMIT 1",
        (item_id,),
    ).fetchone()


def _assert_owned_external_item(row: Any, app_key: str, source_key: str) -> None:
    if row is None or str(row["source_path"] or "") != _source_path(app_key, source_key):
        raise KnowledgeBackupError("Knowledge backup item not found.", 404)


def _assert_kind_scope(identity: dict[str, Any], kind: str) -> None:
    scope = identity.get("scope") or app_scopes.DEFAULT_SCOPE
    if not app_scopes.knowledge_kind_allowed(scope, kind):
        raise KnowledgeBackupError("Knowledge kind is outside this application's allowed scope.", 403)


def upsert_external_knowledge(
    identity: dict[str, Any],
    *,
    source_key: str,
    title: str,
    kind: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    app_key = _clean_key(identity.get("app_key"), _APP_KEY, "application key")
    source_key = _clean_key(source_key, _SOURCE_KEY, "source key")
    kind = str(kind or "transcription").strip().lower()[:40]
    if not kind:
        raise KnowledgeBackupError("Knowledge kind is required.")
    _assert_kind_scope(identity, kind)
    clean_title = str(title or "").strip()[:240]
    if not clean_title:
        raise KnowledgeBackupError("Knowledge title is required.")
    normalized = _normalize_text(str(content or ""))
    if not normalized:
        raise KnowledgeBackupError("Knowledge content is empty.")
    if len(normalized) > 250000:
        raise KnowledgeBackupError("Knowledge content exceeds the 250,000 character limit.", 413)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    with _TRANSFER_LOCK:
        with db() as connection:
            existing = _row_for_source(connection, app_key, source_key)
            old_metadata = _load_metadata(existing) if existing is not None else {}
            assets = old_metadata.get("assets") if isinstance(old_metadata.get("assets"), list) else []
            clean_metadata = {
                "external": {"app_key": app_key, "source_key": source_key},
                "backup": _safe_backup_metadata(metadata),
                "assets": assets,
            }
            encoded = json.dumps(clean_metadata, separators=(",", ":"), ensure_ascii=False)
            if existing is None:
                cursor = connection.execute(
                    """
                    INSERT INTO knowledge_items(title, kind, source_path, content, content_hash, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (clean_title, kind, _source_path(app_key, source_key), normalized, digest, encoded),
                )
                item_id = int(cursor.lastrowid)
                created = True
            else:
                item_id = int(existing["id"])
                connection.execute(
                    """
                    UPDATE knowledge_items
                    SET title=?,kind=?,content=?,content_hash=?,metadata_json=?,updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (clean_title, kind, normalized, digest, encoded, item_id),
                )
                created = False
            chunk_count = _replace_chunks(connection, item_id, clean_title, normalized)
            connection.execute(
                """
                INSERT INTO activity_log(actor_type,actor_key,action,resource_type,resource_key,metadata_json)
                VALUES ('app',?,'knowledge.external_upserted','knowledge',?,?)
                """,
                (app_key, str(item_id), json.dumps({"kind": kind, "created": created, "chunks": chunk_count}, separators=(",", ":"))),
            )
    return {
        "created": created,
        "id": item_id,
        "kind": kind,
        "content_hash": digest,
        "chunk_count": chunk_count,
        "assets": [_safe_asset(asset) for asset in assets if isinstance(asset, dict)],
    }


def external_backup_status(identity: dict[str, Any], *, source_key: str) -> dict[str, Any]:
    app_key = _clean_key(identity.get("app_key"), _APP_KEY, "application key")
    source_key = _clean_key(source_key, _SOURCE_KEY, "source key")
    with db() as connection:
        row = _row_for_source(connection, app_key, source_key)
    if row is None:
        return {"exists": False, "assets": []}
    _assert_kind_scope(identity, str(row["kind"] or ""))
    metadata = _load_metadata(row)
    assets = metadata.get("assets") if isinstance(metadata.get("assets"), list) else []
    return {
        "exists": True,
        "id": int(row["id"]),
        "kind": str(row["kind"] or ""),
        "content_hash": hashlib.sha256(str(row["content"] or "").encode("utf-8")).hexdigest(),
        "assets": [_safe_asset(asset) for asset in assets if isinstance(asset, dict)],
    }


def begin_asset_upload(
    identity: dict[str, Any],
    *,
    source_key: str,
    asset_key: str,
    original_name: str,
    media_type: str,
    size_bytes: int,
    sha256: str,
) -> dict[str, Any]:
    _cleanup_stale_uploads()
    app_key = _clean_key(identity.get("app_key"), _APP_KEY, "application key")
    source_key = _clean_key(source_key, _SOURCE_KEY, "source key")
    asset_key = _clean_key(asset_key, _ASSET_KEY, "asset key")
    digest = str(sha256 or "").strip().lower()
    if not _SHA256.fullmatch(digest):
        raise KnowledgeBackupError("A valid SHA-256 digest is required.")
    media = str(media_type or "").strip().lower()
    if media not in _ALLOWED_MEDIA:
        raise KnowledgeBackupError("This recording media type is not supported.")
    size = int(size_bytes or 0)
    if size < 1 or size > MAX_ASSET_BYTES:
        raise KnowledgeBackupError(f"Recording size must be between 1 byte and {MAX_ASSET_BYTES} bytes.", 413)
    name = _safe_name(original_name, f"{asset_key}{_ALLOWED_MEDIA[media]}")

    with _TRANSFER_LOCK:
        with db() as connection:
            row = _row_for_source(connection, app_key, source_key)
            _assert_owned_external_item(row, app_key, source_key)
            _assert_kind_scope(identity, str(row["kind"] or ""))
            metadata = _load_metadata(row)
            assets = metadata.get("assets") if isinstance(metadata.get("assets"), list) else []
            for asset in assets:
                if not isinstance(asset, dict) or str(asset.get("asset_key") or "") != asset_key:
                    continue
                if str(asset.get("sha256") or "") == digest and int(asset.get("size_bytes") or 0) == size:
                    return {"already_present": True, "item_id": int(row["id"]), "asset": _safe_asset(asset)}
                raise KnowledgeBackupError("An asset with this key already exists with different content.", 409)
            item_id = int(row["id"])

        upload_id = secrets.token_urlsafe(32)
        part_path, sidecar_path = _upload_paths(upload_id)
        state = {
            "upload_id": upload_id,
            "app_key": app_key,
            "source_key": source_key,
            "asset_key": asset_key,
            "item_id": item_id,
            "original_name": name,
            "media_type": media,
            "size_bytes": size,
            "sha256": digest,
            "created_at": int(time.time()),
        }
        part_path.write_bytes(b"")
        sidecar_path.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")
    return {"already_present": False, "upload_id": upload_id, "received_bytes": 0, "chunk_bytes": MAX_CHUNK_BYTES}


def _load_upload(identity: dict[str, Any], upload_id: str) -> tuple[dict[str, Any], Path, Path]:
    part_path, sidecar_path = _upload_paths(upload_id)
    if not sidecar_path.is_file() or not part_path.is_file():
        raise KnowledgeBackupError("Asset upload was not found or expired.", 404)
    try:
        state = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise KnowledgeBackupError("Asset upload state is invalid.", 409) from exc
    if not isinstance(state, dict) or str(state.get("app_key") or "") != str(identity.get("app_key") or ""):
        raise KnowledgeBackupError("Asset upload does not belong to this application.", 403)
    if int(state.get("created_at") or 0) < int(time.time()) - UPLOAD_TTL_SECONDS:
        part_path.unlink(missing_ok=True)
        sidecar_path.unlink(missing_ok=True)
        raise KnowledgeBackupError("Asset upload expired. Start it again.", 410)
    return state, part_path, sidecar_path


def append_asset_chunk(identity: dict[str, Any], *, upload_id: str, offset: int, data_base64: str) -> dict[str, Any]:
    with _TRANSFER_LOCK:
        state, part_path, _ = _load_upload(identity, upload_id)
        current = part_path.stat().st_size
        requested_offset = int(offset or 0)
        if requested_offset != current:
            return {"upload_id": upload_id, "received_bytes": current, "resync": True}
        try:
            raw = base64.b64decode(str(data_base64 or ""), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise KnowledgeBackupError("Asset chunk is not valid base64.") from exc
        if not raw or len(raw) > MAX_CHUNK_BYTES:
            raise KnowledgeBackupError(f"Asset chunks must contain 1 to {MAX_CHUNK_BYTES} bytes.")
        expected = int(state["size_bytes"])
        if current + len(raw) > expected:
            raise KnowledgeBackupError("Asset chunk exceeds the declared recording size.", 409)
        with part_path.open("ab") as handle:
            handle.write(raw)
        return {"upload_id": upload_id, "received_bytes": current + len(raw), "complete": current + len(raw) == expected}


def commit_asset_upload(identity: dict[str, Any], *, upload_id: str) -> dict[str, Any]:
    with _TRANSFER_LOCK:
        state, part_path, sidecar_path = _load_upload(identity, upload_id)
        expected_size = int(state["size_bytes"])
        actual_size = part_path.stat().st_size
        if actual_size != expected_size:
            raise KnowledgeBackupError(f"Recording upload is incomplete ({actual_size}/{expected_size} bytes).", 409)
        digest = hashlib.sha256()
        with part_path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        actual_hash = digest.hexdigest()
        if actual_hash != str(state["sha256"]):
            part_path.unlink(missing_ok=True)
            sidecar_path.unlink(missing_ok=True)
            raise KnowledgeBackupError("Recording SHA-256 verification failed.", 409)

        app_key = str(state["app_key"])
        source_key = str(state["source_key"])
        asset_key = str(state["asset_key"])
        with db() as connection:
            row = _row_for_item(connection, int(state["item_id"]))
            _assert_owned_external_item(row, app_key, source_key)
            _assert_kind_scope(identity, str(row["kind"] or ""))
            metadata = _load_metadata(row)
            assets = metadata.get("assets") if isinstance(metadata.get("assets"), list) else []
            for existing in assets:
                if isinstance(existing, dict) and str(existing.get("asset_key") or "") == asset_key:
                    if str(existing.get("sha256") or "") == actual_hash:
                        part_path.unlink(missing_ok=True)
                        sidecar_path.unlink(missing_ok=True)
                        return {"committed": True, "existing": True, "item_id": int(row["id"]), "asset": _safe_asset(existing)}
                    raise KnowledgeBackupError("An asset with this key already exists with different content.", 409)

            suffix = _ALLOWED_MEDIA[str(state["media_type"])]
            stored_name = f"{uuid.uuid4().hex}{suffix}"
            target = _asset_dir() / stored_name
            part_path.replace(target)
            asset = {
                "asset_key": asset_key,
                "stored_name": f"attachments/{stored_name}",
                "original_name": str(state["original_name"]),
                "media_type": str(state["media_type"]),
                "size_bytes": actual_size,
                "sha256": actual_hash,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            assets.append(asset)
            metadata["assets"] = assets
            connection.execute(
                "UPDATE knowledge_items SET metadata_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (json.dumps(metadata, separators=(",", ":"), ensure_ascii=False), int(row["id"])),
            )
            connection.execute(
                """
                INSERT INTO activity_log(actor_type,actor_key,action,resource_type,resource_key,metadata_json)
                VALUES ('app',?,'knowledge.asset_backed_up','knowledge',?,?)
                """,
                (app_key, str(row["id"]), json.dumps({"asset_key": asset_key, "size_bytes": actual_size, "sha256": actual_hash}, separators=(",", ":"))),
            )
        sidecar_path.unlink(missing_ok=True)
        return {"committed": True, "existing": False, "item_id": int(state["item_id"]), "asset": _safe_asset(asset)}
