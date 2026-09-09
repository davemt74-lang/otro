from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .services.connected_apps import (
    ConnectedAppError,
    connected_app_activity,
    deny_pairing_request,
    list_connected_apps,
    require_repair,
    update_collaboration_grant,
)

router = APIRouter()


class CollaborationGrantUpdate(BaseModel):
    memory_allowed: bool = False
    knowledge_allowed: bool = False
    enabled: bool = True


@router.get("/api/v1/control/connected-apps")
def connected_apps() -> dict:
    return list_connected_apps()


@router.put("/api/v1/control/connected-apps/{consumer_app_id}/collaboration/{source_app_id}")
def update_connected_app_collaboration(
    consumer_app_id: int,
    source_app_id: int,
    payload: CollaborationGrantUpdate,
) -> dict:
    try:
        return update_collaboration_grant(
            consumer_app_id,
            source_app_id,
            memory_allowed=payload.memory_allowed,
            knowledge_allowed=payload.knowledge_allowed,
            enabled=payload.enabled,
        )
    except ConnectedAppError as exc:
        message = str(exc)
        raise HTTPException(
            status_code=422 if "itself" in message else 404 if "not found" in message.lower() else 409,
            detail=message,
        ) from exc


@router.get("/api/v1/control/connected-apps/{app_id}/activity")
def connected_app_history(app_id: int, limit: int = Query(default=50, ge=1, le=100)) -> dict:
    try:
        return connected_app_activity(app_id, limit)
    except ConnectedAppError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/v1/control/connected-apps/{app_id}/require-repair")
def require_connected_app_repair(app_id: int) -> dict:
    try:
        return require_repair(app_id)
    except ConnectedAppError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/v1/control/connected-apps/pending/{request_id}/deny")
def deny_connected_app_pairing(request_id: int) -> dict:
    try:
        return deny_pairing_request(request_id)
    except ConnectedAppError as exc:
        raise HTTPException(status_code=409 if "no longer pending" in str(exc) else 404, detail=str(exc)) from exc
