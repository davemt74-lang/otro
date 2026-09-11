from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from .services import agent_handoffs, agent_routing
from .services.pairing import authenticate

router = APIRouter()


def _current_app(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    identity = authenticate(authorization.removeprefix("Bearer ").strip())
    if identity is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked token")
    return identity


def _require_chat(identity: dict = Depends(_current_app)) -> dict:
    if "agent.chat" not in identity["permissions"]:
        raise HTTPException(status_code=403, detail="Permission required: agent.chat")
    return identity


def _app_source(identity: dict) -> str:
    return f"app:{identity['app_key']}"


def _http_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=int(getattr(exc, "status_code", 422)), detail=str(exc))


@router.get("/api/v1/agent-workflows/handoffs")
def client_handoffs(
    conversation_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=100),
    identity: dict = Depends(_require_chat),
) -> dict:
    return agent_handoffs.list_handoffs(
        _app_source(identity),
        conversation_id=conversation_id,
        limit=limit,
    )


@router.post("/api/v1/agent-workflows/delegations/{task_id}/handoff")
def client_queue_handoff(task_id: int, identity: dict = Depends(_require_chat)) -> dict:
    try:
        return agent_handoffs.queue_task_result(task_id, _app_source(identity), owner=False)
    except (agent_handoffs.AgentHandoffError, agent_routing.AgentRoutingError) as exc:
        raise _http_error(exc) from exc


@router.post("/api/v1/agent-workflows/handoffs/{handoff_id}/revoke")
def client_revoke_handoff(handoff_id: int, identity: dict = Depends(_require_chat)) -> dict:
    try:
        return agent_handoffs.revoke_handoff(handoff_id, _app_source(identity))
    except agent_handoffs.AgentHandoffError as exc:
        raise _http_error(exc) from exc


@router.get("/api/v1/control/agent-workflows/handoffs")
def control_handoffs(
    conversation_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict:
    return agent_handoffs.list_handoffs("owner", conversation_id=conversation_id, limit=limit)


@router.post("/api/v1/control/agent-workflows/delegations/{task_id}/handoff")
def control_queue_handoff(task_id: int) -> dict:
    try:
        return agent_handoffs.queue_task_result(task_id, "owner", owner=True)
    except (agent_handoffs.AgentHandoffError, agent_routing.AgentRoutingError) as exc:
        raise _http_error(exc) from exc


@router.post("/api/v1/control/agent-workflows/handoffs/{handoff_id}/revoke")
def control_revoke_handoff(handoff_id: int) -> dict:
    try:
        return agent_handoffs.revoke_handoff(handoff_id, "owner")
    except agent_handoffs.AgentHandoffError as exc:
        raise _http_error(exc) from exc
