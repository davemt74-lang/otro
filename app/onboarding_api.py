"""Owner-only scripted Agent Chat onboarding and approved provisioning."""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from .services import onboarding_chat

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
