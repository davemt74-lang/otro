from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from .services import tracky_forecast_calibration, tracky_governed_actions, tracky_model_lifecycle, tracky_physical_context
from .services.pairing import authenticate


router = APIRouter()


class ActivePerceptionRequest(BaseModel):
    request_type: str = Field(min_length=2, max_length=60)
    request_id: str | None = Field(default=None, min_length=8, max_length=128)
    correlation_id: str | None = Field(default=None, min_length=8, max_length=128)
    site_id: str | None = Field(default=None, max_length=100)
    target: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=500)

class PhysicalActionProposal(BaseModel):
    intent_id: str | None = Field(default=None, min_length=8, max_length=128)
    correlation_id: str | None = Field(default=None, min_length=8, max_length=128)
    site_id: str | None = Field(default=None, max_length=100)
    origin_kind: str = Field(default="agent_suggestion", max_length=40)
    requested_mode: str = Field(default="suggest_only", max_length=40)
    device_key: str = Field(min_length=1, max_length=80)
    command: str = Field(min_length=1, max_length=40)
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1, max_length=1000)
    source_event_id: str = Field(default="", max_length=128)


def _paired_app(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    identity = authenticate(authorization.removeprefix("Bearer ").strip())
    if identity is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked token")
    return identity


def _physical_reader(identity: dict = Depends(_paired_app)) -> dict:
    if "awareness.read" not in identity.get("permissions", []):
        raise HTTPException(status_code=403, detail="Permission required: awareness.read")
    return identity


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except tracky_physical_context.TrackyPhysicalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except tracky_governed_actions.TrackyActionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except tracky_forecast_calibration.TrackyCalibrationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except tracky_model_lifecycle.TrackyLifecycleError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/api/v1/tracky/capabilities")
def paired_tracky_capabilities(identity: dict = Depends(_physical_reader)) -> dict:
    return {
        "tracky": tracky_physical_context.public_capability(),
        "app": identity["app_key"],
    }


@router.get("/api/v1/tracky/context")
def paired_tracky_context(identity: dict = Depends(_physical_reader)) -> dict:
    return {
        **tracky_physical_context.current_context(),
        "app": identity["app_key"],
    }


@router.post("/api/v1/tracky/active-perception")
def paired_tracky_active_perception(
    payload: ActivePerceptionRequest,
    identity: dict = Depends(_physical_reader),
) -> dict:
    return _call(
        tracky_physical_context.active_perception,
        payload.request_type,
        request_id=payload.request_id,
        correlation_id=payload.correlation_id,
        site_id=payload.site_id,
        target=payload.target,
        reason=payload.reason,
        requested_by=identity["app_key"],
    )


@router.get("/api/v1/tracky/active-perception/{request_id}")
def paired_tracky_active_perception_status(
    request_id: str,
    identity: dict = Depends(_physical_reader),
) -> dict:
    return _call(tracky_physical_context.request_status, request_id)


@router.get("/api/v1/tracky/model-lifecycle")
def paired_tracky_model_lifecycle(
    identity: dict = Depends(_physical_reader),
) -> dict:
    return {
        "lifecycle": tracky_model_lifecycle.current_report(),
        "health": tracky_model_lifecycle.health_summary(),
        "capability": tracky_model_lifecycle.public_capability(),
        "app": identity["app_key"],
    }


@router.get("/api/v1/tracky/calibration")
def paired_tracky_calibration(
    identity: dict = Depends(_physical_reader),
) -> dict:
    return {
        "calibration": tracky_forecast_calibration.current_report(),
        "summary": tracky_forecast_calibration.summary(),
        "app": identity["app_key"],
    }


@router.post("/api/v1/tracky/actions/propose")
def paired_tracky_action_propose(
    payload: PhysicalActionProposal,
    identity: dict = Depends(_physical_reader),
) -> dict:
    return _call(
        tracky_governed_actions.propose_device_action,
        intent_id=payload.intent_id,
        correlation_id=payload.correlation_id,
        site_id=payload.site_id,
        origin_kind=payload.origin_kind,
        requested_mode=payload.requested_mode,
        device_key=payload.device_key,
        command=payload.command,
        arguments=payload.arguments,
        reason=payload.reason,
        requested_by=identity["app_key"],
        granted_permissions=set(identity.get("permissions") or []),
        source_event_id=payload.source_event_id,
    )


@router.get("/api/v1/tracky/actions/{intent_id}")
def paired_tracky_action_status(
    intent_id: str,
    identity: dict = Depends(_physical_reader),
) -> dict:
    return _call(tracky_governed_actions.action_status, intent_id)


@router.post("/api/v1/tracky/cloud-sync")
def paired_tracky_cloud_sync(identity: dict = Depends(_physical_reader)) -> dict:
    return _call(tracky_physical_context.sync_cloud)


@router.get("/api/v1/tracky/cloud-sync")
def paired_tracky_cloud_sync_status(identity: dict = Depends(_physical_reader)) -> dict:
    return {
        "sync": tracky_physical_context.sync_status(),
        "app": identity["app_key"],
    }
