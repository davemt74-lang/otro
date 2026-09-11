from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .services import agent_routing, agent_team_planning
from .services.pairing import authenticate

router = APIRouter()


class TeamPlanContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_memory: bool = True
    include_knowledge: bool = True
    include_contacts: bool = False
    cloud_allowed: bool = True
    max_context_chars: int = Field(default=12000, ge=2000, le=24000)


class TeamPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_agent_id: int = Field(ge=1)
    conversation_id: str = Field(min_length=1, max_length=64)
    objective: str = Field(min_length=1, max_length=agent_team_planning.MAX_OBJECTIVE_CHARS)
    context: TeamPlanContextRequest = Field(default_factory=TeamPlanContextRequest)


class TeamPlanMemberEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_agent_id: int = Field(ge=1)
    task: str = Field(min_length=1, max_length=16000)


class TeamPlanEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str = Field(min_length=1, max_length=agent_team_planning.MAX_OBJECTIVE_CHARS)
    members: list[TeamPlanMemberEdit] = Field(
        min_length=agent_team_planning.MIN_PLAN_MEMBERS,
        max_length=agent_team_planning.MAX_PLAN_MEMBERS,
    )


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


def _http_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=int(getattr(exc, "status_code", 422)), detail=str(exc))


def _propose(source: str, payload: TeamPlanRequest, *, owner: bool, permissions: set[str]) -> dict:
    try:
        context = payload.context
        return agent_team_planning.propose_plan(
            source,
            parent_agent_id=payload.parent_agent_id,
            conversation_id=payload.conversation_id,
            objective=payload.objective,
            owner=owner,
            current_permissions=permissions,
            include_memory=context.include_memory,
            include_knowledge=context.include_knowledge,
            include_contacts=context.include_contacts,
            cloud_allowed=context.cloud_allowed,
            max_context_chars=context.max_context_chars,
        )
    except (agent_team_planning.AgentTeamPlanningError, agent_routing.AgentRoutingError) as exc:
        raise _http_error(exc) from exc


def _edit(source: str, plan_id: int, payload: TeamPlanEditRequest, *, owner: bool) -> dict:
    try:
        return agent_team_planning.update_plan(
            plan_id,
            source,
            objective=payload.objective,
            members=[item.model_dump() for item in payload.members],
            owner=owner,
        )
    except (agent_team_planning.AgentTeamPlanningError, agent_routing.AgentRoutingError) as exc:
        raise _http_error(exc) from exc


def _approve(source: str, plan_id: int, *, owner: bool, permissions: set[str]) -> dict:
    try:
        return agent_team_planning.approve_plan(
            plan_id,
            source,
            owner=owner,
            current_permissions=permissions,
        )
    except (agent_team_planning.AgentTeamPlanningError, agent_routing.AgentRoutingError) as exc:
        raise _http_error(exc) from exc


def _reject(source: str, plan_id: int) -> dict:
    try:
        return agent_team_planning.reject_plan(plan_id, source)
    except agent_team_planning.AgentTeamPlanningError as exc:
        raise _http_error(exc) from exc


@router.get("/api/v1/agent-workflows/team-plans")
def client_team_plans(
    conversation_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=20, ge=1, le=50),
    identity: dict = Depends(_require_chat),
) -> dict:
    return agent_team_planning.list_plans(
        _app_source(identity),
        conversation_id=conversation_id,
        limit=limit,
    )


@router.post("/api/v1/agent-workflows/team-plans")
def client_propose_team_plan(payload: TeamPlanRequest, identity: dict = Depends(_require_chat)) -> dict:
    return _propose(
        _app_source(identity),
        payload,
        owner=False,
        permissions=set(identity["permissions"]),
    )


@router.get("/api/v1/agent-workflows/team-plans/{plan_id}")
def client_team_plan(plan_id: int, identity: dict = Depends(_require_chat)) -> dict:
    try:
        return agent_team_planning.get_plan(plan_id, _app_source(identity))
    except agent_team_planning.AgentTeamPlanningError as exc:
        raise _http_error(exc) from exc


@router.put("/api/v1/agent-workflows/team-plans/{plan_id}")
def client_edit_team_plan(
    plan_id: int,
    payload: TeamPlanEditRequest,
    identity: dict = Depends(_require_chat),
) -> dict:
    return _edit(_app_source(identity), plan_id, payload, owner=False)


@router.post("/api/v1/agent-workflows/team-plans/{plan_id}/approve")
def client_approve_team_plan(plan_id: int, identity: dict = Depends(_require_chat)) -> dict:
    return _approve(
        _app_source(identity),
        plan_id,
        owner=False,
        permissions=set(identity["permissions"]),
    )


@router.post("/api/v1/agent-workflows/team-plans/{plan_id}/reject")
def client_reject_team_plan(plan_id: int, identity: dict = Depends(_require_chat)) -> dict:
    return _reject(_app_source(identity), plan_id)


@router.get("/api/v1/control/agent-workflows/team-plans")
def control_team_plans(
    conversation_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=20, ge=1, le=50),
) -> dict:
    return agent_team_planning.list_plans("owner", conversation_id=conversation_id, limit=limit)


@router.post("/api/v1/control/agent-workflows/team-plans")
def control_propose_team_plan(payload: TeamPlanRequest) -> dict:
    return _propose("owner", payload, owner=True, permissions=set())


@router.get("/api/v1/control/agent-workflows/team-plans/{plan_id}")
def control_team_plan(plan_id: int) -> dict:
    try:
        return agent_team_planning.get_plan(plan_id, "owner")
    except agent_team_planning.AgentTeamPlanningError as exc:
        raise _http_error(exc) from exc


@router.put("/api/v1/control/agent-workflows/team-plans/{plan_id}")
def control_edit_team_plan(plan_id: int, payload: TeamPlanEditRequest) -> dict:
    return _edit("owner", plan_id, payload, owner=True)


@router.post("/api/v1/control/agent-workflows/team-plans/{plan_id}/approve")
def control_approve_team_plan(plan_id: int) -> dict:
    return _approve("owner", plan_id, owner=True, permissions=set())


@router.post("/api/v1/control/agent-workflows/team-plans/{plan_id}/reject")
def control_reject_team_plan(plan_id: int) -> dict:
    return _reject("owner", plan_id)
