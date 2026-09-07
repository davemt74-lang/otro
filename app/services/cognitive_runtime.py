from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from ..database import db
from . import plugins

_EVENT_TYPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_ENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_MEMORY_TYPES = {"working", "episodic", "semantic", "preference", "relationship", "procedural"}
_PROCESS_LOCK = threading.Lock()


class CognitiveError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json_object(value: Any, *, maximum: int = 65536) -> tuple[dict[str, Any], str]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise CognitiveError("Event payload must be an object.")
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > maximum:
        raise CognitiveError("Event payload exceeds 64 KB.")
    return value, encoded


def _bounded(value: Any, maximum: int, label: str, *, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise CognitiveError(f"{label} is required.")
    if len(text) > maximum:
        raise CognitiveError(f"{label} exceeds {maximum} characters.")
    return text


def _safe_event_type(value: Any) -> str:
    event_type = _bounded(value, 160, "event_type", required=True)
    if not _EVENT_TYPE.fullmatch(event_type):
        raise CognitiveError("event_type contains unsupported characters.")
    return event_type


def _safe_entity(value: Any, label: str) -> str | None:
    text = _bounded(value, 160, label)
    if not text:
        return None
    if not _ENTITY.fullmatch(text):
        raise CognitiveError(f"{label} contains unsupported characters.")
    return text


def _memory_type(value: Any, default: str = "episodic") -> str:
    selected = str(value or default).strip().lower()
    return selected if selected in _MEMORY_TYPES else default


def _importance(value: Any, default: float = 0.5) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0.0, min(1.0, parsed))


def _event_dict(row: Any, *, include_payload: bool = True) -> dict[str, Any]:
    item = dict(row)
    item["memory_candidate"] = bool(item.get("memory_candidate"))
    try:
        payload = json.loads(item.pop("payload_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        payload = {}
    if include_payload:
        item["payload"] = payload if isinstance(payload, dict) else {}
    return item


def _schedule_job(connection, event_row_id: int, job_type: str) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO cognition_jobs(event_row_id, job_type) VALUES (?, ?)",
        (event_row_id, job_type),
    )


def emit_event(
    *,
    source_app_key: str,
    event_id: str | None,
    event_type: str,
    summary: str,
    source_kind: str = "app",
    plugin_key: str | None = None,
    entity_type: str | None = None,
    entity_key: str | None = None,
    correlation_id: str | None = None,
    conversation_id: str | None = None,
    importance: float = 0.5,
    privacy_scope: str = "private",
    payload: dict[str, Any] | None = None,
    occurred_at: str | None = None,
    memory_candidate: bool = False,
    memory_type: str | None = None,
    memory_key: str | None = None,
    allow_memory_candidate: bool = False,
) -> dict[str, Any]:
    source = _bounded(source_app_key, 160, "source_app_key", required=True)
    kind = str(source_kind or "app").strip().lower()
    if kind not in {"owner", "app", "plugin", "system"}:
        raise CognitiveError("Invalid cognitive event source kind.")
    safe_event_type = _safe_event_type(event_type)
    safe_summary = _bounded(summary, 2000, "summary", required=True)
    safe_event_id = _bounded(event_id or uuid.uuid4().hex, 160, "event_id", required=True)
    safe_plugin = _bounded(plugin_key, 80, "plugin_key") or None
    safe_entity_type = _safe_entity(entity_type, "entity_type")
    safe_entity_key = _bounded(entity_key, 240, "entity_key") or None
    safe_correlation = _bounded(correlation_id, 160, "correlation_id") or None
    safe_conversation = _bounded(conversation_id, 64, "conversation_id") or None
    scope = str(privacy_scope or "private").strip().lower()
    if scope not in {"private", "shared"}:
        raise CognitiveError("privacy_scope must be private or shared.")
    payload_obj, payload_json = _json_object(payload)
    occurred = _bounded(occurred_at, 80, "occurred_at") or _utc_now()
    try:
        datetime.fromisoformat(occurred.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CognitiveError("occurred_at must be an ISO-8601 timestamp.") from exc
    candidate = bool(memory_candidate and allow_memory_candidate)
    candidate_type = _memory_type(memory_type) if candidate else None
    candidate_key = _bounded(memory_key, 160, "memory_key") or None if candidate else None

    with db() as connection:
        existing = connection.execute(
            "SELECT * FROM cognitive_events WHERE source_app_key=? AND event_id=? LIMIT 1",
            (source, safe_event_id),
        ).fetchone()
        if existing is not None:
            return {"event": _event_dict(existing), "duplicate": True}
        cursor = connection.execute(
            """
            INSERT INTO cognitive_events(
                event_id, source_app_key, source_kind, plugin_key, event_type,
                entity_type, entity_key, correlation_id, conversation_id, summary,
                importance, privacy_scope, memory_candidate, memory_type, memory_key,
                payload_json, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                safe_event_id, source, kind, safe_plugin, safe_event_type,
                safe_entity_type, safe_entity_key, safe_correlation, safe_conversation,
                safe_summary, _importance(importance), scope, int(candidate),
                candidate_type, candidate_key, payload_json, occurred,
            ),
        )
        row_id = int(cursor.lastrowid)
        _schedule_job(connection, row_id, "plugin_dispatch")
        if _importance(importance) >= 0.25:
            _schedule_job(connection, row_id, "awareness")
        if candidate:
            _schedule_job(connection, row_id, "memory_candidate")
        row = connection.execute("SELECT * FROM cognitive_events WHERE id=?", (row_id,)).fetchone()
    return {"event": _event_dict(row), "duplicate": False}


def list_events(
    *,
    limit: int = 100,
    source_app_key: str | None = None,
    event_type: str | None = None,
    include_payload: bool = False,
) -> list[dict[str, Any]]:
    bounded = max(1, min(500, int(limit)))
    clauses: list[str] = []
    params: list[Any] = []
    if source_app_key:
        clauses.append("source_app_key=?")
        params.append(source_app_key)
    if event_type:
        clauses.append("event_type=?")
        params.append(event_type)
    query = "SELECT * FROM cognitive_events"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY occurred_at DESC, id DESC LIMIT ?"
    params.append(bounded)
    with db() as connection:
        rows = connection.execute(query, tuple(params)).fetchall()
    return [_event_dict(row, include_payload=include_payload) for row in rows]


def _activity_importance(action: str) -> float:
    value = action.lower()
    if "failed" in value or "denied" in value:
        return 0.75
    if value in {"app.paired", "app.status", "app.permission"}:
        return 0.65
    if value.startswith("memory."):
        return 0.55
    if value.startswith("knowledge."):
        return 0.4
    if value.startswith("tool.") or value.startswith("task"):
        return 0.45
    if value == "agent.chat":
        return 0.15
    return 0.25


def mirror_activity_log(limit: int = 200) -> dict[str, int]:
    bounded = max(1, min(1000, int(limit)))
    with db() as connection:
        cursor = connection.execute(
            "SELECT cursor_value FROM cognition_cursors WHERE cursor_key='activity_log_id'"
        ).fetchone()
        last_id = int(cursor["cursor_value"] if cursor else 0)
        rows = connection.execute(
            """
            SELECT id, actor_type, actor_key, action, resource_type, resource_key,
                   metadata_json, created_at
            FROM activity_log WHERE id>? ORDER BY id ASC LIMIT ?
            """,
            (last_id, bounded),
        ).fetchall()
    imported = 0
    latest = last_id
    for row in rows:
        latest = max(latest, int(row["id"]))
        actor_type = str(row["actor_type"] or "system")
        source_kind = actor_type if actor_type in {"owner", "app", "system"} else "system"
        source = str(row["actor_key"] or actor_type or "homeserver")
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        try:
            emit_event(
                source_app_key=source,
                event_id=f"activity:{int(row['id'])}",
                event_type=f"homeserver.{str(row['action']).replace(' ', '_')}",
                summary=f"HomeServer activity: {row['action']}",
                source_kind=source_kind,
                entity_type=str(row["resource_type"] or "") or None,
                entity_key=str(row["resource_key"] or "") or None,
                importance=_activity_importance(str(row["action"])),
                payload={"activity_id": int(row["id"]), "metadata": metadata},
                occurred_at=str(row["created_at"]),
                allow_memory_candidate=False,
            )
            imported += 1
        except CognitiveError:
            continue
    if latest != last_id:
        with db() as connection:
            connection.execute(
                """
                INSERT INTO cognition_cursors(cursor_key, cursor_value) VALUES ('activity_log_id', ?)
                ON CONFLICT(cursor_key) DO UPDATE SET cursor_value=excluded.cursor_value, updated_at=CURRENT_TIMESTAMP
                """,
                (str(latest),),
            )
    return {"imported": imported, "cursor": latest}


def _fingerprint(event: dict[str, Any]) -> str:
    identity = "|".join(
        [
            str(event.get("event_type") or "").lower(),
            str(event.get("entity_type") or "").lower(),
            str(event.get("entity_key") or "").lower(),
        ]
    )
    if not event.get("entity_type") and not event.get("entity_key"):
        identity += "|" + str(event.get("summary") or "").strip().lower()[:160]
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _awareness_title(event: dict[str, Any]) -> str:
    entity = str(event.get("entity_key") or "").strip()
    event_type = str(event.get("event_type") or "event").replace("_", " ").replace(".", " · ")
    return (f"{entity} · {event_type}" if entity else event_type)[:240]


def _maybe_cross_app_candidate(connection, awareness_id: int) -> None:
    row = connection.execute(
        "SELECT * FROM awareness_items WHERE id=? LIMIT 1", (awareness_id,)
    ).fetchone()
    if row is None or int(row["occurrence_count"] or 0) < 3:
        return
    try:
        source_apps = json.loads(row["source_apps_json"] or "[]")
    except json.JSONDecodeError:
        source_apps = []
    if not isinstance(source_apps, list) or len(set(map(str, source_apps))) < 2:
        return
    agent = connection.execute("SELECT id FROM agents WHERE is_primary=1 LIMIT 1").fetchone()
    connection.execute(
        """
        INSERT OR IGNORE INTO memory_candidates(
            source_awareness_id, agent_id, memory_type, memory_key, content,
            confidence, importance, entity_type, entity_key, source_app_key
        ) VALUES (?, ?, 'episodic', ?, ?, ?, ?, ?, ?, 'multi-app')
        """,
        (
            awareness_id,
            int(agent["id"]) if agent else None,
            f"Cross-app awareness: {row['event_type']}",
            str(row["summary"]),
            min(0.95, 0.65 + (0.05 * int(row["occurrence_count"]))),
            float(row["importance"]),
            row["entity_type"],
            row["entity_key"],
        ),
    )


def _process_awareness(event: dict[str, Any]) -> dict[str, Any]:
    fingerprint = _fingerprint(event)
    source = str(event["source_app_key"])
    with db() as connection:
        existing = connection.execute(
            "SELECT * FROM awareness_items WHERE fingerprint=? LIMIT 1", (fingerprint,)
        ).fetchone()
        if existing is None:
            cursor = connection.execute(
                """
                INSERT INTO awareness_items(
                    fingerprint, event_type, title, summary, entity_type, entity_key,
                    importance, source_apps_json, first_event_id, last_event_id,
                    first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fingerprint, event["event_type"], _awareness_title(event), event["summary"],
                    event.get("entity_type"), event.get("entity_key"), float(event["importance"]),
                    json.dumps([source], separators=(",", ":")), int(event["id"]), int(event["id"]),
                    event["occurred_at"], event["occurred_at"],
                ),
            )
            awareness_id = int(cursor.lastrowid)
        else:
            try:
                sources = json.loads(existing["source_apps_json"] or "[]")
            except json.JSONDecodeError:
                sources = []
            if not isinstance(sources, list):
                sources = []
            if source not in sources:
                sources.append(source)
            awareness_id = int(existing["id"])
            connection.execute(
                """
                UPDATE awareness_items SET
                    summary=?, title=?, importance=MAX(importance, ?), status='open',
                    occurrence_count=occurrence_count+1, source_apps_json=?, last_event_id=?,
                    last_seen_at=?, resolved_at=NULL
                WHERE id=?
                """,
                (
                    event["summary"], _awareness_title(event), float(event["importance"]),
                    json.dumps(sources[:50], separators=(",", ":")), int(event["id"]),
                    event["occurred_at"], awareness_id,
                ),
            )
        _maybe_cross_app_candidate(connection, awareness_id)
        result = connection.execute("SELECT * FROM awareness_items WHERE id=?", (awareness_id,)).fetchone()
    return _awareness_dict(result)


def _create_event_memory_candidate(event: dict[str, Any]) -> dict[str, Any]:
    with db() as connection:
        agent = connection.execute("SELECT id FROM agents WHERE is_primary=1 LIMIT 1").fetchone()
        connection.execute(
            """
            INSERT OR IGNORE INTO memory_candidates(
                source_event_id, agent_id, memory_type, memory_key, content,
                confidence, importance, entity_type, entity_key, source_app_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(event["id"]), int(agent["id"]) if agent else None,
                _memory_type(event.get("memory_type")), event.get("memory_key"), event["summary"],
                min(0.95, 0.55 + (float(event["importance"]) * 0.4)), float(event["importance"]),
                event.get("entity_type"), event.get("entity_key"), event["source_app_key"],
            ),
        )
        row = connection.execute(
            "SELECT * FROM memory_candidates WHERE source_event_id=? LIMIT 1", (int(event["id"]),)
        ).fetchone()
    return _candidate_dict(row)


def _awareness_dict(row: Any) -> dict[str, Any]:
    item = dict(row)
    try:
        item["source_apps"] = json.loads(item.pop("source_apps_json") or "[]")
    except json.JSONDecodeError:
        item["source_apps"] = []
    try:
        item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
    except json.JSONDecodeError:
        item["metadata"] = {}
    return item


def _candidate_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def list_awareness(*, limit: int = 100, status: str | None = "open") -> list[dict[str, Any]]:
    bounded = max(1, min(500, int(limit)))
    params: list[Any] = []
    query = "SELECT * FROM awareness_items"
    if status:
        state = str(status).strip().lower()
        if state not in {"open", "resolved", "dismissed"}:
            raise CognitiveError("Invalid awareness status filter.")
        query += " WHERE status=?"
        params.append(state)
    query += " ORDER BY importance DESC, last_seen_at DESC, id DESC LIMIT ?"
    params.append(bounded)
    with db() as connection:
        rows = connection.execute(query, tuple(params)).fetchall()
    return [_awareness_dict(row) for row in rows]


def update_awareness_status(awareness_id: int, status: str) -> dict[str, Any]:
    state = str(status).strip().lower()
    if state not in {"open", "resolved", "dismissed"}:
        raise CognitiveError("Awareness status must be open, resolved or dismissed.")
    with db() as connection:
        cursor = connection.execute(
            """
            UPDATE awareness_items SET status=?, resolved_at=CASE WHEN ?='open' THEN NULL ELSE CURRENT_TIMESTAMP END
            WHERE id=?
            """,
            (state, state, int(awareness_id)),
        )
        if cursor.rowcount == 0:
            raise CognitiveError("Awareness item not found.", 404)
        row = connection.execute("SELECT * FROM awareness_items WHERE id=?", (int(awareness_id),)).fetchone()
    return _awareness_dict(row)


def list_memory_candidates(*, limit: int = 100, status: str | None = "pending") -> list[dict[str, Any]]:
    bounded = max(1, min(500, int(limit)))
    params: list[Any] = []
    query = "SELECT * FROM memory_candidates"
    if status:
        state = str(status).strip().lower()
        if state not in {"pending", "accepted", "rejected"}:
            raise CognitiveError("Invalid memory candidate status filter.")
        query += " WHERE status=?"
        params.append(state)
    query += " ORDER BY importance DESC, created_at DESC, id DESC LIMIT ?"
    params.append(bounded)
    with db() as connection:
        rows = connection.execute(query, tuple(params)).fetchall()
    return [_candidate_dict(row) for row in rows]


def decide_memory_candidate(candidate_id: int, decision: str) -> dict[str, Any]:
    state = str(decision).strip().lower()
    if state not in {"accepted", "rejected"}:
        raise CognitiveError("Memory candidate decision must be accepted or rejected.")
    with db() as connection:
        candidate = connection.execute(
            "SELECT * FROM memory_candidates WHERE id=? LIMIT 1", (int(candidate_id),)
        ).fetchone()
        if candidate is None:
            raise CognitiveError("Memory candidate not found.", 404)
        if candidate["status"] != "pending":
            return _candidate_dict(candidate)
        memory_id: int | None = None
        if state == "accepted":
            existing = None
            if candidate["entity_type"] and candidate["entity_key"]:
                existing = connection.execute(
                    """
                    SELECT id FROM agent_memory
                    WHERE agent_id IS ? AND memory_type=? AND entity_type=? AND entity_key=?
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    (
                        candidate["agent_id"], candidate["memory_type"],
                        candidate["entity_type"], candidate["entity_key"],
                    ),
                ).fetchone()
            if existing is not None:
                memory_id = int(existing["id"])
                connection.execute(
                    """
                    UPDATE agent_memory SET content=?, memory_key=COALESCE(?, memory_key),
                        importance=MAX(importance, ?), confidence=MAX(confidence, ?),
                        reinforcement_count=reinforcement_count+1, source_app_key=?, source_event_id=?,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (
                        candidate["content"], candidate["memory_key"], candidate["importance"],
                        candidate["confidence"], candidate["source_app_key"], candidate["source_event_id"], memory_id,
                    ),
                )
            else:
                cursor = connection.execute(
                    """
                    INSERT INTO agent_memory(
                        agent_id, memory_key, content, importance, memory_type, source_app_key,
                        source_event_id, confidence, entity_type, entity_key, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate["agent_id"], candidate["memory_key"], candidate["content"],
                        candidate["importance"], candidate["memory_type"], candidate["source_app_key"],
                        candidate["source_event_id"], candidate["confidence"], candidate["entity_type"],
                        candidate["entity_key"], json.dumps({"memory_candidate_id": int(candidate_id)}, separators=(",", ":")),
                    ),
                )
                memory_id = int(cursor.lastrowid)
        connection.execute(
            """
            UPDATE memory_candidates SET status=?, decided_at=CURRENT_TIMESTAMP, consolidated_memory_id=? WHERE id=?
            """,
            (state, memory_id, int(candidate_id)),
        )
        result = connection.execute("SELECT * FROM memory_candidates WHERE id=?", (int(candidate_id),)).fetchone()
    return _candidate_dict(result)


def _claim_jobs(limit: int) -> list[dict[str, Any]]:
    bounded = max(1, min(100, int(limit)))
    with db() as connection:
        rows = connection.execute(
            """
            SELECT id FROM cognition_jobs
            WHERE status='pending' AND available_at<=CURRENT_TIMESTAMP
            ORDER BY id ASC LIMIT ?
            """,
            (bounded,),
        ).fetchall()
        ids = [int(row["id"]) for row in rows]
        for job_id in ids:
            connection.execute(
                "UPDATE cognition_jobs SET status='running', attempts=attempts+1, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'",
                (job_id,),
            )
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        claimed = connection.execute(
            f"SELECT * FROM cognition_jobs WHERE id IN ({placeholders}) AND status='running' ORDER BY id",
            tuple(ids),
        ).fetchall()
    return [dict(row) for row in claimed]


def process_pending_jobs(limit: int = 50) -> dict[str, int]:
    if not _PROCESS_LOCK.acquire(blocking=False):
        return {"processed": 0, "failed": 0, "busy": 1}
    processed = failed = 0
    try:
        for job in _claim_jobs(limit):
            with db() as connection:
                row = connection.execute("SELECT * FROM cognitive_events WHERE id=?", (job["event_row_id"],)).fetchone()
            if row is None:
                result: dict[str, Any] = {}
                error = "Cognitive event no longer exists."
                ok = False
            else:
                event = _event_dict(row)
                try:
                    if job["job_type"] == "awareness":
                        result = {"awareness": _process_awareness(event)}
                    elif job["job_type"] == "memory_candidate":
                        result = {"memory_candidate": _create_event_memory_candidate(event)}
                    elif job["job_type"] == "plugin_dispatch":
                        result = plugins.dispatch_event(event)
                    else:
                        raise CognitiveError("Unknown cognition job type.")
                    error = None
                    ok = True
                except Exception as exc:
                    result = {}
                    error = str(exc)[:1000]
                    ok = False
            with db() as connection:
                connection.execute(
                    """
                    UPDATE cognition_jobs SET status=?, result_json=?, error=?, updated_at=CURRENT_TIMESTAMP,
                        completed_at=CURRENT_TIMESTAMP WHERE id=?
                    """,
                    (
                        "completed" if ok else "failed",
                        json.dumps(result, ensure_ascii=False, separators=(",", ":"))[:50000],
                        error,
                        int(job["id"]),
                    ),
                )
            if ok:
                processed += 1
            else:
                failed += 1
        return {"processed": processed, "failed": failed, "busy": 0}
    finally:
        _PROCESS_LOCK.release()


def overview() -> dict[str, Any]:
    with db() as connection:
        counts = {
            "events": int(connection.execute("SELECT COUNT(*) FROM cognitive_events").fetchone()[0]),
            "pending_jobs": int(connection.execute("SELECT COUNT(*) FROM cognition_jobs WHERE status='pending'").fetchone()[0]),
            "open_awareness": int(connection.execute("SELECT COUNT(*) FROM awareness_items WHERE status='open'").fetchone()[0]),
            "memory_candidates": int(connection.execute("SELECT COUNT(*) FROM memory_candidates WHERE status='pending'").fetchone()[0]),
            "plugins": int(connection.execute("SELECT COUNT(*) FROM plugins WHERE status='active'").fetchone()[0]),
        }
        cursor = connection.execute("SELECT cursor_value FROM cognition_cursors WHERE cursor_key='activity_log_id'").fetchone()
    return {**counts, "activity_cursor": int(cursor["cursor_value"] if cursor else 0)}


def tick() -> dict[str, Any]:
    mirrored = mirror_activity_log()
    jobs = process_pending_jobs()
    return {"mirrored": mirrored, "jobs": jobs}


class CognitiveScheduler:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="homeserver-cognitive-runtime", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=8)
        self._thread = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _run(self) -> None:
        if self._stop.wait(1.5):
            return
        while not self._stop.is_set():
            try:
                tick()
            except Exception:
                pass
            self._stop.wait(2.5)


scheduler = CognitiveScheduler()
