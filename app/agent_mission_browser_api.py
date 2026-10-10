"""Owner and paired-app routes for supervised worker browser evidence."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StrictInt, StrictBool

from .agent_mission_api import authorized, execute
from .services import agent_mission_browser as browser
from .services import agent_mission_live_browser as live
from .services import agent_mission_browser_actions as dom_actions
from .services import agent_mission_browser_takeover as takeover
from .services import agent_mission_browser_plans as plans
from .database import db
from .services import agent_mission_runtime as mission

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


class OwnerLease(BaseModel):
    lease_id: str = Field(min_length=36,max_length=36)

class ManualControl(OwnerLease):
    request_id: str = Field(min_length=36,max_length=36)
    index: int
    fingerprint: str = Field(min_length=24,max_length=24)
    kind: str = Field(min_length=4,max_length=12)
    value: str | int | bool
    confirmed: bool

class SearchReview(OwnerLease):
    index: int
    fingerprint: str = Field(min_length=24,max_length=24)

class SearchApproval(OwnerLease):
    proposal_id: str = Field(min_length=36,max_length=36)
    confirmed: bool

@router.post("/api/v1/agent-missions/{mission_id}/tasks/{task_id}/live/owner/takeover")
def app_owner_takeover(mission_id: str,task_id: str,identity: dict=Depends(authorized)):
    return execute(takeover.acquire,"app:"+identity["app_key"],mission_id,task_id)

@router.post("/api/v1/agent-missions/{mission_id}/tasks/{task_id}/live/owner/release")
def app_owner_release(mission_id: str,task_id: str,body: OwnerLease,identity: dict=Depends(authorized)):
    return execute(takeover.release,"app:"+identity["app_key"],mission_id,task_id,lease_id=body.lease_id)

@router.post("/api/v1/agent-missions/{mission_id}/tasks/{task_id}/live/owner/control")
def app_owner_control(mission_id: str,task_id: str,body: ManualControl,
                      identity: dict=Depends(authorized)):
    if body.confirmed is not True: raise HTTPException(status_code=422,detail="Owner confirmation required.")
    return execute(takeover.manual,"app:"+identity["app_key"],mission_id,task_id,
                   index=body.index,fingerprint=body.fingerprint,kind=body.kind,value=body.value,request_id=body.request_id,lease_id=body.lease_id)

@router.post("/api/v1/agent-missions/{mission_id}/tasks/{task_id}/live/owner/search/review")
def app_search_review(mission_id: str,task_id: str,body: SearchReview,
                      identity: dict=Depends(authorized)):
    return execute(takeover.review_search,"app:"+identity["app_key"],mission_id,task_id,
                   index=body.index,fingerprint=body.fingerprint,lease_id=body.lease_id)

@router.post("/api/v1/agent-missions/{mission_id}/tasks/{task_id}/live/owner/search/submit")
def app_search_submit(mission_id: str,task_id: str,body: SearchApproval,
                      identity: dict=Depends(authorized)):
    if body.confirmed is not True: raise HTTPException(status_code=422,detail="Confirm exact GET search.")
    return execute(takeover.submit_search,"app:"+identity["app_key"],mission_id,task_id,
                   proposal_id=body.proposal_id,lease_id=body.lease_id)

@router.post("/api/v1/control/agent-missions/{mission_id}/tasks/{task_id}/live/owner/takeover")
def local_owner_takeover(mission_id: str,task_id: str):
    return execute(takeover.acquire,"owner",mission_id,task_id)

@router.post("/api/v1/control/agent-missions/{mission_id}/tasks/{task_id}/live/owner/release")
def local_owner_release(mission_id: str,task_id: str,body: OwnerLease):
    return execute(takeover.release,"owner",mission_id,task_id,lease_id=body.lease_id)

@router.post("/api/v1/control/agent-missions/{mission_id}/tasks/{task_id}/live/owner/control")
def local_owner_control(mission_id: str,task_id: str,body: ManualControl):
    if body.confirmed is not True: raise HTTPException(status_code=422,detail="Owner confirmation required.")
    return execute(takeover.manual,"owner",mission_id,task_id,
                   index=body.index,fingerprint=body.fingerprint,kind=body.kind,value=body.value,request_id=body.request_id,lease_id=body.lease_id)

@router.post("/api/v1/control/agent-missions/{mission_id}/tasks/{task_id}/live/owner/search/review")
def local_search_review(mission_id: str,task_id: str,body: SearchReview):
    return execute(takeover.review_search,"owner",mission_id,task_id,
                   index=body.index,fingerprint=body.fingerprint,lease_id=body.lease_id)

@router.post("/api/v1/control/agent-missions/{mission_id}/tasks/{task_id}/live/owner/search/submit")
def local_search_submit(mission_id: str,task_id: str,body: SearchApproval):
    if body.confirmed is not True: raise HTTPException(status_code=422,detail="Confirm exact GET search.")
    return execute(takeover.submit_search,"owner",mission_id,task_id,
                   proposal_id=body.proposal_id,lease_id=body.lease_id)


class BrowserPlan(BaseModel):
    request_id: str = Field(min_length=36,max_length=36)
    confirmed: bool

@router.post("/api/v1/agent-missions/{mission_id}/tasks/{task_id}/live/plan")
def app_browser_plan(mission_id: str,task_id: str,body: BrowserPlan,identity: dict=Depends(authorized)):
    execute(plans.run,"app:"+identity["app_key"],mission_id,task_id,request_id=body.request_id,confirmed=body.confirmed)
    return execute(live.get,"app:"+identity["app_key"],mission_id,task_id)

@router.post("/api/v1/control/agent-missions/{mission_id}/tasks/{task_id}/live/plan")
def owner_browser_plan(mission_id: str,task_id: str,body: BrowserPlan):
    execute(plans.run,"owner",mission_id,task_id,request_id=body.request_id,confirmed=body.confirmed)
    return execute(live.get,"owner",mission_id,task_id)


class OwnerWorkspaceOperation(BaseModel):
    action: str
    mission_id: str | None = None
    task_id: str | None = None
    index: int | None = None
    fingerprint: str | None = None
    kind: str | None = None
    value: str | int | bool | None = None
    request_id: str | None = None
    lease_id: str | None = None
    proposal_id: str | None = None
    url: str | None = None
    confirmed: StrictBool = False
    assignments: dict | None = None
    expected_revision: StrictInt | None = None
    allow_reexecution: StrictBool = False
    action_id: str | None = None
    expected_hash: str | None = None
    decision: str | None = None
    objective: str | None = Field(default=None, max_length=4000)
    conversation_id: str | None = Field(default=None, max_length=160)
    parent_agent_id: StrictInt | None = None
    schedule_id: str | None = None
    timing: dict | None = None


@router.post('/api/v1/control/agent-browser-workspaces')
def owner_workspace(body: OwnerWorkspaceOperation):
    # OwnerGateway protects this local route. Resolve the actual source here;
    # never elevate a paired app's permission when operating its workspace.
    if body.action.startswith('schedule.'):
        from .services import agent_mission_schedules as schedules
        if body.action == 'schedule.list': return {'ok':True,'schedules':execute(schedules.owner_list)}
        if body.action == 'schedule.create':
            with db() as conn:
                row=conn.execute('SELECT source_app_key FROM agent_missions_v1 WHERE id=?',(body.mission_id,)).fetchone()
            if not row: raise HTTPException(404,'Mission not found.')
            result=execute(schedules.create,row['source_app_key'],body.mission_id,body.timing,request_id=body.request_id,expected_revision=body.expected_revision,confirmed=body.confirmed)
        elif body.action in ('schedule.pause','schedule.resume','schedule.cancel'):
            with db() as conn:
                row=conn.execute('SELECT source_app_key FROM agent_mission_schedules_v1 WHERE id=?',(body.schedule_id,)).fetchone()
            if not row: raise HTTPException(404,'Schedule not found.')
            result=execute(schedules.change,row['source_app_key'],body.schedule_id,body.action.split('.')[1],request_id=body.request_id,expected_revision=body.expected_revision,confirmed=body.confirmed,local_owner=True)
        else: raise HTTPException(422,'Unsupported schedule operation.')
        return {'ok':True,'schedules':[result]}
    if body.action=='task.prepare':
        from .services import agent_mission_chat_tasks as chat_tasks
        return {'ok':True,'mission':execute(chat_tasks.owner_prepare,objective=body.objective,
            request_id=body.request_id,conversation_id=body.conversation_id,parent_agent_id=body.parent_agent_id)}
    if body.action=='list':
        with db() as conn:
            rows=conn.execute('SELECT id,source_app_key FROM agent_missions_v1 ORDER BY created_at DESC,id DESC LIMIT 8').fetchall()
        return {'ok':True,'items':[execute(mission.get_mission,row['source_app_key'],row['id']) for row in rows]}
    with db() as conn:
        row=conn.execute('SELECT source_app_key FROM agent_missions_v1 WHERE id=?',(body.mission_id,)).fetchone()
    if not row: raise HTTPException(404,'Mission not found.')
    source=row['source_app_key'];mid=body.mission_id;tid=body.task_id
    from .services import agent_mission_actions as changes
    if body.action=='actions.list': return {'ok':True,'actions':execute(changes.list_actions,source,mid)}
    if body.action=='actions.review': return {'ok':True,'actions':execute(changes.review,source,mid,body.action_id,expected_hash=body.expected_hash,decision=body.decision,request_id=body.request_id,confirmed=body.confirmed,local_owner=True)}
    if body.action=='actions.recover':
        from .services import agent_mission_outcomes as outcomes
        return {'ok':True,'actions':execute(outcomes.recover,source,mid,body.action_id,expected_hash=body.expected_hash,request_id=body.request_id,confirmed=body.confirmed,local_owner=True)}
    if body.action=='get': return {'ok':True,'mission':execute(mission.get_mission,source,mid)}
    from .services import agent_mission_tool_contracts as contracts, agent_mission_orchestration as orchestration
    from .services import agent_mission_control as control
    if body.action=='tools.get': return {'ok':True,'tools':execute(contracts.get,source,mid)}
    if body.action=='tools.status': return {'ok':True,'orchestration':execute(orchestration.status,source,mid)}
    if body.action=='tools.configure':
        return {'ok':True,'tools':execute(contracts.configure,source,mid,body.assignments,request_id=body.request_id,expected_revision=body.expected_revision,confirmed=body.confirmed)}
    if body.action=='tools.start':
        return {'ok':True,'mission':execute(orchestration.start,source,mid,request_id=body.request_id,expected_revision=body.expected_revision,confirmed=body.confirmed)}
    lifecycle={'start':mission.start_mission,'cancel':mission.cancel_mission,'pause':control.pause}
    if body.action in lifecycle: return {'ok':True,'mission':execute(lifecycle[body.action],source,mid)}
    if body.action=='resume': return {'ok':True,'mission':execute(control.resume,source,mid,allow_reexecution=body.allow_reexecution)}
    if body.action=='retry': return {'ok':True,'mission':execute(control.retry,source,mid,tid)}
    if not tid: raise HTTPException(422,'Worker ID required.')
    mapping={'browser.get':browser.inspect,'browser.revoke':browser.revoke,
             'browser.live.get':live.get,'browser.live.start':live.start,
             'browser.live.refresh':live.refresh,'browser.live.propose':live.propose,
             'browser.live.stop':live.stop,'browser.owner.takeover':takeover.acquire,
             'browser.action.propose':dom_actions.suggest}
    if body.action in mapping:
        result=execute(mapping[body.action],source,mid,tid)
    elif body.action=='browser.grant': result=execute(browser.authorize,source,mid,tid,body.url)
    elif body.action=='browser.capture': result=execute(browser.capture,source,mid,tid,body.url)
    elif body.action=='browser.owner.search.review':
        if not body.lease_id: raise HTTPException(422,'Owner lease required.')
        result=execute(takeover.review_search,source,mid,tid,index=body.index,fingerprint=body.fingerprint,lease_id=body.lease_id)
    elif body.action=='browser.owner.release':
        if not body.lease_id: raise HTTPException(422,'Owner lease required.')
        result=execute(takeover.release,source,mid,tid,lease_id=body.lease_id)
    elif body.action in {'browser.owner.control','browser.owner.search.submit','browser.live.plan','browser.live.approve','browser.action.approve'}:
        if body.confirmed is not True: raise HTTPException(422,'Explicit confirmation required.')
        if body.action.startswith('browser.owner.') and not body.lease_id: raise HTTPException(422,'Owner lease required.')
        if body.action=='browser.owner.control':
            if not body.request_id: raise HTTPException(422,'Operation ID required.')
            result=execute(takeover.manual,source,mid,tid,index=body.index,fingerprint=body.fingerprint,kind=body.kind,value=body.value,request_id=body.request_id,lease_id=body.lease_id)
        elif body.action=='browser.owner.search.submit': result=execute(takeover.submit_search,source,mid,tid,proposal_id=body.proposal_id,lease_id=body.lease_id)
        elif body.action=='browser.live.plan':
            if not body.request_id: raise HTTPException(422,'Plan operation ID required.')
            execute(plans.run,source,mid,tid,request_id=body.request_id,confirmed=True)
            result=execute(live.get,source,mid,tid)
        elif body.action=='browser.live.approve': result=execute(live.approve_navigation,source,mid,tid,body.proposal_id)
        else: result=execute(dom_actions.approve,source,mid,tid,body.proposal_id,value=body.value)
    else: raise HTTPException(422,'Unsupported browser workspace action.')
    key='browser' if body.action in {'browser.get','browser.grant','browser.capture','browser.revoke'} else 'live_browser'
    return {'ok':True,key:result}
