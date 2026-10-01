from __future__ import annotations

from fastapi import APIRouter

from .services import health_repair

router=APIRouter()


@router.get("/api/v1/control/health")
def control_health()->dict:
    return health_repair.status()


@router.get("/api/v1/control/health/repair-plan")
def control_health_repair_plan()->dict:
    return health_repair.repair_plan()


@router.get("/api/v1/control/health/brain-context")
def control_health_brain_context()->dict:
    return health_repair.brain_context()


@router.get("/api/v1/control/health/capability")
def control_health_capability()->dict:
    return health_repair.public_capability()
