from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .services import (
    action_policy,
    app_scopes,
    approvals,
    local_file_actions_approvals,
    tools,
    vp3_commerce_agent_agent,
    vp3_commerce_agent_approvals,
    vp3_commerce_agent_remote,
    vp3_commerce_agent_tools,
    vp3_scheduling_agent,
    vp3_scheduling_approvals,
    vp3_scheduling_remote,
    vp3_scheduling_tools,
)
from .services.pairing import authenticate

vp3_scheduling_tools.install()
vp3_scheduling_approvals.install()
vp3_scheduling_agent.install()
vp3_scheduling_remote.install()
vp3_commerce_agent_tools.install()
vp3_commerce_agent_approvals.install()
vp3_commerce_agent_agent.install()
vp3_commerce_agent_remote.install()

router = APIRouter()


class ToolExecuteRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolPolicyUpdate(BaseModel):
    enabled: bool


def _current_app(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    identity = authenticate(authorization.removeprefix("Bearer ").strip())
    if identity is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked token")
    return identity


def _tool_or_http(source: str, tool_key: str, payload: ToolExecuteRequest, permissions: set[str], *, owner: bool) -> dict:
    try:
        return tools.execute_tool(source, tool_key, payload.arguments, permissions, owner=owner)
    except tools.ToolError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _approval_or_http(tool_key: str, source: str, arguments: dict[str, Any]) -> dict:
    try:
        if tool_key == "memory.write":
            return approvals.create_memory_write_request(source, arguments, owner=False)
        if tool_key == "tasks.create":
            return approvals.create_task_create_request(source, arguments, owner=False)
        if tool_key == "files.update":
            return local_file_actions_approvals.create_file_update_request(source, arguments, owner=False)
        if tool_key == "files.delete":
            return local_file_actions_approvals.create_file_delete_request(source, arguments, owner=False)
        if tool_key in vp3_scheduling_approvals.ACTIONS:
            return vp3_scheduling_approvals.create_request(source, tool_key, arguments, owner=False)
        if tool_key in vp3_commerce_agent_approvals.ACTIONS:
            return vp3_commerce_agent_approvals.create_request(source, tool_key, arguments, owner=False)
    except approvals.ApprovalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    raise HTTPException(status_code=409, detail="This write tool does not support deferred approval yet.")


def _scope_tool_result(tool_key: str, result: dict, scope: dict) -> dict:
    if not isinstance(result, dict):
        return result
    payload = result.get("result")
    if not isinstance(payload, dict):
        return result
    items = payload.get("items")
    if not isinstance(items, list):
        return result

    if tool_key == "memory.list":
        filtered = [item for item in items if isinstance(item, dict) and app_scopes.memory_key_allowed(scope, item.get("memory_key"))]
    elif tool_key == "knowledge.search":
        filtered = [item for item in items if isinstance(item, dict) and app_scopes.knowledge_kind_allowed(scope, item.get("kind"))]
    else:
        return result

    scoped = dict(result)
    scoped_payload = dict(payload)
    scoped_payload["items"] = filtered
    scoped_payload["count"] = len(filtered)
    scoped["result"] = scoped_payload
    return scoped


def _available_tool(tool_key: str, permissions: set[str]) -> dict:
    item = next((entry for entry in tools.list_tools(permissions) if entry.get("key") == tool_key), None)
    if item is None:
        raise HTTPException(status_code=404, detail="Tool not found")
    if not bool(item.get("enabled")):
        raise HTTPException(status_code=403, detail="Tool is disabled by the HomeServer owner")
    missing = list(item.get("missing_permissions") or [])
    if missing:
        raise HTTPException(status_code=403, detail=f"Missing tool permissions: {', '.join(missing)}")
    return item


@router.get("/api/v1/tools")
def client_tools(identity: dict = Depends(_current_app)) -> dict:
    permissions = set(identity["permissions"])
    scope = identity.get("scope") or app_scopes.DEFAULT_SCOPE
    items = [item for item in tools.list_tools(permissions) if app_scopes.tool_allowed(scope, item.get("key"))]
    policies = {
        item["tool_key"]: item
        for item in action_policy.list_policy_for_app(int(identity["id"]), str(identity["app_key"]))
    }
    for item in items:
        item["execution_policy"] = policies.get(str(item.get("key")))
    return {
        "items": items,
        "app": identity["app_key"],
        "app_scope": app_scopes.normalize(scope),
    }


@router.get("/api/v1/skills")
def client_skills(identity: dict = Depends(_current_app)) -> dict:
    permissions = set(identity["permissions"])
    scope = identity.get("scope") or app_scopes.DEFAULT_SCOPE
    items = [
        item for item in tools.list_skills(permissions)
        if all(app_scopes.tool_allowed(scope, tool_key) for tool_key in item.get("tools") or [])
    ]
    return {"items": items, "app": identity["app_key"]}


@router.post("/api/v1/tools/{tool_key}/execute")
def client_tool_execute(
    tool_key: str,
    payload: ToolExecuteRequest,
    identity: dict = Depends(_current_app),
) -> dict:
    scope = identity.get("scope") or app_scopes.DEFAULT_SCOPE
    if not app_scopes.tool_allowed(scope, tool_key):
        raise HTTPException(status_code=403, detail="Tool is outside this application's allowed scope")
    if tool_key == "memory.write" and not app_scopes.memory_key_allowed(scope, payload.arguments.get("memory_key")):
        raise HTTPException(status_code=403, detail="Memory key is outside this application's allowed scope")

    permissions = set(identity["permissions"])
    item = _available_tool(tool_key, permissions)
    app_id = int(identity["id"])
    app_key = str(identity["app_key"])
    policy = action_policy.resolve_policy(app_id, app_key, tool_key)
    policy_mode = str(policy["policy_mode"])
    tool_mode = str(item.get("mode") or "")
    source = f"app:{app_key}"
    audit_meta = {"tool_mode": tool_mode, "inherited": bool(policy.get("inherited"))}

    if policy_mode == action_policy.SENSITIVE_HIGH_IMPACT:
        action_policy.record_decision(
            app_id, app_key, tool_key, policy_mode, "blocked",
            reason="Sensitive/high-impact tools require local HomeServer owner control.",
            metadata=audit_meta,
        )
        raise HTTPException(
            status_code=403,
            detail="This tool is classified sensitive/high-impact and can only be run from local HomeServer owner control.",
        )

    if tool_mode == "write" and policy_mode == action_policy.APPROVAL_REQUIRED:
        request = _approval_or_http(tool_key, source, payload.arguments)
        request_id = str(((request.get("result") or {}).get("request_id") or "")) or None
        action_policy.record_decision(
            app_id, app_key, tool_key, policy_mode, "approval_requested",
            request_id=request_id,
            reason="Owner approval is required before this write executes.",
            metadata=audit_meta,
        )
        return {**request, "execution_policy": policy, "approval_required": True}

    if tool_mode == "write" and policy_mode != action_policy.SAFE_AUTOMATIC:
        action_policy.record_decision(
            app_id, app_key, tool_key, policy_mode, "blocked",
            reason="Write execution is not authorized for this policy mode.",
            metadata=audit_meta,
        )
        raise HTTPException(status_code=403, detail="Write execution is not authorized by this app's action policy.")

    decision = "allowed_read" if tool_mode == "read" else "allowed_automatic"
    action_policy.record_decision(
        app_id, app_key, tool_key, policy_mode, decision,
        reason="Execution allowed by owner-defined action policy.",
        metadata=audit_meta,
    )
    result = _tool_or_http(source, tool_key, payload, permissions, owner=False)
    result["execution_policy"] = policy
    return _scope_tool_result(tool_key, result, scope)


@router.get("/api/v1/control/tools")
def control_tools() -> dict:
    return {"items": tools.list_tools(owner=True)}


@router.get("/api/v1/control/skills")
def control_skills() -> dict:
    return {"items": tools.list_skills(owner=True)}


@router.put("/api/v1/control/tools/{tool_key}")
def control_tool_policy(tool_key: str, payload: ToolPolicyUpdate) -> dict:
    try:
        return {"tool": tools.set_tool_enabled(tool_key, payload.enabled)}
    except tools.ToolError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/api/v1/control/tools/{tool_key}/execute")
def control_tool_execute(tool_key: str, payload: ToolExecuteRequest) -> dict:
    return _tool_or_http("owner", tool_key, payload, set(), owner=True)


@router.get("/api/v1/control/tool-runs")
def control_tool_runs(limit: int = Query(default=100, ge=1, le=500)) -> dict:
    return {"items": tools.list_tool_runs(limit)}
