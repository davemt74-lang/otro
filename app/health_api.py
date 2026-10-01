from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path

from .services import health_repair, maintenance_conversation

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


@router.get("/api/v1/control/health/issues/{issue_key}")
def control_health_issue(issue_key:str=Path(min_length=1,max_length=160))->dict:
    try:
        return maintenance_conversation.issue_detail({"issue_key":issue_key})
    except maintenance_conversation.MaintenanceError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc
