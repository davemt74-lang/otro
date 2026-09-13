from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .services.cloud_pairing import (
    CloudPairingError,
    bootstrap_vp3_remote_bridge,
    redeem_vp3_pairing_token,
)
from .services.connected_apps_pairing import ConnectedAppsPairingError, approve_pending_pairing
from .services.remote_bridge import (
    RemoteBridgeError,
    bridge_status,
    list_bridge_events,
    save_bridge_settings,
)


router = APIRouter()
UI_DIR = Path(__file__).resolve().parents[1] / "ui"


class RemoteBridgeSettingsUpdate(BaseModel):
    enabled: bool = False
    broker_url: str = Field(default="", max_length=1000)


class Vp3CloudPairingRequest(BaseModel):
    pairing_token: str = Field(min_length=72, max_length=100)


class Vp3CloudApprovalRequest(BaseModel):
    pairing_id: int = Field(gt=0)


@router.get("/remote", include_in_schema=False)
def remote_bridge_workspace():
    page = UI_DIR / "remote.html"
    if not page.is_file():
        raise HTTPException(status_code=503, detail="Remote bridge workspace is unavailable")
    return FileResponse(page)


@router.get("/api/v1/control/remote-bridge")
def control_remote_bridge(limit: int = Query(default=80, ge=1, le=500)) -> dict:
    return {
        **bridge_status(),
        "events": list_bridge_events(limit),
    }


@router.put("/api/v1/control/remote-bridge")
def control_remote_bridge_update(payload: RemoteBridgeSettingsUpdate) -> dict:
    try:
        configured = save_bridge_settings(payload.enabled, payload.broker_url)
    except RemoteBridgeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "updated": True,
        "settings": configured,
        "status": bridge_status(),
    }


@router.post("/api/v1/control/remote-bridge/bootstrap-vp3")
def control_remote_bridge_bootstrap_vp3() -> dict:
    try:
        result = bootstrap_vp3_remote_bridge()
    except CloudPairingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {**result, "status": bridge_status()}


@router.post("/api/v1/control/remote-bridge/pair-vp3")
def control_remote_bridge_pair_vp3(payload: Vp3CloudPairingRequest) -> dict:
    try:
        result = redeem_vp3_pairing_token(payload.pairing_token)
    except CloudPairingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result


@router.post("/api/v1/control/remote-bridge/approve-vp3")
def control_remote_bridge_approve_vp3(payload: Vp3CloudApprovalRequest) -> dict:
    try:
        return approve_pending_pairing(payload.pairing_id)
    except ConnectedAppsPairingError as exc:
        raise HTTPException(status_code=409 if "no longer pending" in str(exc) else 404, detail=str(exc)) from exc
