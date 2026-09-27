from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .services import approvals, room_device_automation, tracky_governed_actions

router = APIRouter()


class RoomUpsert(BaseModel):
    room_key: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    enabled: bool = True


class ProviderUpsert(BaseModel):
    provider_key: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    provider_type: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    executable: bool = False
    status: str = Field(default="connected", max_length=40)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DeviceUpsert(BaseModel):
    device_key: str = Field(min_length=1, max_length=80)
    provider_key: str = Field(min_length=1, max_length=80)
    provider_device_id: str = Field(min_length=1, max_length=240)
    name: str = Field(min_length=1, max_length=160)
    category: str = Field(default="other", max_length=40)
    room_key: str | None = Field(default=None, max_length=80)
    enabled: bool = True
    controllable: bool = False
    capabilities: dict[str, Any] = Field(default_factory=dict)
    state: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DeviceCommandRequest(BaseModel):
    command: str = Field(min_length=1, max_length=40)
    arguments: dict[str, Any] = Field(default_factory=dict)

class TrackyEventRuleUpsert(BaseModel):
    rule_key: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    event_type: str = Field(min_length=3, max_length=100)
    routine_key: str = Field(min_length=1, max_length=80)
    enabled: bool = False
    room_id: str = Field(default="", max_length=128)
    subject_type: str = Field(default="", max_length=60)
    min_confidence: float = Field(default=0.80, ge=0.0, le=1.0)
    cooldown_seconds: int = Field(default=60, ge=0, le=86400)


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except room_device_automation.RoomDeviceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except approvals.ApprovalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except tracky_governed_actions.TrackyActionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/api/v1/control/vp3-os/automation")
def owner_automation_overview() -> dict:
    return {
        "version": room_device_automation.AUTOMATION_VERSION,
        "rooms": room_device_automation.list_rooms(),
        "providers": room_device_automation.list_providers(),
        "devices": room_device_automation.list_devices(limit=500),
        "suggestions": room_device_automation.list_suggestions(status="suggested", limit=100),
        "recent_actions": room_device_automation.list_actions(100),
        "tracky_event_rules": tracky_governed_actions.list_event_rules(),
        "governance": {
            "device_commands_require_approval": True,
            "ambient_direct_execution": False,
            "safe_control_categories": sorted(room_device_automation.SAFE_CONTROL_CATEGORIES),
            "discovery_only_categories": sorted(
                room_device_automation.DISCOVERABLE_CATEGORIES
                - room_device_automation.SAFE_CONTROL_CATEGORIES
            ),
        },
    }


@router.put("/api/v1/control/vp3-os/automation/rooms/{room_key}")
def owner_room_upsert(room_key: str, payload: RoomUpsert) -> dict:
    if room_key.strip().lower() != payload.room_key.strip().lower():
        raise HTTPException(status_code=422, detail="room_key path and payload must match")
    return {"room": _call(
        room_device_automation.upsert_room,
        payload.room_key,
        payload.name,
        description=payload.description,
        enabled=payload.enabled,
    )}


@router.delete("/api/v1/control/vp3-os/automation/rooms/{room_key}")
def owner_room_delete(room_key: str) -> dict:
    return _call(room_device_automation.delete_room, room_key)


@router.put("/api/v1/control/vp3-os/automation/providers/{provider_key}")
def owner_provider_upsert(provider_key: str, payload: ProviderUpsert) -> dict:
    if provider_key.strip().lower() != payload.provider_key.strip().lower():
        raise HTTPException(status_code=422, detail="provider_key path and payload must match")
    return {"provider": _call(
        room_device_automation.upsert_provider,
        payload.provider_key,
        payload.name,
        payload.provider_type,
        enabled=payload.enabled,
        executable=payload.executable,
        status=payload.status,
        metadata=payload.metadata,
    )}


@router.put("/api/v1/control/vp3-os/automation/devices/{device_key}")
def owner_device_upsert(device_key: str, payload: DeviceUpsert) -> dict:
    if device_key.strip().lower() != payload.device_key.strip().lower():
        raise HTTPException(status_code=422, detail="device_key path and payload must match")
    return {"device": _call(
        room_device_automation.upsert_device,
        payload.device_key,
        payload.provider_key,
        payload.provider_device_id,
        payload.name,
        payload.category,
        room_key=payload.room_key,
        enabled=payload.enabled,
        controllable=payload.controllable,
        capabilities=payload.capabilities,
        state=payload.state,
        metadata=payload.metadata,
    )}


@router.post("/api/v1/control/vp3-os/automation/devices/{device_key}/request")
def owner_device_command_request(device_key: str, payload: DeviceCommandRequest) -> dict:
    request = _call(
        approvals.create_device_command_request,
        "owner",
        {
            "device_key": device_key,
            "command": payload.command,
            "arguments": payload.arguments,
        },
        owner=True,
    )
    return {**request, "approval_required": True}


@router.get("/api/v1/control/vp3-os/automation/actions")
def owner_automation_actions(limit: int = Query(default=100, ge=1, le=500)) -> dict:
    return {"items": room_device_automation.list_actions(limit)}


@router.get("/api/v1/control/vp3-os/automation/suggestions")
def owner_automation_suggestions(
    status: str | None = Query(default="suggested", max_length=40),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    return {"items": _call(room_device_automation.list_suggestions, status, limit)}


@router.post("/api/v1/control/vp3-os/automation/suggestions/{suggestion_id}/request")
def owner_automation_suggestion_request(suggestion_id: int) -> dict:
    suggestion = _call(room_device_automation.get_suggestion, suggestion_id)
    if not suggestion.get("device_key") or not suggestion.get("command"):
        raise HTTPException(status_code=409, detail="Suggestion does not contain a device command")
    request = _call(
        approvals.create_device_command_request,
        "owner",
        {
            "device_key": suggestion["device_key"],
            "command": suggestion["command"],
            "arguments": suggestion.get("arguments") or {},
        },
        owner=True,
    )
    request_id = str(((request.get("result") or {}).get("request_id") or ""))
    _call(room_device_automation.mark_suggestion_requested, suggestion_id, request_id)
    return {**request, "suggestion_id": suggestion_id, "approval_required": True}


@router.post("/api/v1/control/vp3-os/automation/suggestions/{suggestion_id}/dismiss")
def owner_automation_suggestion_dismiss(suggestion_id: int) -> dict:
    return {"suggestion": _call(room_device_automation.dismiss_suggestion, suggestion_id)}

@router.get("/api/v1/control/vp3-os/automation/tracky-rules")
def owner_tracky_event_rules() -> dict:
    return {
        "items": _call(tracky_governed_actions.list_event_rules),
        "capability": tracky_governed_actions.public_capability(),
    }


@router.put("/api/v1/control/vp3-os/automation/tracky-rules/{rule_key}")
def owner_tracky_event_rule_upsert(rule_key: str, payload: TrackyEventRuleUpsert) -> dict:
    if rule_key.strip().lower() != payload.rule_key.strip().lower():
        raise HTTPException(status_code=422, detail="rule_key path and payload must match")
    return {
        "rule": _call(
            tracky_governed_actions.upsert_event_rule,
            payload.rule_key,
            payload.name,
            event_type=payload.event_type,
            routine_key=payload.routine_key,
            enabled=payload.enabled,
            room_id=payload.room_id,
            subject_type=payload.subject_type,
            min_confidence=payload.min_confidence,
            cooldown_seconds=payload.cooldown_seconds,
        )
    }


@router.delete("/api/v1/control/vp3-os/automation/tracky-rules/{rule_key}")
def owner_tracky_event_rule_delete(rule_key: str) -> dict:
    return _call(tracky_governed_actions.delete_event_rule, rule_key)

