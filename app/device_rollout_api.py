from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .services import device_rollout

router = APIRouter()


class RolloutSettingsUpdate(BaseModel):
    release_channel: str | None = None
    rollout_ring: str | None = None
    watchdog_enabled: bool | None = None
    max_failed_starts: int | None = Field(default=None, ge=1, le=10)


def _raise(exc: device_rollout.RolloutError):
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/api/v1/control/vp3-os/rollout")
def rollout_overview() -> dict:
    try:
        return device_rollout.overview()
    except device_rollout.RolloutError as exc:
        _raise(exc)


@router.put("/api/v1/control/vp3-os/rollout/settings")
def rollout_settings(payload: RolloutSettingsUpdate) -> dict:
    try:
        return {
            "updated": True,
            "settings": device_rollout.update_settings(
                release_channel=payload.release_channel,
                rollout_ring=payload.rollout_ring,
                watchdog_enabled=payload.watchdog_enabled,
                max_failed_starts=payload.max_failed_starts,
            ),
        }
    except device_rollout.RolloutError as exc:
        _raise(exc)


@router.get("/api/v1/control/vp3-os/commissioning")
def commissioning() -> dict:
    try:
        return device_rollout.commissioning_report()
    except device_rollout.RolloutError as exc:
        _raise(exc)


@router.post("/api/v1/control/vp3-os/certifications")
def certify_hardware() -> dict:
    try:
        return device_rollout.certify_hardware()
    except device_rollout.RolloutError as exc:
        _raise(exc)


@router.get("/api/v1/control/vp3-os/certifications")
def certifications(limit: int = 20) -> dict:
    try:
        return {"items": device_rollout.list_certifications(limit)}
    except device_rollout.RolloutError as exc:
        _raise(exc)


@router.get("/api/v1/control/vp3-os/updates")
def updates(limit: int = 20) -> dict:
    try:
        return {"items": device_rollout.list_packages(limit)}
    except device_rollout.RolloutError as exc:
        _raise(exc)


@router.post("/api/v1/control/vp3-os/updates/stage")
def stage_update(package: UploadFile = File(...)) -> dict:
    try:
        return device_rollout.stage_package(
            package.file,
            package.filename or "vp3-os-update.zip",
        )
    except device_rollout.RolloutError as exc:
        _raise(exc)
    finally:
        package.file.close()


@router.post("/api/v1/control/vp3-os/updates/{package_id}/approve")
def approve_update(package_id: int) -> dict:
    try:
        return device_rollout.approve_package(package_id)
    except device_rollout.RolloutError as exc:
        _raise(exc)


@router.post("/api/v1/control/vp3-os/updates/{package_id}/apply")
def apply_update(package_id: int) -> dict:
    try:
        return device_rollout.request_apply(package_id)
    except device_rollout.RolloutError as exc:
        _raise(exc)


@router.post("/api/v1/control/vp3-os/updates/{package_id}/discard")
def discard_update(package_id: int) -> dict:
    try:
        return device_rollout.discard_package(package_id)
    except device_rollout.RolloutError as exc:
        _raise(exc)


@router.post("/api/v1/control/vp3-os/support-bundle")
def support_bundle():
    try:
        bundle = device_rollout.support_bundle()
    except device_rollout.RolloutError as exc:
        _raise(exc)
    return FileResponse(
        bundle["path"],
        media_type="application/zip",
        filename=bundle["name"],
        headers={"X-VP3-SHA256": bundle["sha256"]},
    )


@router.get("/api/v1/control/vp3-os/rollout/events")
def rollout_events(limit: int = 50) -> dict:
    return {"items": device_rollout.list_events(limit)}
