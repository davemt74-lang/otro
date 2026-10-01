from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, Header, Request

from .services import health_repair, maintenance_conversation, runtime_diagnostics, live_certification, governed_recordings

from pydantic import BaseModel, Field
from fastapi.responses import FileResponse

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


# Owner-only private recording routes. Recording never runs through a public
# media endpoint or the Cloud bridge.
class SaveRecordingRequest(BaseModel):
    kind: str
    seconds: int = Field(ge=2, le=30)
    consent: bool = False
    physical_capture_ack: bool = False


def _local_capture_request(request: Request, requested_with: str | None) -> None:
    if requested_with != "XMLHttpRequest":
        raise HTTPException(403, detail="Use the local owner recording interface.")
    # Owner may open this HomeServer over its LAN hostname. Keep capture local
    # to the server process and reject cross-site browser submissions instead
    # of incorrectly insisting that the BROWSER itself uses localhost.
    site = (request.headers.get("sec-fetch-site") or "").lower()
    if site and site not in ("same-origin", "none"):
        raise HTTPException(403, detail="Cross-site recording request blocked.")
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise HTTPException(403, detail="Recording origin mismatch.")


@router.get("/api/v1/control/governed-recordings")
def control_governed_recordings() -> dict:
    return {
        "status": governed_recordings.readiness(),
        "recordings": governed_recordings.list_recordings(),
    }


@router.post("/api/v1/control/governed-recordings/capture")
def control_governed_recordings_capture(
    body: SaveRecordingRequest,
    request: Request,
    requested_with: str | None = Header(None, alias="X-Requested-With"),
) -> dict:
    _local_capture_request(request, requested_with)
    try:
        return governed_recordings.capture(
            body.kind, body.seconds,
            consent=body.consent, capture_ack=body.physical_capture_ack,
        )
    except governed_recordings.RecordingError as exc:
        raise HTTPException(exc.status_code, detail=exc.reason) from exc


@router.get("/api/v1/control/governed-recordings/{recording_id}/download")
def control_governed_recordings_download(recording_id: str):
    try:
        path, item = governed_recordings.resolve(recording_id)
    except governed_recordings.RecordingError as exc:
        raise HTTPException(exc.status_code, detail="Recording unavailable.") from exc
    extension = "wav" if item["kind"] == "audio" else "mp4"
    response = FileResponse(
        path,
        media_type="audio/wav" if extension == "wav" else "video/mp4",
        filename="HomeServer-recording-" + recording_id + "." + extension,
        content_disposition_type="attachment",
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@router.delete("/api/v1/control/governed-recordings/{recording_id}")
def control_governed_recordings_delete(
    recording_id: str,
    request: Request,
    requested_with: str | None = Header(None, alias="X-Requested-With"),
) -> dict:
    _local_capture_request(request, requested_with)
    try:
        return governed_recordings.delete(recording_id)
    except governed_recordings.RecordingError as exc:
        raise HTTPException(exc.status_code, detail="Recording unavailable.") from exc


@router.post("/api/v1/control/governed-recordings/{recording_id}/transcribe")
def control_governed_recordings_transcribe(
    recording_id:str,request:Request,
    requested_with:str|None=Header(None,alias="X-Requested-With"),
)->dict:
    _local_capture_request(request,requested_with)
    try:
        return governed_recordings.transcribe_saved(recording_id)
    except governed_recordings.RecordingError as exc:
        raise HTTPException(exc.status_code,detail=exc.reason) from exc


@router.get("/api/v1/control/governed-recordings/{recording_id}/transcript")
def control_governed_recordings_transcript(recording_id:str)->dict:
    try:
        return governed_recordings.private_transcript(recording_id)
    except governed_recordings.RecordingError as exc:
        raise HTTPException(exc.status_code,detail="Private transcript unavailable.") from exc
