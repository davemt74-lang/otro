from __future__ import annotations

import json
import time
from typing import Any

from ..database import db
from .contacts import list_contacts
from .knowledge import list_knowledge
from .tasks import TaskError, create_task, list_notifications, list_tasks


TOOL_EXECUTE_PERMISSION = "tools.execute"

TOOL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "contacts.search": {
        "key": "contacts.search",
        "name": "Search Contacts",
        "description": "Search private local contacts and relationship context.",
        "mode": "read",
        "required_permissions": ["contacts.read"],
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "maxLength": 240},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    "knowledge.search": {
        "key": "knowledge.search",
        "name": "Search Knowledge",
        "description": "Search the private SQLite knowledge index and return bounded local excerpts.",
        "mode": "read",
        "required_permissions": ["knowledge.search"],
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "maxLength": 240},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    "memory.list": {
        "key": "memory.list",
        "name": "Read Memory",
        "description": "Read bounded durable memory records from the primary local agent.",
        "mode": "read",
        "required_permissions": ["memory.read"],
        "input_schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}},
            "additionalProperties": False,
        },
    },
    "memory.write": {
        "key": "memory.write",
        "name": "Write Memory",
        "description": "Create one durable memory record for the primary local agent.",
        "mode": "write",
        "required_permissions": ["memory.write"],
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "maxLength": 50000},
                "memory_key": {"type": ["string", "null"], "maxLength": 160},
                "importance": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["content"],
            "additionalProperties": False,
        },
    },
    "notifications.list": {
        "key": "notifications.list",
        "name": "Read Notifications",
        "description": "Read the local HomeServer notification inbox without modifying it.",
        "mode": "read",
        "required_permissions": ["notifications.read"],
        "input_schema": {
            "type": "object",
            "properties": {
                "unread_only": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "additionalProperties": False,
        },
    },
    "tasks.list": {
        "key": "tasks.list",
        "name": "Read Tasks",
        "description": "Read bounded local tasks, due dates, reminders and contact links.",
        "mode": "read",
        "required_permissions": ["tasks.read"],
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": ["string", "null"], "enum": ["pending", "in_progress", "completed", "cancelled", None]},
                "query": {"type": "string", "maxLength": 240},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "additionalProperties": False,
        },
    },
    "tasks.create": {
        "key": "tasks.create",
        "name": "Create Task",
        "description": "Create one durable local task or reminder.",
        "mode": "write",
        "required_permissions": ["tasks.write"],
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "maxLength": 240},
                "description": {"type": "string", "maxLength": 20000},
                "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
                "due_at": {"type": ["string", "null"]},
                "remind_at": {"type": ["string", "null"]},
                "recurrence": {"type": "string", "enum": ["none", "daily", "weekly", "monthly"]},
                "recurrence_interval": {"type": "integer", "minimum": 1, "maximum": 365},
                "contact_id": {"type": ["integer", "null"], "minimum": 1},
            },
            "required": ["title"],
            "additionalProperties": False,
        },
    },
}

SKILL_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "local.research",
        "name": "Local Research",
        "description": "Search private knowledge and read durable memory without leaving HomeServer.",
        "tools": ["knowledge.search", "memory.list"],
    },
    {
        "key": "relationship.context",
        "name": "Relationship Context",
        "description": "Search private contacts and relationship notes through an explicit read capability.",
        "tools": ["contacts.search"],
    },
    {
        "key": "memory.manager",
        "name": "Memory Manager",
        "description": "Read and create durable agent memory through explicit local capabilities.",
        "tools": ["memory.list", "memory.write"],
    },
    {
        "key": "task.manager",
        "name": "Task & Reminder Manager",
        "description": "Read tasks and notifications and create durable reminders through explicit capabilities.",
        "tools": ["tasks.list", "notifications.list", "tasks.create"],
    },
)


class ToolError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _tool_definition(tool_key: str) -> dict[str, Any]:
    tool = TOOL_DEFINITIONS.get(tool_key.strip())
    if tool is None:
        raise ToolError("Tool not found.", 404)
    return tool


def _policy_map() -> dict[str, bool]:
    with db() as connection:
        rows = connection.execute("SELECT tool_key, enabled FROM tool_policies").fetchall()
    return {row["tool_key"]: bool(row["enabled"]) for row in rows}


def _missing_permissions(tool: dict[str, Any], granted_permissions: set[str], owner: bool) -> list[str]:
    if owner:
        return []
    required = {TOOL_EXECUTE_PERMISSION, *tool["required_permissions"]}
    return sorted(required - granted_permissions)


def list_tools(granted_permissions: set[str] | None = None, *, owner: bool = False) -> list[dict[str, Any]]:
    granted = set(granted_permissions or set())
    policies = _policy_map()
    result: list[dict[str, Any]] = []
    for key in sorted(TOOL_DEFINITIONS):
        tool = TOOL_DEFINITIONS[key]
        enabled = policies.get(key, True)
        missing = _missing_permissions(tool, granted, owner)
        result.append({**tool, "enabled": enabled, "available": enabled and not missing, "missing_permissions": missing})
    return result


def list_skills(granted_permissions: set[str] | None = None, *, owner: bool = False) -> list[dict[str, Any]]:
    tool_items = {item["key"]: item for item in list_tools(granted_permissions, owner=owner)}
    skills: list[dict[str, Any]] = []
    for skill in SKILL_DEFINITIONS:
        required: set[str] = set()
        available = True
        for tool_key in skill["tools"]:
            item = tool_items[tool_key]
            required.update(item["required_permissions"])
            if not owner:
                required.add(TOOL_EXECUTE_PERMISSION)
            available = available and bool(item["available"])
        skills.append({**skill, "required_permissions": sorted(required), "available": available})
    return skills


def set_tool_enabled(tool_key: str, enabled: bool) -> dict[str, Any]:
    tool = _tool_definition(tool_key)
    with db() as connection:
        connection.execute(
            """
            INSERT INTO tool_policies(tool_key, enabled)
            VALUES (?, ?)
            ON CONFLICT(tool_key) DO UPDATE SET enabled=excluded.enabled, updated_at=CURRENT_TIMESTAMP
            """,
            (tool["key"], 1 if enabled else 0),
        )
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES ('owner', 'control-center', 'tool.policy', 'tool', ?, ?)
            """,
            (tool["key"], json.dumps({"enabled": bool(enabled)}, separators=(",", ":"))),
        )
    return next(item for item in list_tools(owner=True) if item["key"] == tool["key"])


def _safe_numeric(value: Any, default: int | float | None = None) -> int | float | None:
    if value is None:
        return default
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _safe_argument_metadata(tool_key: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if tool_key in {"contacts.search", "knowledge.search", "tasks.list"}:
        query = str(arguments.get("query") or "")
        return {"query_length": len(query), "limit": _safe_numeric(arguments.get("limit"), 8)}
    if tool_key == "notifications.list":
        return {"unread_only": bool(arguments.get("unread_only", False)), "limit": _safe_numeric(arguments.get("limit"), 20)}
    if tool_key == "memory.list":
        return {"limit": _safe_numeric(arguments.get("limit"), 20)}
    if tool_key == "memory.write":
        content = str(arguments.get("content") or "")
        key = arguments.get("memory_key")
        return {
            "content_length": len(content),
            "memory_key_length": len(str(key)) if key is not None else 0,
            "importance": _safe_numeric(arguments.get("importance"), 0.5),
        }
    if tool_key == "tasks.create":
        return {
            "title_length": len(str(arguments.get("title") or "")),
            "description_length": len(str(arguments.get("description") or "")),
            "has_due_at": bool(arguments.get("due_at")),
            "has_remind_at": bool(arguments.get("remind_at")),
            "recurrence": str(arguments.get("recurrence") or "none")[:20],
            "contact_id": _safe_numeric(arguments.get("contact_id")),
        }
    return {"argument_count": len(arguments)}


def _record_run(*, tool_key: str, source_app_key: str, actor_type: str, status: str,
                required_permissions: list[str], arguments_meta: dict[str, Any],
                result_meta: dict[str, Any] | None = None, duration_ms: int | None = None,
                error: str | None = None) -> int:
    with db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO tool_runs(
                tool_key, source_app_key, actor_type, status, required_permissions_json,
                arguments_meta_json, result_meta_json, duration_ms, error, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                tool_key, source_app_key, actor_type, status,
                json.dumps(required_permissions, separators=(",", ":")),
                json.dumps(arguments_meta, separators=(",", ":")),
                json.dumps(result_meta or {}, separators=(",", ":")),
                duration_ms, (error or "")[:1000] or None,
            ),
        )
        run_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES (?, ?, ?, 'tool', ?, ?)
            """,
            (actor_type, source_app_key, f"tool.{status}", tool_key,
             json.dumps({"run_id": run_id, "tool": tool_key}, separators=(",", ":"))),
        )
    return run_id


def _bounded_int(value: Any, default: int, minimum: int, maximum: int, label: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ToolError(f"{label} must be an integer.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ToolError(f"{label} must be an integer.") from exc
    if parsed < minimum or parsed > maximum:
        raise ToolError(f"{label} must be between {minimum} and {maximum}.")
    return parsed


def _contacts_search(arguments: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    unknown = set(arguments) - {"query", "limit"}
    if unknown:
        raise ToolError(f"Unsupported contacts.search argument: {sorted(unknown)[0]}")
    query = str(arguments.get("query") or "").strip()
    if not query:
        raise ToolError("contacts.search requires a query.")
    if len(query) > 240:
        raise ToolError("contacts.search query exceeds 240 characters.")
    limit = _bounded_int(arguments.get("limit"), 8, 1, 20, "limit")
    rows = list_contacts(query, limit=limit)
    items = [{"id": row["id"], "display_name": row["display_name"], "organization": row.get("organization"), "email": row.get("email"), "phone": row.get("phone"), "relationship": row.get("relationship"), "notes": str(row.get("notes") or "")[:1600]} for row in rows]
    return {"items": items, "count": len(items)}, {"count": len(items)}


def _knowledge_search(arguments: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    unknown = set(arguments) - {"query", "limit"}
    if unknown:
        raise ToolError(f"Unsupported knowledge.search argument: {sorted(unknown)[0]}")
    query = str(arguments.get("query") or "").strip()
    if not query:
        raise ToolError("knowledge.search requires a query.")
    if len(query) > 240:
        raise ToolError("knowledge.search query exceeds 240 characters.")
    limit = _bounded_int(arguments.get("limit"), 8, 1, 20, "limit")
    rows = list_knowledge(query, limit=limit)
    items: list[dict[str, Any]] = []
    for row in rows:
        excerpt = str(row.get("snippet") or row.get("content") or "").strip()[:1600]
        items.append({"id": row["id"], "title": row.get("title"), "kind": row.get("kind"), "source_path": row.get("source_path"), "excerpt": excerpt})
    return {"items": items, "count": len(items)}, {"count": len(items)}


def _memory_list(arguments: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    unknown = set(arguments) - {"limit"}
    if unknown:
        raise ToolError(f"Unsupported memory.list argument: {sorted(unknown)[0]}")
    limit = _bounded_int(arguments.get("limit"), 20, 1, 50, "limit")
    with db() as connection:
        rows = connection.execute(
            "SELECT id, agent_id, memory_key, content, importance, created_at, updated_at FROM agent_memory ORDER BY importance DESC, updated_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    items = [dict(row) for row in rows]
    return {"items": items, "count": len(items)}, {"count": len(items)}


def _memory_write(arguments: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    unknown = set(arguments) - {"content", "memory_key", "importance"}
    if unknown:
        raise ToolError(f"Unsupported memory.write argument: {sorted(unknown)[0]}")
    content = str(arguments.get("content") or "").strip()
    if not content:
        raise ToolError("memory.write requires content.")
    if len(content) > 50000:
        raise ToolError("memory.write content exceeds 50,000 characters.")
    memory_key_raw = arguments.get("memory_key")
    memory_key = str(memory_key_raw).strip() if memory_key_raw is not None else None
    if memory_key == "":
        memory_key = None
    if memory_key is not None and len(memory_key) > 160:
        raise ToolError("memory.write memory_key exceeds 160 characters.")
    try:
        importance = float(arguments.get("importance", 0.5))
    except (TypeError, ValueError) as exc:
        raise ToolError("memory.write importance must be a number.") from exc
    if importance < 0 or importance > 1:
        raise ToolError("memory.write importance must be between 0 and 1.")
    with db() as connection:
        primary = connection.execute("SELECT id FROM agents WHERE is_primary=1 LIMIT 1").fetchone()
        agent_id = primary["id"] if primary else None
        cursor = connection.execute("INSERT INTO agent_memory(agent_id, memory_key, content, importance) VALUES (?, ?, ?, ?)", (agent_id, memory_key, content, importance))
        memory_id = int(cursor.lastrowid)
    return {"created": True, "id": memory_id}, {"created": True, "id": memory_id}


def _tasks_list(arguments: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    unknown = set(arguments) - {"status", "query", "limit"}
    if unknown:
        raise ToolError(f"Unsupported tasks.list argument: {sorted(unknown)[0]}")
    status = arguments.get("status")
    status = str(status).strip() if status not in (None, "") else None
    query = str(arguments.get("query") or "").strip()
    limit = _bounded_int(arguments.get("limit"), 20, 1, 50, "limit")
    try:
        rows = list_tasks(status=status, q=query, limit=limit)
    except TaskError as exc:
        raise ToolError(str(exc), exc.status_code) from exc
    items = [{key: row.get(key) for key in ("id", "title", "description", "status", "priority", "due_at", "remind_at", "recurrence", "recurrence_interval", "contact_id", "contact_name", "created_at", "updated_at")} for row in rows]
    return {"items": items, "count": len(items)}, {"count": len(items)}


def _notifications_list(arguments: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    unknown = set(arguments) - {"unread_only", "limit"}
    if unknown:
        raise ToolError(f"Unsupported notifications.list argument: {sorted(unknown)[0]}")
    unread_raw = arguments.get("unread_only", False)
    if not isinstance(unread_raw, bool):
        raise ToolError("notifications.list unread_only must be boolean.")
    limit = _bounded_int(arguments.get("limit"), 20, 1, 50, "limit")
    rows = list_notifications(unread_only=unread_raw, include_dismissed=False, limit=limit)
    return {"items": rows, "count": len(rows)}, {"count": len(rows)}


def _tasks_create(arguments: dict[str, Any], source: str, created_by_type: str) -> tuple[dict[str, Any], dict[str, Any]]:
    source_key = source.removeprefix("app:") if source.startswith("app:") else (None if source == "owner" else source)
    try:
        task = create_task(arguments, source_app_key=source_key, created_by_type=created_by_type)
    except TaskError as exc:
        raise ToolError(str(exc), exc.status_code) from exc
    return {"created": True, "task": task}, {"created": True, "id": task["id"]}


def execute_tool(source_app_key: str, tool_key: str, arguments: dict[str, Any] | None,
                 granted_permissions: set[str] | None = None, *, owner: bool = False) -> dict[str, Any]:
    tool = _tool_definition(tool_key)
    source = source_app_key.strip() or ("owner" if owner else "app:unknown")
    actor_type = "owner" if owner else "app"
    granted = set(granted_permissions or set())
    required = [] if owner else sorted({TOOL_EXECUTE_PERMISSION, *tool["required_permissions"]})
    payload = dict(arguments or {})
    try:
        encoded = json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ToolError("Tool arguments must be JSON serializable.") from exc
    if len(encoded.encode("utf-8")) > 65536:
        raise ToolError("Tool arguments exceed the 64 KB limit.", 413)

    arguments_meta = _safe_argument_metadata(tool["key"], payload)
    policies = _policy_map()
    if not policies.get(tool["key"], True):
        run_id = _record_run(tool_key=tool["key"], source_app_key=source, actor_type=actor_type, status="denied", required_permissions=required, arguments_meta=arguments_meta, error="Tool is disabled by the HomeServer owner.")
        raise ToolError(f"Tool is disabled by the HomeServer owner. Run {run_id} was recorded.", 403)

    missing = _missing_permissions(tool, granted, owner)
    if missing:
        run_id = _record_run(tool_key=tool["key"], source_app_key=source, actor_type=actor_type, status="denied", required_permissions=required, arguments_meta=arguments_meta, error=f"Missing permissions: {', '.join(missing)}")
        raise ToolError(f"Missing tool permissions: {', '.join(missing)}. Run {run_id} was recorded.", 403)

    started = time.perf_counter()
    try:
        if tool["key"] == "contacts.search":
            result, result_meta = _contacts_search(payload)
        elif tool["key"] == "knowledge.search":
            result, result_meta = _knowledge_search(payload)
        elif tool["key"] == "memory.list":
            result, result_meta = _memory_list(payload)
        elif tool["key"] == "memory.write":
            result, result_meta = _memory_write(payload)
        elif tool["key"] == "tasks.list":
            result, result_meta = _tasks_list(payload)
        elif tool["key"] == "notifications.list":
            result, result_meta = _notifications_list(payload)
        elif tool["key"] == "tasks.create":
            task_creator = "agent" if owner and source.startswith("app:") else actor_type
            result, result_meta = _tasks_create(payload, source, task_creator)
        else:
            raise ToolError("Tool implementation is unavailable.", 503)
    except ToolError as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        run_id = _record_run(tool_key=tool["key"], source_app_key=source, actor_type=actor_type, status="failed", required_permissions=required, arguments_meta=arguments_meta, duration_ms=duration_ms, error=str(exc))
        raise ToolError(f"{exc} Run {run_id} was recorded.", exc.status_code) from exc
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        run_id = _record_run(tool_key=tool["key"], source_app_key=source, actor_type=actor_type, status="failed", required_permissions=required, arguments_meta=arguments_meta, duration_ms=duration_ms, error="Internal tool failure.")
        raise ToolError(f"Tool failed safely. Run {run_id} was recorded.", 500) from exc

    duration_ms = int((time.perf_counter() - started) * 1000)
    run_id = _record_run(tool_key=tool["key"], source_app_key=source, actor_type=actor_type, status="completed", required_permissions=required, arguments_meta=arguments_meta, result_meta=result_meta, duration_ms=duration_ms)
    return {"tool": tool["key"], "run_id": run_id, "status": "completed", "result": result}


def list_tool_runs(limit: int = 100) -> list[dict[str, Any]]:
    limit = max(1, min(500, int(limit)))
    with db() as connection:
        rows = connection.execute(
            """
            SELECT id, tool_key, source_app_key, actor_type, status, required_permissions_json,
                   arguments_meta_json, result_meta_json, duration_ms, error, created_at, completed_at
            FROM tool_runs ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        for source_key, target_key, fallback in (
            ("required_permissions_json", "required_permissions", []),
            ("arguments_meta_json", "arguments", {}),
            ("result_meta_json", "result", {}),
        ):
            raw = item.pop(source_key, None)
            try:
                item[target_key] = json.loads(raw or "null") or fallback
            except json.JSONDecodeError:
                item[target_key] = fallback
        result.append(item)
    return result
