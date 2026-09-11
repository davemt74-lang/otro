from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .brain_api import ChatRequest, client_chat
from .services import agent_routing, agent_workflow_tool, agent_workflows, brain, delegation
from .services.pairing import authenticate

router = APIRouter()
agent_workflow_tool.install()


class DelegatedAgent(BaseModel):
    name: str = Field(min_length=1, max_length=190)
    role: str = Field(default="", max_length=80)
    instructions: str = Field(default="", max_length=4000)


class DelegatedMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=6000)


class DelegationRequest(BaseModel):
    message: str = Field(min_length=1, max_length=32000)
    conversation_id: str | None = Field(default=None, max_length=64)
    agent_id: int | None = Field(default=None, ge=1)
    external_conversation_id: str | None = Field(default=None, max_length=160)
    delegation: DelegatedAgent | None = None
    history: list[DelegatedMessage] = Field(default_factory=list, max_length=12)
    surface_context: dict[str, Any] = Field(default_factory=dict)
    include_memory: bool | None = None
    include_knowledge: bool | None = None
    include_contacts: bool | None = None
    cloud_allowed: bool | None = None
    max_context_chars: int | None = Field(default=None, ge=2000, le=24000)


class WorkflowTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_agent_id: int = Field(ge=1)
    worker_agent_id: int = Field(ge=1)
    task: str = Field(min_length=1, max_length=agent_workflows.MAX_TASK_CHARS)
    conversation_id: str | None = Field(default=None, max_length=64)
    external_conversation_id: str | None = Field(default=None, max_length=160)
    include_memory: bool = True
    include_knowledge: bool = True
    include_contacts: bool = False
    cloud_allowed: bool = True
    max_context_chars: int = Field(default=12000, ge=2000, le=24000)


class WorkflowPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    max_context_chars: int = Field(default=12000, ge=2000, le=24000)


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


def _workflow_http_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=int(getattr(exc, "status_code", 422)), detail=str(exc))


def _delegate(payload: DelegationRequest, identity: dict) -> dict:
    if payload.delegation is None:
        raise HTTPException(status_code=422, detail="Delegation metadata is required.")
    external_id = str(payload.external_conversation_id or "").strip()
    if not external_id:
        raise HTTPException(status_code=422, detail="external_conversation_id is required for delegated Agent Chat.")
    permissions = set(identity["permissions"])
    try:
        return delegation.chat(
            _app_source(identity),
            payload.message,
            external_id,
            payload.delegation.model_dump(),
            [item.model_dump() for item in payload.history],
            payload.surface_context,
            agent_id=payload.agent_id,
            include_memory=(payload.include_memory is not False) and "memory.read" in permissions,
            include_knowledge=(payload.include_knowledge is not False) and "knowledge.search" in permissions,
            include_contacts=(payload.include_contacts is not False) and "contacts.read" in permissions,
            cloud_allowed=payload.cloud_allowed is not False,
            max_context_chars=payload.max_context_chars or 12000,
            tool_permissions=permissions,
        )
    except (agent_routing.AgentRoutingError, brain.BrainError) as exc:
        raise _workflow_http_error(exc) from exc


def _create_workflow(
    source: str,
    payload: WorkflowTaskRequest,
    *,
    owner: bool,
    permissions: set[str],
) -> dict:
    try:
        return agent_workflows.create_task(
            source,
            parent_agent_id=payload.parent_agent_id,
            worker_agent_id=payload.worker_agent_id,
            task=payload.task,
            owner=owner,
            permissions=permissions,
            conversation_id=payload.conversation_id,
            external_conversation_id=payload.external_conversation_id,
            include_memory=payload.include_memory and (owner or "memory.read" in permissions),
            include_knowledge=payload.include_knowledge and (owner or "knowledge.search" in permissions),
            include_contacts=payload.include_contacts and (owner or "contacts.read" in permissions),
            cloud_allowed=payload.cloud_allowed,
            max_context_chars=payload.max_context_chars,
            metadata={"created_via": "owner_api" if owner else "paired_app_api"},
        )
    except (agent_workflows.AgentWorkflowError, agent_routing.AgentRoutingError, brain.BrainError) as exc:
        raise _workflow_http_error(exc) from exc


def _run_workflow(source: str, task_id: int, *, owner: bool, permissions: set[str]) -> dict:
    try:
        with agent_workflow_tool.worker_scope():
            return agent_workflows.execute_task(
                task_id,
                source,
                owner=owner,
                current_permissions=permissions,
            )
    except (agent_workflows.AgentWorkflowError, agent_routing.AgentRoutingError, brain.BrainError) as exc:
        raise _workflow_http_error(exc) from exc


@router.post("/api/v1/chat")
def compatible_chat(payload: DelegationRequest, identity: dict = Depends(_require_chat)) -> dict:
    if payload.delegation is not None:
        return _delegate(payload, identity)

    # v0.18-compatible payloads continue through the canonical stateful
    # HomeServer chat implementation. Omitting agent_id still selects primary.
    legacy = ChatRequest(
        message=payload.message,
        conversation_id=payload.conversation_id,
        agent_id=payload.agent_id,
        include_memory=payload.include_memory,
        include_knowledge=payload.include_knowledge,
        include_contacts=payload.include_contacts,
        cloud_allowed=payload.cloud_allowed,
        max_context_chars=payload.max_context_chars,
    )
    return client_chat(legacy, identity)


@router.get("/api/v1/agent-workflows/workers")
def client_workflow_workers(
    parent_agent_id: int = Query(ge=1),
    identity: dict = Depends(_require_chat),
) -> dict:
    try:
        return agent_workflows.available_workers(_app_source(identity), parent_agent_id, owner=False)
    except (agent_workflows.AgentWorkflowError, agent_routing.AgentRoutingError) as exc:
        raise _workflow_http_error(exc) from exc


@router.get("/api/v1/agent-workflows/delegations")
def client_workflow_tasks(
    limit: int = Query(default=50, ge=1, le=100),
    conversation_id: str | None = Query(default=None, max_length=64),
    identity: dict = Depends(_require_chat),
) -> dict:
    return agent_workflows.list_tasks(_app_source(identity), limit=limit, conversation_id=conversation_id)


@router.post("/api/v1/agent-workflows/delegations")
def client_create_workflow(payload: WorkflowTaskRequest, identity: dict = Depends(_require_chat)) -> dict:
    permissions = set(identity["permissions"])
    return _create_workflow(_app_source(identity), payload, owner=False, permissions=permissions)


@router.get("/api/v1/agent-workflows/delegations/{task_id}")
def client_workflow_task(task_id: int, identity: dict = Depends(_require_chat)) -> dict:
    try:
        return agent_workflows.get_task(task_id, _app_source(identity))
    except agent_workflows.AgentWorkflowError as exc:
        raise _workflow_http_error(exc) from exc


@router.post("/api/v1/agent-workflows/delegations/{task_id}/run")
def client_run_workflow(task_id: int, identity: dict = Depends(_require_chat)) -> dict:
    return _run_workflow(
        _app_source(identity),
        task_id,
        owner=False,
        permissions=set(identity["permissions"]),
    )


@router.post("/api/v1/agent-workflows/delegations/{task_id}/cancel")
def client_cancel_workflow(task_id: int, identity: dict = Depends(_require_chat)) -> dict:
    try:
        return agent_workflows.cancel_task(task_id, _app_source(identity))
    except agent_workflows.AgentWorkflowError as exc:
        raise _workflow_http_error(exc) from exc


@router.get("/api/v1/control/agent-workflows/policy")
def control_workflow_policy() -> dict:
    return agent_workflows.get_policy()


@router.put("/api/v1/control/agent-workflows/policy")
def control_save_workflow_policy(payload: WorkflowPolicyRequest) -> dict:
    try:
        return agent_workflows.save_policy(payload.enabled, payload.max_context_chars)
    except agent_workflows.AgentWorkflowError as exc:
        raise _workflow_http_error(exc) from exc


@router.get("/api/v1/control/agent-workflows/workers")
def control_workflow_workers(parent_agent_id: int = Query(ge=1)) -> dict:
    try:
        return agent_workflows.available_workers("owner", parent_agent_id, owner=True)
    except (agent_workflows.AgentWorkflowError, agent_routing.AgentRoutingError) as exc:
        raise _workflow_http_error(exc) from exc


@router.get("/api/v1/control/agent-workflows/delegations")
def control_workflow_tasks(
    limit: int = Query(default=50, ge=1, le=100),
    conversation_id: str | None = Query(default=None, max_length=64),
) -> dict:
    return agent_workflows.list_tasks("owner", limit=limit, conversation_id=conversation_id)


@router.post("/api/v1/control/agent-workflows/delegations")
def control_create_workflow(payload: WorkflowTaskRequest) -> dict:
    return _create_workflow("owner", payload, owner=True, permissions=set())


@router.get("/api/v1/control/agent-workflows/delegations/{task_id}")
def control_workflow_task(task_id: int) -> dict:
    try:
        return agent_workflows.get_task(task_id, "owner")
    except agent_workflows.AgentWorkflowError as exc:
        raise _workflow_http_error(exc) from exc


@router.post("/api/v1/control/agent-workflows/delegations/{task_id}/run")
def control_run_workflow(task_id: int) -> dict:
    return _run_workflow("owner", task_id, owner=True, permissions=set())


@router.post("/api/v1/control/agent-workflows/delegations/{task_id}/cancel")
def control_cancel_workflow(task_id: int) -> dict:
    try:
        return agent_workflows.cancel_task(task_id, "owner")
    except agent_workflows.AgentWorkflowError as exc:
        raise _workflow_http_error(exc) from exc
