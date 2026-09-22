from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from .services import app_scopes, device_audio, hardware_adapters, physical_agent, physical_meeting, vp3_os
from .services.pairing import authenticate

router = APIRouter()


class StatusLightUpdate(BaseModel):
    mode: str = Field(min_length=2, max_length=32)


class PhysicalMeetingStart(BaseModel):
    title: str = Field(default="", max_length=240)


class PlacementRequest(BaseModel):
    sensitivity: str = Field(default="internal", max_length=20)
    raw_audio: bool = False
    needs_local_data: bool = False
    needs_cloud_model: bool = False
    collaboration: bool = False
    hardware: list[str] = Field(default_factory=list, max_length=16)
    cloud_ready: bool = False
    allow_cloud: bool = True


def _current_app(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    identity = authenticate(authorization.removeprefix("Bearer ").strip())
    if identity is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked token")
    return identity


def _plan(payload: PlacementRequest, *, scope_cloud_allowed: bool) -> dict:
    return vp3_os.placement_plan(
        {
            "sensitivity": payload.sensitivity,
            "raw_audio": payload.raw_audio,
            "needs_local_data": payload.needs_local_data,
            "needs_cloud_model": payload.needs_cloud_model,
            "collaboration": payload.collaboration,
            "hardware": payload.hardware,
        },
        cloud_ready=payload.cloud_ready,
        cloud_allowed=bool(scope_cloud_allowed and payload.allow_cloud),
    )


@router.get("/api/v1/control/vp3-os")
def owner_vp3_os_status() -> dict:
    return {
        **vp3_os.owner_status(),
        "hardware_adapter": hardware_adapters.status(),
        "physical_agent": physical_agent.status(),
        "physical_meeting": physical_meeting.status(),
        "device_audio": device_audio.status(),
    }


@router.get("/api/v1/control/vp3-os/hardware/events")
def owner_vp3_os_hardware_events(limit: int = 50) -> dict:
    return {"items": hardware_adapters.recent_events(limit=limit)}


@router.post("/api/v1/control/vp3-os/hardware/status-light")
def owner_vp3_os_status_light(payload: StatusLightUpdate) -> dict:
    try:
        return hardware_adapters.set_status_light(payload.mode)
    except hardware_adapters.HardwareAdapterError as exc:
        message = str(exc)
        status_code = 409 if "not connected" in message.lower() else 422
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.get("/api/v1/control/vp3-os/physical-agent")
def owner_physical_agent_status() -> dict:
    return physical_agent.status()


@router.post("/api/v1/control/vp3-os/physical-agent/listen")
def owner_physical_agent_listen() -> dict:
    try:
        return physical_agent.begin_listening()
    except physical_agent.PhysicalAgentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/v1/control/vp3-os/physical-agent/release")
def owner_physical_agent_release() -> dict:
    return physical_agent.finish_listening()


@router.post("/api/v1/control/vp3-os/physical-agent/cancel")
def owner_physical_agent_cancel() -> dict:
    return physical_agent.cancel("owner_cancelled")


@router.post("/api/v1/control/vp3-os/physical-agent/reset-conversation")
def owner_physical_agent_reset_conversation() -> dict:
    return physical_agent.reset_conversation()


@router.get("/api/v1/control/vp3-os/physical-meeting")
def owner_physical_meeting_status() -> dict:
    return physical_meeting.status()


@router.post("/api/v1/control/vp3-os/physical-meeting/start")
def owner_physical_meeting_start(payload: PhysicalMeetingStart) -> dict:
    try:
        return physical_meeting.start_meeting(payload.title, trigger="owner")
    except physical_meeting.PhysicalMeetingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/v1/control/vp3-os/physical-meeting/end")
def owner_physical_meeting_end() -> dict:
    return physical_meeting.end_meeting("owner")


@router.post("/api/v1/control/vp3-os/physical-meeting/interrupt")
def owner_physical_meeting_interrupt() -> dict:
    return physical_meeting.interrupt("owner_interrupted")


@router.post("/api/v1/control/vp3-os/placement")
def owner_vp3_os_placement(payload: PlacementRequest) -> dict:
    return _plan(payload, scope_cloud_allowed=True)


@router.get("/api/v1/vp3-os/status")
def paired_vp3_os_status(identity: dict = Depends(_current_app)) -> dict:
    # The paired view intentionally omits detailed hardware state and any local
    # filesystem/process metadata. Device identity is the existing opaque
    # HomeServer bridge identity, not a host serial/MAC/hostname.
    return {
        **vp3_os.capability_projection(),
        "hardware_adapter": hardware_adapters.paired_status(),
        "physical_agent": physical_agent.paired_status(),
        "physical_meeting": physical_meeting.paired_status(),
        "app": str(identity.get("app_key") or "")[:80],
    }


@router.post("/api/v1/vp3-os/placement")
def paired_vp3_os_placement(payload: PlacementRequest, identity: dict = Depends(_current_app)) -> dict:
    scope = app_scopes.normalize(identity.get("scope") if isinstance(identity.get("scope"), dict) else None)
    return {
        **_plan(payload, scope_cloud_allowed=bool(scope["cloud_allowed"])),
        "app": str(identity.get("app_key") or "")[:80],
    }
