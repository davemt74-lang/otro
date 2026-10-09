from __future__ import annotations

import json
import re
import time
from typing import Any

from ..database import db
from . import action_policy, approvals, app_scopes, maintenance_conversation, plugins, tools

MODEL_TOOL_NAMES = {
    "homeserver_workspace_get": "workspace.get",
    "homeserver_workspace_search": "workspace.search",
    "homeserver_health_status": "health.status",
    "homeserver_health_issue": "health.issue",
    "homeserver_runtime_diagnostics": "runtime.diagnostics",
    "homeserver_health_repair_plan": "health.repair-plan",
    "homeserver_contacts_search": "contacts.search",
    "homeserver_devices_list": "devices.list",
    "homeserver_knowledge_search": "knowledge.search",
    "homeserver_memory_list": "memory.list",
    "homeserver_notifications_list": "notifications.list",
    "homeserver_tasks_list": "tasks.list",
    "homeserver_calendar_list": "calendar.list",
    "homeserver_apps_list": "apps.list",
    "homeserver_app_get": "apps.status",
    "homeserver_app_releases": "apps.releases",
    "homeserver_app_source_status": "apps.source.status",
    "homeserver_app_actions": "apps.actions",
    "homeserver_app_compatibility": "apps.compatibility",
    "homeserver_app_settings": "apps.settings.get",
    "homeserver_app_hosting_status": "apps.hosting.status",
    "homeserver_app_invoke_read": "apps.invoke.read",
}
MEMORY_PROPOSAL_TOOL_NAME = "homeserver_memory_write_request"
MEMORY_PROPOSAL_TOOL_KEY = "memory.write"
MEMORY_UPDATE_PROPOSAL_TOOL_NAME = "homeserver_memory_update_request"
MEMORY_UPDATE_PROPOSAL_TOOL_KEY = "memory.update"
MEMORY_DELETE_PROPOSAL_TOOL_NAME = "homeserver_memory_delete_request"
MEMORY_DELETE_PROPOSAL_TOOL_KEY = "memory.delete"
TASK_PROPOSAL_TOOL_NAME = "homeserver_task_create_request"
TASK_PROPOSAL_TOOL_KEY = "tasks.create"
MAINTENANCE_PROPOSAL_TOOL_NAME="homeserver_maintenance_repair_request"
DEVICE_PROPOSAL_TOOL_NAME = "homeserver_device_command_request"
DEVICE_PROPOSAL_TOOL_KEY = "devices.command"
WORKSPACE_PROPOSAL_TOOL_NAME = "homeserver_workspace_update_request"

APP_ACTION_MODEL_TOOLS = {
    "homeserver_app_install_prebuilt_request": "apps.prebuilt.install",
    "homeserver_app_build_install_request": "apps.build_install",
    "homeserver_app_rollback_request": "apps.rollback",
    "homeserver_app_recover_request": "apps.recover",
    "homeserver_app_start_request": "apps.start",
    "homeserver_app_stop_request": "apps.stop",
    "homeserver_app_git_inspect_request": "apps.git.inspect",
    "homeserver_app_source_install_request": "apps.source.install",
    "homeserver_app_source_detach_request": "apps.source.detach",
    "homeserver_app_permission_request": "apps.permission.set",
    "homeserver_app_settings_request": "apps.settings.set",
    "homeserver_app_invoke_request": "apps.invoke",
}


class AgentToolError(RuntimeError):
    pass


def get_policy() -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            "SELECT enabled, max_calls, allow_write_proposals, created_at, updated_at FROM agent_tool_policy WHERE id=1"
        ).fetchone()
    if row is None:
        return {
            "enabled": False,
            "max_calls": 3,
            "allow_write_proposals": False,
            "created_at": None,
            "updated_at": None,
        }
    item = dict(row)
    item["enabled"] = bool(item["enabled"])
    item["allow_write_proposals"] = bool(item["allow_write_proposals"])
    return item


def save_policy(enabled: bool, max_calls: int, allow_write_proposals: bool = False) -> dict[str, Any]:
    calls = int(max_calls)
    if calls < 1 or calls > 3:
        raise AgentToolError("Agent tool-call limit must be between 1 and 3.")
    with db() as connection:
        connection.execute(
            """
            INSERT INTO agent_tool_policy(id, enabled, max_calls, allow_write_proposals)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                enabled=excluded.enabled,
                max_calls=excluded.max_calls,
                allow_write_proposals=excluded.allow_write_proposals,
                updated_at=CURRENT_TIMESTAMP
            """,
            (1 if enabled else 0, calls, 1 if allow_write_proposals else 0),
        )
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES ('owner', 'control-center', 'agent.tools.policy', 'agent_tools', 'bounded', ?)
            """,
            (
                json.dumps(
                    {
                        "enabled": bool(enabled),
                        "max_calls": calls,
                        "allow_write_proposals": bool(allow_write_proposals),
                    },
                    separators=(",", ":"),
                ),
            ),
        )
    return get_policy()


def _execution_policy(source_app_key: str | None, tool_key: str, owner: bool) -> dict[str, Any] | None:
    if owner or not source_app_key:
        return None
    return action_policy.resolve_policy_for_source(source_app_key, tool_key)


def _scope_allows(source_app_key: str, tool_key: str, arguments: dict[str, Any], policy: dict[str, Any] | None) -> bool:
    if policy is None:
        return True
    scope = app_scopes.get_scope_for_source(source_app_key)
    if not app_scopes.tool_allowed(scope, tool_key):
        return False
    if tool_key == "memory.write" and not app_scopes.memory_key_allowed(scope, arguments.get("memory_key")):
        return False
    return True


def _record_policy(policy: dict[str, Any] | None, decision: str, *, request_id: str | None = None, reason: str = "") -> None:
    if policy is None:
        return
    action_policy.record_decision(
        int(policy["app_id"]),
        str(policy["app_key"]),
        str(policy["tool_key"]),
        str(policy["policy_mode"]),
        decision,
        request_id=request_id,
        reason=reason,
        metadata={"tool_mode": str(policy.get("tool_mode") or ""), "agent_tool": True},
    )


def model_tool_schemas(
    granted_permissions: set[str] | None = None,
    *,
    owner: bool = False,
    allow_write_proposals: bool = False,
    source_app_key: str | None = None,
) -> list[dict[str, Any]]:
    by_key = {item["key"]: item for item in tools.list_tools(granted_permissions, owner=owner)}
    schemas: list[dict[str, Any]] = []
    for model_name, tool_key in MODEL_TOOL_NAMES.items():
        if tool_key=='workspace.search' and not owner:
            continue
        item = by_key.get(tool_key)
        if not item or item.get("mode") != "read" or not item.get("available"):
            continue
        execution = _execution_policy(source_app_key, tool_key, owner)
        if execution and execution["policy_mode"] == action_policy.SENSITIVE_HIGH_IMPACT:
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

    # v0.17 plugin tools are intentionally read-only and only appear when a
    # packaged/in-process handler has explicitly bound to the manifest tool.
    for item in plugins.available_model_tools(granted_permissions, owner=owner):
        if not item.get("available") or item.get("mode") != "read":
            continue
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": item["model_name"],
                    "description": f"{item.get('name')}: {item.get('description') or ''}"[:1200],
                    "parameters": item["input_schema"],
                },
            }
        )

    if allow_write_proposals:
        workspace_tool=by_key.get('workspace.update')
        if owner and workspace_tool and workspace_tool.get('available'):
            schemas.append({'type':'function','function':{'name':WORKSPACE_PROPOSAL_TOOL_NAME,'description':'Propose changed fields for a synced Cloud record. Read workspace.get first for its exact key, supported fields and revision. Nothing is saved until the owner approves, Cloud accepts and sync confirms the change. Use a unique change ID.','parameters':workspace_tool['input_schema']}})
        memory_tool = by_key.get(MEMORY_PROPOSAL_TOOL_KEY)
        memory_execution = _execution_policy(source_app_key, MEMORY_PROPOSAL_TOOL_KEY, owner)
        if (
            memory_tool
            and memory_tool.get("available")
            and not (memory_execution and memory_execution["policy_mode"] == action_policy.SENSITIVE_HIGH_IMPACT)
        ):
            automatic = bool(memory_execution and memory_execution["policy_mode"] == action_policy.SAFE_AUTOMATIC)
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": MEMORY_PROPOSAL_TOOL_NAME,
                        "description": (
                            "Write durable memory using the owner-defined execution policy. This action executes immediately only when the owner has marked it safe automatic; otherwise HomeServer creates a pending approval request."
                            if automatic
                            else "Propose a durable memory write for review. This does not modify memory now; HomeServer creates a pending approval request and executes only after an authorized approval."
                        ),
                        "parameters": memory_tool["input_schema"],
                    },
                }
            )
        for proposal_name, proposal_key, proposal_label in (
            (MEMORY_UPDATE_PROPOSAL_TOOL_NAME, MEMORY_UPDATE_PROPOSAL_TOOL_KEY, "update"),
            (MEMORY_DELETE_PROPOSAL_TOOL_NAME, MEMORY_DELETE_PROPOSAL_TOOL_KEY, "delete"),
        ):
            proposal_tool = by_key.get(proposal_key)
            proposal_execution = _execution_policy(source_app_key, proposal_key, owner)
            if (
                proposal_tool
                and proposal_tool.get("available")
                and not (
                    proposal_execution
                    and proposal_execution["policy_mode"] == action_policy.SENSITIVE_HIGH_IMPACT
                )
            ):
                automatic = bool(
                    proposal_execution
                    and proposal_execution["policy_mode"] == action_policy.SAFE_AUTOMATIC
                )
                schemas.append(
                    {
                        "type": "function",
                        "function": {
                            "name": proposal_name,
                            "description": (
                                f"{proposal_label.title()} one HomeServer Agent Brain memory using the owner-defined execution policy. This action executes immediately only when the owner has marked it safe automatic; otherwise HomeServer creates a pending approval request."
                                if automatic
                                else f"Propose an Agent Brain memory {proposal_label} for review. HomeServer executes only after authorized approval."
                            ),
                            "parameters": proposal_tool["input_schema"],
                        },
                    }
                )
        task_tool = by_key.get(TASK_PROPOSAL_TOOL_KEY)
        task_execution = _execution_policy(source_app_key, TASK_PROPOSAL_TOOL_KEY, owner)
        if (
            task_tool
            and task_tool.get("available")
            and not (task_execution and task_execution["policy_mode"] == action_policy.SENSITIVE_HIGH_IMPACT)
        ):
            automatic = bool(task_execution and task_execution["policy_mode"] == action_policy.SAFE_AUTOMATIC)
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": TASK_PROPOSAL_TOOL_NAME,
                        "description": (
                            "Create a local task using the owner-defined execution policy. This action executes immediately only when the owner has marked it safe automatic; otherwise HomeServer creates a pending approval request."
                            if automatic
                            else "Propose a local task or reminder for review. This does not create the task now; HomeServer creates a pending approval request and executes only after an authorized approval."
                        ),
                        "parameters": task_tool["input_schema"],
                    },
                }
            )

        recovery=by_key.get("apps.recover")
        diagnosis=by_key.get("health.issue")
        if owner and recovery and recovery.get("available") and diagnosis and diagnosis.get("available"):
            schemas.append({
                "type":"function",
                "function":{
                    "name":MAINTENANCE_PROPOSAL_TOOL_NAME,
                    "description":"Request governed recovery of a CURRENT health issue by issue_key. This rechecks health, derives a canonical action and only creates a pending owner approval. Never executes immediately.",
                    "parameters":{"type":"object","properties":{"issue_key":{"type":"string","minLength":1,"maxLength":160}},"required":["issue_key"],"additionalProperties":False},
                },
            })
        for model_name, tool_key in APP_ACTION_MODEL_TOOLS.items():
            app_tool=by_key.get(tool_key)
            if not app_tool or not app_tool.get("available") or not owner:
                continue
            schemas.append(
                {
                    "type":"function",
                    "function":{
                        "name":model_name,
                        "description":(
                            app_tool["description"]+
                            " This does not execute immediately; HomeServer creates an owner approval request."
                        ),
                        "parameters":app_tool["input_schema"],
                    },
                }
            )
        device_tool = by_key.get(DEVICE_PROPOSAL_TOOL_KEY)
        device_execution = _execution_policy(source_app_key, DEVICE_PROPOSAL_TOOL_KEY, owner)
        if (
            device_tool
            and device_tool.get("available")
            and not (device_execution and device_execution["policy_mode"] == action_policy.SENSITIVE_HIGH_IMPACT)
        ):
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": DEVICE_PROPOSAL_TOOL_NAME,
                        "description": (
                            "Propose one bounded physical device command for owner approval. "
                            "VP3 OS v0.60 never allows Agent device commands to execute automatically."
                        ),
                        "parameters": device_tool["input_schema"],
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


def _record_plugin_run(
    *,
    source_app_key: str,
    owner: bool,
    plugin_key: str,
    tool_key: str,
    status: str,
    arguments: dict[str, Any],
    result: dict[str, Any] | None,
    duration_ms: int,
    error: str | None = None,
) -> int:
    actor_type = "owner" if owner else "app"
    audit_key = f"plugin:{plugin_key}:{tool_key}"[:240]
    with db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO tool_runs(
                tool_key, source_app_key, actor_type, status, required_permissions_json,
                arguments_meta_json, result_meta_json, duration_ms, error, completed_at
            ) VALUES (?, ?, ?, ?, '[]', ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                audit_key,
                source_app_key,
                actor_type,
                status,
                json.dumps({"argument_count": len(arguments)}, separators=(",", ":")),
                json.dumps({"result_key_count": len(result or {})}, separators=(",", ":")),
                duration_ms,
                (error or "")[:1000] or None,
            ),
        )
        run_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES (?, ?, ?, 'tool', ?, ?)
            """,
            (
                actor_type,
                source_app_key,
                f"tool.{status}",
                audit_key,
                json.dumps({"run_id": run_id, "plugin_key": plugin_key, "tool_key": tool_key}, separators=(",", ":")),
            ),
        )
    return run_id


def _deny_unavailable(source_app_key: str, owner: bool) -> AgentToolError:
    run_id = _record_unknown_model_call(source_app_key, "owner" if owner else "app")
    return AgentToolError(f"Tool is not available to this conversation. Run {run_id} was recorded.")


def execute_model_tool(
    source_app_key: str,
    model_tool_name: str,
    arguments: dict[str, Any] | None,
    granted_permissions: set[str] | None = None,
    *,
    owner: bool = False,
) -> dict[str, Any]:
    granted = set(granted_permissions or set())
    available = {
        item["key"]: item
        for item in tools.list_tools(granted, owner=owner)
        if item.get("available")
    }
    args = arguments or {}

    if model_tool_name == WORKSPACE_PROPOSAL_TOOL_NAME:
        policy=get_policy()
        if not owner or not policy['enabled'] or not policy['allow_write_proposals'] or not available.get('workspace.update'):
            raise _deny_unavailable(source_app_key,owner)
        try:
            return approvals.create_workspace_update_request(source_app_key,args,owner=True)
        except approvals.ApprovalError as exc:
            raise AgentToolError(str(exc)) from exc

    if model_tool_name == MAINTENANCE_PROPOSAL_TOOL_NAME:
        policy=get_policy()
        if (not owner or not policy["enabled"] or not policy["allow_write_proposals"]
                or not available.get("apps.recover") or not available.get("health.issue")):
            raise _deny_unavailable(source_app_key,owner)
        try:
            return maintenance_conversation.propose_repair(args,source_app_key=source_app_key,owner=True)
        except (maintenance_conversation.MaintenanceError,approvals.ApprovalError) as exc:
            raise AgentToolError(str(exc)) from exc

    if model_tool_name in {
        MEMORY_PROPOSAL_TOOL_NAME,
        MEMORY_UPDATE_PROPOSAL_TOOL_NAME,
        MEMORY_DELETE_PROPOSAL_TOOL_NAME,
        TASK_PROPOSAL_TOOL_NAME,
        DEVICE_PROPOSAL_TOOL_NAME,
        *APP_ACTION_MODEL_TOOLS.keys(),
    }:
        global_policy = get_policy()
        if not global_policy["enabled"] or not global_policy["allow_write_proposals"]:
            raise _deny_unavailable(source_app_key, owner)
        if model_tool_name in APP_ACTION_MODEL_TOOLS:
            tool_key=APP_ACTION_MODEL_TOOLS[model_tool_name]
        elif model_tool_name == MEMORY_PROPOSAL_TOOL_NAME:
            tool_key = MEMORY_PROPOSAL_TOOL_KEY
        elif model_tool_name == MEMORY_UPDATE_PROPOSAL_TOOL_NAME:
            tool_key = MEMORY_UPDATE_PROPOSAL_TOOL_KEY
        elif model_tool_name == MEMORY_DELETE_PROPOSAL_TOOL_NAME:
            tool_key = MEMORY_DELETE_PROPOSAL_TOOL_KEY
        elif model_tool_name == TASK_PROPOSAL_TOOL_NAME:
            tool_key = TASK_PROPOSAL_TOOL_KEY
        else:
            tool_key = DEVICE_PROPOSAL_TOOL_KEY
        write_tool = available.get(tool_key)
        if not write_tool or write_tool.get("mode") != "write":
            raise _deny_unavailable(source_app_key, owner)
        execution = _execution_policy(source_app_key, tool_key, owner)
        if execution and execution["policy_mode"] == action_policy.SENSITIVE_HIGH_IMPACT:
            _record_policy(
                execution,
                "blocked",
                reason="Sensitive/high-impact tools require local HomeServer owner control.",
            )
            raise _deny_unavailable(source_app_key, owner)
        if not _scope_allows(source_app_key, tool_key, args, execution):
            _record_policy(execution, "blocked", reason="The paired application's private resource scope blocks this tool request.")
            raise _deny_unavailable(source_app_key, owner)
        if execution and execution["policy_mode"] == action_policy.SAFE_AUTOMATIC:
            try:
                result = tools.execute_tool(source_app_key, tool_key, args, granted, owner=owner)
            except tools.ToolError as exc:
                raise AgentToolError(str(exc)) from exc
            _record_policy(execution, "allowed_automatic", reason="Agent Tool execution allowed by owner-defined safe-automatic policy.")
            return result
        try:
            if model_tool_name in APP_ACTION_MODEL_TOOLS:
                result=approvals.create_app_action_request(
                    source_app_key,
                    APP_ACTION_MODEL_TOOLS[model_tool_name],
                    args,
                    owner=owner,
                )
            elif model_tool_name == MEMORY_PROPOSAL_TOOL_NAME:
                result = approvals.create_memory_write_request(source_app_key, args, owner=owner)
            elif model_tool_name == MEMORY_UPDATE_PROPOSAL_TOOL_NAME:
                result = approvals.create_memory_update_request(source_app_key, args, owner=owner)
            elif model_tool_name == MEMORY_DELETE_PROPOSAL_TOOL_NAME:
                result = approvals.create_memory_delete_request(source_app_key, args, owner=owner)
            elif model_tool_name == TASK_PROPOSAL_TOOL_NAME:
                result = approvals.create_task_create_request(source_app_key, args, owner=owner, created_by_type="agent")
            else:
                result = approvals.create_device_command_request(source_app_key, args, owner=owner)
            request_id = str(((result.get("result") or {}).get("request_id") or "")) or None
            _record_policy(
                execution,
                "approval_requested",
                request_id=request_id,
                reason="Agent Tool write requires approval before execution.",
            )
            return result
        except approvals.ApprovalError as exc:
            raise AgentToolError(str(exc)) from exc

    tool_key = MODEL_TOOL_NAMES.get(model_tool_name)
    if tool_key is not None:
        read_tool = available.get(tool_key)
        if not read_tool or read_tool.get("mode") != "read":
            raise _deny_unavailable(source_app_key, owner)
        execution = _execution_policy(source_app_key, tool_key, owner)
        if execution and execution["policy_mode"] == action_policy.SENSITIVE_HIGH_IMPACT:
            _record_policy(
                execution,
                "blocked",
                reason="Sensitive/high-impact tools require local HomeServer owner control.",
            )
            raise _deny_unavailable(source_app_key, owner)
        if not _scope_allows(source_app_key, tool_key, args, execution):
            _record_policy(execution, "blocked", reason="The paired application's private resource scope blocks this tool request.")
            raise _deny_unavailable(source_app_key, owner)
        _record_policy(execution, "allowed_read", reason="Agent Tool read allowed by owner-defined action policy.")
        try:
            return tools.execute_tool(
                source_app_key,
                tool_key,
                args,
                granted,
                owner=owner,
            )
        except tools.ToolError as exc:
            raise AgentToolError(str(exc)) from exc

    plugin_tool = next(
        (
            item
            for item in plugins.available_model_tools(granted, owner=owner)
            if item.get("model_name") == model_tool_name
        ),
        None,
    )
    if plugin_tool is None or not plugin_tool.get("available"):
        run_id = _record_unknown_model_call(source_app_key, "owner" if owner else "app")
        raise AgentToolError(f"Tool is not available to the agent. Run {run_id} was recorded.")

    started = time.perf_counter()
    try:
        plugin_result = plugins.execute_model_tool(
            model_tool_name,
            args,
            granted,
            owner=owner,
            source_app_key=source_app_key,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        run_id = _record_plugin_run(
            source_app_key=source_app_key,
            owner=owner,
            plugin_key=plugin_result["plugin_key"],
            tool_key=plugin_result["tool_key"],
            status="completed",
            arguments=args,
            result=plugin_result.get("result"),
            duration_ms=duration_ms,
        )
        return {"run_id": run_id, "result": plugin_result.get("result", {})}
    except plugins.PluginError as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        run_id = _record_plugin_run(
            source_app_key=source_app_key,
            owner=owner,
            plugin_key=plugin_tool["plugin_key"],
            tool_key=plugin_tool["key"],
            status="failed",
            arguments=args,
            result=None,
            duration_ms=duration_ms,
            error=str(exc),
        )
        raise AgentToolError(f"Plugin tool failed. Run {run_id} was recorded.") from exc


def tool_result_message(result: dict[str, Any], max_chars: int = 6000) -> str:
    text = json.dumps(result.get("result", {}), ensure_ascii=False, separators=(",", ":"))
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "… [truncated by HomeServer]"


def extract_run_id(message: str) -> int | None:
    match = re.search(r"\bRun (\d+)\b", message)
    return int(match.group(1)) if match else None
