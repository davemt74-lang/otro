from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .services import ambient_orchestration

router = APIRouter()


class ModeUpsert(BaseModel):
    mode_key: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    routine_key: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=1000)
    room_keys: list[str] = Field(default_factory=list, max_length=12)
    priority: int = Field(default=50, ge=0, le=100)
    suggest_trigger: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class ModeActivation(BaseModel):
    reason: str = Field(default="", max_length=1000)
    supersede_conflicts: bool = False


class EnabledUpdate(BaseModel):
    enabled: bool


class SessionReason(BaseModel):
    reason: str = Field(default="", max_length=1000)


class SettingsUpdate(BaseModel):
    enabled: bool = True
    poll_seconds: int = Field(default=30, ge=10, le=300)
    suggestion_cooldown_seconds: int = Field(
        default=14400, ge=300, le=604800
    )
    max_open_sessions: int = Field(default=12, ge=1, le=50)


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ambient_orchestration.OrchestrationError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=str(exc),
        ) from exc


@router.get("/api/v1/control/vp3-os/orchestration")
def owner_orchestration_overview() -> dict:
    return _call(ambient_orchestration.overview)


@router.put("/api/v1/control/vp3-os/orchestration/settings")
def owner_orchestration_settings(payload: SettingsUpdate) -> dict:
    return {
        "settings": _call(
            ambient_orchestration.update_settings,
            enabled=payload.enabled,
            poll_seconds=payload.poll_seconds,
            suggestion_cooldown_seconds=payload.suggestion_cooldown_seconds,
            max_open_sessions=payload.max_open_sessions,
        )
    }


@router.put("/api/v1/control/vp3-os/orchestration/modes/{mode_key}")
def owner_mode_upsert(mode_key: str, payload: ModeUpsert) -> dict:
    if mode_key.strip().lower() != payload.mode_key.strip().lower():
        raise HTTPException(
            status_code=422,
            detail="mode_key path and payload must match",
        )
    return {
        "mode": _call(
            ambient_orchestration.upsert_mode,
            payload.mode_key,
            payload.name,
            routine_key=payload.routine_key,
            description=payload.description,
            room_keys=payload.room_keys,
            priority=payload.priority,
            suggest_trigger=payload.suggest_trigger,
            enabled=payload.enabled,
        )
    }


@router.put(
    "/api/v1/control/vp3-os/orchestration/modes/{mode_key}/enabled"
)
def owner_mode_enabled(mode_key: str, payload: EnabledUpdate) -> dict:
    return {
        "mode": _call(
            ambient_orchestration.set_mode_enabled,
            mode_key,
            payload.enabled,
        )
    }


@router.get(
    "/api/v1/control/vp3-os/orchestration/modes/{mode_key}/simulate"
)
def owner_mode_simulate(mode_key: str) -> dict:
    return _call(ambient_orchestration.simulate_mode, mode_key)


@router.post(
    "/api/v1/control/vp3-os/orchestration/modes/{mode_key}/activate"
)
def owner_mode_activate(
    mode_key: str,
    payload: ModeActivation,
) -> dict:
    return {
        "session": _call(
            ambient_orchestration.activate_mode,
            mode_key,
            source_kind="owner",
            reason=payload.reason,
            supersede_conflicts=payload.supersede_conflicts,
        )
    }


@router.post("/api/v1/control/vp3-os/orchestration/evaluate")
def owner_orchestration_evaluate() -> dict:
    return {
        "suggestions": _call(
            ambient_orchestration.evaluate_mode_suggestions
        )
    }


@router.post(
    "/api/v1/control/vp3-os/orchestration/sessions/{session_id}/accept"
)
def owner_session_accept(
    session_id: int,
    payload: ModeActivation,
) -> dict:
    return {
        "session": _call(
            ambient_orchestration.accept_suggestion,
            session_id,
            supersede_conflicts=payload.supersede_conflicts,
        )
    }


@router.post(
    "/api/v1/control/vp3-os/orchestration/sessions/{session_id}/dismiss"
)
def owner_session_dismiss(session_id: int) -> dict:
    return {
        "session": _call(
            ambient_orchestration.dismiss_suggestion,
            session_id,
        )
    }


@router.post(
    "/api/v1/control/vp3-os/orchestration/sessions/{session_id}/suspend"
)
def owner_session_suspend(
    session_id: int,
    payload: SessionReason,
) -> dict:
    return {
        "session": _call(
            ambient_orchestration.suspend_session,
            session_id,
            reason=payload.reason or "Owner suspended the Room Mode.",
        )
    }


@router.post(
    "/api/v1/control/vp3-os/orchestration/sessions/{session_id}/end"
)
def owner_session_end(
    session_id: int,
    payload: SessionReason,
) -> dict:
    return {
        "session": _call(
            ambient_orchestration.end_session,
            session_id,
            reason=payload.reason or "Owner ended the Room Mode.",
        )
    }


@router.post(
    "/api/v1/control/vp3-os/orchestration/sessions/{session_id}/refresh"
)
def owner_session_refresh(session_id: int) -> dict:
    return {
        "session": _call(
            ambient_orchestration.refresh_session,
            session_id,
        )
    }


@router.get("/api/v1/control/vp3-os/orchestration/conflicts")
def owner_conflicts(
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    return {
        "items": _call(
            ambient_orchestration.list_conflicts,
            limit,
        )
    }
