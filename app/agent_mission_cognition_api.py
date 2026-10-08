"""A4 supervised staffing through established paired-app and owner gateways."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from .agent_mission_api import authorized, execute
from .services import agent_mission_cognition as cognition

router = APIRouter()

class ReviewRequest(BaseModel):
    client_request_id: str = Field(min_length=8, max_length=128)

class DecisionRequest(BaseModel):
    approve: bool

@router.post("/api/v1/agent-missions/{mission_id}/supervisor/evaluate")
def evaluate(mission_id: str, body: ReviewRequest, identity: dict = Depends(authorized)):
    return execute(cognition.propose, "app:" + identity["app_key"], mission_id, body.client_request_id)

@router.get("/api/v1/agent-missions/{mission_id}/supervisor/decisions")
def decisions(mission_id: str, identity: dict = Depends(authorized)):
    return execute(cognition.list_decisions, "app:" + identity["app_key"], mission_id)

@router.post("/api/v1/agent-missions/{mission_id}/supervisor/decisions/{decision_id}")
def decide(mission_id: str, decision_id: str, body: DecisionRequest,
           identity: dict = Depends(authorized)):
    return execute(cognition.decide, "app:" + identity["app_key"], mission_id,
                   decision_id, approve=body.approve)

@router.post("/api/v1/control/agent-missions/{mission_id}/supervisor/evaluate")
def owner_evaluate(mission_id: str, body: ReviewRequest):
    return execute(cognition.propose, "owner", mission_id, body.client_request_id)

@router.get("/api/v1/control/agent-missions/{mission_id}/supervisor/decisions")
def owner_decisions(mission_id: str):
    return execute(cognition.list_decisions, "owner", mission_id)

@router.post("/api/v1/control/agent-missions/{mission_id}/supervisor/decisions/{decision_id}")
def owner_decide(mission_id: str, decision_id: str, body: DecisionRequest):
    return execute(cognition.decide, "owner", mission_id, decision_id, approve=body.approve)
