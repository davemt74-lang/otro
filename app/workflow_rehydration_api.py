from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from .services import agent_workflow_rehydration
from .services.pairing import authenticate

router = APIRouter()


class WorkflowRehydrateRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=200)
    plan_id: int = Field(ge=1)


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


def _rehydrate(
    source: str,
    payload: WorkflowRehydrateRequest,
    *,
    owner: bool,
    permissions: set[str],
) -> dict:
    try:
        return agent_workflow_rehydration.rehydrate_workflow(
            source,
            payload.conversation_id,
            payload.plan_id,
            owner=owner,
            current_permissions=permissions,
        )
    except agent_workflow_rehydration.AgentWorkflowRehydrationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/api/v1/agent-workflows/rehydrate")
def client_workflow_rehydrate(
    payload: WorkflowRehydrateRequest,
    identity: dict = Depends(_require_chat),
) -> dict:
    return _rehydrate(
        _app_source(identity),
        payload,
        owner=False,
        permissions=set(identity["permissions"]),
    )


@router.post("/api/v1/control/agent-workflows/rehydrate")
def control_workflow_rehydrate(payload: WorkflowRehydrateRequest) -> dict:
    return _rehydrate("owner", payload, owner=True, permissions=set())


@router.get("/api/v1/agent-workflows/rehydrate/capability")
def client_workflow_rehydrate_capability(identity: dict = Depends(_require_chat)) -> dict:
    return {
        "version": agent_workflow_rehydration.AGENT_WORKFLOW_REHYDRATION_VERSION,
        "persistent_checkpoint": True,
        "idempotent": True,
        "drift_detection": True,
        "canonical_plan_run_state": True,
        "auto_execute": False,
        "explicit_actions_preserved": True,
        "paired_app_scoped": True,
    }


@router.get("/api/v1/control/agent-workflows/rehydrate/capability")
def control_workflow_rehydrate_capability() -> dict:
    return {
        "version": agent_workflow_rehydration.AGENT_WORKFLOW_REHYDRATION_VERSION,
        "persistent_checkpoint": True,
        "idempotent": True,
        "drift_detection": True,
        "canonical_plan_run_state": True,
        "auto_execute": False,
        "explicit_actions_preserved": True,
        "paired_app_scoped": True,
    }
