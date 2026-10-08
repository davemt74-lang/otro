"""Owner and paired-app routes for supervised worker browser evidence."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .agent_mission_api import authorized, execute
from .services import agent_mission_browser as browser

router = APIRouter()

class GrantRequest(BaseModel):
    url: str = Field(min_length=9, max_length=1400)

class CaptureRequest(BaseModel):
    url: str | None = Field(default=None, max_length=1400)

@router.get("/api/v1/agent-missions/{mission_id}/tasks/{task_id}/browser")
def app_get(mission_id: str, task_id: str, identity: dict = Depends(authorized)):
    return execute(browser.inspect, "app:" + identity["app_key"], mission_id, task_id, image=True)

@router.post("/api/v1/agent-missions/{mission_id}/tasks/{task_id}/browser/grant")
def app_grant(mission_id: str, task_id: str, body: GrantRequest,
              identity: dict = Depends(authorized)):
    return execute(browser.authorize, "app:" + identity["app_key"], mission_id, task_id, body.url)

@router.post("/api/v1/agent-missions/{mission_id}/tasks/{task_id}/browser/capture")
def app_capture(mission_id: str, task_id: str, body: CaptureRequest,
                identity: dict = Depends(authorized)):
    return execute(browser.capture, "app:" + identity["app_key"], mission_id, task_id, body.url)

@router.post("/api/v1/agent-missions/{mission_id}/tasks/{task_id}/browser/revoke")
def app_revoke(mission_id: str, task_id: str, identity: dict = Depends(authorized)):
    return execute(browser.revoke, "app:" + identity["app_key"], mission_id, task_id)

@router.get("/api/v1/control/agent-missions/{mission_id}/tasks/{task_id}/browser")
def owner_get(mission_id: str, task_id: str):
    return execute(browser.inspect, "owner", mission_id, task_id, image=True)

@router.post("/api/v1/control/agent-missions/{mission_id}/tasks/{task_id}/browser/grant")
def owner_grant(mission_id: str, task_id: str, body: GrantRequest):
    return execute(browser.authorize, "owner", mission_id, task_id, body.url)

@router.post("/api/v1/control/agent-missions/{mission_id}/tasks/{task_id}/browser/capture")
def owner_capture(mission_id: str, task_id: str, body: CaptureRequest):
    return execute(browser.capture, "owner", mission_id, task_id, body.url)

@router.post("/api/v1/control/agent-missions/{mission_id}/tasks/{task_id}/browser/revoke")
def owner_revoke(mission_id: str, task_id: str):
    return execute(browser.revoke, "owner", mission_id, task_id)
