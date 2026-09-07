from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from ..database import db
from . import tools


class ApprovalError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_numeric(value: Any, default: float = 0.5) -> float | None:
    if value is None:
        return default
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _safe_raw_meta(arguments: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(arguments or {})
    content = str(payload.get("content") or "")
    key = payload.get("memory_key")
    return {
        "content_length": len(content),
        "memory_key_length": len(str(key)) if key is not None else 0,
        "importance": _safe_numeric(payload.get("importance")),
        "argument_count": len(payload),
    }


def _safe_task_meta(arguments: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(arguments or {})
    return {
        "title_length": len(str(payload.get("title") or "")),
        "description_length": len(str(payload.get("description") or "")),
        "has_due_at": bool(payload.get("due_at")),
        "has_remind_at": bool(payload.get("remind_at")),
        "recurrence": str(payload.get("recurrence") or "none")[:20],
        "contact_id": _safe_numeric(payload.get("contact_id"), None),
        "argument_count": len(payload),
    }


def _validate_memory_write(arguments: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(arguments or {})
    unknown = set(payload) - {"content", "memory_key", "importance"}
    if unknown:
        raise ApprovalError(f"Unsupported memory.write proposal argument: {sorted(unknown)[0]}")
    content = str(payload.get("content") or "").strip()
    if not content:
        raise ApprovalError("memory.write proposal requires content.")
    if len(content) > 50000:
        raise ApprovalError("memory.write proposal content exceeds 50,000 characters.")
    memory_key_raw = payload.get("memory_key")
    memory_key = str(memory_key_raw).strip() if memory_key_raw is not None else None
    if memory_key == "":
        memory_key = None
    if memory_key is not None and len(memory_key) > 160:
        raise ApprovalError("memory.write proposal memory_key exceeds 160 characters.")
    importance_raw = payload.get("importance", 0.5)
    if isinstance(importance_raw, bool):
        raise ApprovalError("memory.write proposal importance must be a number.")
    try:
        importance = float(importance_raw)
    except (TypeError, ValueError) as exc:
        raise ApprovalError("memory.write proposal importance must be a number.") from exc
    if importance < 0 or importance > 1:
        raise ApprovalError("memory.write proposal importance must be between 0 and 1.")
    return {"content": content, "memory_key": memory_key, "importance": importance}


def _validate_task_create(arguments: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(arguments or {})
    allowed = {"title", "description", "priority", "due_at", "remind_at", "recurrence", "recurrence_interval", "contact_id"}
    unknown = set(payload) - allowed
    if unknown:
        raise ApprovalError(f"Unsupported tasks.create proposal argument: {sorted(unknown)[0]}")
    title = str(payload.get("title") or "").strip()
    if not title:
        raise ApprovalError("tasks.create proposal requires title.")
    if len(title) > 240:
        raise ApprovalError("tasks.create proposal title exceeds 240 characters.")
    description = str(payload.get("description") or "").strip()
    if len(description) > 20000:
        raise ApprovalError("tasks.create proposal description exceeds 20,000 characters.")
    priority = str(payload.get("priority") or "normal").strip().lower()
    if priority not in {"low", "normal", "high", "urgent"}:
        raise ApprovalError("tasks.create proposal priority is invalid.")
    recurrence = str(payload.get("recurrence") or "none").strip().lower()
    if recurrence not in {"none", "daily", "weekly", "monthly"}:
        raise ApprovalError("tasks.create proposal recurrence is invalid.")
    raw_interval = payload.get("recurrence_interval", 1)
    if isinstance(raw_interval, bool):
        raise ApprovalError("tasks.create proposal recurrence_interval must be an integer.")
    try:
        interval = int(raw_interval)
    except (TypeError, ValueError) as exc:
        raise ApprovalError("tasks.create proposal recurrence_interval must be an integer.") from exc
    if interval < 1 or interval > 365:
        raise ApprovalError("tasks.create proposal recurrence_interval must be between 1 and 365.")

    def normalize_time(key: str) -> str | None:
        value = payload.get(key)
        if value in (None, ""):
            return None
        raw = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ApprovalError(f"tasks.create proposal {key} must be ISO-8601.") from exc
        if parsed.tzinfo is None:
            raise ApprovalError(f"tasks.create proposal {key} must include a timezone offset.")
        return parsed.astimezone(timezone.utc).isoformat()

    due_at = normalize_time("due_at")
    remind_at = normalize_time("remind_at")
    if recurrence != "none" and not remind_at:
        raise ApprovalError("Recurring task proposals require remind_at.")
    contact_raw = payload.get("contact_id")
    contact_id = None
    if contact_raw not in (None, ""):
        if isinstance(contact_raw, bool):
            raise ApprovalError("tasks.create proposal contact_id must be a positive integer.")
        try:
            contact_id = int(contact_raw)
        except (TypeError, ValueError) as exc:
            raise ApprovalError("tasks.create proposal contact_id must be a positive integer.") from exc
        if contact_id <= 0:
            raise ApprovalError("tasks.create proposal contact_id must be a positive integer.")
    return {
        "title": title,
        "description": description,
        "priority": priority,
        "due_at": due_at,
        "remind_at": remind_at,
        "recurrence": recurrence,
        "recurrence_interval": interval,
        "contact_id": contact_id,
    }


def _record_failed_proposal(source: str, actor_type: str, tool_key: str, required: list[str], meta: dict[str, Any], error: str) -> int:
    request_tool_key = f"{tool_key}.request"
    with db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO tool_runs(
                tool_key, source_app_key, actor_type, status, required_permissions_json,
                arguments_meta_json, result_meta_json, error, completed_at
            ) VALUES (?, ?, ?, 'failed', ?, ?, '{}', ?, CURRENT_TIMESTAMP)
            """,
            (request_tool_key, source, actor_type, json.dumps(required, separators=(",", ":")), json.dumps(meta, separators=(",", ":")), error[:1000]),
        )
        run_id = int(cursor.lastrowid)
        connection.execute(
            "INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json) VALUES (?, ?, 'tool.failed', 'tool', ?, ?)",
            (actor_type, source, request_tool_key, json.dumps({"run_id": run_id}, separators=(",", ":"))),
        )
    return run_id


def _create_action_request(source: str, actor_type: str, action_key: str, normalized: dict[str, Any], arguments_meta: dict[str, Any], required: list[str]) -> dict[str, Any]:
    request_id = uuid.uuid4().hex
    expires_at = (_now() + timedelta(hours=24)).isoformat()
    request_tool_key = f"{action_key}.request"
    with db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO tool_runs(
                tool_key, source_app_key, actor_type, status, required_permissions_json,
                arguments_meta_json, result_meta_json, completed_at
            ) VALUES (?, ?, ?, 'completed', ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (request_tool_key, source, actor_type, json.dumps(required, separators=(",", ":")), json.dumps(arguments_meta, separators=(",", ":")), json.dumps({"request_id": request_id, "status": "pending"}, separators=(",", ":"))),
        )
        run_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO action_requests(
                id, action_key, source_app_key, actor_type, arguments_json,
                arguments_meta_json, request_tool_run_id, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (request_id, action_key, source, actor_type, json.dumps(normalized, ensure_ascii=False, separators=(",", ":")), json.dumps(arguments_meta, separators=(",", ":")), run_id, expires_at),
        )
        connection.execute(
            "INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json) VALUES (?, ?, 'action.requested', 'action_request', ?, ?)",
            (actor_type, source, request_id, json.dumps({"action": action_key, "tool_run_id": run_id}, separators=(",", ":"))),
        )
    return {
        "tool": request_tool_key,
        "run_id": run_id,
        "status": "completed",
        "result": {"request_id": request_id, "status": "pending", "action": action_key, "owner_approval_required": True, "expires_at": expires_at},
    }


def create_memory_write_request(source_app_key: str, arguments: dict[str, Any] | None, *, owner: bool = False) -> dict[str, Any]:
    source = source_app_key.strip() or ("owner" if owner else "app:unknown")
    actor_type = "owner" if owner else "app"
    raw_meta = _safe_raw_meta(arguments)
    required = [] if owner else ["memory.write", "tools.execute"]
    try:
        normalized = _validate_memory_write(arguments)
    except ApprovalError as exc:
        run_id = _record_failed_proposal(source, actor_type, "memory.write", required, raw_meta, str(exc))
        raise ApprovalError(f"{exc} Run {run_id} was recorded.", exc.status_code) from exc
    meta = {"content_length": len(normalized["content"]), "memory_key_length": len(normalized["memory_key"] or ""), "importance": normalized["importance"]}
    return _create_action_request(source, actor_type, "memory.write", normalized, meta, required)


def create_task_create_request(source_app_key: str, arguments: dict[str, Any] | None, *, owner: bool = False) -> dict[str, Any]:
    source = source_app_key.strip() or ("owner" if owner else "app:unknown")
    actor_type = "owner" if owner else "app"
    raw_meta = _safe_task_meta(arguments)
    required = [] if owner else ["tasks.write", "tools.execute"]
    try:
        normalized = _validate_task_create(arguments)
    except ApprovalError as exc:
        run_id = _record_failed_proposal(source, actor_type, "tasks.create", required, raw_meta, str(exc))
        raise ApprovalError(f"{exc} Run {run_id} was recorded.", exc.status_code) from exc
    meta = _safe_task_meta(normalized)
    return _create_action_request(source, actor_type, "tasks.create", normalized, meta, required)


def _decode_row(row, *, include_arguments: bool) -> dict[str, Any]:
    item = dict(row)
    raw_meta = item.pop("arguments_meta_json", None)
    try:
        item["arguments_meta"] = json.loads(raw_meta or "{}")
    except json.JSONDecodeError:
        item["arguments_meta"] = {}
    raw_arguments = item.pop("arguments_json", None)
    if include_arguments:
        try:
            item["arguments"] = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError:
            item["arguments"] = {}
    return item


def _expire_pending() -> None:
    now_iso = _now().isoformat()
    with db() as connection:
        connection.execute(
            "UPDATE action_requests SET status='expired', decided_at=CURRENT_TIMESTAMP, error='Approval request expired.' WHERE status='pending' AND expires_at <= ?",
            (now_iso,),
        )


def list_requests(status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    _expire_pending()
    safe_limit = max(1, min(500, int(limit)))
    params: list[Any] = []
    where = ""
    if status:
        if status not in {"pending", "executing", "executed", "denied", "failed", "expired"}:
            raise ApprovalError("Invalid action request status.")
        where = "WHERE status=?"
        params.append(status)
    params.append(safe_limit)
    with db() as connection:
        rows = connection.execute(
            f"""
            SELECT id, action_key, source_app_key, actor_type, status, arguments_json,
                   arguments_meta_json, request_tool_run_id, execution_tool_run_id,
                   error, created_at, expires_at, decided_at, executed_at
            FROM action_requests {where}
            ORDER BY created_at DESC LIMIT ?
            """,
            params,
        ).fetchall()
    return [_decode_row(row, include_arguments=True) for row in rows]


def get_request_for_source(request_id: str, source_app_key: str) -> dict[str, Any] | None:
    _expire_pending()
    with db() as connection:
        row = connection.execute(
            """
            SELECT id, action_key, source_app_key, actor_type, status, arguments_json,
                   arguments_meta_json, request_tool_run_id, execution_tool_run_id,
                   error, created_at, expires_at, decided_at, executed_at
            FROM action_requests WHERE id=? AND source_app_key=? LIMIT 1
            """,
            (request_id.strip(), source_app_key.strip()),
        ).fetchone()
    if row is None:
        return None
    item = _decode_row(row, include_arguments=False)
    item.pop("arguments_meta", None)
    return item


def _request_for_owner(request_id: str) -> dict[str, Any]:
    _expire_pending()
    with db() as connection:
        row = connection.execute("SELECT * FROM action_requests WHERE id=? LIMIT 1", (request_id.strip(),)).fetchone()
    if row is None:
        raise ApprovalError("Action request not found.", 404)
    return _decode_row(row, include_arguments=True)


def _extract_run_id(message: str) -> int | None:
    match = re.search(r"\bRun (\d+)\b", message)
    return int(match.group(1)) if match else None


def approve_request(request_id: str) -> dict[str, Any]:
    request = _request_for_owner(request_id)
    if request["status"] != "pending":
        raise ApprovalError(f"Action request is already {request['status']}.", 409)
    if request["action_key"] not in {"memory.write", "tasks.create"}:
        raise ApprovalError("Action type is not approved for local execution.", 403)
    with db() as connection:
        reserved = connection.execute(
            "UPDATE action_requests SET status='executing', decided_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'",
            (request["id"],),
        )
        if reserved.rowcount != 1:
            raise ApprovalError("Action request is no longer pending.", 409)

    try:
        execution = tools.execute_tool(request["source_app_key"], request["action_key"], request["arguments"], set(), owner=True)
    except tools.ToolError as exc:
        execution_run_id = _extract_run_id(str(exc))
        with db() as connection:
            connection.execute(
                "UPDATE action_requests SET status='failed', execution_tool_run_id=?, error=?, executed_at=CURRENT_TIMESTAMP WHERE id=? AND status='executing'",
                (execution_run_id, str(exc)[:1000], request["id"]),
            )
            connection.execute(
                "INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json) VALUES ('owner', 'control-center', 'action.failed', 'action_request', ?, ?)",
                (request["id"], json.dumps({"execution_tool_run_id": execution_run_id}, separators=(",", ":"))),
            )
        raise ApprovalError(f"Approved action could not execute: {exc}", exc.status_code) from exc

    with db() as connection:
        connection.execute(
            "UPDATE action_requests SET status='executed', execution_tool_run_id=?, error=NULL, executed_at=CURRENT_TIMESTAMP WHERE id=? AND status='executing'",
            (execution["run_id"], request["id"]),
        )
        connection.execute(
            "INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json) VALUES ('owner', 'control-center', 'action.approved', 'action_request', ?, ?)",
            (request["id"], json.dumps({"execution_tool_run_id": execution["run_id"]}, separators=(",", ":"))),
        )
    return _request_for_owner(request["id"])


def deny_request(request_id: str) -> dict[str, Any]:
    request = _request_for_owner(request_id)
    if request["status"] != "pending":
        raise ApprovalError(f"Action request is already {request['status']}.", 409)
    with db() as connection:
        updated = connection.execute(
            "UPDATE action_requests SET status='denied', decided_at=CURRENT_TIMESTAMP, error=NULL WHERE id=? AND status='pending'",
            (request["id"],),
        )
        if updated.rowcount != 1:
            raise ApprovalError("Action request is no longer pending.", 409)
        connection.execute(
            "INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json) VALUES ('owner', 'control-center', 'action.denied', 'action_request', ?, '{}')",
            (request["id"],),
        )
    return _request_for_owner(request["id"])
