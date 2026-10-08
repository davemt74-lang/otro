"""A4 cognitive review endpoints; paired-app and owner authorities stay separate."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .agent_mission_api import authorized, execute
from .services import agent_mission_cognition as cognition

router = APIRouter()


class Config(BaseModel):
    enabled: bool


class Decision(BaseModel):
    approve: bool


@router.get("/api/v1/agent-missions/{mission_id}/cognition")
def app_latest(mission_id: str, identity: dict = Depends(authorized)):
    source = "app:" + identity["app_key"]
    return {"settings": execute(cognition.settings, source, mission_id),
            "review": execute(cognition.latest, source, mission_id)}


@router.post("/api/v1/agent-missions/{mission_id}/cognition/configure")
def app_config(mission_id: str, body: Config, identity: dict = Depends(authorized)):
    return execute(cognition.configure, "app:" + identity["app_key"],
                   mission_id, enabled=body.enabled)


@router.post("/api/v1/agent-missions/{mission_id}/cognition/evaluate")
def app_evaluate(mission_id: str, identity: dict = Depends(authorized)):
    return execute(cognition.evaluate, "app:" + identity["app_key"], mission_id)


@router.post("/api/v1/agent-missions/{mission_id}/cognition/{review_id}/decision")
def app_decision(mission_id: str, review_id: str, body: Decision,
                 identity: dict = Depends(authorized)):
    return execute(cognition.decide, "app:" + identity["app_key"],
                   mission_id, review_id, approve=body.approve)


@router.get("/api/v1/control/agent-missions/{mission_id}/cognition")
def owner_latest(mission_id: str):
    return {"settings": execute(cognition.settings, "owner", mission_id),
            "review": execute(cognition.latest, "owner", mission_id)}


@router.post("/api/v1/control/agent-missions/{mission_id}/cognition/configure")
def owner_config(mission_id: str, body: Config):
    return execute(cognition.configure, "owner", mission_id, enabled=body.enabled)


@router.post("/api/v1/control/agent-missions/{mission_id}/cognition/evaluate")
def owner_evaluate(mission_id: str):
    return execute(cognition.evaluate, "owner", mission_id)


@router.post("/api/v1/control/agent-missions/{mission_id}/cognition/{review_id}/decision")
def owner_decision(mission_id: str, review_id: str, body: Decision):
    return execute(cognition.decide, "owner", mission_id, review_id, approve=body.approve)
