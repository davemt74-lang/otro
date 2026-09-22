from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .services import fleet_management
from .services.pairing import authenticate

router = APIRouter()


class FleetSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    controller_app_key: str | None = Field(default=None, max_length=80)
    device_label: str | None = Field(default=None, max_length=120)
    remote_diagnostics: bool | None = None
    remote_update_requests: bool | None = None
    remote_support_summary: bool | None = None
    telemetry_interval_seconds: int | None = Field(default=None, ge=60, le=86400)
    stale_after_seconds: int | None = Field(default=None, ge=120, le=604800)
    rollout_failure_threshold: int | None = Field(default=None, ge=1, le=100)


class FleetCheckin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: str = Field(default="vp3-fleet-device-v1", max_length=40)
    device_id: str = Field(min_length=8, max_length=80)
    label: str = Field(default="", max_length=120)
    profile_key: str = Field(default="custom", max_length=80)
    os_version: str = Field(min_length=3, max_length=80)
    release_channel: str = Field(max_length=20)
    rollout_ring: str = Field(max_length=20)
    commissioning_state: str = Field(max_length=20)
    certification_result: str | None = Field(default=None, max_length=20)
    privacy_fault: bool = False
    update_status: str = Field(default="idle", max_length=40)
    backup_state: str = Field(default="unknown", max_length=20)
    storage_state: str = Field(default="unknown", max_length=20)
    watchdog_failures: int = Field(default=0, ge=0, le=1000)
    reported_at: str = Field(max_length=64)
    privacy: dict[str, bool] | None = None


class RolloutCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    release_version: str = Field(min_length=3, max_length=80)
    channel: str = Field(max_length=20)
    rollout_ring: str = Field(max_length=20)
    failure_threshold: int | None = Field(default=None, ge=1, le=100)


class RolloutStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(max_length=20)
    reason: str = Field(default="", max_length=240)


class RolloutOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=8, max_length=80)
    outcome: str = Field(max_length=30)
    detail_code: str = Field(default="", max_length=80)


class FleetUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_key: str = Field(min_length=8, max_length=128)
    package_sha256: str = Field(min_length=64, max_length=64)
    release_version: str = Field(min_length=3, max_length=80)
    rollout_id: int | None = Field(default=None, ge=1)


def _raise(exc: fleet_management.FleetError):
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _paired_app(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    identity = authenticate(authorization.removeprefix("Bearer ").strip())
    if identity is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked token")
    return identity


def _controller(identity: dict[str, Any], permission: str) -> dict[str, Any]:
    if not fleet_management.controller_authorized(identity, permission):
        raise HTTPException(
            status_code=403,
            detail=f"Enrolled fleet controller permission required: {permission}",
        )
    return identity


def _fleet_reader(identity: dict[str, Any] = Depends(_paired_app)) -> dict[str, Any]:
    return _controller(identity, "fleet.read")


def _fleet_manager(identity: dict[str, Any] = Depends(_paired_app)) -> dict[str, Any]:
    return _controller(identity, "fleet.manage")


def _fleet_telemetry(identity: dict[str, Any] = Depends(_paired_app)) -> dict[str, Any]:
    return _controller(identity, "fleet.telemetry")


@router.get("/api/v1/control/vp3-os/fleet")
def owner_fleet_overview() -> dict:
    try:
        return fleet_management.overview()
    except fleet_management.FleetError as exc:
        _raise(exc)


@router.put("/api/v1/control/vp3-os/fleet/settings")
def owner_fleet_settings(payload: FleetSettingsUpdate) -> dict:
    try:
        return {
            "updated": True,
            "settings": fleet_management.update_settings(**payload.model_dump()),
        }
    except fleet_management.FleetError as exc:
        _raise(exc)


@router.post("/api/v1/control/vp3-os/fleet/decommission")
def owner_fleet_decommission() -> dict:
    return {
        "decommissioned": True,
        "settings": fleet_management.decommission_local(),
        "private_data_deleted": False,
    }


@router.delete("/api/v1/control/vp3-os/fleet/devices/{device_id}")
def owner_remove_fleet_device(device_id: str) -> dict:
    try:
        return fleet_management.remove_inventory_device(device_id)
    except fleet_management.FleetError as exc:
        _raise(exc)


@router.post("/api/v1/control/vp3-os/fleet/rollouts")
def owner_create_fleet_rollout(payload: RolloutCreate) -> dict:
    try:
        return fleet_management.create_rollout(
            payload.release_version,
            payload.channel,
            payload.rollout_ring,
            payload.failure_threshold,
        )
    except fleet_management.FleetError as exc:
        _raise(exc)


@router.post("/api/v1/control/vp3-os/fleet/rollouts/{rollout_id}/status")
def owner_fleet_rollout_status(rollout_id: int, payload: RolloutStatusUpdate) -> dict:
    try:
        return fleet_management.set_rollout_status(
            rollout_id,
            payload.status,
            payload.reason,
        )
    except fleet_management.FleetError as exc:
        _raise(exc)


@router.post("/api/v1/control/vp3-os/fleet/update-requests/{request_id}/approve")
def owner_approve_fleet_update(request_id: int) -> dict:
    try:
        return fleet_management.approve_update_request(request_id)
    except fleet_management.FleetError as exc:
        _raise(exc)


@router.post("/api/v1/control/vp3-os/fleet/update-requests/{request_id}/dismiss")
def owner_dismiss_fleet_update(request_id: int) -> dict:
    try:
        return fleet_management.dismiss_update_request(request_id)
    except fleet_management.FleetError as exc:
        _raise(exc)


@router.get("/api/v1/fleet/device/status")
def paired_fleet_device_status(identity: dict[str, Any] = Depends(_fleet_reader)) -> dict:
    return {
        **fleet_management.local_device_snapshot(),
        "controller_app": identity["app_key"],
    }


@router.post("/api/v1/fleet/device/diagnostics")
def paired_fleet_device_diagnostics(identity: dict[str, Any] = Depends(_fleet_manager)) -> dict:
    fleet = fleet_management.get_settings()
    if not fleet["remote_diagnostics"]:
        raise HTTPException(status_code=403, detail="Remote fleet diagnostics are disabled")
    return {
        **fleet_management.remote_diagnostics_summary(),
        "controller_app": identity["app_key"],
    }


@router.post("/api/v1/fleet/device/support-summary")
def paired_fleet_support_summary(identity: dict[str, Any] = Depends(_fleet_manager)) -> dict:
    fleet = fleet_management.get_settings()
    if not fleet["remote_support_summary"]:
        raise HTTPException(status_code=403, detail="Remote fleet support summaries are disabled")
    return {
        **fleet_management.remote_support_summary(),
        "controller_app": identity["app_key"],
    }


@router.post("/api/v1/fleet/device/update-requests")
def paired_fleet_update_request(
    payload: FleetUpdateRequest,
    identity: dict[str, Any] = Depends(_fleet_manager),
) -> dict:
    try:
        return fleet_management.request_update(
            identity["app_key"],
            payload.request_key,
            payload.package_sha256,
            payload.release_version,
            payload.rollout_id,
        )
    except fleet_management.FleetError as exc:
        _raise(exc)


@router.post("/api/v1/fleet/check-ins")
def paired_fleet_checkin(
    payload: FleetCheckin,
    identity: dict[str, Any] = Depends(_fleet_telemetry),
) -> dict:
    if payload.format != "vp3-fleet-device-v1":
        raise HTTPException(status_code=422, detail="Unsupported fleet telemetry format")
    try:
        item = fleet_management.record_checkin(payload.model_dump())
    except fleet_management.FleetError as exc:
        _raise(exc)
    return {"recorded": True, "device": item, "controller_app": identity["app_key"]}


@router.post("/api/v1/fleet/rollouts/{rollout_id}/outcomes")
def paired_fleet_rollout_outcome(
    rollout_id: int,
    payload: RolloutOutcome,
    identity: dict[str, Any] = Depends(_fleet_telemetry),
) -> dict:
    try:
        rollout = fleet_management.record_rollout_outcome(
            rollout_id,
            payload.device_id,
            payload.outcome,
            payload.detail_code,
        )
    except fleet_management.FleetError as exc:
        _raise(exc)
    return {"recorded": True, "rollout": rollout, "controller_app": identity["app_key"]}


@router.get("/api/v1/fleet/inventory")
def paired_fleet_inventory(
    limit: int = Query(default=100, ge=1, le=500),
    identity: dict[str, Any] = Depends(_fleet_reader),
) -> dict:
    items = fleet_management.list_inventory()[:limit]
    return {"items": items, "controller_app": identity["app_key"]}


@router.get("/api/v1/fleet/rollouts")
def paired_fleet_rollouts(
    limit: int = Query(default=50, ge=1, le=100),
    identity: dict[str, Any] = Depends(_fleet_reader),
) -> dict:
    return {
        "items": fleet_management.list_rollouts(limit),
        "controller_app": identity["app_key"],
    }
