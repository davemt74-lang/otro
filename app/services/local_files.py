from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

from ..database import db
from . import app_scopes, knowledge_collection_policy


FILE_CAPABILITY_VERSION = "v0.38"
FILE_REF_RE = re.compile(r"^hsf-(\d+)-([0-9a-f]{16})$")
MAX_LIST_LIMIT = 50
MAX_READ_CHARS = 12000
MAX_OFFSET = 10_000_000


class LocalFileError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int, label: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise LocalFileError(f"{label} must be an integer.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise LocalFileError(f"{label} must be an integer.") from exc
    if parsed < minimum or parsed > maximum:
        raise LocalFileError(f"{label} must be between {minimum} and {maximum}.")
    return parsed


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _version(content_hash: Any) -> str:
    value = str(content_hash or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        return "0" * 16
    return value[:16]


def _file_ref(file_id: int, content_hash: Any) -> str:
    return f"hsf-{int(file_id)}-{_version(content_hash)}"


def _parse_file_ref(value: str) -> tuple[int, str]:
    match = FILE_REF_RE.fullmatch(str(value or "").strip().lower())
    if not match:
        raise LocalFileError("Invalid HomeServer file reference.", status_code=404)
    file_id = int(match.group(1))
    if file_id < 1:
        raise LocalFileError("Invalid HomeServer file reference.", status_code=404)
    return file_id, match.group(2)


def _policy(identity: dict[str, Any] | None, *, owner: bool) -> tuple[set[str] | None, dict[str, Any]]:
    if owner:
        return None, app_scopes.normalize(None)
    app_id = int((identity or {}).get("id") or 0)
    if app_id < 1:
        raise LocalFileError("Connected application identity is unavailable.", status_code=401)
    allowed_collections = knowledge_collection_policy.allowed_collection_keys(app_id)
    scope = app_scopes.normalize((identity or {}).get("scope"))
    return allowed_collections, scope


def _base_query(select_clause: str) -> str:
    return f"""
        SELECT {select_clause}
        FROM knowledge_source_files ksf
        JOIN knowledge_sources ks ON ks.id=ksf.source_id
        JOIN knowledge_items ki ON ki.id=ksf.knowledge_item_id
        LEFT JOIN knowledge_collection_items dci ON dci.knowledge_item_id=ki.id
        LEFT JOIN knowledge_collections dc ON dc.id=dci.collection_id
        LEFT JOIN knowledge_collection_sources sci ON sci.source_id=ks.id
        LEFT JOIN knowledge_collections sc ON sc.id=sci.collection_id
    """


def _row_select() -> str:
    return """
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
        COALESCE(ki.content_hash, '') AS knowledge_content_hash,
        COALESCE(ki.content, '') AS indexed_text,
        COALESCE(dc.collection_key, sc.collection_key, 'general') AS collection_key,
        COALESCE(dc.name, sc.name, 'General') AS collection_name
    """


def _scope_where(
    allowed_collections: set[str] | None,
    scope: dict[str, Any],
) -> tuple[str, list[Any]]:
    clauses = ["ks.enabled=1", "ksf.status='indexed'", "ksf.knowledge_item_id IS NOT NULL"]
    params: list[Any] = []

    if allowed_collections is not None:
        keys = sorted(str(value) for value in allowed_collections)
        if not keys:
            clauses.append("1=0")
        else:
            placeholders = ",".join("?" for _ in keys)
            clauses.append(
                f"COALESCE(dc.collection_key, sc.collection_key, 'general') IN ({placeholders})"
            )
            params.extend(keys)

    kinds = [str(value) for value in scope.get("knowledge_kinds", []) if str(value)]
    if kinds:
        placeholders = ",".join("?" for _ in kinds)
        clauses.append(f"ki.kind IN ({placeholders})")
        params.extend(kinds)

    return " WHERE " + " AND ".join(clauses), params


def _visible_row(row: Any, allowed_collections: set[str] | None, scope: dict[str, Any]) -> bool:
    """Defense-in-depth check mirroring the SQL policy constraints."""
    if not bool(row["source_enabled"]):
        return False
    if str(row["status"] or "") != "indexed":
        return False
    if allowed_collections is not None and str(row["collection_key"] or "general") not in allowed_collections:
        return False
    if not app_scopes.knowledge_kind_allowed(scope, row["kind"]):
        return False
    return True


def _safe_metadata(row: Any) -> dict[str, Any]:
    relative_path = str(row["relative_path"] or "").replace("\\", "/").lstrip("/")[:1000]
    name = PurePosixPath(relative_path).name[:255] if relative_path else str(row["title"] or "File")[:255]
    return {
        "ref": _file_ref(int(row["file_id"]), row["file_content_hash"]),
        "name": name,
        "relative_path": relative_path,
        "source_label": str(row["source_label"] or "Local folder")[:120],
        "collection_key": str(row["collection_key"] or "general")[:64],
        "collection_name": str(row["collection_name"] or "General")[:120],
        "kind": str(row["kind"] or "")[:80],
        "size_bytes": max(0, int(row["size_bytes"] or 0)),
        "status": "indexed",
        "content_version": _version(row["file_content_hash"]),
        "updated_at": row["file_updated_at"],
    }


def count_files(identity: dict[str, Any] | None, *, owner: bool = False) -> int:
    allowed_collections, scope = _policy(identity, owner=owner)
    where, params = _scope_where(allowed_collections, scope)
    with db() as connection:
        row = connection.execute(
            _base_query("COUNT(DISTINCT ksf.id) AS file_count") + where,
            tuple(params),
        ).fetchone()
    return max(0, int(row["file_count"] if row is not None else 0))


def list_files(
    identity: dict[str, Any] | None,
    query: str = "",
    limit: int = 20,
    *,
    owner: bool = False,
) -> dict[str, Any]:
    bounded = _bounded_int(limit, default=20, minimum=1, maximum=MAX_LIST_LIMIT, label="limit")
    q = str(query or "").strip()[:240]
    allowed_collections, scope = _policy(identity, owner=owner)
    where, params = _scope_where(allowed_collections, scope)

    sql = _base_query(_row_select()) + where
    if q:
        term = f"%{_escape_like(q.lower())}%"
        sql += (
            " AND (LOWER(ksf.relative_path) LIKE ? ESCAPE '\\'"
            " OR LOWER(ks.label) LIKE ? ESCAPE '\\'"
            " OR LOWER(ki.title) LIKE ? ESCAPE '\\')"
        )
        params.extend([term, term, term])
    sql += " ORDER BY ksf.updated_at DESC, ksf.id DESC LIMIT ?"
    params.append(bounded)

    items: list[dict[str, Any]] = []
    with db() as connection:
        rows = connection.execute(sql, tuple(params)).fetchall()
        for row in rows:
            if _visible_row(row, allowed_collections, scope):
                items.append(_safe_metadata(row))

    return {
        "items": items,
        "count": len(items),
        "query": q,
        "capability_version": FILE_CAPABILITY_VERSION,
        "privacy": {
            "absolute_paths_exposed": False,
            "caller_paths_accepted": False,
            "original_files_reopened": False,
            "indexed_text_only": True,
        },
    }


def read_file(
    identity: dict[str, Any] | None,
    file_ref: str,
    *,
    offset: int = 0,
    max_chars: int = 6000,
    owner: bool = False,
) -> dict[str, Any]:
    file_id, requested_version = _parse_file_ref(file_ref)
    bounded_offset = _bounded_int(offset, default=0, minimum=0, maximum=MAX_OFFSET, label="offset")
    bounded_chars = _bounded_int(max_chars, default=6000, minimum=1, maximum=MAX_READ_CHARS, label="max_chars")
    allowed_collections, scope = _policy(identity, owner=owner)

    with db() as connection:
        row = connection.execute(
            _base_query(_row_select()) + " WHERE ksf.id=? LIMIT 1",
            (file_id,),
        ).fetchone()
    if row is None or not _visible_row(row, allowed_collections, scope):
        raise LocalFileError("File is unavailable to this application.", status_code=404)

    current_version = _version(row["file_content_hash"])
    if requested_version != current_version:
        raise LocalFileError("File reference is stale. Discover the file again before reading it.", status_code=409)

    text = str(row["indexed_text"] or "")
    total_chars = len(text)
    if bounded_offset > total_chars:
        raise LocalFileError("offset is beyond the indexed file content.", status_code=416)
    end = min(total_chars, bounded_offset + bounded_chars)
    content = text[bounded_offset:end]
    next_offset = end if end < total_chars else None
    metadata = _safe_metadata(row)
    return {
        "file": metadata,
        "text": content,
        "offset": bounded_offset,
        "returned_chars": len(content),
        "total_chars": total_chars,
        "next_offset": next_offset,
        "truncated": next_offset is not None,
        "capability_version": FILE_CAPABILITY_VERSION,
        "privacy": {
            "absolute_paths_exposed": False,
            "caller_paths_accepted": False,
            "original_files_reopened": False,
            "indexed_text_only": True,
        },
    }
