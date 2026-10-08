"""A5 mission model routing: authenticated, scoped, and queued-task only."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .agent_mission_api import authorized, execute
from .services import agent_mission_execution as execution

router = APIRouter()


class BindProvider(BaseModel):
    provider_key: str = Field(min_length=3, max_length=20)


@router.get("/api/v1/agent-missions/{mission_id}/execution")
def app_execution(mission_id: str, identity: dict = Depends(authorized)):
    return execute(execution.list_profiles, "app:" + identity["app_key"], mission_id)


@router.post("/api/v1/agent-missions/{mission_id}/tasks/{task_id}/execution")
def app_bind(mission_id: str, task_id: str, body: BindProvider,
             identity: dict = Depends(authorized)):
    return execute(execution.configure, "app:" + identity["app_key"],
                   mission_id, task_id, body.provider_key)


@router.get("/api/v1/control/agent-missions/{mission_id}/execution")
def owner_execution(mission_id: str):
    return execute(execution.list_profiles, "owner", mission_id)


@router.post("/api/v1/control/agent-missions/{mission_id}/tasks/{task_id}/execution")
def owner_bind(mission_id: str, task_id: str, body: BindProvider):
    return execute(execution.configure, "owner", mission_id, task_id, body.provider_key)
