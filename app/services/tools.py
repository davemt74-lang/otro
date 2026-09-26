from __future__ import annotations

import json
import time
from typing import Any

from ..database import db
from . import app_scopes, contacts, knowledge as knowledge_service, knowledge_collection_policy, local_files, room_device_automation, task_calendar_continuity as continuity
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
    "contacts.create": {
        "key": "contacts.create",
        "name": "Create HomeServer Contact",
        "description": "Propose creation of one HomeServer-native address-book contact.",
        "mode": "write",
        "required_permissions": ["contacts.write"],
        "input_schema": {
            "type": "object",
            "properties": {
                "display_name": {"type": ["string", "null"], "maxLength": 240},
                "first_name": {"type": ["string", "null"], "maxLength": 120},
                "last_name": {"type": ["string", "null"], "maxLength": 120},
                "organization": {"type": ["string", "null"], "maxLength": 240},
                "email": {"type": ["string", "null"], "maxLength": 320},
                "phone": {"type": ["string", "null"], "maxLength": 80},
                "relationship": {"type": ["string", "null"], "maxLength": 160},
                "notes": {"type": "string", "maxLength": 50000},
                "mutation_id": {"type": "string", "minLength": 8, "maxLength": 128},
            },
            "required": ["mutation_id"],
            "additionalProperties": False,
        },
    },
    "contacts.update": {
        "key": "contacts.update",
        "name": "Update HomeServer Contact",
        "description": "Propose changes to one HomeServer-native address-book contact by canonical federated identity.",
        "mode": "write",
        "required_permissions": ["contacts.write"],
        "input_schema": {
            "type": "object",
            "properties": {
                "canonical_id": {"type": "string", "pattern": "^fd24_[0-9a-f]{40}$", "maxLength": 45},
                "display_name": {"type": ["string", "null"], "maxLength": 240},
                "first_name": {"type": ["string", "null"], "maxLength": 120},
                "last_name": {"type": ["string", "null"], "maxLength": 120},
                "organization": {"type": ["string", "null"], "maxLength": 240},
                "email": {"type": ["string", "null"], "maxLength": 320},
                "phone": {"type": ["string", "null"], "maxLength": 80},
                "relationship": {"type": ["string", "null"], "maxLength": 160},
                "notes": {"type": "string", "maxLength": 50000},
                "mutation_id": {"type": "string", "minLength": 8, "maxLength": 128},
                "expected_revision": {"type": "string", "pattern": "^[0-9a-f]{64}$", "maxLength": 64},
            },
            "required": ["canonical_id", "mutation_id", "expected_revision"],
            "additionalProperties": False,
        },
    },
    "contacts.delete": {
        "key": "contacts.delete",
        "name": "Delete HomeServer Contact",
        "description": "Propose deletion of one HomeServer-native address-book contact by canonical federated identity.",
        "mode": "write",
        "required_permissions": ["contacts.write"],
        "input_schema": {
            "type": "object",
            "properties": {
                "canonical_id": {"type": "string", "pattern": "^fd24_[0-9a-f]{40}$", "maxLength": 45},
                "mutation_id": {"type": "string", "minLength": 8, "maxLength": 128},
                "expected_revision": {"type": "string", "pattern": "^[0-9a-f]{64}$", "maxLength": 64},
            },
            "required": ["canonical_id", "mutation_id", "expected_revision"],
            "additionalProperties": False,
        },
    },
    "files.list": {
        "key": "files.list",
        "name": "List Local Files",
        "description": "Discover bounded file metadata from owner-approved local Knowledge Sources within the app's collection scope.",
        "mode": "read",
        "required_permissions": ["files.read"],
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "maxLength": 240},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "additionalProperties": False,
        },
    },
    "files.read": {
        "key": "files.read",
        "name": "Read Local File",
        "description": "Read a bounded slice of indexed text using an opaque HomeServer file reference; arbitrary filesystem paths are never accepted.",
        "mode": "read",
        "required_permissions": ["files.read"],
        "input_schema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "pattern": "^hsf-[0-9]+-[0-9a-f]{16}$", "maxLength": 96},
                "offset": {"type": "integer", "minimum": 0, "maximum": 10000000},
                "max_chars": {"type": "integer", "minimum": 1, "maximum": 12000},
            },
            "required": ["ref"],
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
    "knowledge.create": {
        "key": "knowledge.create",
        "name": "Create HomeServer Knowledge",
        "description": "Propose creation of one direct HomeServer-native Knowledge item.",
        "mode": "write",
        "required_permissions": ["knowledge.write"],
        "input_schema": {
            "type": "object",
            "properties": {
                "mutation_id": {"type": "string", "minLength": 8, "maxLength": 128},
                "collection_key": {"type": "string", "maxLength": 64},
                "title": {"type": "string", "minLength": 1, "maxLength": 240},
                "content": {"type": "string", "minLength": 1, "maxLength": 250000},
                "kind": {"type": "string", "maxLength": 40},
            },
            "required": ["mutation_id", "title", "content"],
            "additionalProperties": False,
        },
    },
    "knowledge.update": {
        "key": "knowledge.update",
        "name": "Update HomeServer Knowledge",
        "description": "Propose changes to one direct HomeServer-native Knowledge item by canonical federated identity.",
        "mode": "write",
        "required_permissions": ["knowledge.write"],
        "input_schema": {
            "type": "object",
            "properties": {
                "canonical_id": {"type": "string", "pattern": "^fd24_[0-9a-f]{40}$", "maxLength": 45},
                "mutation_id": {"type": "string", "minLength": 8, "maxLength": 128},
                "expected_revision": {"type": "string", "pattern": "^[0-9a-f]{64}$", "maxLength": 64},
                "collection_key": {"type": "string", "maxLength": 64},
                "title": {"type": "string", "minLength": 1, "maxLength": 240},
                "content": {"type": "string", "minLength": 1, "maxLength": 250000},
                "kind": {"type": "string", "maxLength": 40},
            },
            "required": ["canonical_id", "mutation_id", "expected_revision"],
            "additionalProperties": False,
        },
    },
    "knowledge.delete": {
        "key": "knowledge.delete",
        "name": "Delete HomeServer Knowledge",
        "description": "Propose deletion of one direct HomeServer-native Knowledge item by canonical federated identity.",
        "mode": "write",
        "required_permissions": ["knowledge.write"],
        "input_schema": {
            "type": "object",
            "properties": {
                "canonical_id": {"type": "string", "pattern": "^fd24_[0-9a-f]{40}$", "maxLength": 45},
                "mutation_id": {"type": "string", "minLength": 8, "maxLength": 128},
                "expected_revision": {"type": "string", "pattern": "^[0-9a-f]{64}$", "maxLength": 64},
            },
            "required": ["canonical_id", "mutation_id", "expected_revision"],
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
    "devices.list": {
        "key": "devices.list",
        "name": "Read Rooms & Devices",
        "description": "Read bounded local room/device inventory and normalized state without executing physical actions.",
        "mode": "read",
        "required_permissions": ["devices.read"],
        "input_schema": {
            "type": "object",
            "properties": {
                "room_key": {"type": ["string", "null"], "maxLength": 80},
                "category": {"type": ["string", "null"], "maxLength": 40},
                "controllable_only": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100}
            },
            "additionalProperties": False
        }
    },
    "devices.command": {
        "key": "devices.command",
        "name": "Control Local Device",
        "description": "Execute one previously approved physical device command through a registered local provider driver.",
        "mode": "write",
        "required_permissions": ["devices.control"],
        "input_schema": {
            "type": "object",
            "properties": {
                "device_key": {"type": "string", "maxLength": 80},
                "command": {"type": "string", "maxLength": 40},
                "arguments": {"type": "object"}
            },
            "required": ["device_key", "command"],
            "additionalProperties": False
        }
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
                "mutation_id": {"type": "string", "minLength": 8, "maxLength": 128},
            },
            "required": ["title"],
            "additionalProperties": False,
        },
    },
    "tasks.update": {
        "key": "tasks.update",
        "name": "Update HomeServer Task",
        "description": "Propose changes to one HomeServer-native task by canonical federated identity.",
        "mode": "write",
        "required_permissions": ["tasks.write"],
        "input_schema": {
            "type": "object",
            "properties": {
                "canonical_id": {"type": "string", "pattern": "^fd24_[0-9a-f]{40}$", "maxLength": 45},
                "mutation_id": {"type": "string", "minLength": 8, "maxLength": 128},
                "expected_revision": {"type": "string", "pattern": "^[0-9a-f]{64}$", "maxLength": 64},
                "title": {"type": "string", "maxLength": 240},
                "description": {"type": "string", "maxLength": 20000},
                "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "cancelled"]},
                "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
                "due_at": {"type": ["string", "null"]},
                "remind_at": {"type": ["string", "null"]},
                "recurrence": {"type": "string", "enum": ["none", "daily", "weekly", "monthly"]},
                "recurrence_interval": {"type": "integer", "minimum": 1, "maximum": 365},
                "contact_id": {"type": ["integer", "null"], "minimum": 1},
            },
            "required": ["canonical_id", "mutation_id", "expected_revision"],
            "additionalProperties": False,
        },
    },
    "tasks.delete": {
        "key": "tasks.delete",
        "name": "Delete HomeServer Task",
        "description": "Propose deletion of one HomeServer-native task by canonical federated identity.",
        "mode": "write",
        "required_permissions": ["tasks.write"],
        "input_schema": {
            "type": "object",
            "properties": {
                "canonical_id": {"type": "string", "pattern": "^fd24_[0-9a-f]{40}$", "maxLength": 45},
                "mutation_id": {"type": "string", "minLength": 8, "maxLength": 128},
                "expected_revision": {"type": "string", "pattern": "^[0-9a-f]{64}$", "maxLength": 64},
            },
            "required": ["canonical_id", "mutation_id", "expected_revision"],
            "additionalProperties": False,
        },
    },
    "calendar.list": {
        "key": "calendar.list",
        "name": "Read HomeServer Calendar",
        "description": "Read bounded HomeServer-native calendar events.",
        "mode": "read",
        "required_permissions": ["events.read"],
        "input_schema": {
            "type": "object",
            "properties": {
                "from_at": {"type": ["string", "null"]},
                "to_at": {"type": ["string", "null"]},
                "query": {"type": "string", "maxLength": 240},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "additionalProperties": False,
        },
    },
    "calendar.create": {
        "key": "calendar.create",
        "name": "Create HomeServer Calendar Event",
        "description": "Propose creation of one HomeServer-native calendar event.",
        "mode": "write",
        "required_permissions": ["events.write"],
        "input_schema": {
            "type": "object",
            "properties": {
                "mutation_id": {"type": "string", "minLength": 8, "maxLength": 128},
                "title": {"type": "string", "minLength": 1, "maxLength": 240},
                "description": {"type": "string", "maxLength": 20000},
                "location": {"type": "string", "maxLength": 500},
                "start_at": {"type": "string"},
                "end_at": {"type": "string"},
                "timezone": {"type": "string", "maxLength": 80},
                "all_day": {"type": "boolean"},
            },
            "required": ["mutation_id", "title", "start_at", "end_at"],
            "additionalProperties": False,
        },
    },
    "calendar.update": {
        "key": "calendar.update",
        "name": "Update HomeServer Calendar Event",
        "description": "Propose changes to one HomeServer-native calendar event by canonical federated identity.",
        "mode": "write",
        "required_permissions": ["events.write"],
        "input_schema": {
            "type": "object",
            "properties": {
                "canonical_id": {"type": "string", "pattern": "^fd24_[0-9a-f]{40}$", "maxLength": 45},
                "mutation_id": {"type": "string", "minLength": 8, "maxLength": 128},
                "expected_revision": {"type": "string", "pattern": "^[0-9a-f]{64}$", "maxLength": 64},
                "title": {"type": "string", "minLength": 1, "maxLength": 240},
                "description": {"type": "string", "maxLength": 20000},
                "location": {"type": "string", "maxLength": 500},
                "start_at": {"type": "string"},
                "end_at": {"type": "string"},
                "timezone": {"type": "string", "maxLength": 80},
                "all_day": {"type": "boolean"},
            },
            "required": ["canonical_id", "mutation_id", "expected_revision"],
            "additionalProperties": False,
        },
    },
    "calendar.delete": {
        "key": "calendar.delete",
        "name": "Delete HomeServer Calendar Event",
        "description": "Propose cancellation of one HomeServer-native calendar event by canonical federated identity.",
        "mode": "write",
        "required_permissions": ["events.write"],
        "input_schema": {
            "type": "object",
            "properties": {
                "canonical_id": {"type": "string", "pattern": "^fd24_[0-9a-f]{40}$", "maxLength": 45},
                "mutation_id": {"type": "string", "minLength": 8, "maxLength": 128},
                "expected_revision": {"type": "string", "pattern": "^[0-9a-f]{64}$", "maxLength": 64},
            },
            "required": ["canonical_id", "mutation_id", "expected_revision"],
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
        "key": "local.files",
        "name": "Local File Reader",
        "description": "Discover and read bounded indexed text from owner-approved local files without arbitrary filesystem access.",
        "tools": ["files.list", "files.read"],
    },
    {
        "key": "relationship.context",
        "name": "Relationship Context",
        "description": "Search private contacts and relationship notes through an explicit read capability.",
        "tools": ["contacts.search", "contacts.create", "contacts.update", "contacts.delete"],
    },
    {
        "key": "memory.manager",
        "name": "Memory Manager",
        "description": "Read and create durable agent memory through explicit local capabilities.",
        "tools": ["memory.list", "memory.write"],
    },
    {
        "key": "room.automation",
        "name": "Room & Device Automation",
        "description": "Read local rooms/devices and propose governed physical device commands.",
        "tools": ["devices.list", "devices.command"],
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
    if tool_key in {"contacts.create", "contacts.update", "contacts.delete"}:
        return contacts.safe_contact_mutation_meta(tool_key, arguments)
    if tool_key in {"knowledge.create", "knowledge.update", "knowledge.delete"}:
        return knowledge_service.safe_knowledge_mutation_meta(tool_key, arguments)
    if tool_key in {"tasks.create", "tasks.update", "tasks.delete"}:
        return continuity.safe_task_mutation_meta(tool_key, arguments)
    if tool_key in {"calendar.create", "calendar.update", "calendar.delete"}:
        return continuity.safe_calendar_mutation_meta(tool_key, arguments)
    if tool_key in {"contacts.search", "knowledge.search", "tasks.list", "calendar.list", "files.list"}:
        query = str(arguments.get("query") or "")
        return {"query_length": len(query), "limit": _safe_numeric(arguments.get("limit"), 8)}
    if tool_key == "files.read":
        ref = str(arguments.get("ref") or "")
        return {
            "ref_length": len(ref),
            "offset": _safe_numeric(arguments.get("offset"), 0),
            "max_chars": _safe_numeric(arguments.get("max_chars"), 6000),
        }
    if tool_key == "devices.list":
        return {
            "room_key_length": len(str(arguments.get("room_key") or "")),
            "category": str(arguments.get("category") or "")[:40] or None,
            "controllable_only": bool(arguments.get("controllable_only", False)),
            "limit": _safe_numeric(arguments.get("limit"), 50),
        }
    if tool_key == "devices.command":
        nested = arguments.get("arguments")
        return {
            "device_key_length": len(str(arguments.get("device_key") or "")),
            "command": str(arguments.get("command") or "")[:40],
            "argument_count": len(nested) if isinstance(nested, dict) else 0,
        }
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


def _contact_tool_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "canonical_id": row.get("canonical_id"),
        "authority_source": row.get("authority_source"),
        "authority_key": row.get("authority_key"),
        "contact_class": row.get("contact_class") or "address_book",
        "display_name": row.get("display_name"),
        "organization": row.get("organization"),
        "email": row.get("email"),
        "phone": row.get("phone"),
        "relationship": row.get("relationship"),
        "notes": str(row.get("notes") or "")[:1600],
        "updated_at": row.get("updated_at"),
        "allowed_mutations": list(row.get("allowed_mutations") or []),
    }


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
    try:
        rows = contacts.list_federated_contacts(query, limit=limit)
    except contacts.ContactError as exc:
        raise ToolError(str(exc), exc.status_code) from exc
    items = [_contact_tool_item(row) for row in rows]
    return {"items": items, "count": len(items)}, {"count": len(items), "federation_version": "2.4"}


def _contacts_create(
    arguments: dict[str, Any],
    source_app_key: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        normalized = contacts.normalize_contact_create_arguments(arguments)
        item = contacts.create_federated_contact(normalized, source_app_key=source_app_key)
    except contacts.ContactError as exc:
        raise ToolError(str(exc), exc.status_code) from exc
    result = _contact_tool_item(item)
    return {"contact": result, "created": True}, {
        "canonical_id": str(item.get("canonical_id") or "")[:45],
        "contact_class": "address_book",
    }


def _contacts_update(
    arguments: dict[str, Any],
    source_app_key: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        normalized = contacts.normalize_contact_update_arguments(arguments)
        canonical = str(normalized.pop("canonical_id"))
        item = contacts.update_federated_contact(canonical, normalized, source_app_key=source_app_key)
    except contacts.ContactError as exc:
        raise ToolError(str(exc), exc.status_code) from exc
    result = _contact_tool_item(item)
    return {"contact": result, "updated": True}, {
        "canonical_id": str(item.get("canonical_id") or "")[:45],
        "contact_class": "address_book",
    }


def _contacts_delete(
    arguments: dict[str, Any],
    source_app_key: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        normalized = contacts.normalize_contact_delete_arguments(arguments)
        canonical = str(normalized["canonical_id"])
        deleted = contacts.delete_federated_contact(
            canonical,
            mutation_id=str(normalized["mutation_id"]),
            expected_revision=str(normalized["expected_revision"]),
            source_app_key=source_app_key,
        )
    except contacts.ContactError as exc:
        raise ToolError(str(exc), exc.status_code) from exc
    if not deleted:
        raise ToolError("HomeServer contact not found.", 404)
    return {"deleted": True, "canonical_id": canonical}, {
        "canonical_id": canonical[:45],
        "contact_class": "address_book",
    }


def _file_identity(source_app_key: str, *, owner: bool) -> dict[str, Any] | None:
    if owner:
        return None
    app_id = knowledge_collection_policy.app_id_for_source(source_app_key)
    if app_id is None:
        raise ToolError("Connected application is unavailable.", 403)
    return {"id": app_id, "scope": app_scopes.get_scope(app_id)}


def _files_list(
    arguments: dict[str, Any], source_app_key: str, *, owner: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    unknown = set(arguments) - {"query", "limit"}
    if unknown:
        raise ToolError(f"Unsupported files.list argument: {sorted(unknown)[0]}")
    query = str(arguments.get("query") or "").strip()
    if len(query) > 240:
        raise ToolError("files.list query exceeds 240 characters.")
    limit = _bounded_int(arguments.get("limit"), 20, 1, 50, "limit")
    try:
        result = local_files.list_files(
            _file_identity(source_app_key, owner=owner), query, limit, owner=owner
        )
    except local_files.LocalFileError as exc:
        raise ToolError(str(exc), exc.status_code) from exc
    return result, {"count": int(result.get("count", 0)), "capability_version": local_files.FILE_CAPABILITY_VERSION}


def _files_read(
    arguments: dict[str, Any], source_app_key: str, *, owner: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    unknown = set(arguments) - {"ref", "offset", "max_chars"}
    if unknown:
        raise ToolError(f"Unsupported files.read argument: {sorted(unknown)[0]}")
    file_ref = str(arguments.get("ref") or "").strip()
    if not file_ref:
        raise ToolError("files.read requires a HomeServer file reference.")
    offset = _bounded_int(arguments.get("offset"), 0, 0, 10_000_000, "offset")
    max_chars = _bounded_int(arguments.get("max_chars"), 6000, 1, 12000, "max_chars")
    try:
        result = local_files.read_file(
            _file_identity(source_app_key, owner=owner),
            file_ref,
            offset=offset,
            max_chars=max_chars,
            owner=owner,
        )
    except local_files.LocalFileError as exc:
        raise ToolError(str(exc), exc.status_code) from exc
    return result, {
        "returned_chars": int(result.get("returned_chars", 0)),
        "truncated": bool(result.get("truncated")),
        "capability_version": local_files.FILE_CAPABILITY_VERSION,
    }


def _knowledge_search(
    arguments: dict[str, Any],
    source_app_key: str,
    *,
    owner: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    unknown = set(arguments) - {"query", "limit"}
    if unknown:
        raise ToolError(f"Unsupported knowledge.search argument: {sorted(unknown)[0]}")
    query = str(arguments.get("query") or "").strip()
    if not query:
        raise ToolError("knowledge.search requires a query.")
    if len(query) > 240:
        raise ToolError("knowledge.search query exceeds 240 characters.")
    limit = _bounded_int(arguments.get("limit"), 8, 1, 20, "limit")

    if not owner and source_app_key.startswith("app:"):
        app_id = knowledge_collection_policy.app_id_for_source(source_app_key)
        if app_id is None:
            raise ToolError("Connected application is unavailable.", 403)
        identity = {
            "id": app_id,
            "scope": app_scopes.get_scope(app_id),
        }
        result = knowledge_collection_policy.scoped_search(identity, query, limit=limit)
        safe = {
            "items": result.get("items", []),
            "count": int(result.get("count", 0)),
            "scope": result.get("scope", {}),
            "privacy": result.get("privacy", {}),
            "citation_version": result.get("citation_version", "v0.37"),
        }
        return safe, {"count": safe["count"], "citation_version": safe["citation_version"]}

    rows = list_knowledge(query, limit=limit)
    items: list[dict[str, Any]] = []
    for row in rows:
        excerpt = str(row.get("snippet") or row.get("content") or "").strip()[:1600]
        items.append({"id": row["id"], "title": row.get("title"), "kind": row.get("kind"), "source_path": row.get("source_path"), "excerpt": excerpt})
    return {"items": items, "count": len(items)}, {"count": len(items)}


def _knowledge_tool_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(item.get("id") or 0),
        "title": str(item.get("title") or "")[:240],
        "kind": str(item.get("kind") or "")[:40],
        "collection_key": str(item.get("collection_key") or "general")[:64],
        "source_type": str(item.get("source_type") or "local_item")[:40],
        "updated_at": item.get("updated_at"),
        "authority_source": "homeserver",
        "authority_key": str(item.get("authority_key") or "")[:180],
        "canonical_id": str(item.get("canonical_id") or "")[:45],
        "record_revision": str(item.get("record_revision") or "")[:64],
        "federation_version": str(item.get("federation_version") or "2.4")[:20],
        "read_only": bool(item.get("read_only")),
        "mutation_route": str(item.get("mutation_route") or "")[:40],
        "allowed_mutations": list(item.get("allowed_mutations") or []),
    }


def _knowledge_mutation_scope(
    source_app_key: str,
    *,
    kind: str,
    collection_key: str,
) -> None:
    if not source_app_key.startswith("app:"):
        return
    app_id = knowledge_collection_policy.app_id_for_source(source_app_key)
    if app_id is None:
        raise ToolError("Connected application is unavailable.", 403)
    scope = app_scopes.get_scope(app_id)
    if not app_scopes.knowledge_kind_allowed(scope, kind):
        raise ToolError("Knowledge kind is outside this application's allowed scope.", 403)
    allowed = knowledge_collection_policy.allowed_collection_keys(app_id)
    if allowed is not None and collection_key not in allowed:
        raise ToolError("Knowledge collection is outside this application's allowed scope.", 403)


def _knowledge_create(arguments: dict[str, Any], source_app_key: str) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        normalized = knowledge_service.normalize_knowledge_create_arguments(arguments)
        _knowledge_mutation_scope(
            source_app_key,
            kind=str(normalized["kind"]),
            collection_key=str(normalized["collection_key"]),
        )
        item = knowledge_service.create_federated_knowledge(normalized, source_app_key=source_app_key)
    except knowledge_service.FederatedKnowledgeError as exc:
        raise ToolError(str(exc), exc.status_code) from exc
    safe = _knowledge_tool_item(item)
    return {"knowledge": safe, "created": True}, {
        "canonical_id": safe["canonical_id"],
        "collection_key": safe["collection_key"],
        "kind": safe["kind"],
    }


def _knowledge_update(arguments: dict[str, Any], source_app_key: str) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        normalized = knowledge_service.normalize_knowledge_update_arguments(arguments)
        canonical = str(normalized.pop("canonical_id"))
        current = knowledge_service.get_federated_knowledge_by_canonical(canonical)
        if current is None:
            raise knowledge_service.FederatedKnowledgeError("HomeServer Knowledge item not found.", 404)
        _knowledge_mutation_scope(
            source_app_key,
            kind=str(normalized.get("kind") or current.get("kind") or "note"),
            collection_key=str(normalized.get("collection_key") or current.get("collection_key") or "general"),
        )
        item = knowledge_service.update_federated_knowledge(
            canonical,
            normalized,
            source_app_key=source_app_key,
        )
    except knowledge_service.FederatedKnowledgeError as exc:
        raise ToolError(str(exc), exc.status_code) from exc
    safe = _knowledge_tool_item(item)
    return {"knowledge": safe, "updated": True}, {
        "canonical_id": safe["canonical_id"],
        "collection_key": safe["collection_key"],
        "kind": safe["kind"],
    }


def _knowledge_delete(arguments: dict[str, Any], source_app_key: str) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        normalized = knowledge_service.normalize_knowledge_delete_arguments(arguments)
        canonical = str(normalized["canonical_id"])
        current = knowledge_service.get_federated_knowledge_by_canonical(canonical)
        if current is None:
            raise knowledge_service.FederatedKnowledgeError("HomeServer Knowledge item not found.", 404)
        _knowledge_mutation_scope(
            source_app_key,
            kind=str(current.get("kind") or "note"),
            collection_key=str(current.get("collection_key") or "general"),
        )
        deleted = knowledge_service.delete_federated_knowledge(
            canonical,
            mutation_id=str(normalized["mutation_id"]),
            expected_revision=str(normalized["expected_revision"]),
            source_app_key=source_app_key,
        )
    except knowledge_service.FederatedKnowledgeError as exc:
        raise ToolError(str(exc), exc.status_code) from exc
    if not deleted:
        raise ToolError("HomeServer Knowledge item not found.", 404)
    return {"deleted": True, "canonical_id": canonical}, {
        "canonical_id": canonical[:45],
    }


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
        items = continuity.list_federated_tasks(status=status, q=query, limit=limit)
    except (TaskError, continuity.TaskCalendarContinuityError) as exc:
        raise ToolError(str(exc), getattr(exc, "status_code", 422)) from exc
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


def _devices_list(arguments: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    unknown = set(arguments) - {"room_key", "category", "controllable_only", "limit"}
    if unknown:
        raise ToolError(f"Unsupported devices.list argument: {sorted(unknown)[0]}")
    room_key = str(arguments.get("room_key") or "").strip() or None
    category = str(arguments.get("category") or "").strip() or None
    controllable_raw = arguments.get("controllable_only", False)
    if not isinstance(controllable_raw, bool):
        raise ToolError("devices.list controllable_only must be boolean.")
    limit = _bounded_int(arguments.get("limit"), 50, 1, 100, "limit")
    try:
        rows = room_device_automation.list_devices(
            room_key=room_key,
            category=category,
            enabled_only=True,
            controllable_only=controllable_raw,
            limit=limit,
        )
    except room_device_automation.RoomDeviceError as exc:
        raise ToolError(str(exc), exc.status_code) from exc
    items = []
    for row in rows:
        items.append(
            {
                "device_key": row["device_key"],
                "name": row["name"],
                "category": row["category"],
                "room_key": row.get("room_key"),
                "room_name": row.get("room_name"),
                "controllable": bool(row["controllable"]),
                "currently_executable": bool(row["currently_executable"]),
                "capabilities": row["capabilities"],
                "state": row["state"],
            }
        )
    return {"items": items, "count": len(items)}, {"count": len(items), "automation_version": room_device_automation.AUTOMATION_VERSION}


def _devices_command(
    arguments: dict[str, Any],
    source: str,
    *,
    approval_request_id: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not approval_request_id:
        raise ToolError(
            "Physical device commands require an approved action request in VP3 OS v0.60.",
            403,
        )
    unknown = set(arguments) - {"device_key", "command", "arguments"}
    if unknown:
        raise ToolError(f"Unsupported devices.command argument: {sorted(unknown)[0]}")
    try:
        result = room_device_automation.execute_command(
            str(arguments.get("device_key") or ""),
            str(arguments.get("command") or ""),
            arguments.get("arguments") if isinstance(arguments.get("arguments"), dict) else {},
            source_app_key=source,
            action_request_id=approval_request_id,
        )
    except room_device_automation.RoomDeviceError as exc:
        raise ToolError(str(exc), exc.status_code) from exc
    return result, {
        "executed": True,
        "action_id": int(result["action_id"]),
        "device_key": str(result["device_key"])[:80],
        "command": str(result["command"])[:40],
    }


def _tasks_create(arguments: dict[str, Any], source: str, created_by_type: str) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        if "mutation_id" in arguments or source.startswith("app:"):
            task = continuity.create_federated_task(
                arguments,
                source_app_key=source,
                created_by_type=created_by_type,
            )
        else:
            source_key = source.removeprefix("app:") if source.startswith("app:") else (None if source == "owner" else source)
            task = continuity.federated_task_item(create_task(arguments, source_app_key=source_key, created_by_type=created_by_type))
    except (TaskError, continuity.TaskCalendarContinuityError) as exc:
        raise ToolError(str(exc), getattr(exc, "status_code", 422)) from exc
    return {"created": True, "task": task}, {"created": True, "canonical_id": task.get("canonical_id")}


def _tasks_update(arguments: dict[str, Any], source: str) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        task = continuity.update_federated_task(arguments, source_app_key=source)
    except continuity.TaskCalendarContinuityError as exc:
        raise ToolError(str(exc), exc.status_code) from exc
    return {"updated": True, "task": task}, {"updated": True, "canonical_id": task.get("canonical_id")}


def _tasks_delete(arguments: dict[str, Any], source: str) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        deleted = continuity.delete_federated_task(arguments, source_app_key=source)
    except continuity.TaskCalendarContinuityError as exc:
        raise ToolError(str(exc), exc.status_code) from exc
    return {"deleted": deleted, "canonical_id": arguments.get("canonical_id")}, {"deleted": deleted}


def _calendar_list(arguments: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    unknown = set(arguments) - {"from_at", "to_at", "query", "limit"}
    if unknown:
        raise ToolError(f"Unsupported calendar.list argument: {sorted(unknown)[0]}")
    try:
        items = continuity.list_federated_calendar(
            from_at=arguments.get("from_at"), to_at=arguments.get("to_at"),
            q=str(arguments.get("query") or ""),
            limit=_bounded_int(arguments.get("limit"), 50, 1, 100, "limit"),
        )
    except continuity.TaskCalendarContinuityError as exc:
        raise ToolError(str(exc), exc.status_code) from exc
    return {"items": items, "count": len(items)}, {"count": len(items)}


def _calendar_create(arguments: dict[str, Any], source: str) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        event = continuity.create_federated_calendar(arguments, source_app_key=source)
    except continuity.TaskCalendarContinuityError as exc:
        raise ToolError(str(exc), exc.status_code) from exc
    return {"created": True, "event": event}, {"created": True, "canonical_id": event.get("canonical_id")}


def _calendar_update(arguments: dict[str, Any], source: str) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        event = continuity.update_federated_calendar(arguments, source_app_key=source)
    except continuity.TaskCalendarContinuityError as exc:
        raise ToolError(str(exc), exc.status_code) from exc
    return {"updated": True, "event": event}, {"updated": True, "canonical_id": event.get("canonical_id")}


def _calendar_delete(arguments: dict[str, Any], source: str) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        deleted = continuity.delete_federated_calendar(arguments, source_app_key=source)
    except continuity.TaskCalendarContinuityError as exc:
        raise ToolError(str(exc), exc.status_code) from exc
    return {"deleted": deleted, "canonical_id": arguments.get("canonical_id")}, {"deleted": deleted}


def execute_tool(source_app_key: str, tool_key: str, arguments: dict[str, Any] | None,
                 granted_permissions: set[str] | None = None, *, owner: bool = False,
                 approval_request_id: str | None = None) -> dict[str, Any]:
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
        elif tool["key"] == "contacts.create":
            result, result_meta = _contacts_create(payload, source)
        elif tool["key"] == "contacts.update":
            result, result_meta = _contacts_update(payload, source)
        elif tool["key"] == "contacts.delete":
            result, result_meta = _contacts_delete(payload, source)
        elif tool["key"] == "files.list":
            result, result_meta = _files_list(payload, source, owner=owner)
        elif tool["key"] == "files.read":
            result, result_meta = _files_read(payload, source, owner=owner)
        elif tool["key"] == "knowledge.search":
            result, result_meta = _knowledge_search(payload, source, owner=owner)
        elif tool["key"] == "knowledge.create":
            result, result_meta = _knowledge_create(payload, source)
        elif tool["key"] == "knowledge.update":
            result, result_meta = _knowledge_update(payload, source)
        elif tool["key"] == "knowledge.delete":
            result, result_meta = _knowledge_delete(payload, source)
        elif tool["key"] == "memory.list":
            result, result_meta = _memory_list(payload)
        elif tool["key"] == "memory.write":
            result, result_meta = _memory_write(payload)
        elif tool["key"] == "tasks.list":
            result, result_meta = _tasks_list(payload)
        elif tool["key"] == "calendar.list":
            result, result_meta = _calendar_list(payload)
        elif tool["key"] == "notifications.list":
            result, result_meta = _notifications_list(payload)
        elif tool["key"] == "devices.list":
            result, result_meta = _devices_list(payload)
        elif tool["key"] == "devices.command":
            result, result_meta = _devices_command(
                payload,
                source,
                approval_request_id=approval_request_id,
            )
        elif tool["key"] == "tasks.create":
            fallback_creator = "agent" if owner and source.startswith("app:") else actor_type
            task_creator = continuity.current_task_creator_provenance(fallback_creator)
            result, result_meta = _tasks_create(payload, source, task_creator)
        elif tool["key"] == "tasks.update":
            result, result_meta = _tasks_update(payload, source)
        elif tool["key"] == "tasks.delete":
            result, result_meta = _tasks_delete(payload, source)
        elif tool["key"] == "calendar.create":
            result, result_meta = _calendar_create(payload, source)
        elif tool["key"] == "calendar.update":
            result, result_meta = _calendar_update(payload, source)
        elif tool["key"] == "calendar.delete":
            result, result_meta = _calendar_delete(payload, source)
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
