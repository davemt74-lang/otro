from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from .services import agent_workflow_continuation
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


def _continuation(
    source: str,
    conversation_id: str,
    *,
    owner: bool,
    permissions: set[str],
) -> dict:
    try:
        return agent_workflow_continuation.conversation_continuation(
            source,
            conversation_id,
            owner=owner,
            current_permissions=permissions,
        )
    except agent_workflow_continuation.AgentWorkflowContinuationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/api/v1/agent-workflows/continuation")
def client_workflow_continuation(
    conversation_id: str = Query(min_length=1, max_length=64),
    identity: dict = Depends(_require_chat),
) -> dict:
    return _continuation(
        _app_source(identity),
        conversation_id,
        owner=False,
        permissions=set(identity["permissions"]),
    )


@router.get("/api/v1/control/agent-workflows/continuation")
def control_workflow_continuation(
    conversation_id: str = Query(min_length=1, max_length=64),
) -> dict:
    return _continuation("owner", conversation_id, owner=True, permissions=set())
