from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ..database import db
from . import federated_data, local_file_actions, local_files

_CANONICAL_ID = re.compile(r"^fd24_[0-9a-f]{40}$")
_REVISION = re.compile(r"^[0-9a-f]{64}$")
_MUTATION_ID = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


class FileContinuityError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _mutation_id(value: Any) -> str:
    mutation = _text(value, 128)
    if not _MUTATION_ID.fullmatch(mutation):
        raise FileContinuityError("mutation_id must be 8 to 128 safe characters.")
    return mutation


def _revision(value: Any) -> str:
    revision = _text(value, 64).lower()
    if not _REVISION.fullmatch(revision):
        raise FileContinuityError("expected_revision must be a SHA-256 value.")
    return revision


def _authority_key(file_id: int) -> str:
    value = int(file_id)
    if value < 1:
        raise FileContinuityError("File identity is invalid.", 500)
    return f"local_file:{value}"


def _row(file_id: int) -> dict[str, Any] | None:
    with db() as connection:
        row = connection.execute(
            """
            SELECT
                ksf.id AS file_id,
                ksf.source_id,
                ksf.relative_path,
                ksf.content_hash AS file_content_hash,
                ksf.size_bytes,
                ksf.status,
                ksf.updated_at AS file_updated_at,
                ks.label AS source_label,
                ks.enabled AS source_enabled,
                ki.id AS knowledge_item_id,
                ki.title,
                ki.kind,
                COALESCE(dc.collection_key, sc.collection_key, 'general') AS collection_key,
                COALESCE(dc.name, sc.name, 'General') AS collection_name
            FROM knowledge_source_files ksf
            JOIN knowledge_sources ks ON ks.id=ksf.source_id
            JOIN knowledge_items ki ON ki.id=ksf.knowledge_item_id
            LEFT JOIN knowledge_collection_items dci ON dci.knowledge_item_id=ki.id
            LEFT JOIN knowledge_collections dc ON dc.id=dci.collection_id
            LEFT JOIN knowledge_collection_sources sci ON sci.source_id=ks.id
            LEFT JOIN knowledge_collections sc ON sc.id=sci.collection_id
            WHERE ksf.id=? LIMIT 1
            """,
            (int(file_id),),
        ).fetchone()
    return dict(row) if row else None


def current_file(file_id: int) -> dict[str, Any] | None:
    row = _row(file_id)
    if row is None or not bool(row.get("source_enabled")) or str(row.get("status") or "") != "indexed":
        return None
    return local_files._safe_metadata(row)


def list_federated_files(
    identity: dict[str, Any] | None,
    query: str = "",
    limit: int = 50,
    *,
    owner: bool = False,
) -> list[dict[str, Any]]:
    result = local_files.list_files(identity, query, limit, owner=owner)
    return [dict(item) for item in result.get("items", []) if isinstance(item, dict)]


def _file_id_from_canonical(canonical_id_value: str) -> int:
    canonical = _text(canonical_id_value, 45).lower()
    if not _CANONICAL_ID.fullmatch(canonical):
        raise FileContinuityError("File canonical identity is invalid.")
    key = federated_data.resolve_authority_key(
        canonical,
        authority_source="homeserver",
        dataset="files",
        observed_source="homeserver",
    )
    if not key or not key.startswith("local_file:"):
        raise FileContinuityError("HomeServer file not found.", 404)
    try:
        file_id = int(key.split(":", 1)[1])
    except ValueError as exc:
        raise FileContinuityError("File canonical identity is invalid.", 409) from exc
    if federated_data.canonical_id("homeserver", "files", _authority_key(file_id)) != canonical:
        raise FileContinuityError("File canonical identity does not match its authority.", 409)
    return file_id


def get_federated_file_by_canonical(canonical_id_value: str) -> dict[str, Any] | None:
    try:
        file_id = _file_id_from_canonical(canonical_id_value)
    except FileContinuityError as exc:
        if exc.status_code == 404:
            return None
        raise
    return current_file(file_id)


def normalize_file_update_arguments(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    allowed = {"canonical_id", "mutation_id", "expected_revision", "content"}
    unknown = set(raw) - allowed
    if unknown:
        raise FileContinuityError(f"Unsupported files.update argument: {sorted(unknown)[0]}")
    canonical = _text(raw.get("canonical_id"), 45).lower()
    if not _CANONICAL_ID.fullmatch(canonical):
        raise FileContinuityError("files.update requires a valid canonical_id.")
    if "content" not in raw or not isinstance(raw.get("content"), str):
        raise FileContinuityError("files.update requires text content.")
    content = str(raw.get("content") or "")
    if not content.strip():
        raise FileContinuityError("files.update requires non-empty indexed text.")
    if len(content) > 50000:
        raise FileContinuityError("files.update content exceeds 50,000 characters.", 413)
    return {
        "canonical_id": canonical,
        "mutation_id": _mutation_id(raw.get("mutation_id")),
        "expected_revision": _revision(raw.get("expected_revision")),
        "content": content,
    }


def normalize_file_delete_arguments(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    allowed = {"canonical_id", "mutation_id", "expected_revision"}
    unknown = set(raw) - allowed
    if unknown:
        raise FileContinuityError(f"Unsupported files.delete argument: {sorted(unknown)[0]}")
    canonical = _text(raw.get("canonical_id"), 45).lower()
    if not _CANONICAL_ID.fullmatch(canonical):
        raise FileContinuityError("files.delete requires a valid canonical_id.")
    return {
        "canonical_id": canonical,
        "mutation_id": _mutation_id(raw.get("mutation_id")),
        "expected_revision": _revision(raw.get("expected_revision")),
    }


def safe_file_mutation_meta(action: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    return {
        "action": _text(action, 32),
        "canonical_id_present": bool(_text(raw.get("canonical_id"), 45)),
        "mutation_id_present": bool(_text(raw.get("mutation_id"), 128)),
        "expected_revision_present": bool(_text(raw.get("expected_revision"), 64)),
        "ref_present": bool(_text(raw.get("ref"), 96)),
        "content_length": len(str(raw.get("content") or "")),
        "content_bytes": len(str(raw.get("content") or "").encode("utf-8")),
        "argument_count": len(raw),
    }


def _request_hash(action_key: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        {"action": action_key, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _replay(source_app_key: str, mutation_id: str, action_key: str, request_hash: str) -> dict[str, Any] | None:
    with db() as connection:
        row = connection.execute(
            """
            SELECT action_key,request_hash,result_json
            FROM federated_file_mutations
            WHERE source_app_key=? AND mutation_id=? LIMIT 1
            """,
            (source_app_key, mutation_id),
        ).fetchone()
    if row is None:
        return None
    if str(row["action_key"]) != action_key or str(row["request_hash"]) != request_hash:
        raise FileContinuityError("mutation_id was already used with different arguments.", 409)
    result = json.loads(row["result_json"] or "{}")
    if not isinstance(result, dict):
        result = {}
    result["idempotent_replay"] = True
    return result


def _record(
    source_app_key: str,
    mutation_id: str,
    action_key: str,
    request_hash: str,
    canonical_id_value: str,
    result: dict[str, Any],
) -> None:
    with db() as connection:
        connection.execute(
            """
            INSERT INTO federated_file_mutations(
                source_app_key,mutation_id,action_key,request_hash,canonical_id,result_json
            ) VALUES (?,?,?,?,?,?)
            """,
            (
                source_app_key,
                mutation_id,
                action_key,
                request_hash,
                canonical_id_value,
                json.dumps(result, ensure_ascii=False, separators=(",", ":")),
            ),
        )


def update_federated_file(payload: dict[str, Any], *, source_app_key: str) -> dict[str, Any]:
    normalized = normalize_file_update_arguments(payload)
    canonical = str(normalized["canonical_id"])
    mutation = str(normalized["mutation_id"])
    expected = str(normalized["expected_revision"])
    request_hash = _request_hash("files.update", normalized)
    replay = _replay(source_app_key, mutation, "files.update", request_hash)
    if replay is not None:
        return replay

    file_id = _file_id_from_canonical(canonical)
    current = current_file(file_id)
    if current is None:
        raise FileContinuityError("HomeServer file not found.", 404)
    if str(current.get("record_revision") or "") != expected:
        raise FileContinuityError(
            "HomeServer file changed after this edit was prepared. Refresh and try again.",
            409,
        )
    try:
        action = local_file_actions.update_file(
            source_app_key,
            str(current["ref"]),
            str(normalized["content"]),
        )
    except local_file_actions.LocalFileActionError as exc:
        raise FileContinuityError(str(exc), exc.status_code) from exc
    saved = current_file(file_id)
    if saved is None:
        raise FileContinuityError("Updated HomeServer file is unavailable.", 500)
    result = {
        "updated": bool(action.get("updated")),
        "unchanged": bool(action.get("unchanged")),
        "file": saved,
    }
    _record(source_app_key, mutation, "files.update", request_hash, canonical, result)
    return result


def delete_federated_file(payload: dict[str, Any], *, source_app_key: str) -> dict[str, Any]:
    normalized = normalize_file_delete_arguments(payload)
    canonical = str(normalized["canonical_id"])
    mutation = str(normalized["mutation_id"])
    expected = str(normalized["expected_revision"])
    request_hash = _request_hash("files.delete", normalized)
    replay = _replay(source_app_key, mutation, "files.delete", request_hash)
    if replay is not None:
        return replay

    file_id = _file_id_from_canonical(canonical)
    current = current_file(file_id)
    if current is None:
        raise FileContinuityError("HomeServer file not found.", 404)
    if str(current.get("record_revision") or "") != expected:
        raise FileContinuityError(
            "HomeServer file changed after this delete was prepared. Refresh and try again.",
            409,
        )
    try:
        action = local_file_actions.delete_file(source_app_key, str(current["ref"]))
    except local_file_actions.LocalFileActionError as exc:
        raise FileContinuityError(str(exc), exc.status_code) from exc
    if not bool(action.get("deleted")):
        raise FileContinuityError("HomeServer file could not be deleted.", 409)
    federated_data.mark_tombstone(
        "homeserver",
        "files",
        _authority_key(file_id),
        observed_source="homeserver",
    )
    result = {"deleted": True, "canonical_id": canonical, "file": current}
    _record(source_app_key, mutation, "files.delete", request_hash, canonical, result)
    return result
