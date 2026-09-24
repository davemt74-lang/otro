from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Callable

from ..database import db

WORK_CONTINUITY_VERSION = "2.3"
STALE_RUNNING_SECONDS = 300
_STABLE_KEY = re.compile(r"^[a-f0-9]{64}$")
_TERMINAL = {"completed", "failed", "cancelled"}


class WorkContinuityError(RuntimeError):
    pass


class WorkContinuityPending(WorkContinuityError):
    pass


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any, limit: int = 1000) -> str:
    return " ".join(str(value or "").split())[:limit]


def _parse_db_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _running_is_stale(item: dict[str, Any]) -> bool:
    heartbeat = _parse_db_time(item.get("heartbeat_at") or item.get("updated_at"))
    if heartbeat is None:
        return True
    return (datetime.now(timezone.utc) - heartbeat).total_seconds() >= STALE_RUNNING_SECONDS


def _require_key(value: Any) -> str:
    key = str(value or "").strip().lower()
    if not _STABLE_KEY.fullmatch(key):
        raise WorkContinuityError("A valid 64-character continuity key is required.")
    return key


def _activity(action: str, key: str, metadata: dict[str, Any] | None = None) -> None:
    try:
        with db() as connection:
            connection.execute(
                """
                INSERT INTO activity_log(actor_type,actor_key,action,resource_type,resource_key,metadata_json)
                VALUES ('app','vp3',?,'cloud_work',?,?)
                """,
                (str(action)[:120], str(key)[:190], json.dumps(metadata or {}, separators=(",", ":"))),
            )
    except Exception:
        return


def _decode(value: Any) -> dict[str, Any]:
    try:
        decoded = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def get(continuity_key: str) -> dict[str, Any] | None:
    key = _require_key(continuity_key)
    stale_recovered = False
    with db() as connection:
        row = connection.execute(
            "SELECT * FROM cloud_work_continuity WHERE continuity_key=? LIMIT 1",
            (key,),
        ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["result"] = _decode(item.pop("result_json", None))
    item["version"] = WORK_CONTINUITY_VERSION
    return item


def status(continuity_key: str) -> dict[str, Any]:
    item = get(continuity_key)
    if item is None:
        return {
            "version": WORK_CONTINUITY_VERSION,
            "continuity_key": _require_key(continuity_key),
            "status": "missing",
            "terminal": False,
        }
    item["terminal"] = str(item.get("status") or "") in _TERMINAL
    return item


def cancel(continuity_key: str) -> dict[str, Any]:
    key = _require_key(continuity_key)
    with db() as connection:
        row = connection.execute(
            "SELECT status FROM cloud_work_continuity WHERE continuity_key=? LIMIT 1",
            (key,),
        ).fetchone()
        if row is None:
            return {"version": WORK_CONTINUITY_VERSION, "continuity_key": key, "status": "missing"}
        current = str(row["status"] or "")
        if current in _TERMINAL:
            return status(key)
        next_status = "cancel_requested" if current == "running" else "cancelled"
        connection.execute(
            """
            UPDATE cloud_work_continuity
            SET status=?, updated_at=CURRENT_TIMESTAMP,
                completed_at=CASE WHEN ?='cancelled' THEN CURRENT_TIMESTAMP ELSE completed_at END
            WHERE continuity_key=?
            """,
            (next_status, next_status, key),
        )
    item = status(key)
    _activity("cloud_work.cancel_requested" if item.get("status") == "cancel_requested" else "cloud_work.cancelled", key, {"status": item.get("status")})
    return item


def list_recent(limit: int = 100) -> list[dict[str, Any]]:
    limit = max(1, min(250, int(limit)))
    with db() as connection:
        rows = connection.execute(
            "SELECT * FROM cloud_work_continuity ORDER BY updated_at DESC, created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["result"] = _decode(item.pop("result_json", None))
        item["version"] = WORK_CONTINUITY_VERSION
        item["terminal"] = str(item.get("status") or "") in _TERMINAL
        items.append(item)
    return items


def _claim(
    continuity_key: str,
    cloud_run_id: int,
    cloud_action_id: int,
    source_app_key: str,
    conversation_id: str | None,
    operation: str,
) -> tuple[str, dict[str, Any] | None]:
    key = _require_key(continuity_key)
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM cloud_work_continuity WHERE continuity_key=? LIMIT 1",
            (key,),
        ).fetchone()
        if row is not None:
            item = dict(row)
            current = str(item.get("status") or "")
            if current == "completed":
                connection.commit()
                item["result"] = _decode(item.get("result_json"))
                _activity("cloud_work.replayed", key, {"cloud_run_id": item.get("cloud_run_id"), "cloud_action_id": item.get("cloud_action_id")})
                return "replay", item
            if current == "cancelled" or current == "cancel_requested":
                connection.commit()
                raise WorkContinuityError("This HomeServer work item was cancelled.")
            if current == "running" and not _running_is_stale(item):
                connection.commit()
                _activity("cloud_work.pending", key, {"cloud_run_id": item.get("cloud_run_id"), "cloud_action_id": item.get("cloud_action_id")})
                return "pending", item
            if current == "running":
                connection.execute(
                    """
                    UPDATE cloud_work_continuity
                    SET status='failed', error_class='stale_running_recovered',
                        summary='Recovered an interrupted HomeServer execution after restart or lost worker.',
                        updated_at=CURRENT_TIMESTAMP
                    WHERE continuity_key=?
                    """,
                    (key,),
                )
                stale_recovered = True
            connection.execute(
                """
                UPDATE cloud_work_continuity
                SET status='running', attempt_count=attempt_count+1,
                    error_class='', summary='', started_at=COALESCE(started_at,CURRENT_TIMESTAMP),
                    heartbeat_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                WHERE continuity_key=?
                """,
                (key,),
            )
        else:
            connection.execute(
                """
                INSERT INTO cloud_work_continuity (
                    continuity_key,cloud_run_id,cloud_action_id,source_app_key,
                    conversation_id,operation,status,attempt_count,started_at,heartbeat_at
                ) VALUES (?,?,?,?,?,?,'running',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
                """,
                (
                    key,
                    max(0, int(cloud_run_id)),
                    max(0, int(cloud_action_id)),
                    _clean(source_app_key, 120) or "vp3",
                    _clean(conversation_id, 128) or None,
                    _clean(operation, 80) or "agent.chat",
                ),
            )
        connection.commit()
    if stale_recovered:
        _activity("cloud_work.stale_recovered", key, {"cloud_run_id": cloud_run_id, "cloud_action_id": cloud_action_id})
    _activity("cloud_work.claimed", key, {"cloud_run_id": cloud_run_id, "cloud_action_id": cloud_action_id, "operation": operation})
    return "execute", None


def _finish(continuity_key: str, result: dict[str, Any], summary: str = "") -> None:
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    with db() as connection:
        connection.execute(
            """
            UPDATE cloud_work_continuity
            SET status='completed', result_json=?, summary=?, error_class='',
                heartbeat_at=CURRENT_TIMESTAMP, completed_at=CURRENT_TIMESTAMP,
                updated_at=CURRENT_TIMESTAMP
            WHERE continuity_key=?
            """,
            (encoded, _clean(summary, 1000), _require_key(continuity_key)),
        )
    _activity("cloud_work.completed", _require_key(continuity_key), {"summary": _clean(summary, 300)})


def _fail(continuity_key: str, error_class: str, summary: str) -> None:
    with db() as connection:
        connection.execute(
            """
            UPDATE cloud_work_continuity
            SET status='failed', error_class=?, summary=?,
                heartbeat_at=CURRENT_TIMESTAMP, completed_at=CURRENT_TIMESTAMP,
                updated_at=CURRENT_TIMESTAMP
            WHERE continuity_key=?
            """,
            (_clean(error_class, 120), _clean(summary, 1000), _require_key(continuity_key)),
        )
    _activity("cloud_work.failed", _require_key(continuity_key), {"error_class": _clean(error_class, 120), "summary": _clean(summary, 300)})


def execute(
    continuity: dict[str, Any],
    operation: str,
    runner: Callable[[], dict[str, Any]],
    *,
    source_app_key: str = "vp3",
) -> dict[str, Any]:
    if not isinstance(continuity, dict):
        raise WorkContinuityError("Continuity metadata must be an object.")
    key = _require_key(continuity.get("key"))
    run_id = max(0, int(continuity.get("cloud_run_id") or 0))
    action_id = max(0, int(continuity.get("cloud_action_id") or 0))
    if run_id < 1 or action_id < 1:
        raise WorkContinuityError("Continuity metadata requires Cloud run and action IDs.")
    conversation_id = _clean(continuity.get("conversation_id"), 128) or None

    mode, existing = _claim(
        key, run_id, action_id, source_app_key, conversation_id, operation
    )
    if mode == "replay":
        result = dict(existing.get("result") or {}) if existing else {}
        result["continuity"] = {
            "version": WORK_CONTINUITY_VERSION,
            "key": key,
            "status": "completed",
            "replayed": True,
        }
        return result
    if mode == "pending":
        return {
            "ok": True,
            "reply": "",
            "continuity": {
                "version": WORK_CONTINUITY_VERSION,
                "key": key,
                "status": "running",
                "replayed": False,
            },
        }

    try:
        result = runner()
        if not isinstance(result, dict):
            raise WorkContinuityError("HomeServer work returned an invalid result.")
        _finish(key, result, _clean(result.get("reply") or "HomeServer work completed.", 1000))
        result = dict(result)
        result["continuity"] = {
            "version": WORK_CONTINUITY_VERSION,
            "key": key,
            "status": "completed",
            "replayed": False,
        }
        return result
    except Exception as exc:
        _fail(key, exc.__class__.__name__, str(exc))
        raise
