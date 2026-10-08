"""Owner and paired-app routes for supervised worker browser evidence."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .agent_mission_api import authorized, execute
from .services import agent_mission_browser as browser
from .services import agent_mission_live_browser as live
from .services import agent_mission_browser_actions as dom_actions
from .services import agent_mission_browser_takeover as takeover

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


class ApproveAction(BaseModel):
    proposal_id: str = Field(min_length=36,max_length=36)
    value: str | int | bool
    confirmed: bool


@router.post("/api/v1/agent-missions/{mission_id}/tasks/{task_id}/live/actions/propose")
def app_action_propose(mission_id: str,task_id: str,identity: dict=Depends(authorized)):
    return execute(dom_actions.suggest,"app:"+identity["app_key"],mission_id,task_id)


@router.post("/api/v1/agent-missions/{mission_id}/tasks/{task_id}/live/actions/approve")
def app_action_approve(mission_id: str,task_id: str,body: ApproveAction,
                       identity: dict=Depends(authorized)):
    if body.confirmed is not True:
        raise HTTPException(status_code=422, detail="Explicit confirmation required.")
    return execute(dom_actions.approve,"app:"+identity["app_key"],mission_id,task_id,
                   body.proposal_id,value=body.value)


@router.post("/api/v1/control/agent-missions/{mission_id}/tasks/{task_id}/live/actions/propose")
def owner_action_propose(mission_id: str,task_id: str):
    return execute(dom_actions.suggest,"owner",mission_id,task_id)


@router.post("/api/v1/control/agent-missions/{mission_id}/tasks/{task_id}/live/actions/approve")
def owner_action_approve(mission_id: str,task_id: str,body: ApproveAction):
    if body.confirmed is not True:
        raise HTTPException(status_code=422, detail="Explicit confirmation required.")
    return execute(dom_actions.approve,"owner",mission_id,task_id,
                   body.proposal_id,value=body.value)


class ManualControl(BaseModel):
    index: int
    fingerprint: str = Field(min_length=24,max_length=24)
    kind: str = Field(min_length=4,max_length=12)
    value: str | int | bool
    confirmed: bool

class SearchReview(BaseModel):
    index: int
    fingerprint: str = Field(min_length=24,max_length=24)

class SearchApproval(BaseModel):
    proposal_id: str = Field(min_length=36,max_length=36)
    confirmed: bool

@router.post("/api/v1/agent-missions/{mission_id}/tasks/{task_id}/live/owner/takeover")
def app_owner_takeover(mission_id: str,task_id: str,identity: dict=Depends(authorized)):
    return execute(takeover.acquire,"app:"+identity["app_key"],mission_id,task_id)

@router.post("/api/v1/agent-missions/{mission_id}/tasks/{task_id}/live/owner/release")
def app_owner_release(mission_id: str,task_id: str,identity: dict=Depends(authorized)):
    return execute(takeover.release,"app:"+identity["app_key"],mission_id,task_id)

@router.post("/api/v1/agent-missions/{mission_id}/tasks/{task_id}/live/owner/control")
def app_owner_control(mission_id: str,task_id: str,body: ManualControl,
                      identity: dict=Depends(authorized)):
    if body.confirmed is not True: raise HTTPException(status_code=422,detail="Owner confirmation required.")
    return execute(takeover.manual,"app:"+identity["app_key"],mission_id,task_id,
                   index=body.index,fingerprint=body.fingerprint,kind=body.kind,value=body.value)

@router.post("/api/v1/agent-missions/{mission_id}/tasks/{task_id}/live/owner/search/review")
def app_search_review(mission_id: str,task_id: str,body: SearchReview,
                      identity: dict=Depends(authorized)):
    return execute(takeover.review_search,"app:"+identity["app_key"],mission_id,task_id,
                   index=body.index,fingerprint=body.fingerprint)

@router.post("/api/v1/agent-missions/{mission_id}/tasks/{task_id}/live/owner/search/submit")
def app_search_submit(mission_id: str,task_id: str,body: SearchApproval,
                      identity: dict=Depends(authorized)):
    if body.confirmed is not True: raise HTTPException(status_code=422,detail="Confirm exact GET search.")
    return execute(takeover.submit_search,"app:"+identity["app_key"],mission_id,task_id,
                   proposal_id=body.proposal_id)

@router.post("/api/v1/control/agent-missions/{mission_id}/tasks/{task_id}/live/owner/takeover")
def local_owner_takeover(mission_id: str,task_id: str):
    return execute(takeover.acquire,"owner",mission_id,task_id)

@router.post("/api/v1/control/agent-missions/{mission_id}/tasks/{task_id}/live/owner/release")
def local_owner_release(mission_id: str,task_id: str):
    return execute(takeover.release,"owner",mission_id,task_id)

@router.post("/api/v1/control/agent-missions/{mission_id}/tasks/{task_id}/live/owner/control")
def local_owner_control(mission_id: str,task_id: str,body: ManualControl):
    if body.confirmed is not True: raise HTTPException(status_code=422,detail="Owner confirmation required.")
    return execute(takeover.manual,"owner",mission_id,task_id,
                   index=body.index,fingerprint=body.fingerprint,kind=body.kind,value=body.value)

@router.post("/api/v1/control/agent-missions/{mission_id}/tasks/{task_id}/live/owner/search/review")
def local_search_review(mission_id: str,task_id: str,body: SearchReview):
    return execute(takeover.review_search,"owner",mission_id,task_id,
                   index=body.index,fingerprint=body.fingerprint)

@router.post("/api/v1/control/agent-missions/{mission_id}/tasks/{task_id}/live/owner/search/submit")
def local_search_submit(mission_id: str,task_id: str,body: SearchApproval):
    if body.confirmed is not True: raise HTTPException(status_code=422,detail="Confirm exact GET search.")
    return execute(takeover.submit_search,"owner",mission_id,task_id,
                   proposal_id=body.proposal_id)
