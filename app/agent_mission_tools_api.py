"""Authenticated A5C capability-contract endpoints; owner gateway protects control."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from .agent_mission_api import authorized,execute
from .services import agent_mission_tool_contracts as contracts

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
