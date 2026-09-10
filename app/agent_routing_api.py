from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict

from .services import agent_routing
from .services.pairing import authenticate

router = APIRouter()


class AgentGrantUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowed: bool


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


def _http_error(exc: agent_routing.AgentRoutingError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/api/v1/agents")
def client_selectable_agents(identity: dict = Depends(_require_chat)) -> dict:
    try:
        return agent_routing.selectable_agents(_app_source(identity), owner=False)
    except agent_routing.AgentRoutingError as exc:
        raise _http_error(exc) from exc


@router.get("/api/v1/conversations/{conversation_id}/routing")
def client_conversation_routing(conversation_id: str, identity: dict = Depends(_require_chat)) -> dict:
    try:
        return agent_routing.conversation_binding(_app_source(identity), conversation_id)
    except agent_routing.AgentRoutingError as exc:
        raise _http_error(exc) from exc


@router.get("/api/v1/control/agent-routing")
def control_selectable_agents() -> dict:
    try:
        return agent_routing.selectable_agents("owner", owner=True)
    except agent_routing.AgentRoutingError as exc:
        raise _http_error(exc) from exc


@router.get("/api/v1/control/conversations/{conversation_id}/routing")
def control_conversation_routing(conversation_id: str) -> dict:
    try:
        return agent_routing.conversation_binding("owner", conversation_id)
    except agent_routing.AgentRoutingError as exc:
        raise _http_error(exc) from exc


@router.get("/api/v1/control/connected-apps/{app_id}/agents")
def control_app_agent_access(app_id: int) -> dict:
    try:
        return agent_routing.app_agent_access(app_id)
    except agent_routing.AgentRoutingError as exc:
        raise _http_error(exc) from exc


@router.put("/api/v1/control/connected-apps/{app_id}/agents/{agent_id}")
def control_app_agent_grant(app_id: int, agent_id: int, payload: AgentGrantUpdate) -> dict:
    try:
        return agent_routing.save_app_agent_grant(app_id, agent_id, payload.allowed)
    except agent_routing.AgentRoutingError as exc:
        raise _http_error(exc) from exc
