from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .services import hardware_experience

router = APIRouter()


class ExperienceSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    brightness_percent: int | None = Field(default=None, ge=0, le=100)
    volume_percent: int | None = Field(default=None, ge=0, le=100)
    led_intensity_percent: int | None = Field(default=None, ge=0, le=100)
    screen_timeout_seconds: int | None = Field(default=None, ge=15, le=86400)
    wake_behavior: str | None = Field(default=None, max_length=20)
    agent_button_action: str | None = Field(default=None, max_length=30)
    hold_action: str | None = Field(default=None, max_length=30)
    display_detail: str | None = Field(default=None, max_length=20)
    quiet_visuals: bool | None = None


class DisplayCardCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    card_key: str = Field(min_length=1, max_length=100)
    card_type: str = Field(min_length=1, max_length=30)
    title: str = Field(min_length=1, max_length=180)
    subtitle: str = Field(default="", max_length=240)
    priority: int = Field(default=50, ge=0, le=100)
    payload: dict = Field(default_factory=dict)


class DisplayCardState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str = Field(max_length=20)


def _raise(exc: hardware_experience.HardwareExperienceError):
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/api/v1/control/vp3-os/hardware-experience")
def owner_hardware_experience() -> dict:
    try:
        return hardware_experience.status()
    except hardware_experience.HardwareExperienceError as exc:
        _raise(exc)


@router.put("/api/v1/control/vp3-os/hardware-experience/settings")
def owner_hardware_experience_settings(payload: ExperienceSettingsUpdate) -> dict:
    try:
        return {
            "updated": True,
            "settings": hardware_experience.update_settings(**payload.model_dump()),
        }
    except hardware_experience.HardwareExperienceError as exc:
        _raise(exc)


@router.post("/api/v1/control/vp3-os/hardware-experience/certifications")
def owner_hardware_experience_certify() -> dict:
    try:
        return hardware_experience.certification()
    except hardware_experience.HardwareExperienceError as exc:
        _raise(exc)


@router.get("/api/v1/control/vp3-os/hardware-experience/certifications")
def owner_hardware_experience_certifications(limit: int = 20) -> dict:
    return {"items": hardware_experience.list_certifications(limit)}


@router.get("/api/v1/control/vp3-os/hardware-experience/events")
def owner_hardware_experience_events(limit: int = 50) -> dict:
    return {"items": hardware_experience.recent_events(limit)}


@router.post("/api/v1/control/vp3-os/hardware-experience/cards")
def owner_hardware_experience_card(payload: DisplayCardCreate) -> dict:
    try:
        return hardware_experience.upsert_card(
            payload.card_key,
            payload.card_type,
            payload.title,
            subtitle=payload.subtitle,
            priority=payload.priority,
            payload=payload.payload,
        )
    except hardware_experience.HardwareExperienceError as exc:
        _raise(exc)


@router.put("/api/v1/control/vp3-os/hardware-experience/cards/{card_key}/state")
def owner_hardware_experience_card_state(
    card_key: str,
    payload: DisplayCardState,
) -> dict:
    try:
        return hardware_experience.set_card_state(card_key, payload.state)
    except hardware_experience.HardwareExperienceError as exc:
        _raise(exc)
