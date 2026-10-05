from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from ..database import db
from . import app_scopes


federated_reviewer: ContextVar[str | None] = ContextVar("federated_reviewer", default=None)


def require_current_app(
    source: str, tool_key: str, arguments: dict[str, Any],
    permissions: set[str] | None = None, *, approval: bool = False,
) -> set[str]:
    """Resolve authority at execution, including deferred owner approvals."""
    from . import action_policy, tools

    if not source.startswith("app:") or not source[4:].strip():
        raise tools.ToolError("Connected application identity is unavailable.", 403)
    with db() as connection:
        app = connection.execute(
            "SELECT id, app_key, status FROM paired_apps WHERE app_key=? LIMIT 1",
            (source[4:],),
        ).fetchone()
        if app is None or app["status"] != "active":
            raise tools.ToolError("Connected application is no longer active.", 403)
        live = {str(row["permission"]) for row in connection.execute(
            "SELECT permission FROM app_permissions WHERE paired_app_id=? AND allowed=1",
            (app["id"],),
        )}
    effective = live if permissions is None else live & set(permissions)
    definition = tools._tool_definition(tool_key)
    required = {tools.TOOL_EXECUTE_PERMISSION, *definition["required_permissions"]}
    if not required.issubset(effective):
        raise tools.ToolError("Connected application no longer has required tool permissions.", 403)
    reviewer = federated_reviewer.get()
    if reviewer is not None and (reviewer != source or "approvals.review" not in live):
        raise tools.ToolError("Federated approval permission is no longer available.", 403)
    scope = app_scopes.get_scope(int(app["id"]))
    if not app_scopes.tool_allowed(scope, tool_key):
        raise tools.ToolError("Tool is outside this application's current scope.", 403)
    if tool_key == "memory.write" and not app_scopes.memory_key_allowed(scope, arguments.get("memory_key")):
        raise tools.ToolError("Memory is outside this application's current scope.", 403)
    policy = action_policy.resolve_policy(int(app["id"]), str(app["app_key"]), tool_key)
    mode = str(policy["policy_mode"])
    if mode == action_policy.SENSITIVE_HIGH_IMPACT:
        raise tools.ToolError("Tool is currently blocked as sensitive/high-impact.", 403)
    if not approval and definition.get("mode") == "write" and mode != action_policy.SAFE_AUTOMATIC:
        raise tools.ToolError("Current action policy requires owner approval.", 403)
    return effective


def execution_authority(
    source: str, tool_key: str, arguments: dict[str, Any], permissions: set[str],
    *, owner: bool, approval_request_id: str | None,
) -> tuple[set[str], bool]:
    from . import tools

    if approval_request_id:
        with db() as connection:
            request = connection.execute("SELECT * FROM action_requests WHERE id=?", (approval_request_id,)).fetchone()
        if request is None or request["status"] != "executing" or request["source_app_key"] != source or request["action_key"] != tool_key:
            raise tools.ToolError("Approved action execution identity is invalid.", 403)
        import json
        if json.loads(request["arguments_json"]) != arguments:
            raise tools.ToolError("Approved action arguments have changed.", 403)
        if request["actor_type"] == "app":
            return require_current_app(source, tool_key, arguments, approval=True), False
        return permissions, owner
    if owner:
        return permissions, True
    return require_current_app(source, tool_key, arguments, permissions), False
