from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .services import agent_routing, agent_workflow_timeline
from .services.pairing import authenticate

router = APIRouter()


class SynthesisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(min_length=1, max_length=64)
    task_ids: list[int] = Field(min_length=2, max_length=agent_workflow_timeline.MAX_SYNTHESIS_TASKS)


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


@router.get("/api/v1/agent-workflows/timeline")
def client_workflow_timeline(
    conversation_id: str = Query(min_length=1, max_length=64),
    limit: int = Query(default=200, ge=1, le=agent_workflow_timeline.MAX_TIMELINE_EVENTS),
    identity: dict = Depends(_require_chat),
) -> dict:
    try:
        return agent_workflow_timeline.timeline(
            _app_source(identity),
            conversation_id,
            owner=False,
            current_permissions=set(identity["permissions"]),
            limit=limit,
        )
    except (agent_workflow_timeline.AgentWorkflowTimelineError, agent_routing.AgentRoutingError) as exc:
        raise _http_error(exc) from exc


@router.post("/api/v1/agent-workflows/synthesis")
def client_prepare_synthesis(payload: SynthesisRequest, identity: dict = Depends(_require_chat)) -> dict:
    try:
        return agent_workflow_timeline.prepare_synthesis(
            _app_source(identity),
            payload.conversation_id,
            payload.task_ids,
            owner=False,
            current_permissions=set(identity["permissions"]),
        )
    except (agent_workflow_timeline.AgentWorkflowTimelineError, agent_routing.AgentRoutingError) as exc:
        raise _http_error(exc) from exc


@router.get("/api/v1/control/agent-workflows/timeline")
def control_workflow_timeline(
    conversation_id: str = Query(min_length=1, max_length=64),
    limit: int = Query(default=200, ge=1, le=agent_workflow_timeline.MAX_TIMELINE_EVENTS),
) -> dict:
    try:
        return agent_workflow_timeline.timeline(
            "owner",
            conversation_id,
            owner=True,
            current_permissions=set(),
            limit=limit,
        )
    except (agent_workflow_timeline.AgentWorkflowTimelineError, agent_routing.AgentRoutingError) as exc:
        raise _http_error(exc) from exc


@router.post("/api/v1/control/agent-workflows/synthesis")
def control_prepare_synthesis(payload: SynthesisRequest) -> dict:
    try:
        return agent_workflow_timeline.prepare_synthesis(
            "owner",
            payload.conversation_id,
            payload.task_ids,
            owner=True,
            current_permissions=set(),
        )
    except (agent_workflow_timeline.AgentWorkflowTimelineError, agent_routing.AgentRoutingError) as exc:
        raise _http_error(exc) from exc
