"""Mission v1 A2 control endpoints. Owner requests use the existing owner gateway."""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from .agent_mission_api import authorized, execute
from .services import agent_mission_control as control

router = APIRouter()


class ResumeRequest(BaseModel):
    allow_reexecution: bool = False


@router.get("/api/v1/agent-missions/{mission_id}/events")
def app_events(mission_id: str, after: int = Query(default=0, ge=0),
               limit: int = Query(default=50, ge=1, le=100),
               identity: dict = Depends(authorized)):
    return execute(control.events, f"app:{identity['app_key']}", mission_id,
                   after=after, limit=limit)


@router.post("/api/v1/agent-missions/{mission_id}/pause")
def app_pause(mission_id: str, identity: dict = Depends(authorized)):
    return execute(control.pause, f"app:{identity['app_key']}", mission_id)


@router.post("/api/v1/agent-missions/{mission_id}/resume")
def app_resume(mission_id: str, body: ResumeRequest,
               identity: dict = Depends(authorized)):
    return execute(control.resume, f"app:{identity['app_key']}", mission_id,
                   allow_reexecution=body.allow_reexecution)


@router.post("/api/v1/agent-missions/{mission_id}/tasks/{task_id}/retry")
def app_retry(mission_id: str, task_id: str, identity: dict = Depends(authorized)):
    return execute(control.retry, f"app:{identity['app_key']}", mission_id, task_id)


@router.get("/api/v1/control/agent-missions/{mission_id}/events")
def owner_events(mission_id: str, after: int = Query(default=0, ge=0),
                 limit: int = Query(default=50, ge=1, le=100)):
    return execute(control.events, "owner", mission_id, after=after, limit=limit)


@router.post("/api/v1/control/agent-missions/{mission_id}/pause")
def owner_pause(mission_id: str):
    return execute(control.pause, "owner", mission_id)


@router.post("/api/v1/control/agent-missions/{mission_id}/resume")
def owner_resume(mission_id: str, body: ResumeRequest):
    return execute(control.resume, "owner", mission_id,
                   allow_reexecution=body.allow_reexecution)


@router.post("/api/v1/control/agent-missions/{mission_id}/tasks/{task_id}/retry")
def owner_retry(mission_id: str, task_id: str):
    return execute(control.retry, "owner", mission_id, task_id)
