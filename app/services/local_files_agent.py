from __future__ import annotations

from typing import Any, Callable


def install() -> None:
    """Expose governed local-file reads through existing Agent Tool plumbing."""
    from . import agent_tools, app_scopes

    if getattr(agent_tools, "_local_files_v038_installed", False):
        return

    additions = {
        "homeserver_files_list": "files.list",
        "homeserver_file_read": "files.read",
    }
    for model_name, tool_key in additions.items():
        existing = agent_tools.MODEL_TOOL_NAMES.get(model_name)
        if existing not in (None, tool_key):
            raise RuntimeError(f"Agent tool name collision: {model_name}")
        agent_tools.MODEL_TOOL_NAMES[model_name] = tool_key

    original: Callable[..., list[dict[str, Any]]] = agent_tools.model_tool_schemas

    def scoped_model_tool_schemas(
        granted_permissions: set[str] | None = None,
        *,
        owner: bool = False,
        allow_write_proposals: bool = False,
        source_app_key: str | None = None,
    ) -> list[dict[str, Any]]:
        schemas = original(
            granted_permissions,
            owner=owner,
            allow_write_proposals=allow_write_proposals,
            source_app_key=source_app_key,
        )
        if owner or not source_app_key:
            return schemas
        scope = app_scopes.get_scope_for_source(source_app_key)
        output: list[dict[str, Any]] = []
        for schema in schemas:
            function = schema.get("function") if isinstance(schema, dict) else None
            model_name = str(function.get("name") or "") if isinstance(function, dict) else ""
            tool_key = agent_tools.MODEL_TOOL_NAMES.get(model_name)
            if tool_key is not None and not app_scopes.tool_allowed(scope, tool_key):
                continue
            output.append(schema)
        return output

    agent_tools.model_tool_schemas = scoped_model_tool_schemas
    agent_tools._local_files_v038_installed = True
