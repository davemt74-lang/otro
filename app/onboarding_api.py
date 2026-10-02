"""Owner-only scripted Agent Chat onboarding and approved provisioning."""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from .services import onboarding_chat, onboarding_visual, tracky_owner_perception, tracky_native_camera, tracky_native_diagnosis, tracky_native_certification, tracky_native_managed_session, tracky_agent_eyes, tracky_agent_eyes_recovery, tracky_agent_eyes_acceptance, tracky_visual_contact_link, tracky_physical_context, contacts

router = APIRouter(prefix="/api/v1/control/onboarding", tags=["agent-onboarding"])


def _require_ui(header: str | None) -> None:
    # State-changing operations must come from the authenticated local chat UI.
    if header != "XMLHttpRequest":
        raise HTTPException(status_code=403, detail="Use the authorized HomeServer Agent Chat canvas.")


def _invoke(function):
    try:
        return function()
    except onboarding_chat.OnboardingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/summary")
def summary() -> dict:
    return onboarding_chat.summary()


@router.get("/device/status")
def device_status() -> dict:
    return onboarding_chat.device_status()


@router.post("/voice/start")
def voice_start(x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    return _invoke(onboarding_chat.start_voice)


@router.post("/device/start")
def device_start(x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    return _invoke(onboarding_chat.new_device_code)


@router.post("/device/poll")
def device_poll(x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    return _invoke(onboarding_chat.poll_device_code)


@router.post("/device/reset")
def device_reset(x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    return _invoke(onboarding_chat.clear_pending_code)


class VisualStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent: bool
    scope: str = Field(max_length=80)


class VisualReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session: str = Field(min_length=32, max_length=128)
    participant_id: str = Field(min_length=8, max_length=100)
    samples: int = Field(ge=3, le=5)


class VisualDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    participant_id: str = Field(min_length=8, max_length=100)


def _visual_call(fn):
    try:
        return fn()
    except onboarding_visual.VisualOnboardingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/visual/status")
def visual_status() -> dict:
    return onboarding_visual.status()


@router.post("/visual/start")
def visual_start(payload: VisualStart, x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    return _visual_call(lambda: onboarding_visual.start(consent=payload.consent, scope=payload.scope))


@router.post("/visual/report")
def visual_report(payload: VisualReport, x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    return _visual_call(lambda: onboarding_visual.report(
        session=payload.session, participant_id=payload.participant_id, samples=payload.samples
    ))


@router.post("/visual/cancel")
def visual_cancel(x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    return onboarding_visual.cancel()


@router.post("/visual/delete")
def visual_delete(payload: VisualDelete, x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    return _visual_call(lambda: onboarding_visual.delete_report(participant_id=payload.participant_id))



# One-shot local browser perception. The session proof stays only in the
# authenticated owner's browser; never stored in persistent onboarding state.
class EyesOpen(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent: bool
    scope: str = Field(max_length=80)
    model_ready: bool
    camera_ready: bool


class EyesSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session: str = Field(min_length=32, max_length=128)


class EyesObservation(EyesSession):
    request_id: str = Field(min_length=16, max_length=128)
    face_count: int = Field(strict=True, ge=0, le=2)
    confidence: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    model_ready: bool
    camera_ready: bool


def _eyes_call(fn):
    try:
        return fn()
    except tracky_owner_perception.OwnerPerceptionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/visual/eyes/status")
def visual_eyes_status() -> dict:
    return tracky_owner_perception.status()


@router.post("/visual/eyes/open")
def visual_eyes_open(payload: EyesOpen, x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    return _eyes_call(lambda: tracky_owner_perception.open_session(
        consent=payload.consent, scope=payload.scope,
        model_ready=payload.model_ready, camera_ready=payload.camera_ready
    ))


@router.post("/visual/eyes/heartbeat")
def visual_eyes_heartbeat(payload: EyesSession, x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    return _eyes_call(lambda: tracky_owner_perception.heartbeat(session=payload.session))


@router.post("/visual/eyes/next")
def visual_eyes_next(payload: EyesSession, x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    return _eyes_call(lambda: tracky_owner_perception.next_request(session=payload.session))


@router.post("/visual/eyes/submit")
def visual_eyes_submit(payload: EyesObservation, x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    return _eyes_call(lambda: tracky_owner_perception.submit(
        session=payload.session, request_id=payload.request_id,
        face_count=payload.face_count, confidence=payload.confidence,
        model_ready=payload.model_ready, camera_ready=payload.camera_ready
    ))


@router.post("/visual/eyes/test")
def visual_eyes_test(payload: EyesSession, x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    return _eyes_call(lambda: tracky_owner_perception.run_owner_test(session=payload.session))


@router.post("/visual/eyes/close")
def visual_eyes_close(payload: EyesSession, x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    return _eyes_call(lambda: tracky_owner_perception.close(session=payload.session))


class NativeCameraTest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent: bool
    scope: str = Field(max_length=80)
    camera_index: int = Field(strict=True, ge=0, le=2)


def _native_call(fn):
    try:
        return fn()
    except tracky_native_camera.NativeCameraError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/visual/native/status")
def native_camera_status() -> dict:
    return tracky_native_camera.status()


@router.post("/visual/native/test")
def native_camera_test(payload: NativeCameraTest,
                       x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    def run():
        # Preserve validation without recording a phantom started attempt.
        if payload.consent is not True or payload.scope != tracky_native_camera.SCOPE:
            raise tracky_native_camera.NativeCameraError("Explicit native-camera consent is required.",403)
        state=tracky_native_camera.status()
        if state["running"] or state["provider_conflict"] or state["privacy_engaged"]:
            return tracky_native_camera.test(consent=payload.consent,scope=payload.scope,
                                            camera_index=payload.camera_index)
        tracky_native_diagnosis.before_test()
        try:
            result=tracky_native_camera.test(
                consent=payload.consent,scope=payload.scope,camera_index=payload.camera_index
            )
            phase="completed" if result.get("request",{}).get("status")=="completed" else "failed"
            tracky_native_diagnosis.after_test(status=phase)
            return result
        except Exception:
            tracky_native_diagnosis.after_test(status="failed")
            raise
    return _native_call(run)


@router.post("/visual/native/cancel")
def native_camera_cancel(x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    return tracky_native_camera.cancel()



class NativePrivacyReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent: bool


@router.get("/visual/native/diagnose")
def native_camera_diagnose() -> dict:
    return tracky_native_diagnosis.diagnose()


@router.post("/visual/native/privacy-review")
def native_camera_privacy_review(
    payload: NativePrivacyReview, x_requested_with: str | None = Header(default=None)
) -> dict:
    _require_ui(x_requested_with)
    if payload.consent is not True:
        raise HTTPException(status_code=403,detail="Explicit owner approval is required.")
    return tracky_native_diagnosis.privacy_review(consent=True)


class NativeOwnerAcceptance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent: bool
    installed_device: bool
    camera_release_observed: bool
    software_privacy_gate_observed: bool


@router.get("/visual/native/certification")
def native_certification_status() -> dict:
    return tracky_native_certification.status()


@router.post("/visual/native/owner-review")
def native_certification_owner_review(payload: NativeOwnerAcceptance,
                                      x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    try:
        return tracky_native_certification.accept_owner_review(**payload.model_dump())
    except tracky_native_certification.NativeCertificationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


class NativeManagedSessionStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent: bool
    scope: str = Field(max_length=80)
    camera_index: int = Field(strict=True, ge=0, le=2)
    sample_count: int = Field(default=3, strict=True, ge=1, le=12)
    interval_seconds: int = Field(default=5, strict=True, ge=5, le=15)


@router.get("/visual/native/session/status")
def native_managed_status() -> dict:
    return tracky_native_managed_session.status()


@router.post("/visual/native/session/start")
def native_managed_start(payload: NativeManagedSessionStart,
                         x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    try:
        return tracky_native_managed_session.start(**payload.model_dump())
    except tracky_native_managed_session.ManagedSessionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/visual/native/session/stop")
def native_managed_stop(x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    return tracky_native_managed_session.stop()


@router.post("/visual/native/session/heartbeat")
def native_managed_heartbeat(x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    try:
        return tracky_native_managed_session.heartbeat()
    except tracky_native_managed_session.ManagedSessionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


class AgentEyesStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent: bool = Field(strict=True)
    scope: str = Field(max_length=80)
    camera_index: int = Field(strict=True, ge=0, le=2)
    sample_count: int = Field(default=6, strict=True, ge=1, le=60)
    interval_seconds: int = Field(default=5, strict=True, ge=5, le=30)
    max_session_seconds: int = Field(default=120, strict=True, ge=60, le=600)
    max_cpu_seconds: int = Field(default=12, strict=True, ge=4, le=30)


@router.get("/visual/agent-eyes/status")
def agent_eyes_status() -> dict:
    return tracky_agent_eyes.status()


@router.post("/visual/agent-eyes/start")
def agent_eyes_start(payload: AgentEyesStart,
                     x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    try:
        return tracky_agent_eyes.start(**payload.model_dump())
    except tracky_agent_eyes.AgentEyesError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/visual/agent-eyes/stop")
def agent_eyes_stop(x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    return tracky_agent_eyes.stop()


@router.post("/visual/agent-eyes/heartbeat")
def agent_eyes_heartbeat(x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    try:
        return tracky_agent_eyes.heartbeat()
    except tracky_agent_eyes.AgentEyesError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


class AgentEyesRecoveryAck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent: bool = Field(strict=True)
    camera_stopped_observed: bool = Field(strict=True)
    fresh_consent_understood: bool = Field(strict=True)


@router.get("/visual/agent-eyes/recovery")
def agent_eyes_recovery_status() -> dict:
    return tracky_agent_eyes_recovery.status()


@router.post("/visual/agent-eyes/recovery/acknowledge")
def agent_eyes_recovery_acknowledge(payload: AgentEyesRecoveryAck,
                                   x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    try:
        return tracky_agent_eyes_recovery.acknowledge(**payload.model_dump())
    except tracky_agent_eyes_recovery.RecoveryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


class AgentEyesInstalledExercise(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step: str = Field(max_length=30)
    consent: bool = Field(strict=True)
    inspected_camera_release: bool = Field(strict=True)


@router.get("/visual/agent-eyes/installed-exercise")
def agent_eyes_installed_exercise_status() -> dict:
    return tracky_agent_eyes_acceptance.status()


@router.post("/visual/agent-eyes/installed-exercise/record")
def agent_eyes_installed_exercise_record(
    payload: AgentEyesInstalledExercise,
    x_requested_with: str | None = Header(default=None),
) -> dict:
    _require_ui(x_requested_with)
    try:
        return tracky_agent_eyes_acceptance.record(**payload.model_dump())
    except tracky_agent_eyes_acceptance.AcceptanceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


class VisualContactAssociation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent: bool
    scope: str = Field(max_length=80)
    participant_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    contact_id: int = Field(strict=True, ge=1)


class VisualContactRevoke(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent: bool


def _visual_link_call(fn):
    try:
        return fn()
    except tracky_visual_contact_link.VisualContactLinkError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/visual/contact-link/status")
def visual_contact_link_status() -> dict:
    return tracky_visual_contact_link.status()


@router.get("/visual/contact-link/contacts")
def visual_contact_link_contacts(q: str = Query(default="", max_length=120)) -> dict:
    # Only searched existing local contacts, never federated Cloud mirrors.
    items = contacts.list_contacts(query=q, limit=50)
    return {"items": [{"id": row["id"], "display_name": row["display_name"]}
                      for row in items], "automatic_contact_creation": False}


@router.post("/visual/contact-link/associate")
def visual_contact_link_associate(payload: VisualContactAssociation,
                                  x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    return _visual_link_call(lambda: tracky_visual_contact_link.associate(**payload.model_dump()))


@router.post("/visual/contact-link/revoke")
def visual_contact_link_revoke(payload: VisualContactRevoke,
                               x_requested_with: str | None = Header(default=None)) -> dict:
    _require_ui(x_requested_with)
    return _visual_link_call(lambda: tracky_visual_contact_link.revoke(consent=payload.consent))


@router.get("/visual/contact-link/receipt")
def visual_contact_link_receipt() -> dict:
    return tracky_visual_contact_link.local_receipt()


class VisualCloudStatusSharing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent: bool = Field(strict=True)
    scope: str = Field(max_length=80)
    enabled: bool = Field(strict=True)


class VisualCloudStatusSync(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent: bool = Field(strict=True)


@router.post("/visual/contact-link/cloud-sharing")
def visual_contact_cloud_sharing(
    payload: VisualCloudStatusSharing,
    x_requested_with: str | None = Header(default=None),
) -> dict:
    _require_ui(x_requested_with)
    return _visual_link_call(
        lambda: tracky_visual_contact_link.set_cloud_sharing(**payload.model_dump())
    )


@router.post("/visual/contact-link/cloud-sync")
def visual_contact_cloud_sync(
    payload: VisualCloudStatusSync,
    x_requested_with: str | None = Header(default=None),
) -> dict:
    _require_ui(x_requested_with)
    if payload.consent is not True:
        raise HTTPException(status_code=403, detail="Owner must explicitly approve status sync.")
    if tracky_visual_contact_link.cloud_projection() is None:
        raise HTTPException(status_code=409, detail="No approved semantic status or revocation to deliver.")
    try:
        outcome = tracky_physical_context.sync_cloud(force=True)
    except tracky_physical_context.TrackyPhysicalError as exc:
        raise HTTPException(status_code=exc.status_code,
                            detail="Authenticated Tracky Cloud sync is unavailable.") from exc
    association = tracky_visual_contact_link.status()
    visual_delivery = outcome.get("visual_owner_delivery")
    if not isinstance(visual_delivery, dict):
        visual_delivery = {}
    return {
        "site_sync_accepted": outcome.get("ok") is True,
        # Cloud may have received the previous generation if consent was
        # revoked while HTTPS was in flight. Never claim CURRENT delivery.
        "visual_status_sent": str(visual_delivery.get("status_sent") or ""),
        "visual_status_current_generation_acknowledged": bool(
            visual_delivery.get("current_generation_acknowledged") is True
            and association.get("cloud_current_generation_acknowledged") is True
            and association.get("cloud_last_accepted_status") == visual_delivery.get("status_sent")
        ),
        "association": association,
        "cloud_account_consent_independently_required": True,
        "face_recognition_verified": False,
        "cloud_biometric_storage": False,
    }
