from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from ..database import db

FEDERATED_DATA_VERSION = "2.4"
DATASETS = ("memory", "knowledge", "contacts", "tasks", "notifications", "profile_context")
SOURCES = ("homeserver", "vp3_cloud")
MAX_KEY_CHARS = 180
MAX_TITLE_CHARS = 240
MAX_CONTENT_CHARS = 6000


class FederatedDataError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def validate_source(source: str) -> str:
    value = _text(source, 40).lower()
    if value not in SOURCES:
        raise FederatedDataError("Unknown federated data authority source.")
    return value


def validate_dataset(dataset: str) -> str:
    value = _text(dataset, 60).lower()
    if value not in DATASETS:
        raise FederatedDataError("Unknown federated data dataset.")
    return value


def canonical_id(authority_source: str, dataset: str, authority_key: Any) -> str:
    source = validate_source(authority_source)
    name = validate_dataset(dataset)
    key = _text(authority_key, MAX_KEY_CHARS)
    if not key:
        raise FederatedDataError("Federated data authority key is required.")
    digest = hashlib.sha256(f"{source}|{name}|{key}".encode("utf-8")).hexdigest()
    return f"fd24_{digest[:40]}"


def record_revision(title: str, content: str, updated_at: str | None = None) -> str:
    body = {
        "title": _text(title, MAX_TITLE_CHARS),
        "content": _text(content, MAX_CONTENT_CHARS),
        "updated_at": _text(updated_at or "", 80),
    }
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def envelope(
    authority_source: str,
    dataset: str,
    authority_key: Any,
    *,
    title: Any = "",
    content: Any = "",
    updated_at: Any = None,
) -> dict[str, Any]:
    source = validate_source(authority_source)
    name = validate_dataset(dataset)
    key = _text(authority_key, MAX_KEY_CHARS)
    clean_title = _text(title, MAX_TITLE_CHARS)
    clean_content = _text(content, MAX_CONTENT_CHARS)
    clean_updated = _text(updated_at, 80) or None
    return {
        "federation_version": FEDERATED_DATA_VERSION,
        "canonical_id": canonical_id(source, name, key),
        "authority_source": source,
        "authority_key": key,
        "dataset": name,
        "title": clean_title,
        "content": clean_content,
        "updated_at": clean_updated,
        "record_revision": record_revision(clean_title, clean_content, clean_updated),
        "mirror_only": source != "homeserver",
    }


def normalize_envelope(record: dict[str, Any], *, default_source: str, dataset: str, index: int = 0) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise FederatedDataError("Federated record must be an object.")
    source = validate_source(str(record.get("authority_source") or default_source))
    name = validate_dataset(dataset)
    key = _text(record.get("authority_key") or record.get("key") or record.get("id") or f"{name}:{index}", MAX_KEY_CHARS)
    item = envelope(
        source,
        name,
        key,
        title=record.get("title") or record.get("subject") or record.get("name") or name.title(),
        content=record.get("content") or record.get("text") or record.get("body") or record.get("description") or record.get("notes") or "",
        updated_at=record.get("updated_at") or record.get("last_seen_at") or record.get("created_at"),
    )
    supplied = _text(record.get("canonical_id"), 80)
    if supplied and supplied != item["canonical_id"]:
        raise FederatedDataError("Federated record canonical identity does not match its authority tuple.")
    return item


def observe(record: dict[str, Any], *, observed_source: str = "homeserver") -> dict[str, Any]:
    source = validate_source(str(record.get("authority_source") or ""))
    dataset = validate_dataset(str(record.get("dataset") or ""))
    observed = validate_source(observed_source)
    key = _text(record.get("authority_key"), MAX_KEY_CHARS)
    expected = canonical_id(source, dataset, key)
    if _text(record.get("canonical_id"), 80) != expected:
        raise FederatedDataError("Federated record identity is invalid.")
    revision = _text(record.get("record_revision"), 64)
    if not revision:
        revision = record_revision(
            str(record.get("title") or ""),
            str(record.get("content") or ""),
            str(record.get("updated_at") or ""),
        )
    with db() as connection:
        connection.execute(
            """
            INSERT INTO federated_record_links(
                authority_source,dataset,authority_key,canonical_id,observed_source,
                record_hash,source_updated_at,tombstoned,last_seen_at
            ) VALUES (?,?,?,?,?,?,?,0,CURRENT_TIMESTAMP)
            ON CONFLICT(authority_source,dataset,authority_key,observed_source) DO UPDATE SET
                canonical_id=excluded.canonical_id,
                record_hash=excluded.record_hash,
                source_updated_at=excluded.source_updated_at,
                tombstoned=0,
                last_seen_at=CURRENT_TIMESTAMP
            """,
            (source,dataset,key,expected,observed,revision,_text(record.get("updated_at"),80) or None),
        )
    return {"canonical_id": expected, "record_revision": revision, "observed_source": observed}


def observe_snapshot(snapshot: dict[str, Any], *, observed_source: str = "homeserver") -> dict[str, Any]:
    datasets = snapshot.get("datasets")
    if not isinstance(datasets, dict):
        raise FederatedDataError("Federated snapshot datasets are missing.")
    counts: dict[str, int] = {}
    for dataset in DATASETS:
        rows = datasets.get(dataset)
        if not isinstance(rows, list):
            continue
        count = 0
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            normalized = normalize_envelope(
                row,
                default_source=str(snapshot.get("authoritative_source") or "vp3_cloud"),
                dataset=dataset,
                index=index,
            )
            observe(normalized, observed_source=observed_source)
            count += 1
        counts[dataset] = count
    return {"version": FEDERATED_DATA_VERSION, "observed": counts}


def update_cursor(
    peer_source: str,
    dataset: str,
    *,
    revision: str = "",
    cursor: str = "",
    success: bool = True,
    error: str = "",
) -> None:
    peer = validate_source(peer_source)
    name = validate_dataset(dataset)
    rev = _text(revision, 128)
    cur = _text(cursor, 256)
    err = _text(error, 500)
    with db() as connection:
        connection.execute(
            """
            INSERT INTO federated_sync_cursors(
                peer_source,dataset,revision,sync_cursor,last_sync_at,last_success_at,last_error
            ) VALUES (?,?,?,?,CURRENT_TIMESTAMP,CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END,?)
            ON CONFLICT(peer_source,dataset) DO UPDATE SET
                revision=excluded.revision,
                sync_cursor=excluded.sync_cursor,
                last_sync_at=CURRENT_TIMESTAMP,
                last_success_at=CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE federated_sync_cursors.last_success_at END,
                last_error=excluded.last_error
            """,
            (peer,name,rev,cur,1 if success else 0,err,1 if success else 0),
        )


def registry() -> dict[str, Any]:
    with db() as connection:
        sources = [dict(row) for row in connection.execute(
            "SELECT source_id,source_type,authority_scope,writable,priority,capabilities_json,updated_at FROM federated_data_sources ORDER BY priority DESC,source_id"
        ).fetchall()]
        cursor_rows = [dict(row) for row in connection.execute(
            "SELECT peer_source,dataset,revision,sync_cursor,last_sync_at,last_success_at,last_error FROM federated_sync_cursors ORDER BY peer_source,dataset"
        ).fetchall()]
        link_count = int(connection.execute("SELECT COUNT(*) AS c FROM federated_record_links").fetchone()["c"])
    clean_sources = []
    for row in sources:
        try:
            capabilities = json.loads(row.get("capabilities_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            capabilities = {}
        clean_sources.append({
            "source_id": row["source_id"],
            "source_type": row["source_type"],
            "authority_scope": row["authority_scope"],
            "writable": bool(row["writable"]),
            "priority": int(row["priority"]),
            "capabilities": capabilities if isinstance(capabilities, dict) else {},
        })
    return {
        "version": FEDERATED_DATA_VERSION,
        "mode": "native_authority_mirrored_continuity",
        "account_scoped_identity": True,
        "datasets": list(DATASETS),
        "sources": clean_sources,
        "rules": {
            "native_source_remains_authoritative": True,
            "remote_records_are_mirrors": True,
            "no_cross_database_id_writes": True,
            "canonical_identity": "sha256(authority_source|dataset|authority_key)",
            "conflict_resolution": "authority_wins",
        },
        "mirror_link_count": link_count,
        "sync_cursors": cursor_rows,
    }
