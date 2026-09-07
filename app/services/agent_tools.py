from __future__ import annotations

import json
import re
from typing import Any

from ..database import db
from . import tools

MODEL_TOOL_NAMES = {
    "homeserver_knowledge_search": "knowledge.search",
    "homeserver_memory_list": "memory.list",
}


class AgentToolError(RuntimeError):
    pass


def get_policy() -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            "SELECT enabled, max_calls, created_at, updated_at FROM agent_tool_policy WHERE id=1"
        ).fetchone()
    if row is None:
        return {"enabled": False, "max_calls": 3, "created_at": None, "updated_at": None}
    item = dict(row)
    item["enabled"] = bool(item["enabled"])
    return item


def save_policy(enabled: bool, max_calls: int) -> dict[str, Any]:
    calls = int(max_calls)
    if calls < 1 or calls > 3:
        raise AgentToolError("Agent tool-call limit must be between 1 and 3.")
    with db() as connection:
        connection.execute(
            """
            INSERT INTO agent_tool_policy(id, enabled, max_calls)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                enabled=excluded.enabled,
                max_calls=excluded.max_calls,
                updated_at=CURRENT_TIMESTAMP
            """,
            (1 if enabled else 0, calls),
        )
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES ('owner', 'control-center', 'agent.tools.policy', 'agent_tools', 'read_only', ?)
            """,
            (json.dumps({"enabled": bool(enabled), "max_calls": calls}, separators=(",", ":")),),
        )
    return get_policy()


def model_tool_schemas(
    granted_permissions: set[str] | None = None,
    *,
    owner: bool = False,
) -> list[dict[str, Any]]:
    by_key = {item["key"]: item for item in tools.list_tools(granted_permissions, owner=owner)}
    schemas: list[dict[str, Any]] = []
    for model_name, tool_key in MODEL_TOOL_NAMES.items():
        item = by_key.get(tool_key)
        if not item or item.get("mode") != "read" or not item.get("available"):
            continue
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": model_name,
                    "description": item["description"],
                    "parameters": item["input_schema"],
                },
            }
        )
    return schemas


def _record_unknown_model_call(source_app_key: str, actor_type: str) -> int:
    with db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO tool_runs(
                tool_key, source_app_key, actor_type, status, required_permissions_json,
                arguments_meta_json, result_meta_json, error, completed_at
            ) VALUES ('agent.unknown_tool', ?, ?, 'denied', '[]', '{}', '{}',
                      'Model requested a tool that was not offered.', CURRENT_TIMESTAMP)
            """,
            (source_app_key, actor_type),
        )
        run_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES (?, ?, 'tool.denied', 'tool', 'agent.unknown_tool', ?)
            """,
            (actor_type, source_app_key, json.dumps({"run_id": run_id}, separators=(",", ":"))),
        )
    return run_id


def execute_model_tool(
    source_app_key: str,
    model_tool_name: str,
    arguments: dict[str, Any] | None,
    granted_permissions: set[str] | None = None,
    *,
    owner: bool = False,
) -> dict[str, Any]:
    tool_key = MODEL_TOOL_NAMES.get(model_tool_name)
    if tool_key is None:
        run_id = _record_unknown_model_call(source_app_key, "owner" if owner else "app")
        raise AgentToolError(f"Tool is not available to the agent. Run {run_id} was recorded.")

    available = {
        item["key"]: item
        for item in tools.list_tools(granted_permissions, owner=owner)
        if item.get("mode") == "read" and item.get("available")
    }
    if tool_key not in available:
        run_id = _record_unknown_model_call(source_app_key, "owner" if owner else "app")
        raise AgentToolError(f"Tool is not available to this conversation. Run {run_id} was recorded.")

    try:
        return tools.execute_tool(
            source_app_key,
            tool_key,
            arguments or {},
            granted_permissions,
            owner=owner,
        )
    except tools.ToolError as exc:
        raise AgentToolError(str(exc)) from exc


def tool_result_message(result: dict[str, Any], max_chars: int = 6000) -> str:
    text = json.dumps(result.get("result", {}), ensure_ascii=False, separators=(",", ":"))
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "… [truncated by HomeServer]"


def extract_run_id(message: str) -> int | None:
    match = re.search(r"\bRun (\d+)\b", message)
    return int(match.group(1)) if match else None
