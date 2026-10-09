"""Authenticated A5C capability-contract endpoints; owner gateway protects control."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from .agent_mission_api import authorized,execute
from .services import agent_mission_tool_contracts as contracts
from .services import agent_mission_orchestration as orchestration

router=APIRouter()

class AssignTools(BaseModel):
    model_config=ConfigDict(extra='forbid')
    assignments:dict
    request_id:str=Field(min_length=36,max_length=36)
    expected_revision:int=Field(ge=0,strict=True)
    confirmed:bool=Field(strict=True)

@router.get('/api/v1/agent-missions/{mission_id}/tools')
def app_tools(mission_id:str,identity:dict=Depends(authorized)):
    return execute(contracts.get,'app:'+identity['app_key'],mission_id)

@router.post('/api/v1/agent-missions/{mission_id}/tools')
def app_assign(mission_id:str,body:AssignTools,identity:dict=Depends(authorized)):
    return execute(contracts.configure,'app:'+identity['app_key'],mission_id,body.assignments,request_id=body.request_id,expected_revision=body.expected_revision,confirmed=body.confirmed)

@router.get('/api/v1/control/agent-missions/{mission_id}/tools')
def owner_tools(mission_id:str):
    return execute(contracts.get,'owner',mission_id)

@router.post('/api/v1/control/agent-missions/{mission_id}/tools')
def owner_assign(mission_id:str,body:AssignTools):
    return execute(contracts.configure,'owner',mission_id,body.assignments,request_id=body.request_id,expected_revision=body.expected_revision,confirmed=body.confirmed)

class StartTools(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:str=Field(min_length=36,max_length=36)
    expected_revision:int=Field(ge=1,strict=True)
    confirmed:bool=Field(strict=True)

@router.post('/api/v1/agent-missions/{mission_id}/tools/start')
def app_start(mission_id:str,body:StartTools,identity:dict=Depends(authorized)):
    return execute(orchestration.start,'app:'+identity['app_key'],mission_id,request_id=body.request_id,expected_revision=body.expected_revision,confirmed=body.confirmed)

@router.post('/api/v1/control/agent-missions/{mission_id}/tools/start')
def owner_start(mission_id:str,body:StartTools):
    return execute(orchestration.start,'owner',mission_id,request_id=body.request_id,expected_revision=body.expected_revision,confirmed=body.confirmed)

@router.get('/api/v1/agent-missions/{mission_id}/tools/status')
def app_status(mission_id:str,identity:dict=Depends(authorized)):
    return execute(orchestration.status,'app:'+identity['app_key'],mission_id)

@router.get('/api/v1/control/agent-missions/{mission_id}/tools/status')
def owner_status(mission_id:str):
    return execute(orchestration.status,'owner',mission_id)

class ReviewChange(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action_id:str=Field(min_length=36,max_length=36)
    expected_hash:str=Field(pattern='^[0-9a-f]{64}$')
    decision:str=Field(pattern='^(approve|deny)$')
    request_id:str=Field(min_length=36,max_length=36)
    confirmed:bool=Field(strict=True)

@router.get('/api/v1/agent-missions/{mission_id}/actions')
def app_changes(mission_id:str,identity:dict=Depends(authorized)):
    from .services import agent_mission_actions as changes
    return execute(changes.list_actions,'app:'+identity['app_key'],mission_id)

@router.post('/api/v1/agent-missions/{mission_id}/actions/review')
def app_review_change(mission_id:str,body:ReviewChange,identity:dict=Depends(authorized)):
    from .services import agent_mission_actions as changes
    return execute(changes.review,'app:'+identity['app_key'],mission_id,body.action_id,expected_hash=body.expected_hash,decision=body.decision,request_id=body.request_id,confirmed=body.confirmed)
