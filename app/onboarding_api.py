"""Owner-only scripted Agent Chat onboarding and approved provisioning."""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from .services import onboarding_chat, onboarding_visual

router = APIRouter(prefix="/api/v1/control/onboarding", tags=["agent-onboarding"])


def _require_ui(header: str | None) -> None:
    # State-changing operations must come from the authenticated local chat UI.
    if header != "XMLHttpRequest":
        raise HTTPException(status_code=403, detail="Use the authorized HomeServer Agent Chat canvas.")


def _invoke(function):
    try:
        return function()
    except onboarding_chat.OnboardingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/summary")
def summary() -> dict:
    return onboarding_chat.summary()


@router.get("/device/status")
def device_status() -> dict:
    return onboarding_chat.device_status()


@router.post("/voice/start")
def voice_start(x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    return _invoke(onboarding_chat.start_voice)


@router.post("/device/start")
def device_start(x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    return _invoke(onboarding_chat.new_device_code)


@router.post("/device/poll")
def device_poll(x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    return _invoke(onboarding_chat.poll_device_code)


@router.post("/device/reset")
def device_reset(x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    return _invoke(onboarding_chat.clear_pending_code)


class VisualStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent: bool
    scope: str = Field(max_length=80)


class VisualReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session: str = Field(min_length=32, max_length=128)
    participant_id: str = Field(min_length=8, max_length=100)
    samples: int = Field(ge=3, le=5)


class VisualDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    participant_id: str = Field(min_length=8, max_length=100)


def _visual_call(fn):
    try:
        return fn()
    except onboarding_visual.VisualOnboardingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/visual/status")
def visual_status() -> dict:
    return onboarding_visual.status()


@router.post("/visual/start")
def visual_start(payload: VisualStart, x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    return _visual_call(lambda: onboarding_visual.start(consent=payload.consent, scope=payload.scope))


@router.post("/visual/report")
def visual_report(payload: VisualReport, x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    return _visual_call(lambda: onboarding_visual.report(
        session=payload.session, participant_id=payload.participant_id, samples=payload.samples
    ))


@router.post("/visual/cancel")
def visual_cancel(x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    return onboarding_visual.cancel()


@router.post("/visual/delete")
def visual_delete(payload: VisualDelete, x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    return _visual_call(lambda: onboarding_visual.delete_report(participant_id=payload.participant_id))
