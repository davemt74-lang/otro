"""Consent-gated, one-shot Tracky browser perception provider.

Connects HomeServer's bundled Tracky camera/inference engine to the canonical
active_perception API. There is no autonomous camera start or cloud biometric
transport. A browser-reported test is NOT server-native hardware certification.
Everything in this module is volatile; session/restarts fail closed.
"""
from __future__ import annotations

import hmac
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from . import tracky_physical_context as tracky, vp3_os

PROTOCOL = "tracky_owner_browser_perception.v1"
SCOPE = "owner_live_single_observation.v1"
HEARTBEAT_MAX_SECONDS = 7.0
SESSION_MAX_SECONDS = 90.0
OBSERVATION_WAIT_SECONDS = 9.0
ALLOWED_CALLER = "homeserver_owner_visual_runtime"
_COND = threading.Condition(threading.RLock())
_SESSION: dict[str, Any] | None = None
_PENDING: dict[str, Any] | None = None
_RESULT: dict[str, Any] | None = None
_ALLOWED_REQUEST = ""
_LAST: dict[str, Any] = {}


class OwnerPerceptionError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _privacy() -> bool:
    return bool(vp3_os.manifest(include_hardware=False, include_device_id=False)
                .get("privacy", {}).get("privacy_switch_engaged"))


def _our_provider() -> bool:
    return tracky._provider_snapshot()[0] is _provider


def _close_locked() -> None:
    global _SESSION, _PENDING, _RESULT, _ALLOWED_REQUEST
    _SESSION = None
    _PENDING = None
    _RESULT = None
    _ALLOWED_REQUEST = ""
    if _our_provider():
        tracky.unregister_provider()
    _COND.notify_all()


def _expired_locked() -> bool:
    if _SESSION is None:
        return True
    now = time.monotonic()
    return (now - float(_SESSION["started"]) > SESSION_MAX_SECONDS
            or now - float(_SESSION["last_seen"]) > HEARTBEAT_MAX_SECONDS)


def _ensure_live_locked() -> None:
    if _privacy() or _expired_locked():
        _close_locked()
        raise OwnerPerceptionError("Tracky session stopped: privacy, timeout, or browser disconnect.", 409)


def _verify_locked(token: str) -> None:
    if _SESSION is None or not isinstance(token, str) or not hmac.compare_digest(
        str(_SESSION["token"]), token
    ):
        raise OwnerPerceptionError("No matching owner-approved Tracky session.", 403)
    _ensure_live_locked()


def _touch_locked() -> None:
    if _SESSION is not None:
        _SESSION["last_seen"] = time.monotonic()


def status() -> dict[str, Any]:
    """Non-sensitive readiness; never implies native hardware certification."""
    with _COND:
        if _SESSION and (_expired_locked() or _privacy()):
            _close_locked()
        return {
            "protocol": PROTOCOL,
            "active": _SESSION is not None and _our_provider(),
            "surface": "integrated_homeserver_agent_chat_browser",
            "camera_permission": "owner_browser_permission_only",
            "live_browser_report": _SESSION is not None,
            "server_native_camera_certified": False,
            "provider_certified": False,
            "background_tracking_enabled": False,
            "face_identity_recognition_certified": False,
            "cloud_biometrics": False,
            "one_shot_only": True,
            "pending_observation": _PENDING is not None,
            "last_observation": dict(_LAST),
        }


def open_session(*, consent: bool, scope: str,
                 model_ready: bool, camera_ready: bool) -> dict[str, Any]:
    """Only invoked after a real owner gesture, browser permission and model load.

    Browser booleans are client claims, NOT hardware attestation.
    """
    if consent is not True or scope != SCOPE:
        raise OwnerPerceptionError("Approve the separate one-shot Agent Eyes test.", 403)
    if model_ready is not True or camera_ready is not True:
        raise OwnerPerceptionError("Open a live browser camera and load Tracky first.", 422)
    if _privacy():
        raise OwnerPerceptionError("Physical privacy is engaged.", 403)
    global _SESSION
    with _COND:
        if _SESSION:
            if _expired_locked():
                _close_locked()
            else:
                raise OwnerPerceptionError("A consented Tracky session is already active.", 409)
        existing, _, _ = tracky._provider_snapshot()
        if existing is not None:
            raise OwnerPerceptionError("Another Tracky perception provider is registered.", 409)
        token = secrets.token_urlsafe(32)
        _SESSION = {"token": token, "started": time.monotonic(),
                    "last_seen": time.monotonic()}
        try:
            tracky.register_provider(
                _provider,
                name="homeserver-owner-browser-one-shot",
                capabilities={
                    "requires_camera": False,  # Camera lives in browser, not OS inventory.
                    "timeout_seconds": OBSERVATION_WAIT_SECONDS + 1,
                    "surface": "owner_browser",
                    "owner_gesture_required": True,
                    "server_native_camera": False,
                    "background_tracking": False,
                },
            )
        except Exception:
            _close_locked()
            raise
        return {"session": token, "status": status()}


def heartbeat(*, session: str) -> dict[str, Any]:
    with _COND:
        _verify_locked(session)
        _touch_locked()
        return status()


def next_request(*, session: str) -> dict[str, Any]:
    with _COND:
        _verify_locked(session)
        _touch_locked()
        if _PENDING is None:
            return {"pending": False}
        return {
            "pending": True,
            "request_id": str(_PENDING["request_id"]),
            "request_type": "refresh_current_view",
        }


def _provider(request: dict[str, Any]) -> dict[str, Any]:
    global _PENDING, _RESULT, _ALLOWED_REQUEST
    with _COND:
        _ensure_live_locked()
        # The existing Tracky callback does not carry caller identity.
        # Read its durable governed request row rather than trusting a browser
        # claim or expanding the global provider payload contract.
        row = tracky._request_row(str(request.get("request_id") or ""))
        if (row is None or row.get("requested_by") != ALLOWED_CALLER
                or str(request.get("request_id") or "") != _ALLOWED_REQUEST
                or request.get("request_type") != "refresh_current_view"):
            raise tracky.TrackyPhysicalError(
                "This owner browser allows only its explicitly initiated one-shot test.", 403
            )
        if _PENDING is not None:
            raise tracky.TrackyPhysicalError("An owner browser observation is already pending.", 409)
        request_id = str(request["request_id"])
        _PENDING = {"request_id": request_id}
        _RESULT = None
        _COND.notify_all()
        until = time.monotonic() + OBSERVATION_WAIT_SECONDS
        try:
            while True:
                if _RESULT is not None:
                    observation = dict(_RESULT)
                    confidence = float(observation["confidence"])
                    count = int(observation["face_count"])
                    label = "none" if count == 0 else "one" if count == 1 else "multiple"
                    return {
                        "summary": "Owner-consented browser inference completed; faces detected: " + label,
                        "confidence": confidence,
                        # Deliberately no identifying data, raw media or auto-ingested events.
                    }
                if _SESSION is None or _expired_locked() or _privacy():
                    _close_locked()
                    raise tracky.TrackyPhysicalError("Owner browser or privacy session ended.", 409)
                remaining = until - time.monotonic()
                if remaining <= 0:
                    raise tracky.TrackyPhysicalError("Owner browser did not return a live observation.", 504)
                _COND.wait(timeout=min(0.4, remaining))
        finally:
            _PENDING = None
            _RESULT = None
            _ALLOWED_REQUEST = ""
            _COND.notify_all()


def submit(*, session: str, request_id: str, face_count: int,
           confidence: float, model_ready: bool, camera_ready: bool) -> dict[str, Any]:
    """Accept only bounded semantic observations from the consented browser."""
    global _RESULT
    with _COND:
        _verify_locked(session)
        if not model_ready or not camera_ready:
            raise OwnerPerceptionError("Local camera and inference must both be running.", 422)
        if _PENDING is None or not hmac.compare_digest(str(_PENDING["request_id"]), str(request_id)):
            raise OwnerPerceptionError("There is no matching outstanding observation.", 409)
        if _RESULT is not None:
            raise OwnerPerceptionError("This one-shot result has already been submitted.", 409)
        if (isinstance(face_count, bool) or not isinstance(face_count, int)
                or face_count < 0 or face_count > 2):
            raise OwnerPerceptionError("Face count is invalid.", 422)
        if (isinstance(confidence, bool) or not isinstance(confidence, (float, int))
                or not 0 <= confidence <= 1):
            raise OwnerPerceptionError("Detection confidence is invalid.", 422)
        _RESULT = {"face_count": face_count, "confidence": float(confidence)}
        _touch_locked()
        _COND.notify_all()
        return {"accepted": True, "biometrics_transmitted": False}


def run_owner_test(*, session: str) -> dict[str, Any]:
    """Governed canonical request. Never accept request IDs from external callers."""
    global _ALLOWED_REQUEST, _LAST
    with _COND:
        _verify_locked(session)
        if _ALLOWED_REQUEST:
            raise OwnerPerceptionError("Another Tracky test is already running.", 409)
        _ALLOWED_REQUEST = "tracky-owner-" + uuid.uuid4().hex
        request_id = _ALLOWED_REQUEST
        _touch_locked()
    try:
        outcome = tracky.active_perception(
            "refresh_current_view", request_id=request_id,
            reason="Owner-clicked one-shot local browser perception test",
            requested_by=ALLOWED_CALLER,
        )
        state = outcome.get("request") or {}
        success = state.get("status") == "completed"
        with _COND:
            _LAST = {
                "status": "browser_observation_completed" if success else "unable",
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "owner_review_required": True,
                "native_hardware_certified": False,
            }
        return {"request": {
            "status": str(state.get("status") or "unknown"),
            "reason": str((state.get("result") or {}).get("reason") or "")[:64],
        }, "readiness": status()}
    finally:
        with _COND:
            if _ALLOWED_REQUEST == request_id:
                _ALLOWED_REQUEST = ""


def close(*, session: str) -> dict[str, Any]:
    with _COND:
        _verify_locked(session)
        _close_locked()
        return status()
