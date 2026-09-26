from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from ..database import db
from . import app_scopes, federated_data

_CANONICAL_ID = re.compile(r"^fd24_[0-9a-f]{40}$")
_REVISION = re.compile(r"^[0-9a-f]{64}$")
_MUTATION_ID = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_MEMORY_TYPES = {"working", "episodic", "semantic", "preference", "relationship", "procedural"}


class MemoryContinuityError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _float(value: Any, default: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _mutation_id(value: Any, *, required: bool = True) -> str:
    mutation = _text(value, 128)
    if not mutation and not required:
        return ""
    if not _MUTATION_ID.fullmatch(mutation):
        raise MemoryContinuityError("mutation_id must be 8 to 128 safe characters.")
    return mutation


def _revision(value: Any) -> str:
    revision = _text(value, 64).lower()
    if not _REVISION.fullmatch(revision):
        raise MemoryContinuityError("expected_revision must be a SHA-256 value.")
    return revision


def _memory_type(value: Any, default: str = "semantic") -> str:
    selected = _text(value, 40).lower() or default
    if selected not in _MEMORY_TYPES:
        raise MemoryContinuityError("memory_type is invalid.")
    return selected


def _authority_key(memory_id: int) -> str:
    value = int(memory_id)
    if value < 1:
        raise MemoryContinuityError("Memory identity is invalid.", 500)
    return f"agent_memory:{value}"


def _metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        decoded = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _row(memory_id: int) -> dict[str, Any] | None:
    with db() as connection:
        row = connection.execute(
            """
            SELECT
                id,agent_id,memory_key,content,importance,metadata_json,
                memory_type,source_app_key,source_event_id,confidence,
                entity_type,entity_key,reinforcement_count,created_at,updated_at
            FROM agent_memory WHERE id=? LIMIT 1
            """,
            (int(memory_id),),
        ).fetchone()
    return dict(row) if row else None


def _revision_for(row: dict[str, Any]) -> str:
    canonical = {
        "agent_id": row.get("agent_id"),
        "memory_key": row.get("memory_key"),
        "content": row.get("content"),
        "importance": round(_float(row.get("importance"), 0.5), 6),
        "memory_type": _text(row.get("memory_type"), 40) or "semantic",
        "source_app_key": _text(row.get("source_app_key"), 160),
        "source_event_id": row.get("source_event_id"),
        "confidence": round(_float(row.get("confidence"), 0.5), 6),
        "entity_type": _text(row.get("entity_type"), 160),
        "entity_key": _text(row.get("entity_key"), 240),
        "reinforcement_count": int(row.get("reinforcement_count") or 0),
        "metadata": _metadata(row.get("metadata_json")),
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def project_memory(row: dict[str, Any]) -> dict[str, Any]:
    memory_id = int(row["id"])
    authority_key = _authority_key(memory_id)
    revision = _revision_for(row)
    canonical = federated_data.canonical_id("homeserver", "memory", authority_key)
    metadata = _metadata(row.get("metadata_json"))
    item = {
        "id": memory_id,
        "agent_id": row.get("agent_id"),
        "memory_key": row.get("memory_key"),
        "content": str(row.get("content") or ""),
        "importance": _float(row.get("importance"), 0.5),
        "memory_type": _text(row.get("memory_type"), 40) or "semantic",
        "confidence": _float(row.get("confidence"), 0.5),
        "source_app_key": _text(row.get("source_app_key"), 160) or None,
        "source_event_id": row.get("source_event_id"),
        "entity_type": _text(row.get("entity_type"), 160) or None,
        "entity_key": _text(row.get("entity_key"), 240) or None,
        "reinforcement_count": int(row.get("reinforcement_count") or 0),
        "metadata": metadata,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "authority_source": "homeserver",
        "authority_key": authority_key,
        "canonical_id": canonical,
        "record_revision": revision,
        "federation_version": federated_data.FEDERATED_DATA_VERSION,
        "mirror_only": False,
        "mutation_route": "homeserver_owner_approval",
        "allowed_mutations": ["update", "delete"],
    }
    federated_data.observe(
        federated_data.envelope(
            "homeserver",
            "memory",
            authority_key,
            title=_text(row.get("memory_key"), 240) or item["memory_type"].title() + " memory",
            content=_text(row.get("content"), federated_data.MAX_CONTENT_CHARS),
            updated_at=row.get("updated_at"),
        ),
        observed_source="homeserver",
    )
    return item


def get_federated_memory(memory_id: int) -> dict[str, Any] | None:
    row = _row(memory_id)
    return project_memory(row) if row else None


def _memory_id_from_canonical(canonical_id_value: str) -> int:
    canonical = _text(canonical_id_value, 45).lower()
    if not _CANONICAL_ID.fullmatch(canonical):
        raise MemoryContinuityError("Memory canonical identity is invalid.")
    key = federated_data.resolve_authority_key(
        canonical,
        authority_source="homeserver",
        dataset="memory",
        observed_source="homeserver",
    )
    if not key or not key.startswith("agent_memory:"):
        raise MemoryContinuityError("HomeServer memory not found.", 404)
    try:
        memory_id = int(key.split(":", 1)[1])
    except ValueError as exc:
        raise MemoryContinuityError("Memory canonical identity is invalid.", 409) from exc
    if federated_data.canonical_id("homeserver", "memory", _authority_key(memory_id)) != canonical:
        raise MemoryContinuityError("Memory canonical identity does not match its authority.", 409)
    return memory_id


def get_federated_memory_by_canonical(canonical_id_value: str) -> dict[str, Any] | None:
    try:
        return get_federated_memory(_memory_id_from_canonical(canonical_id_value))
    except MemoryContinuityError as exc:
        if exc.status_code == 404:
            return None
        raise


def _scope_for_source(source_app_key: str, owner: bool) -> dict[str, Any]:
    return dict(app_scopes.DEFAULT_SCOPE) if owner else app_scopes.get_scope_for_source(source_app_key)


def _assert_memory_scope(source_app_key: str, memory_key: str | None, *, owner: bool = False) -> None:
    if owner:
        return
    if not app_scopes.memory_key_allowed(_scope_for_source(source_app_key, owner), memory_key):
        raise MemoryContinuityError("Memory is outside this application's allowed scope.", 403)


def list_federated_memories(
    query: str = "",
    limit: int = 50,
    *,
    source_app_key: str = "owner",
    owner: bool = False,
) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 100))
    q = _text(query, 240).lower()
    with db() as connection:
        rows = connection.execute(
            """
            SELECT
                id,agent_id,memory_key,content,importance,metadata_json,
                memory_type,source_app_key,source_event_id,confidence,
                entity_type,entity_key,reinforcement_count,created_at,updated_at
            FROM agent_memory
            ORDER BY importance DESC,confidence DESC,updated_at DESC,id DESC
            LIMIT 300
            """
        ).fetchall()
    out: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        _assert_memory_scope(source_app_key, row.get("memory_key"), owner=owner)
        haystack = " ".join(
            str(row.get(key) or "")
            for key in ("memory_key", "content", "memory_type", "entity_type", "entity_key")
        ).lower()
        if q and not all(token in haystack for token in q.split() if token):
            continue
        out.append(project_memory(row))
        if len(out) >= bounded:
            break
    return out


def normalize_memory_create_arguments(
    payload: dict[str, Any] | None,
    *,
    require_mutation: bool = False,
) -> dict[str, Any]:
    raw = dict(payload or {})
    allowed = {"content", "memory_key", "importance", "memory_type", "confidence", "entity_type", "entity_key", "mutation_id"}
    unknown = set(raw) - allowed
    if unknown:
        raise MemoryContinuityError(f"Unsupported memory.write argument: {sorted(unknown)[0]}")
    content = str(raw.get("content") or "").strip()
    if not content:
        raise MemoryContinuityError("memory.write requires content.")
    if len(content) > 50000:
        raise MemoryContinuityError("memory.write content exceeds 50,000 characters.")
    memory_key = _text(raw.get("memory_key"), 160) or None
    entity_type = _text(raw.get("entity_type"), 160) or None
    entity_key = _text(raw.get("entity_key"), 240) or None
    return {
        "content": content,
        "memory_key": memory_key,
        "importance": _float(raw.get("importance"), 0.5),
        "memory_type": _memory_type(raw.get("memory_type"), "semantic"),
        "confidence": _float(raw.get("confidence"), 0.75),
        "entity_type": entity_type,
        "entity_key": entity_key,
        "mutation_id": _mutation_id(raw.get("mutation_id"), required=require_mutation),
    }


def normalize_memory_update_arguments(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    allowed = {
        "canonical_id", "mutation_id", "expected_revision", "content", "memory_key",
        "importance", "memory_type", "confidence", "entity_type", "entity_key",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise MemoryContinuityError(f"Unsupported memory.update argument: {sorted(unknown)[0]}")
    canonical = _text(raw.get("canonical_id"), 45).lower()
    if not _CANONICAL_ID.fullmatch(canonical):
        raise MemoryContinuityError("memory.update requires a valid canonical_id.")
    mutable = set(raw) - {"canonical_id", "mutation_id", "expected_revision"}
    if not mutable:
        raise MemoryContinuityError("memory.update requires at least one mutable field.")
    normalized: dict[str, Any] = {
        "canonical_id": canonical,
        "mutation_id": _mutation_id(raw.get("mutation_id")),
        "expected_revision": _revision(raw.get("expected_revision")),
    }
    if "content" in raw:
        content = str(raw.get("content") or "").strip()
        if not content:
            raise MemoryContinuityError("memory.update content cannot be empty.")
        if len(content) > 50000:
            raise MemoryContinuityError("memory.update content exceeds 50,000 characters.")
        normalized["content"] = content
    if "memory_key" in raw:
        normalized["memory_key"] = _text(raw.get("memory_key"), 160) or None
    if "importance" in raw:
        normalized["importance"] = _float(raw.get("importance"), 0.5)
    if "memory_type" in raw:
        normalized["memory_type"] = _memory_type(raw.get("memory_type"))
    if "confidence" in raw:
        normalized["confidence"] = _float(raw.get("confidence"), 0.75)
    if "entity_type" in raw:
        normalized["entity_type"] = _text(raw.get("entity_type"), 160) or None
    if "entity_key" in raw:
        normalized["entity_key"] = _text(raw.get("entity_key"), 240) or None
    return normalized


def normalize_memory_delete_arguments(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    allowed = {"canonical_id", "mutation_id", "expected_revision"}
    unknown = set(raw) - allowed
    if unknown:
        raise MemoryContinuityError(f"Unsupported memory.delete argument: {sorted(unknown)[0]}")
    canonical = _text(raw.get("canonical_id"), 45).lower()
    if not _CANONICAL_ID.fullmatch(canonical):
        raise MemoryContinuityError("memory.delete requires a valid canonical_id.")
    return {
        "canonical_id": canonical,
        "mutation_id": _mutation_id(raw.get("mutation_id")),
        "expected_revision": _revision(raw.get("expected_revision")),
    }


def safe_memory_mutation_meta(action: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    return {
        "action": _text(action, 32),
        "content_length": len(str(raw.get("content") or "")),
        "memory_key_length": len(str(raw.get("memory_key") or "")),
        "memory_type": _text(raw.get("memory_type"), 40),
        "importance": _float(raw.get("importance"), 0.5),
        "confidence": _float(raw.get("confidence"), 0.75),
        "canonical_id_present": bool(_text(raw.get("canonical_id"), 45)),
        "mutation_id_present": bool(_text(raw.get("mutation_id"), 128)),
        "expected_revision_present": bool(_text(raw.get("expected_revision"), 64)),
        "entity_type_present": bool(_text(raw.get("entity_type"), 160)),
        "entity_key_present": bool(_text(raw.get("entity_key"), 240)),
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
    if not mutation_id:
        return None
    with db() as connection:
        row = connection.execute(
            """
            SELECT action_key,request_hash,result_json
            FROM federated_memory_mutations
            WHERE source_app_key=? AND mutation_id=? LIMIT 1
            """,
            (source_app_key, mutation_id),
        ).fetchone()
    if row is None:
        return None
    if str(row["action_key"]) != action_key or str(row["request_hash"]) != request_hash:
        raise MemoryContinuityError("mutation_id was already used with different arguments.", 409)
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
    if not mutation_id:
        return
    with db() as connection:
        connection.execute(
            """
            INSERT INTO federated_memory_mutations(
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


def create_federated_memory(
    payload: dict[str, Any],
    *,
    source_app_key: str,
    owner: bool = False,
) -> dict[str, Any]:
    normalized = normalize_memory_create_arguments(payload, require_mutation=False)
    _assert_memory_scope(source_app_key, normalized.get("memory_key"), owner=owner)
    mutation = str(normalized.pop("mutation_id") or "")
    request_payload = {**normalized, "mutation_id": mutation}
    request_hash = _request_hash("memory.write", request_payload)
    replay = _replay(source_app_key, mutation, "memory.write", request_hash)
    if replay is not None:
        return replay

    with db() as connection:
        primary = connection.execute("SELECT id FROM agents WHERE is_primary=1 LIMIT 1").fetchone()
        agent_id = int(primary["id"]) if primary else None
        cursor = connection.execute(
            """
            INSERT INTO agent_memory(
                agent_id,memory_key,content,importance,memory_type,source_app_key,
                confidence,entity_type,entity_key,metadata_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                agent_id,
                normalized["memory_key"],
                normalized["content"],
                normalized["importance"],
                normalized["memory_type"],
                source_app_key,
                normalized["confidence"],
                normalized["entity_type"],
                normalized["entity_key"],
                json.dumps(
                    {
                        "continuity_version": "2.4",
                        "created_via": "owner" if owner else "federated_action",
                    },
                    separators=(",", ":"),
                ),
            ),
        )
        memory_id = int(cursor.lastrowid)
    memory = get_federated_memory(memory_id)
    if memory is None:
        raise MemoryContinuityError("Created HomeServer memory is unavailable.", 500)
    result = {"created": True, "memory": memory}
    _record(source_app_key, mutation, "memory.write", request_hash, str(memory["canonical_id"]), result)
    return result


def update_federated_memory(
    payload: dict[str, Any],
    *,
    source_app_key: str,
    owner: bool = False,
) -> dict[str, Any]:
    normalized = normalize_memory_update_arguments(payload)
    canonical = str(normalized["canonical_id"])
    mutation = str(normalized["mutation_id"])
    expected = str(normalized["expected_revision"])
    request_hash = _request_hash("memory.update", normalized)
    replay = _replay(source_app_key, mutation, "memory.update", request_hash)
    if replay is not None:
        return replay

    memory_id = _memory_id_from_canonical(canonical)
    current = get_federated_memory(memory_id)
    if current is None:
        raise MemoryContinuityError("HomeServer memory not found.", 404)
    _assert_memory_scope(source_app_key, current.get("memory_key"), owner=owner)
    if str(current["record_revision"]) != expected:
        raise MemoryContinuityError("HomeServer memory changed after this update was prepared. Refresh and try again.", 409)

    next_key = normalized.get("memory_key", current.get("memory_key"))
    _assert_memory_scope(source_app_key, next_key, owner=owner)

    fields: list[str] = []
    values: list[Any] = []
    mapping = {
        "content": "content",
        "memory_key": "memory_key",
        "importance": "importance",
        "memory_type": "memory_type",
        "confidence": "confidence",
        "entity_type": "entity_type",
        "entity_key": "entity_key",
    }
    for key, column in mapping.items():
        if key in normalized:
            fields.append(f"{column}=?")
            values.append(normalized[key])
    if not fields:
        raise MemoryContinuityError("memory.update did not contain mutable fields.")
    values.append(memory_id)
    with db() as connection:
        connection.execute(
            f"UPDATE agent_memory SET {', '.join(fields)}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            tuple(values),
        )
    updated = get_federated_memory(memory_id)
    if updated is None:
        raise MemoryContinuityError("Updated HomeServer memory is unavailable.", 500)
    result = {"updated": True, "memory": updated}
    _record(source_app_key, mutation, "memory.update", request_hash, canonical, result)
    return result


def delete_federated_memory(
    payload: dict[str, Any],
    *,
    source_app_key: str,
    owner: bool = False,
) -> dict[str, Any]:
    normalized = normalize_memory_delete_arguments(payload)
    canonical = str(normalized["canonical_id"])
    mutation = str(normalized["mutation_id"])
    expected = str(normalized["expected_revision"])
    request_hash = _request_hash("memory.delete", normalized)
    replay = _replay(source_app_key, mutation, "memory.delete", request_hash)
    if replay is not None:
        return replay

    memory_id = _memory_id_from_canonical(canonical)
    current = get_federated_memory(memory_id)
    if current is None:
        raise MemoryContinuityError("HomeServer memory not found.", 404)
    _assert_memory_scope(source_app_key, current.get("memory_key"), owner=owner)
    if str(current["record_revision"]) != expected:
        raise MemoryContinuityError("HomeServer memory changed after this delete was prepared. Refresh and try again.", 409)

    with db() as connection:
        cursor = connection.execute("DELETE FROM agent_memory WHERE id=?", (memory_id,))
        if cursor.rowcount != 1:
            raise MemoryContinuityError("HomeServer memory not found.", 404)
    federated_data.mark_tombstone(
        "homeserver",
        "memory",
        _authority_key(memory_id),
        observed_source="homeserver",
    )
    result = {"deleted": True, "canonical_id": canonical, "memory": current}
    _record(source_app_key, mutation, "memory.delete", request_hash, canonical, result)
    return result


def ensure_mutation_id(payload: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(payload or {})
    if not _text(out.get("mutation_id"), 128):
        out["mutation_id"] = uuid.uuid4().hex
    return out
