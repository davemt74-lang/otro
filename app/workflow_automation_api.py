from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .services import agent_workflow_automation
from .services.pairing import authenticate


@asynccontextmanager
async def workflow_automation_lifespan(_):
    agent_workflow_automation.scheduler.start()
    try:
        yield
    finally:
        agent_workflow_automation.scheduler.stop()


router = APIRouter(lifespan=workflow_automation_lifespan)


class WorkflowAutomationCreate(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=200)
    plan_id: int = Field(ge=1)
    trigger_type: str = Field(min_length=1, max_length=20)
    run_at: str | None = Field(default=None, max_length=80)
    every_seconds: int | None = Field(default=None, ge=1, le=agent_workflow_automation.MAX_INTERVAL_SECONDS)
    activity_action: str | None = Field(default=None, max_length=160)
    resource_type: str | None = Field(default=None, max_length=120)
    resource_key: str | None = Field(default=None, max_length=240)
    max_steps: int = Field(
        default=agent_workflow_automation.agent_workflow_supervision.MAX_SUPERVISED_STEPS,
        ge=1,
        le=agent_workflow_automation.agent_workflow_supervision.MAX_SUPERVISED_STEPS,
    )


class WorkflowAutomationUpdate(BaseModel):
    enabled: bool


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


def _raise(exc: agent_workflow_automation.AgentWorkflowAutomationError):
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _create(source: str, payload: WorkflowAutomationCreate, *, owner: bool, permissions: set[str]) -> dict:
    try:
        return agent_workflow_automation.create_automation(
            source,
            payload.conversation_id,
            payload.plan_id,
            trigger_type=payload.trigger_type,
            run_at=payload.run_at,
            every_seconds=payload.every_seconds,
            activity_action=payload.activity_action,
            resource_type=payload.resource_type,
            resource_key=payload.resource_key,
            max_steps=payload.max_steps,
            owner=owner,
            current_permissions=permissions,
        )
    except agent_workflow_automation.AgentWorkflowAutomationError as exc:
        _raise(exc)


def _capability() -> dict:
    return {
        "version": agent_workflow_automation.AGENT_WORKFLOW_AUTOMATION_VERSION,
        "requires_explicit_creation": True,
        "trigger_types": ["once", "interval", "activity"],
        "minimum_interval_seconds": agent_workflow_automation.MIN_INTERVAL_SECONDS,
        "max_steps": agent_workflow_automation.agent_workflow_supervision.MAX_SUPERVISED_STEPS,
        "exact_activity_actions": True,
        "safe_activity_prefixes": list(agent_workflow_automation.SAFE_ACTIVITY_PREFIXES),
        "durable_claim": True,
        "restart_resumable": True,
        "canonical_revalidation_at_fire": True,
        "auto_approval": False,
        "auto_retry": False,
        "auto_parent_chat": False,
        "nested_delegation": False,
        "paired_app_scoped": True,
        "uses_supervision": agent_workflow_automation.agent_workflow_supervision.AGENT_WORKFLOW_SUPERVISION_VERSION,
        "uses_rehydration": agent_workflow_automation.agent_workflow_rehydration.AGENT_WORKFLOW_REHYDRATION_VERSION,
    }


@router.get("/api/v1/agent-workflows/automations/capability")
def client_workflow_automation_capability(identity: dict = Depends(_require_chat)) -> dict:
    return _capability()


@router.get("/api/v1/control/agent-workflows/automations/capability")
def control_workflow_automation_capability() -> dict:
    return _capability()


@router.get("/api/v1/agent-workflows/automations")
def client_workflow_automations(
    conversation_id: str | None = Query(default=None, max_length=200),
    identity: dict = Depends(_require_chat),
) -> dict:
    return {
        "version": agent_workflow_automation.AGENT_WORKFLOW_AUTOMATION_VERSION,
        "items": agent_workflow_automation.list_automations(_app_source(identity), conversation_id=conversation_id),
        "app": identity["app_key"],
    }


@router.get("/api/v1/control/agent-workflows/automations")
def control_workflow_automations(conversation_id: str | None = Query(default=None, max_length=200)) -> dict:
    return {
        "version": agent_workflow_automation.AGENT_WORKFLOW_AUTOMATION_VERSION,
        "items": agent_workflow_automation.list_automations("owner", conversation_id=conversation_id),
    }


@router.post("/api/v1/agent-workflows/automations")
def client_workflow_automation_create(
    payload: WorkflowAutomationCreate,
    identity: dict = Depends(_require_chat),
) -> dict:
    return _create(
        _app_source(identity),
        payload,
        owner=False,
        permissions=set(identity["permissions"]),
    )


@router.post("/api/v1/control/agent-workflows/automations")
def control_workflow_automation_create(payload: WorkflowAutomationCreate) -> dict:
    return _create("owner", payload, owner=True, permissions=set())


@router.patch("/api/v1/agent-workflows/automations/{automation_id}")
def client_workflow_automation_update(
    automation_id: int,
    payload: WorkflowAutomationUpdate,
    identity: dict = Depends(_require_chat),
) -> dict:
    try:
        return agent_workflow_automation.set_automation_enabled(
            _app_source(identity), automation_id, payload.enabled
        )
    except agent_workflow_automation.AgentWorkflowAutomationError as exc:
        _raise(exc)


@router.patch("/api/v1/control/agent-workflows/automations/{automation_id}")
def control_workflow_automation_update(automation_id: int, payload: WorkflowAutomationUpdate) -> dict:
    try:
        return agent_workflow_automation.set_automation_enabled("owner", automation_id, payload.enabled)
    except agent_workflow_automation.AgentWorkflowAutomationError as exc:
        _raise(exc)


@router.delete("/api/v1/agent-workflows/automations/{automation_id}")
def client_workflow_automation_delete(automation_id: int, identity: dict = Depends(_require_chat)) -> dict:
    if not agent_workflow_automation.delete_automation(_app_source(identity), automation_id):
        raise HTTPException(status_code=404, detail="Workflow automation not found")
    return {"deleted": True}


@router.delete("/api/v1/control/agent-workflows/automations/{automation_id}")
def control_workflow_automation_delete(automation_id: int) -> dict:
    if not agent_workflow_automation.delete_automation("owner", automation_id):
        raise HTTPException(status_code=404, detail="Workflow automation not found")
    return {"deleted": True}


@router.get("/api/v1/agent-workflows/automations/{automation_id}/runs")
def client_workflow_automation_runs(
    automation_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    identity: dict = Depends(_require_chat),
) -> dict:
    try:
        return {
            "items": agent_workflow_automation.list_automation_runs(
                _app_source(identity), automation_id, limit=limit
            )
        }
    except agent_workflow_automation.AgentWorkflowAutomationError as exc:
        _raise(exc)


@router.get("/api/v1/control/agent-workflows/automations/{automation_id}/runs")
def control_workflow_automation_runs(
    automation_id: int,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    try:
        return {"items": agent_workflow_automation.list_automation_runs("owner", automation_id, limit=limit)}
    except agent_workflow_automation.AgentWorkflowAutomationError as exc:
        _raise(exc)