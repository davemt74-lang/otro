from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from .services import agent_workflow_supervision
from .services.pairing import authenticate
from .workflow_automation_api import router as workflow_automation_router

router = APIRouter()


class WorkflowSuperviseRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=200)
    plan_id: int = Field(ge=1)
    rehydration_id: int = Field(ge=1)
    state_fingerprint: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")
    max_steps: int = Field(default=agent_workflow_supervision.MAX_SUPERVISED_STEPS, ge=1, le=agent_workflow_supervision.MAX_SUPERVISED_STEPS)


def _current_app(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    identity = authenticate(authorization.removeprefix("Bearer ").strip())
    if identity is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked token")
    return identity


def _require_chat(identity: dict = Depends(_current_app)) -> dict:
    if "agent.chat" not in identity["permissions"]:
        raise HTTPException(status_code=403, detail="Permission required: agent.chat")
    return identity


def _app_source(identity: dict) -> str:
    return f"app:{identity['app_key']}"


def _supervise(
    source: str,
    payload: WorkflowSuperviseRequest,
    *,
    owner: bool,
    permissions: set[str],
) -> dict:
    try:
        return agent_workflow_supervision.continue_workflow(
            source,
            payload.conversation_id,
            payload.plan_id,
            payload.rehydration_id,
            payload.state_fingerprint,
            owner=owner,
            current_permissions=permissions,
            max_steps=payload.max_steps,
        )
    except agent_workflow_supervision.AgentWorkflowSupervisionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/api/v1/agent-workflows/supervise")
def client_workflow_supervise(
    payload: WorkflowSuperviseRequest,
    identity: dict = Depends(_require_chat),
) -> dict:
    return _supervise(
        _app_source(identity),
        payload,
        owner=False,
        permissions=set(identity["permissions"]),
    )


@router.post("/api/v1/control/agent-workflows/supervise")
def control_workflow_supervise(payload: WorkflowSuperviseRequest) -> dict:
    return _supervise("owner", payload, owner=True, permissions=set())


def _capability() -> dict:
    return {
        "version": agent_workflow_supervision.AGENT_WORKFLOW_SUPERVISION_VERSION,
        "requires_explicit_start": True,
        "bounded": True,
        "max_steps": agent_workflow_supervision.MAX_SUPERVISED_STEPS,
        "safe_actions": ["run", "prepare"],
        "auto_approval": False,
        "auto_retry": False,
        "auto_parent_chat": False,
        "canonical_revalidation_each_step": True,
        "idempotent_checkpoint_claim": True,
        "paired_app_scoped": True,
        "requires_rehydration": agent_workflow_supervision.agent_workflow_rehydration.AGENT_WORKFLOW_REHYDRATION_VERSION,
        "actions_via": agent_workflow_supervision.agent_team_orchestration.AGENT_TEAM_ORCHESTRATION_VERSION,
        "automation_extension": "v0.58",
    }


@router.get("/api/v1/agent-workflows/supervise/capability")
def client_workflow_supervise_capability(identity: dict = Depends(_require_chat)) -> dict:
    return _capability()


@router.get("/api/v1/control/agent-workflows/supervise/capability")
def control_workflow_supervise_capability() -> dict:
    return _capability()


# v0.58 is a child of the v0.57 safety kernel. Creating an automation is
# explicit, while every later trigger still rehydrates and enters v0.57 rather
# than calling team orchestration directly.
router.include_router(workflow_automation_router)