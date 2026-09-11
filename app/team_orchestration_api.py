from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from .services import agent_team_orchestration
from .services.pairing import authenticate

router = APIRouter()


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


def _get(source: str, plan_id: int, *, owner: bool, permissions: set[str]) -> dict:
    try:
        return agent_team_orchestration.get_orchestration(
            plan_id,
            source,
            owner=owner,
            current_permissions=permissions,
        )
    except agent_team_orchestration.AgentTeamOrchestrationError as exc:
        raise _http_error(exc) from exc


def _run(source: str, plan_id: int, *, owner: bool, permissions: set[str]) -> dict:
    try:
        return agent_team_orchestration.run_plan_team(
            plan_id,
            source,
            owner=owner,
            current_permissions=permissions,
        )
    except agent_team_orchestration.AgentTeamOrchestrationError as exc:
        raise _http_error(exc) from exc


def _retry(source: str, plan_id: int, task_id: int, *, owner: bool, permissions: set[str]) -> dict:
    try:
        return agent_team_orchestration.retry_plan_member(
            plan_id,
            task_id,
            source,
            owner=owner,
            current_permissions=permissions,
        )
    except agent_team_orchestration.AgentTeamOrchestrationError as exc:
        raise _http_error(exc) from exc


def _prepare(source: str, plan_id: int, *, owner: bool, permissions: set[str]) -> dict:
    try:
        return agent_team_orchestration.prepare_plan_synthesis(
            plan_id,
            source,
            owner=owner,
            current_permissions=permissions,
        )
    except agent_team_orchestration.AgentTeamOrchestrationError as exc:
        raise _http_error(exc) from exc


@router.get("/api/v1/agent-workflows/team-orchestrations")
def client_team_orchestrations(
    conversation_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=20, ge=1, le=50),
    identity: dict = Depends(_require_chat),
) -> dict:
    try:
        return agent_team_orchestration.list_orchestrations(
            _app_source(identity),
            owner=False,
            current_permissions=set(identity["permissions"]),
            conversation_id=conversation_id,
            limit=limit,
        )
    except agent_team_orchestration.AgentTeamOrchestrationError as exc:
        raise _http_error(exc) from exc


@router.get("/api/v1/agent-workflows/team-plans/{plan_id}/orchestration")
def client_team_orchestration(plan_id: int, identity: dict = Depends(_require_chat)) -> dict:
    return _get(
        _app_source(identity),
        plan_id,
        owner=False,
        permissions=set(identity["permissions"]),
    )


@router.post("/api/v1/agent-workflows/team-plans/{plan_id}/run")
def client_run_planned_team(plan_id: int, identity: dict = Depends(_require_chat)) -> dict:
    return _run(
        _app_source(identity),
        plan_id,
        owner=False,
        permissions=set(identity["permissions"]),
    )


@router.post("/api/v1/agent-workflows/team-plans/{plan_id}/members/{task_id}/retry")
def client_retry_planned_team_member(
    plan_id: int,
    task_id: int,
    identity: dict = Depends(_require_chat),
) -> dict:
    return _retry(
        _app_source(identity),
        plan_id,
        task_id,
        owner=False,
        permissions=set(identity["permissions"]),
    )


@router.post("/api/v1/agent-workflows/team-plans/{plan_id}/prepare")
def client_prepare_planned_team(plan_id: int, identity: dict = Depends(_require_chat)) -> dict:
    return _prepare(
        _app_source(identity),
        plan_id,
        owner=False,
        permissions=set(identity["permissions"]),
    )


@router.get("/api/v1/control/agent-workflows/team-orchestrations")
def control_team_orchestrations(
    conversation_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=20, ge=1, le=50),
) -> dict:
    try:
        return agent_team_orchestration.list_orchestrations(
            "owner",
            owner=True,
            current_permissions=set(),
            conversation_id=conversation_id,
            limit=limit,
        )
    except agent_team_orchestration.AgentTeamOrchestrationError as exc:
        raise _http_error(exc) from exc


@router.get("/api/v1/control/agent-workflows/team-plans/{plan_id}/orchestration")
def control_team_orchestration(plan_id: int) -> dict:
    return _get("owner", plan_id, owner=True, permissions=set())


@router.post("/api/v1/control/agent-workflows/team-plans/{plan_id}/run")
def control_run_planned_team(plan_id: int) -> dict:
    return _run("owner", plan_id, owner=True, permissions=set())


@router.post("/api/v1/control/agent-workflows/team-plans/{plan_id}/members/{task_id}/retry")
def control_retry_planned_team_member(plan_id: int, task_id: int) -> dict:
    return _retry("owner", plan_id, task_id, owner=True, permissions=set())


@router.post("/api/v1/control/agent-workflows/team-plans/{plan_id}/prepare")
def control_prepare_planned_team(plan_id: int) -> dict:
    return _prepare("owner", plan_id, owner=True, permissions=set())
