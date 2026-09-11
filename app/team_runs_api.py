from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .services import agent_routing, agent_team_runs
from .services.pairing import authenticate

router = APIRouter()


class TeamRunMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_agent_id: int = Field(ge=1)
    task: str = Field(min_length=1, max_length=16000)
    include_memory: bool = True
    include_knowledge: bool = True
    include_contacts: bool = False
    cloud_allowed: bool = True
    max_context_chars: int = Field(default=12000, ge=2000, le=24000)


class TeamRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_agent_id: int = Field(ge=1)
    conversation_id: str = Field(min_length=1, max_length=64)
    objective: str = Field(min_length=1, max_length=agent_team_runs.MAX_OBJECTIVE_CHARS)
    external_conversation_id: str | None = Field(default=None, max_length=160)
    members: list[TeamRunMemberRequest] = Field(
        min_length=agent_team_runs.MIN_TEAM_MEMBERS,
        max_length=agent_team_runs.MAX_TEAM_MEMBERS,
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


def _create(
    source: str,
    payload: TeamRunRequest,
    *,
    owner: bool,
    permissions: set[str],
) -> dict:
    try:
        return agent_team_runs.create_team_run(
            source,
            parent_agent_id=payload.parent_agent_id,
            conversation_id=payload.conversation_id,
            objective=payload.objective,
            members=[item.model_dump() for item in payload.members],
            owner=owner,
            current_permissions=permissions,
            external_conversation_id=payload.external_conversation_id,
        )
    except (agent_team_runs.AgentTeamRunError, agent_routing.AgentRoutingError) as exc:
        raise _http_error(exc) from exc


def _get(source: str, team_run_id: int, *, owner: bool, permissions: set[str]) -> dict:
    try:
        return agent_team_runs.get_team_run(
            team_run_id,
            source,
            owner=owner,
            current_permissions=permissions,
        )
    except (agent_team_runs.AgentTeamRunError, agent_routing.AgentRoutingError) as exc:
        raise _http_error(exc) from exc


def _run(source: str, team_run_id: int, *, owner: bool, permissions: set[str]) -> dict:
    try:
        return agent_team_runs.run_team(
            team_run_id,
            source,
            owner=owner,
            current_permissions=permissions,
        )
    except (agent_team_runs.AgentTeamRunError, agent_routing.AgentRoutingError) as exc:
        raise _http_error(exc) from exc


def _retry(source: str, team_run_id: int, task_id: int, *, owner: bool, permissions: set[str]) -> dict:
    try:
        return agent_team_runs.retry_member(
            team_run_id,
            task_id,
            source,
            owner=owner,
            current_permissions=permissions,
        )
    except (agent_team_runs.AgentTeamRunError, agent_routing.AgentRoutingError) as exc:
        raise _http_error(exc) from exc


def _prepare(source: str, team_run_id: int, *, owner: bool, permissions: set[str]) -> dict:
    try:
        return agent_team_runs.prepare_team_synthesis(
            team_run_id,
            source,
            owner=owner,
            current_permissions=permissions,
        )
    except (agent_team_runs.AgentTeamRunError, agent_routing.AgentRoutingError) as exc:
        raise _http_error(exc) from exc


def _cancel(source: str, team_run_id: int, *, owner: bool, permissions: set[str]) -> dict:
    try:
        return agent_team_runs.cancel_team_run(
            team_run_id,
            source,
            owner=owner,
            current_permissions=permissions,
        )
    except (agent_team_runs.AgentTeamRunError, agent_routing.AgentRoutingError) as exc:
        raise _http_error(exc) from exc


@router.get("/api/v1/agent-workflows/team-runs")
def client_team_runs(
    conversation_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=20, ge=1, le=50),
    identity: dict = Depends(_require_chat),
) -> dict:
    return agent_team_runs.list_team_runs(
        _app_source(identity),
        owner=False,
        current_permissions=set(identity["permissions"]),
        conversation_id=conversation_id,
        limit=limit,
    )


@router.post("/api/v1/agent-workflows/team-runs")
def client_create_team_run(payload: TeamRunRequest, identity: dict = Depends(_require_chat)) -> dict:
    return _create(
        _app_source(identity),
        payload,
        owner=False,
        permissions=set(identity["permissions"]),
    )


@router.get("/api/v1/agent-workflows/team-runs/{team_run_id}")
def client_team_run(team_run_id: int, identity: dict = Depends(_require_chat)) -> dict:
    return _get(
        _app_source(identity),
        team_run_id,
        owner=False,
        permissions=set(identity["permissions"]),
    )


@router.post("/api/v1/agent-workflows/team-runs/{team_run_id}/run")
def client_run_team(team_run_id: int, identity: dict = Depends(_require_chat)) -> dict:
    return _run(
        _app_source(identity),
        team_run_id,
        owner=False,
        permissions=set(identity["permissions"]),
    )


@router.post("/api/v1/agent-workflows/team-runs/{team_run_id}/members/{task_id}/retry")
def client_retry_team_member(team_run_id: int, task_id: int, identity: dict = Depends(_require_chat)) -> dict:
    return _retry(
        _app_source(identity),
        team_run_id,
        task_id,
        owner=False,
        permissions=set(identity["permissions"]),
    )


@router.post("/api/v1/agent-workflows/team-runs/{team_run_id}/prepare")
def client_prepare_team(team_run_id: int, identity: dict = Depends(_require_chat)) -> dict:
    return _prepare(
        _app_source(identity),
        team_run_id,
        owner=False,
        permissions=set(identity["permissions"]),
    )


@router.post("/api/v1/agent-workflows/team-runs/{team_run_id}/cancel")
def client_cancel_team(team_run_id: int, identity: dict = Depends(_require_chat)) -> dict:
    return _cancel(
        _app_source(identity),
        team_run_id,
        owner=False,
        permissions=set(identity["permissions"]),
    )


@router.get("/api/v1/control/agent-workflows/team-runs")
def control_team_runs(
    conversation_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=20, ge=1, le=50),
) -> dict:
    return agent_team_runs.list_team_runs(
        "owner",
        owner=True,
        current_permissions=set(),
        conversation_id=conversation_id,
        limit=limit,
    )


@router.post("/api/v1/control/agent-workflows/team-runs")
def control_create_team_run(payload: TeamRunRequest) -> dict:
    return _create("owner", payload, owner=True, permissions=set())


@router.get("/api/v1/control/agent-workflows/team-runs/{team_run_id}")
def control_team_run(team_run_id: int) -> dict:
    return _get("owner", team_run_id, owner=True, permissions=set())


@router.post("/api/v1/control/agent-workflows/team-runs/{team_run_id}/run")
def control_run_team(team_run_id: int) -> dict:
    return _run("owner", team_run_id, owner=True, permissions=set())


@router.post("/api/v1/control/agent-workflows/team-runs/{team_run_id}/members/{task_id}/retry")
def control_retry_team_member(team_run_id: int, task_id: int) -> dict:
    return _retry("owner", team_run_id, task_id, owner=True, permissions=set())


@router.post("/api/v1/control/agent-workflows/team-runs/{team_run_id}/prepare")
def control_prepare_team(team_run_id: int) -> dict:
    return _prepare("owner", team_run_id, owner=True, permissions=set())


@router.post("/api/v1/control/agent-workflows/team-runs/{team_run_id}/cancel")
def control_cancel_team(team_run_id: int) -> dict:
    return _cancel("owner", team_run_id, owner=True, permissions=set())
