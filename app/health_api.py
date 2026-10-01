from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, Header

from .services import health_repair, maintenance_conversation, runtime_diagnostics, live_certification

from pydantic import BaseModel, Field

router=APIRouter()

class CertificationRequest(BaseModel):
    test_key:str=Field(min_length=2,max_length=60)
    consent:bool=False
    physical_capture_ack:bool=False



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


@router.get("/api/v1/control/runtime-diagnostics")
def control_runtime_diagnostics()->dict:
    """Private, low-impact inventory; no microphone/camera activation."""
    return runtime_diagnostics.inventory(probe=False)


@router.post("/api/v1/control/runtime-diagnostics/safe-probe")
def control_runtime_safe_probe()->dict:
    """Owner-initiated bounded Ollama connectivity check only; no recording."""
    return runtime_diagnostics.inventory(probe=True)


@router.get("/api/v1/control/runtime-certification")
def control_runtime_certification()->dict:
    return {"catalog":live_certification.catalog(),"history":live_certification.history()}


@router.post("/api/v1/control/runtime-certification/run")
def control_runtime_certification_run(
    body:CertificationRequest,
    requested_with:str|None=Header(None,alias="X-Requested-With"),
)->dict:
    # A custom same-origin fetch header blocks ordinary cross-site form posts;
    # the owner session guard remains enforced by the canonical control shell.
    if requested_with!="XMLHttpRequest":
        raise HTTPException(status_code=403,detail="Local owner UI required.")
    try:
        return live_certification.execute(
            body.test_key,consent=body.consent,
            physical_capture_ack=body.physical_capture_ack,
        )
    except live_certification.CertificationError as exc:
        raise HTTPException(status_code=exc.status_code,detail=str(exc)) from exc
