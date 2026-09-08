from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .services import app_scopes, tools
from .services.pairing import authenticate

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


@router.get("/api/v1/tools")
def client_tools(identity: dict = Depends(_current_app)) -> dict:
    permissions = set(identity["permissions"])
    scope = identity.get("scope") or app_scopes.DEFAULT_SCOPE
    items = [item for item in tools.list_tools(permissions) if app_scopes.tool_allowed(scope, item.get("key"))]
    return {"items": items, "app": identity["app_key"]}


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

    result = _tool_or_http(
        f"app:{identity['app_key']}",
        tool_key,
        payload,
        set(identity["permissions"]),
        owner=False,
    )
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
