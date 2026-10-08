"""Owner and paired-app routes for supervised worker browser evidence."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .agent_mission_api import authorized, execute
from .services import agent_mission_browser as browser
from .services import agent_mission_live_browser as live

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


class ApproveNavigation(BaseModel):
    proposal_id: str = Field(min_length=36, max_length=36)


@router.post("/api/v1/agent-missions/{mission_id}/tasks/{task_id}/live/start")
def app_live_start(mission_id: str, task_id: str, identity: dict = Depends(authorized)):
    return execute(live.start, "app:" + identity["app_key"], mission_id, task_id)


@router.get("/api/v1/agent-missions/{mission_id}/tasks/{task_id}/live")
def app_live_get(mission_id: str, task_id: str, identity: dict = Depends(authorized)):
    return execute(live.get, "app:" + identity["app_key"], mission_id, task_id)


@router.post("/api/v1/agent-missions/{mission_id}/tasks/{task_id}/live/refresh")
def app_live_refresh(mission_id: str, task_id: str, identity: dict = Depends(authorized)):
    return execute(live.refresh, "app:" + identity["app_key"], mission_id, task_id)


@router.post("/api/v1/agent-missions/{mission_id}/tasks/{task_id}/live/propose")
def app_live_propose(mission_id: str, task_id: str, identity: dict = Depends(authorized)):
    return execute(live.propose, "app:" + identity["app_key"], mission_id, task_id)


@router.post("/api/v1/agent-missions/{mission_id}/tasks/{task_id}/live/approve")
def app_live_approve(mission_id: str, task_id: str, body: ApproveNavigation,
                     identity: dict = Depends(authorized)):
    return execute(live.approve_navigation, "app:" + identity["app_key"],
                   mission_id, task_id, body.proposal_id)


@router.post("/api/v1/agent-missions/{mission_id}/tasks/{task_id}/live/stop")
def app_live_stop(mission_id: str, task_id: str, identity: dict = Depends(authorized)):
    return execute(live.stop, "app:" + identity["app_key"], mission_id, task_id)


@router.post("/api/v1/control/agent-missions/{mission_id}/tasks/{task_id}/live/start")
def owner_live_start(mission_id: str, task_id: str):
    return execute(live.start, "owner", mission_id, task_id)


@router.get("/api/v1/control/agent-missions/{mission_id}/tasks/{task_id}/live")
def owner_live_get(mission_id: str, task_id: str):
    return execute(live.get, "owner", mission_id, task_id)


@router.post("/api/v1/control/agent-missions/{mission_id}/tasks/{task_id}/live/refresh")
def owner_live_refresh(mission_id: str, task_id: str):
    return execute(live.refresh, "owner", mission_id, task_id)


@router.post("/api/v1/control/agent-missions/{mission_id}/tasks/{task_id}/live/propose")
def owner_live_propose(mission_id: str, task_id: str):
    return execute(live.propose, "owner", mission_id, task_id)


@router.post("/api/v1/control/agent-missions/{mission_id}/tasks/{task_id}/live/approve")
def owner_live_approve(mission_id: str, task_id: str, body: ApproveNavigation):
    return execute(live.approve_navigation, "owner", mission_id, task_id, body.proposal_id)


@router.post("/api/v1/control/agent-missions/{mission_id}/tasks/{task_id}/live/stop")
def owner_live_stop(mission_id: str, task_id: str):
    return execute(live.stop, "owner", mission_id, task_id)
