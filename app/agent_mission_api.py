"""Authenticated additive mission endpoints; legacy delegation API unchanged."""
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .services import agent_mission_runtime as mission
from .services.pairing import authenticate

router = APIRouter()


class CreateMission(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=160)
    objective: str = Field(min_length=1, max_length=16000)
    client_request_id: str = Field(min_length=1, max_length=128)
    parent_agent_id: int | None = None


def authorized(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token.")
    identity = authenticate(authorization.removeprefix("Bearer ").strip())
    if identity is None:
        raise HTTPException(401, "Invalid or revoked bearer token.")
    if "agent.chat" not in identity["permissions"]:
        raise HTTPException(403, "agent.chat permission required.")
    return identity


def execute(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except mission.MissionError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.post("/api/v1/agent-missions")
def create(body: CreateMission, identity: dict = Depends(authorized)):
    return execute(
        mission.create_mission, f"app:{identity['app_key']}",
        conversation_id=body.conversation_id, objective=body.objective,
        client_request_id=body.client_request_id,
        parent_agent_id=body.parent_agent_id, owner=False,
    )


@router.get("/api/v1/agent-missions")
def list_for_app(limit: int = Query(default=20, ge=1, le=50),
                 identity: dict = Depends(authorized)):
    return {"items": execute(mission.list_missions, f"app:{identity['app_key']}", limit)}


@router.get("/api/v1/agent-missions/{mission_id}")
def get(mission_id: str, identity: dict = Depends(authorized)):
    return execute(mission.get_mission, f"app:{identity['app_key']}", mission_id)


@router.post("/api/v1/agent-missions/{mission_id}/start")
def start(mission_id: str, identity: dict = Depends(authorized)):
    return execute(mission.start_mission, f"app:{identity['app_key']}", mission_id)


@router.post("/api/v1/agent-missions/{mission_id}/cancel")
def cancel(mission_id: str, identity: dict = Depends(authorized)):
    return execute(mission.cancel_mission, f"app:{identity['app_key']}", mission_id)


@router.post("/api/v1/control/agent-missions")
def owner_create(body: CreateMission):
    return execute(
        mission.create_mission, "owner",
        conversation_id=body.conversation_id, objective=body.objective,
        client_request_id=body.client_request_id,
        parent_agent_id=body.parent_agent_id, owner=True,
    )


@router.get("/api/v1/control/agent-missions")
def owner_list(limit: int = Query(default=20, ge=1, le=50)):
    return {"items": execute(mission.list_missions, "owner", limit)}


@router.get("/api/v1/control/agent-missions/{mission_id}")
def owner_get(mission_id: str):
    return execute(mission.get_mission, "owner", mission_id)


@router.post("/api/v1/control/agent-missions/{mission_id}/start")
def owner_start(mission_id: str):
    return execute(mission.start_mission, "owner", mission_id)


@router.post("/api/v1/control/agent-missions/{mission_id}/cancel")
def owner_cancel(mission_id: str):
    return execute(mission.cancel_mission, "owner", mission_id)
