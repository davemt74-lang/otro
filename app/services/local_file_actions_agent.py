from __future__ import annotations

from typing import Any, Callable

from . import action_policy, agent_tools, app_scopes, local_file_actions_approvals, tools


PROPOSALS = {
    "homeserver_file_update_request": (
        "files.update",
        local_file_actions_approvals.create_file_update_request,
        "Propose replacing the text of an existing tracked local file. HomeServer uses only its opaque file reference; no filesystem path is accepted.",
    ),
    "homeserver_file_delete_request": (
        "files.delete",
        local_file_actions_approvals.create_file_delete_request,
        "Propose deleting an existing tracked local file. HomeServer uses only its opaque file reference; no filesystem path is accepted.",
    ),
}


def install() -> None:
    if getattr(agent_tools, "_local_file_actions_v039_installed", False):
        return

    original_schemas: Callable[..., list[dict[str, Any]]] = agent_tools.model_tool_schemas

    def model_tool_schemas(
        granted_permissions: set[str] | None = None,
        *,
        owner: bool = False,
        allow_write_proposals: bool = False,
        source_app_key: str | None = None,
    ) -> list[dict[str, Any]]:
        schemas = original_schemas(
            granted_permissions,
            owner=owner,
            allow_write_proposals=allow_write_proposals,
            source_app_key=source_app_key,
        )
        if not allow_write_proposals:
            return schemas

        by_key = {item["key"]: item for item in tools.list_tools(granted_permissions, owner=owner)}
        scope = None
        if not owner and source_app_key:
            scope = app_scopes.get_scope_for_source(source_app_key)

        for model_name, (tool_key, _creator, description) in PROPOSALS.items():
            item = by_key.get(tool_key)
            if not item or item.get("mode") != "write" or not item.get("available"):
                continue
            if scope is not None and not app_scopes.tool_allowed(scope, tool_key):
                continue
            execution = agent_tools._execution_policy(source_app_key, tool_key, owner)
            if execution and execution["policy_mode"] == action_policy.SENSITIVE_HIGH_IMPACT:
                continue
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": model_name,
                        "description": (
                            description
                            + " This always creates a pending action request; the file cannot change until local HomeServer owner control approves it."
                        ),
                        "parameters": item["input_schema"],
                    },
                }
            )
        return schemas

    agent_tools.model_tool_schemas = model_tool_schemas
    original_execute = agent_tools.execute_model_tool

    def execute_model_tool(
        source_app_key: str,
        model_tool_name: str,
        arguments: dict[str, Any] | None,
        granted_permissions: set[str] | None = None,
        *,
        owner: bool = False,
    ) -> dict[str, Any]:
        proposal = PROPOSALS.get(model_tool_name)
        if proposal is None:
            return original_execute(
                source_app_key,
                model_tool_name,
                arguments,
                granted_permissions,
                owner=owner,
            )

        global_policy = agent_tools.get_policy()
        if not global_policy["enabled"] or not global_policy["allow_write_proposals"]:
            raise agent_tools._deny_unavailable(source_app_key, owner)

        tool_key, creator, _description = proposal
        granted = set(granted_permissions or set())
        available = {
            item["key"]: item
            for item in tools.list_tools(granted, owner=owner)
            if item.get("available")
        }
        write_tool = available.get(tool_key)
        if not write_tool or write_tool.get("mode") != "write":
            raise agent_tools._deny_unavailable(source_app_key, owner)

        execution = agent_tools._execution_policy(source_app_key, tool_key, owner)
        if execution and execution["policy_mode"] == action_policy.SENSITIVE_HIGH_IMPACT:
            agent_tools._record_policy(
                execution,
                "blocked",
                reason="Sensitive/high-impact file actions require local HomeServer owner control.",
            )
            raise agent_tools._deny_unavailable(source_app_key, owner)

        args = arguments or {}
        if not agent_tools._scope_allows(source_app_key, tool_key, args, execution):
            agent_tools._record_policy(
                execution,
                "blocked",
                reason="The paired application's current tool scope blocks this file action.",
            )
            raise agent_tools._deny_unavailable(source_app_key, owner)

        # Section 10 deliberately has no Agent auto-execute path. Even if a
        # stale database row or future policy regression tried to classify a
        # file mutation as safe automatic, the model-facing adapter remains a
        # proposal-only boundary and requires local owner approval.
        try:
            result = creator(source_app_key, args, owner=owner)
        except Exception as exc:
            if isinstance(exc, agent_tools.AgentToolError):
                raise
            raise agent_tools.AgentToolError(str(exc)) from exc
        request_id = str(((result.get("result") or {}).get("request_id") or "")) or None
        agent_tools._record_policy(
            execution,
            "approval_requested",
            request_id=request_id,
            reason="Agent file mutation requires local owner approval before execution.",
        )
        return result

    agent_tools.execute_model_tool = execute_model_tool
    agent_tools._local_file_actions_v039_installed = True
