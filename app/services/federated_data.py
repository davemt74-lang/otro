from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from ..database import db

FEDERATED_DATA_VERSION = "2.4"
DATASETS = ("memory", "knowledge", "contacts", "tasks", "calendar", "files", "notifications", "profile_context")
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
    supplied_revision = _text(record.get("record_revision"), 64).lower()
    if supplied_revision:
        if not re.fullmatch(r"[0-9a-f]{64}", supplied_revision):
            raise FederatedDataError("Federated record revision must be a SHA-256 value.")
        item["record_revision"] = supplied_revision
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


def reconciliation_state(peer_source: str) -> dict[str, Any]:
    peer = validate_source(peer_source)
    with db() as connection:
        row = connection.execute(
            """
            SELECT peer_source,needs_reconciliation,last_disconnect_at,last_connected_at,
                   last_reconciled_at,last_snapshot_revision,last_run_id,last_error
            FROM federated_reconciliation_state WHERE peer_source=? LIMIT 1
            """,
            (peer,),
        ).fetchone()
    if row is None:
        return {
            "peer_source": peer,
            "needs_reconciliation": True,
            "last_disconnect_at": None,
            "last_connected_at": None,
            "last_reconciled_at": None,
            "last_snapshot_revision": "",
            "last_run_id": "",
            "last_error": "",
        }
    item = dict(row)
    item["needs_reconciliation"] = bool(item.get("needs_reconciliation"))
    return item


def note_peer_disconnected(peer_source: str, reason: str = "") -> dict[str, Any]:
    peer = validate_source(peer_source)
    err = _text(reason, 500)
    with db() as connection:
        connection.execute(
            """
            INSERT INTO federated_reconciliation_state(
                peer_source,needs_reconciliation,last_disconnect_at,last_error
            ) VALUES (?,1,CURRENT_TIMESTAMP,?)
            ON CONFLICT(peer_source) DO UPDATE SET
                needs_reconciliation=1,
                last_disconnect_at=CURRENT_TIMESTAMP,
                last_error=excluded.last_error
            """,
            (peer, err),
        )
    return reconciliation_state(peer)


def note_peer_connected(peer_source: str) -> dict[str, Any]:
    peer = validate_source(peer_source)
    before = reconciliation_state(peer)
    with db() as connection:
        connection.execute(
            """
            INSERT INTO federated_reconciliation_state(
                peer_source,needs_reconciliation,last_connected_at,last_error
            ) VALUES (?,1,CURRENT_TIMESTAMP,'')
            ON CONFLICT(peer_source) DO UPDATE SET
                last_connected_at=CURRENT_TIMESTAMP,
                last_error=''
            """,
            (peer,),
        )
    after = reconciliation_state(peer)
    after["reconnected"] = bool(
        before.get("needs_reconciliation") and before.get("last_disconnect_at")
    )
    return after


def _existing_link(
    authority_source: str,
    dataset: str,
    authority_key: str,
    observed_source: str,
) -> dict[str, Any] | None:
    with db() as connection:
        row = connection.execute(
            """
            SELECT record_hash,tombstoned
            FROM federated_record_links
            WHERE authority_source=? AND dataset=? AND authority_key=? AND observed_source=?
            LIMIT 1
            """,
            (authority_source, dataset, authority_key, observed_source),
        ).fetchone()
    return dict(row) if row else None


def reconcile_snapshot(
    snapshot: dict[str, Any],
    *,
    observed_source: str = "homeserver",
    trigger_reason: str = "exchange",
) -> dict[str, Any]:
    datasets = snapshot.get("datasets")
    if not isinstance(datasets, dict):
        raise FederatedDataError("Federated snapshot datasets are missing.")
    authority = validate_source(str(snapshot.get("authoritative_source") or "vp3_cloud"))
    observed = validate_source(observed_source)
    mode = _text(snapshot.get("snapshot_mode") or "filtered", 20).lower()
    if mode not in {"full", "filtered"}:
        raise FederatedDataError("Federated snapshot mode must be full or filtered.")
    revision = _text(snapshot.get("revision"), 128)
    run_id = uuid.uuid4().hex
    trigger = _text(trigger_reason, 120) or "exchange"

    with db() as connection:
        connection.execute(
            """
            INSERT INTO federated_reconciliation_runs(
                id,peer_source,observed_source,snapshot_revision,snapshot_mode,
                trigger_reason,status
            ) VALUES (?,?,?,?,?,?,'running')
            """,
            (run_id, authority, observed, revision, mode, trigger),
        )

    totals = {
        "created": 0,
        "updated": 0,
        "restored": 0,
        "unchanged": 0,
        "tombstoned": 0,
        "conflicts": 0,
    }
    dataset_summary: dict[str, dict[str, int]] = {}
    normalized_datasets: dict[str, list[dict[str, Any]]] = {}

    try:
        # Validate the entire snapshot before mutating mirror state. A malformed
        # or mixed-authority full snapshot must fail atomically from the
        # perspective of reconciliation semantics.
        for dataset in DATASETS:
            rows = datasets.get(dataset)
            if not isinstance(rows, list):
                continue
            seen_keys: set[str] = set()
            normalized_rows: list[dict[str, Any]] = []
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    continue
                normalized = normalize_envelope(
                    row,
                    default_source=authority,
                    dataset=dataset,
                    index=index,
                )
                if normalized["authority_source"] != authority:
                    totals["conflicts"] += 1
                    raise FederatedDataError(
                        "Full reconciliation snapshot mixed native authorities."
                    )
                key = str(normalized["authority_key"])
                if key in seen_keys:
                    totals["conflicts"] += 1
                    raise FederatedDataError(
                        "Full reconciliation snapshot contains duplicate authority keys."
                    )
                seen_keys.add(key)
                normalized_rows.append(normalized)
            normalized_datasets[dataset] = normalized_rows

        for dataset, normalized_rows in normalized_datasets.items():
            summary = {
                "created": 0,
                "updated": 0,
                "restored": 0,
                "unchanged": 0,
                "tombstoned": 0,
                "conflicts": 0,
            }
            seen_keys = {str(row["authority_key"]) for row in normalized_rows}

            for normalized in normalized_rows:
                key = str(normalized["authority_key"])
                before = _existing_link(authority, dataset, key, observed)
                observe(normalized, observed_source=observed)
                if before is None:
                    bucket = "created"
                elif bool(before.get("tombstoned")):
                    bucket = "restored"
                elif str(before.get("record_hash") or "") != str(normalized["record_revision"]):
                    bucket = "updated"
                else:
                    bucket = "unchanged"
                summary[bucket] += 1
                totals[bucket] += 1

            if mode == "full":
                with db() as connection:
                    existing = connection.execute(
                        """
                        SELECT authority_key
                        FROM federated_record_links
                        WHERE authority_source=? AND dataset=? AND observed_source=?
                          AND tombstoned=0
                        """,
                        (authority, dataset, observed),
                    ).fetchall()
                for existing_row in existing:
                    key = _text(existing_row["authority_key"], MAX_KEY_CHARS)
                    if key and key not in seen_keys:
                        mark_tombstone(
                            authority,
                            dataset,
                            key,
                            observed_source=observed,
                        )
                        summary["tombstoned"] += 1
                        totals["tombstoned"] += 1

            dataset_summary[dataset] = summary
            update_cursor(
                authority,
                dataset,
                revision=revision,
                success=True,
            )

        with db() as connection:
            connection.execute(
                """
                UPDATE federated_reconciliation_runs
                SET status='completed',
                    created_count=?,updated_count=?,restored_count=?,unchanged_count=?,
                    tombstoned_count=?,conflict_count=?,dataset_summary_json=?,
                    completed_at=CURRENT_TIMESTAMP,error=''
                WHERE id=?
                """,
                (
                    totals["created"],
                    totals["updated"],
                    totals["restored"],
                    totals["unchanged"],
                    totals["tombstoned"],
                    totals["conflicts"],
                    json.dumps(dataset_summary, separators=(",", ":")),
                    run_id,
                ),
            )
            if mode == "full":
                connection.execute(
                    """
                    INSERT INTO federated_reconciliation_state(
                        peer_source,needs_reconciliation,last_reconciled_at,
                        last_snapshot_revision,last_run_id,last_error
                    ) VALUES (?,0,CURRENT_TIMESTAMP,?,?,'')
                    ON CONFLICT(peer_source) DO UPDATE SET
                        needs_reconciliation=0,
                        last_reconciled_at=CURRENT_TIMESTAMP,
                        last_snapshot_revision=excluded.last_snapshot_revision,
                        last_run_id=excluded.last_run_id,
                        last_error=''
                    """,
                    (authority, revision, run_id),
                )
        return {
            "version": FEDERATED_DATA_VERSION,
            "run_id": run_id,
            "peer_source": authority,
            "observed_source": observed,
            "snapshot_mode": mode,
            "snapshot_revision": revision,
            "status": "completed",
            **totals,
            "datasets": dataset_summary,
        }
    except Exception as exc:
        with db() as connection:
            connection.execute(
                """
                UPDATE federated_reconciliation_runs
                SET status='failed',conflict_count=?,dataset_summary_json=?,
                    error=?,completed_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    totals["conflicts"],
                    json.dumps(dataset_summary, separators=(",", ":")),
                    _text(str(exc), 500),
                    run_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO federated_reconciliation_state(
                    peer_source,needs_reconciliation,last_run_id,last_error
                ) VALUES (?,1,?,?)
                ON CONFLICT(peer_source) DO UPDATE SET
                    needs_reconciliation=1,
                    last_run_id=excluded.last_run_id,
                    last_error=excluded.last_error
                """,
                (authority, run_id, _text(str(exc), 500)),
            )
        raise


def recent_reconciliation_runs(limit: int = 20) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 100))
    with db() as connection:
        rows = connection.execute(
            """
            SELECT id,peer_source,observed_source,snapshot_revision,snapshot_mode,
                   trigger_reason,status,created_count,updated_count,restored_count,
                   unchanged_count,tombstoned_count,conflict_count,
                   dataset_summary_json,error,started_at,completed_at
            FROM federated_reconciliation_runs
            ORDER BY started_at DESC,id DESC LIMIT ?
            """,
            (bounded,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for raw in rows:
        item = dict(raw)
        try:
            item["datasets"] = json.loads(item.pop("dataset_summary_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            item["datasets"] = {}
            item.pop("dataset_summary_json", None)
        out.append(item)
    return out


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
        "reconciliation": {
            "vp3_cloud": reconciliation_state("vp3_cloud"),
            "recent_runs": recent_reconciliation_runs(10),
            "absence_tombstones_require_full_snapshot": True,
        },
    }



def mark_tombstone(
    authority_source: str,
    dataset: str,
    authority_key: Any,
    *,
    observed_source: str,
) -> None:
    source = validate_source(authority_source)
    name = validate_dataset(dataset)
    observed = validate_source(observed_source)
    key = _text(authority_key, MAX_KEY_CHARS)
    canonical = canonical_id(source, name, key)
    with db() as connection:
        connection.execute(
            """
            INSERT INTO federated_record_links(
                authority_source,dataset,authority_key,canonical_id,observed_source,
                record_hash,source_updated_at,tombstoned,last_seen_at
            ) VALUES (?,?,?,?,?,'',NULL,1,CURRENT_TIMESTAMP)
            ON CONFLICT(authority_source,dataset,authority_key,observed_source) DO UPDATE SET
                canonical_id=excluded.canonical_id,
                record_hash='',
                source_updated_at=NULL,
                tombstoned=1,
                last_seen_at=CURRENT_TIMESTAMP
            """,
            (source,name,key,canonical,observed),
        )


def resolve_authority_key(
    canonical_id_value: str,
    *,
    authority_source: str,
    dataset: str,
    observed_source: str = "homeserver",
    include_tombstoned: bool = False,
) -> str | None:
    canonical = _text(canonical_id_value, 80)
    if not canonical.startswith("fd24_") or len(canonical) != 45:
        return None
    source = validate_source(authority_source)
    name = validate_dataset(dataset)
    observed = validate_source(observed_source)
    with db() as connection:
        row = connection.execute(
            """
            SELECT authority_key,tombstoned
            FROM federated_record_links
            WHERE canonical_id=? AND authority_source=? AND dataset=? AND observed_source=?
            ORDER BY last_seen_at DESC LIMIT 1
            """,
            (canonical, source, name, observed),
        ).fetchone()
    if row is None:
        return None
    if bool(row["tombstoned"]) and not include_tombstoned:
        return None
    key = _text(row["authority_key"], MAX_KEY_CHARS)
    if not key or canonical_id(source, name, key) != canonical:
        return None
    return key
