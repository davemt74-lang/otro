from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .services import storage_maintenance

router=APIRouter()


class StoragePolicyUpdate(BaseModel):
    minimum_free_bytes:int|None=Field(default=None,ge=268435456,le=1099511627776)
    warning_free_percent:float|None=Field(default=None,ge=1.0,le=50.0)
    critical_free_percent:float|None=Field(default=None,ge=0.5,le=25.0)
    allow_owner_backup_prune:bool|None=None


@router.get("/api/v1/control/storage")
def control_storage()->dict:
    return storage_maintenance.status()


@router.get("/api/v1/control/storage/maintenance")
def control_storage_maintenance()->dict:
    return storage_maintenance.maintenance_plan()


@router.get("/api/v1/control/storage/policy")
def control_storage_policy()->dict:
    return storage_maintenance.policy()


@router.put("/api/v1/control/storage/policy")
def control_storage_policy_update(payload:StoragePolicyUpdate)->dict:
    try:
        return storage_maintenance.update_policy(payload.model_dump(exclude_none=True))
    except storage_maintenance.StorageMaintenanceError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc


@router.post("/api/v1/control/storage/maintenance/prune-backups")
def control_storage_prune_backups()->dict:
    try:
        return storage_maintenance.prune_backups()
    except storage_maintenance.StorageMaintenanceError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc


@router.get("/api/v1/control/storage/brain-context")
def control_storage_brain_context()->dict:
    return storage_maintenance.brain_context()


@router.get("/api/v1/control/storage/capability")
def control_storage_capability()->dict:
    return storage_maintenance.public_capability()
