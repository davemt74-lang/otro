from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from .services import agent_workflow_resume
from .services.pairing import authenticate
from .workflow_rehydration_api import router as workflow_rehydration_router

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


def _resume_index(
    source: str,
    *,
    owner: bool,
    permissions: set[str],
    limit: int,
) -> dict:
    try:
        return agent_workflow_resume.resume_index(
            source,
            owner=owner,
            current_permissions=permissions,
            limit=limit,
        )
    except agent_workflow_resume.AgentWorkflowResumeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/api/v1/agent-workflows/resume")
def client_workflow_resume(
    limit: int = Query(default=50, ge=1, le=50),
    identity: dict = Depends(_require_chat),
) -> dict:
    return _resume_index(
        _app_source(identity),
        owner=False,
        permissions=set(identity["permissions"]),
        limit=limit,
    )


@router.get("/api/v1/control/agent-workflows/resume")
def control_workflow_resume(
    limit: int = Query(default=50, ge=1, le=50),
) -> dict:
    return _resume_index("owner", owner=True, permissions=set(), limit=limit)


# v0.55 remains GET-only discovery/navigation. The v0.56 child router adds
# explicit checkpoint rehydration on separate URLs without changing v0.55's
# method or execution contract.
router.include_router(workflow_rehydration_router)
