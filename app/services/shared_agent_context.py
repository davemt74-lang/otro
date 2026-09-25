from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from ..database import db
from . import contacts, federated_data, knowledge, tasks

SHARED_AGENT_CONTEXT_VERSION = "2.2"
MAX_SNAPSHOT_BYTES = 196_608
MAX_RECORDS_PER_DATASET = 100
_DATASETS = ("memory", "knowledge", "contacts", "tasks", "notifications")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_@.+-]+")


class SharedAgentContextError(RuntimeError):
    pass


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, limit: int = 4000) -> str:
    return " ".join(str(value or "").split())[:limit]


def _mirror_id(source: str, dataset: str, key: str) -> int:
    digest = hashlib.sha256(f"{source}|{dataset}|{key}".encode("utf-8")).hexdigest()
    return (int(digest[:12], 16) % 2_000_000_000) + 1


def _sanitize_record(source: str, dataset: str, record: dict[str, Any], index: int) -> dict[str, Any]:
    item = federated_data.normalize_envelope(
        record,
        default_source=source,
        dataset=dataset,
        index=index,
    )
    return {
        "id": _mirror_id(item["authority_source"], dataset, item["authority_key"]),
        "key": item["authority_key"],
        "title": item["title"],
        "content": item["content"],
        "updated_at": item["updated_at"],
        "authoritative_source": item["authority_source"],
        "authority_source": item["authority_source"],
        "authority_key": item["authority_key"],
        "canonical_id": item["canonical_id"],
        "record_revision": item["record_revision"],
        "federation_version": item["federation_version"],
        "mirror_only": item["mirror_only"],
        "dataset": dataset,
    }


def _sanitize_snapshot(snapshot: dict[str, Any], source: str) -> dict[str, Any]:
    datasets = snapshot.get("datasets")
    if not isinstance(datasets, dict):
        raise SharedAgentContextError("Shared Agent snapshot datasets are missing.")
    clean: dict[str, list[dict[str, Any]]] = {}
    for dataset in _DATASETS:
        rows = datasets.get(dataset)
        if not isinstance(rows, list):
            rows = []
        clean[dataset] = [
            _sanitize_record(source, dataset, row, index)
            for index, row in enumerate(rows[:MAX_RECORDS_PER_DATASET])
            if isinstance(row, dict)
        ]
    revision = _text(snapshot.get("revision"), 128)
    if not revision:
        encoded = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        revision = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return {
        "version": SHARED_AGENT_CONTEXT_VERSION,
        "revision": revision,
        "generated_at": _text(snapshot.get("generated_at") or _iso_now(), 80),
        "authoritative_source": source,
        "federation_version": federated_data.FEDERATED_DATA_VERSION,
        "datasets": clean,
    }


def apply_cloud_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise SharedAgentContextError("Cloud Agent snapshot must be an object.")
    clean = _sanitize_snapshot(snapshot, "vp3_cloud")
    encoded = json.dumps(clean, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise SharedAgentContextError("Cloud Agent snapshot is too large.")
    federated_data.observe_snapshot(clean, observed_source="homeserver")
    for dataset in _DATASETS:
        federated_data.update_cursor("vp3_cloud", dataset, revision=clean["revision"], success=True)
    with db() as connection:
        connection.execute(
            """
            INSERT INTO shared_agent_snapshots(source,version,revision,snapshot_json,updated_at)
            VALUES ('vp3_cloud',?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(source) DO UPDATE SET
                version=excluded.version,
                revision=excluded.revision,
                snapshot_json=excluded.snapshot_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (SHARED_AGENT_CONTEXT_VERSION, clean["revision"], encoded),
        )
    return {
        "source": "vp3_cloud",
        "version": SHARED_AGENT_CONTEXT_VERSION,
        "revision": clean["revision"],
        "dataset_counts": {name: len(rows) for name, rows in clean["datasets"].items()},
    }


def cloud_snapshot() -> dict[str, Any] | None:
    with db() as connection:
        row = connection.execute(
            "SELECT snapshot_json,updated_at FROM shared_agent_snapshots WHERE source='vp3_cloud' LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row["snapshot_json"] or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    payload["mirror_updated_at"] = row["updated_at"]
    return payload


def _tokens(query: str) -> list[str]:
    out: list[str] = []
    for token in _TOKEN_RE.findall(str(query or "").lower()):
        token = token.strip(".-_+")
        if len(token) < 3 or token in out:
            continue
        out.append(token)
    return out[:20]


def cloud_candidates(dataset: str, query: str, limit: int = 8) -> list[dict[str, Any]]:
    if dataset not in _DATASETS:
        return []
    snapshot = cloud_snapshot()
    if not snapshot:
        return []
    rows = snapshot.get("datasets", {}).get(dataset, [])
    if not isinstance(rows, list):
        return []
    tokens = _tokens(query)
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        haystack = f"{row.get('title') or ''} {row.get('content') or ''}".lower()
        hits = sum(1 for token in tokens if token in haystack)
        if tokens and hits == 0:
            continue
        scored.append((hits, -index, row))
    if not scored and not tokens:
        return [row for row in rows[:limit] if isinstance(row, dict)]
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in scored[: max(1, min(int(limit), 20))]]


def _local_memory(query: str, limit: int = 40) -> list[dict[str, Any]]:
    tokens = _tokens(query)
    with db() as connection:
        rows = connection.execute(
            """
            SELECT id,memory_key,content,importance,created_at,updated_at
            FROM agent_memory
            ORDER BY importance DESC,updated_at DESC,id DESC
            LIMIT 160
            """
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        haystack = f"{item.get('memory_key') or ''} {item.get('content') or ''}".lower()
        if tokens and not any(token in haystack for token in tokens):
            continue
        out.append(
            _sanitize_record(
                "homeserver",
                "memory",
                {
                    "id": item["id"],
                    "title": item.get("memory_key") or "Memory",
                    "content": item.get("content") or "",
                    "updated_at": item.get("updated_at"),
                },
                len(out),
            )
        )
        if len(out) >= limit:
            break
    return out


def _fit_datasets(datasets: dict[str, list[dict[str, Any]]], max_bytes: int = 170_000) -> dict[str, list[dict[str, Any]]]:
    order = ("notifications", "tasks", "contacts", "knowledge", "memory")
    while True:
        encoded = json.dumps(datasets, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) <= max_bytes:
            return datasets
        removed = False
        for dataset in order:
            rows = datasets.get(dataset) or []
            if len(rows) > 5:
                rows.pop()
                removed = True
                break
        if not removed:
            return datasets


def local_snapshot(query: str = "") -> dict[str, Any]:
    text = _text(query, 240)
    memory_rows = _local_memory(text)
    knowledge_rows = [
        _sanitize_record(
            "homeserver",
            "knowledge",
            {
                "id": row.get("id"),
                "title": row.get("title") or "Knowledge",
                "content": row.get("snippet") or row.get("content") or "",
                "updated_at": row.get("updated_at"),
            },
            index,
        )
        for index, row in enumerate(knowledge.list_knowledge(text, limit=40)[:40])
        if isinstance(row, dict)
    ]
    contact_rows = [
        _sanitize_record(
            "homeserver",
            "contacts",
            {
                "id": row.get("id"),
                "title": row.get("display_name") or "Contact",
                "content": "; ".join(
                    value
                    for value in (
                        _text(row.get("organization"), 240),
                        _text(row.get("relationship"), 160),
                        _text(row.get("email"), 320),
                        _text(row.get("phone"), 80),
                        _text(row.get("notes"), 1200),
                    )
                    if value
                ),
                "updated_at": row.get("updated_at"),
            },
            index,
        )
        for index, row in enumerate(contacts.list_contacts(text, 60)[:60])
        if isinstance(row, dict)
    ]
    task_rows = [
        _sanitize_record(
            "homeserver",
            "tasks",
            {
                "id": row.get("id"),
                "title": row.get("title") or "Task",
                "content": " · ".join(
                    value
                    for value in (
                        _text(row.get("description"), 1600),
                        f"status: {_text(row.get('status'), 40)}" if row.get("status") else "",
                        f"priority: {_text(row.get('priority'), 40)}" if row.get("priority") else "",
                        f"due: {_text(row.get('due_at'), 80)}" if row.get("due_at") else "",
                        f"contact: {_text(row.get('contact_name'), 240)}" if row.get("contact_name") else "",
                    )
                    if value
                ),
                "updated_at": row.get("updated_at"),
            },
            index,
        )
        for index, row in enumerate(tasks.list_tasks(q=text, limit=50)[:50])
        if isinstance(row, dict)
    ]
    notification_rows = [
        _sanitize_record(
            "homeserver",
            "notifications",
            {
                "id": row.get("id"),
                "title": row.get("title") or "Notification",
                "content": " · ".join(
                    value
                    for value in (
                        _text(row.get("body"), 1200),
                        f"level: {_text(row.get('level'), 40)}" if row.get("level") else "",
                    )
                    if value
                ),
                "updated_at": row.get("created_at"),
            },
            index,
        )
        for index, row in enumerate(tasks.list_notifications(unread_only=False, include_dismissed=False, limit=50)[:50])
        if isinstance(row, dict)
    ]
    datasets = _fit_datasets({
        "memory": memory_rows,
        "knowledge": knowledge_rows,
        "contacts": contact_rows,
        "tasks": task_rows,
        "notifications": notification_rows,
    })
    canonical = json.dumps(datasets, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    snapshot = {
        "version": SHARED_AGENT_CONTEXT_VERSION,
        "revision": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "generated_at": _iso_now(),
        "authoritative_source": "homeserver",
        "federation_version": federated_data.FEDERATED_DATA_VERSION,
        "datasets": datasets,
    }
    federated_data.observe_snapshot(snapshot, observed_source="homeserver")
    return snapshot


def exchange(cloud: dict[str, Any], query: str = "") -> dict[str, Any]:
    applied = apply_cloud_snapshot(cloud)
    return {
        "ok": True,
        "version": SHARED_AGENT_CONTEXT_VERSION,
        "cloud_mirror": applied,
        "homeserver_snapshot": local_snapshot(query),
    }
