"""Native owner-initiated Tracky camera and face-detection runtime (v1).

Camera and Haar model run in the HomeServer process, not an open browser.
A separate explicit owner gesture is required per test; no camera opens during
startup, discovery, diagnostics, heartbeat or routine Cloud requests.

This is *face detection*, not face-identity matching or production hardware
certification. Neither images nor face templates are saved or synchronized.
"""
from __future__ import annotations

import importlib
import importlib.util
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import tracky_physical_context as tracky, vp3_os

CONTRACT = "tracky.native.owner_camera.v1"
SCOPE = "owner-native-single-camera-test.v1"
CALLER = "homeserver_owner_native_camera"
MODEL_NAME = "opencv-bundled-haar-frontalface"
CAMERA_INDICES = (0, 1, 2)
# Do not exceed the existing canonical provider's bounded timeout.
CAPTURE_SECONDS = 7.0
_LOCK = threading.RLock()
# Global OS-camera lock shared by one-shot and supervised native capture.
_CAMERA_CAPTURE_LOCK = threading.Lock()
_BUSY = False
_INFLIGHT = False  # Camera-driver worker still active after an upstream timeout.
_ALLOWED_ID = ""
_CANCEL = threading.Event()
_LAST: dict[str, Any] = {}
_MEASUREMENT: dict[str, Any] = {}


class NativeCameraError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _privacy() -> bool:
    return bool(vp3_os.manifest(include_hardware=False, include_device_id=False)
                .get("privacy", {}).get("privacy_switch_engaged"))


def model_preflight() -> dict[str, Any]:
    try:
        if importlib.util.find_spec('cv2') is None:
            return {'installed': False, 'model_present': False, 'runtime_version': 'missing'}
        cv2 = importlib.import_module('cv2')
        present = (Path(cv2.data.haarcascades) / 'haarcascade_frontalface_default.xml').is_file()
        return {'installed': True, 'model_present': present,
                'runtime_version': str(getattr(cv2, '__version__', 'unknown'))[:64]}
    except Exception:
        return {'installed': False, 'model_present': False, 'runtime_version': 'unavailable'}


def _dependency() -> dict[str, Any]:
    # Passive inspection only: never loads camera, native driver or frame.
    try:
        installed = importlib.util.find_spec("cv2") is not None
    except (ImportError, ValueError):
        installed = False
    return {
        "runtime": MODEL_NAME,
        "installed": installed,
        "model_bundled_with_runtime": model_preflight()["model_present"],
        "auto_installed_with_homeserver": True,
    }


def status() -> dict[str, Any]:
    with _LOCK:
        provider, _, _ = tracky._provider_snapshot()
        return {
            "contract": CONTRACT,
            "native_dependency": _dependency(),
            "camera_indices_supported": list(CAMERA_INDICES),
            "camera_discovery": "on_owner_approved_test_only",
            "running": _BUSY,
            "provider_active": provider is _provider,
            "provider_conflict": provider is not None and provider is not _provider,
            "privacy_engaged": _privacy(),
            "last_test": dict(_LAST),
            "capture_worker_active": _INFLIGHT,
            "capture_at_startup": False,
            "continuous_recognition": False,
            "identity_recognition": False,
            "hardware_certified": False,
            "cloud_media_sync": False,
            "requires_owner_approval": True,
        }


def capture_busy() -> bool:
    return _CAMERA_CAPTURE_LOCK.locked()


def _observe_exclusive(index: int, cancel: threading.Event) -> dict[str, Any]:
    if cancel.is_set() or _privacy():
        raise NativeCameraError("Consent or hardware privacy prevents native capture.", 403)
    try:
        import cv2  # installed and bundled with HomeServer; no runtime downloads
    except ImportError as exc:
        raise NativeCameraError("Native camera runtime is missing; repair the HomeServer installation.", 503) from exc
    cascade_file = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
    if not cascade_file.is_file():
        raise NativeCameraError("Bundled native detection model is missing.", 503)
    capture = None
    released = False
    start = time.monotonic()
    try:
        # Do not enumerate or open other cameras: use only the index selected
        # by the owner. Open is bounded by the canonical provider's timeout.
        capture = cv2.VideoCapture(index)
        if not capture or not capture.isOpened():
            raise NativeCameraError("Selected camera is unavailable.", 503)
        # Driver support is optional; acquisition still has a bounded upper
        # layer and cancellation is rechecked before detection.
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        if cancel.is_set() or _privacy():
            raise NativeCameraError("Capture cancelled or privacy engaged.", 403)
        ok, frame = capture.read()
        if not ok or frame is None:
            raise NativeCameraError("No video frame received from selected camera.", 503)
        if cancel.is_set() or _privacy():
            raise NativeCameraError("Privacy or consent changed before inference.", 403)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        detector = cv2.CascadeClassifier(str(cascade_file))
        if detector.empty():
            raise NativeCameraError("Bundled native detector cannot load.", 503)
        inferred_at = time.monotonic()
        rectangles = detector.detectMultiScale(gray, scaleFactor=1.2,
                                               minNeighbors=5, minSize=(60, 60))
        if cancel.is_set() or _privacy():
            raise NativeCameraError("Privacy or consent changed during inference.", 403)
        return {
            "summary": "Owner-approved native camera inference completed; "
                       + ("no" if len(rectangles) == 0 else "one" if len(rectangles) == 1 else "multiple")
                       + " possible face regions detected.",
            # Cascade has no calibrated probability: 0 avoids invented scores.
            "confidence": 0.0,
            "native_camera_frame_captured": True,
            "native_detector_executed": True,
            "face_regions_detected": min(len(rectangles), 2),
            "capture_and_inference_ms": min(30000, int((time.monotonic()-start)*1000)),
            "inference_ms": min(30000, int((time.monotonic()-inferred_at)*1000)),
        }
    finally:
        if capture is not None:
            capture.release()
            released = True


def _observe(index: int, cancel: threading.Event) -> dict[str, Any]:
    if not _CAMERA_CAPTURE_LOCK.acquire(blocking=False):
        raise NativeCameraError('A native camera capture is already in progress.', 409)
    try:
        return _observe_exclusive(index, cancel)
    finally:
        _CAMERA_CAPTURE_LOCK.release()


def _provider(request: dict[str, Any]) -> dict[str, Any]:
    global _INFLIGHT, _MEASUREMENT
    # Only the request ID generated by the direct owner-only endpoint can use
    # this ephemeral native provider. Cloud-triggered calls are rejected.
    with _LOCK:
        if (not _BUSY or request.get("request_id") != _ALLOWED_ID
                or request.get("request_type") != "refresh_current_view"):
            raise tracky.TrackyPhysicalError(
                "Native Tracky camera requires a new owner-approved test.", 403)
        if _INFLIGHT:
            raise tracky.TrackyPhysicalError("Native camera driver is already capturing.", 409)
        _INFLIGHT = True
        index = _SELECTED_INDEX
    try:
        result = _observe(index, _CANCEL)
        # The local per-process measurement is non-biometric; the canonical
        # Tracky ledger still receives only its governed summary/confidence.
        with _LOCK:
            _MEASUREMENT = {
                "frame_captured": True, "detector_executed": True,
                "camera_release_completed": True,
                "camera_release_call_completed": True,
                "physical_camera_release_verified": False,
                "capture_and_inference_ms": result["capture_and_inference_ms"],
                "inference_ms": result["inference_ms"],
                "face_count_category": "none" if result["face_regions_detected"] == 0
                                      else "one" if result["face_regions_detected"] == 1 else "multiple",
            }
        return {"summary": result["summary"], "confidence": result["confidence"]}
    finally:
        with _LOCK:
            _INFLIGHT = False


_SELECTED_INDEX = 0


def test(*, consent: bool, scope: str, camera_index: int) -> dict[str, Any]:
    global _BUSY, _ALLOWED_ID, _SELECTED_INDEX, _LAST, _MEASUREMENT
    if consent is not True or scope != SCOPE:
        raise NativeCameraError("Explicit owner consent for this native camera test is required.", 403)
    if type(camera_index) is not int or camera_index not in CAMERA_INDICES:
        raise NativeCameraError("Select a supported local camera index.", 422)
    if _privacy():
        raise NativeCameraError("Physical privacy is engaged.", 403)
    if not model_preflight()["model_present"]:
        raise NativeCameraError("Native camera detector is not installed.", 503)
    with _LOCK:
        if _BUSY or _INFLIGHT:
            raise NativeCameraError("A native Tracky camera driver is still active.", 409)
        provider, _, _ = tracky._provider_snapshot()
        if provider is not None:
            raise NativeCameraError("Another Tracky perception provider is active.", 409)
        _BUSY = True
        _CANCEL.clear()
        _MEASUREMENT = {}
        _SELECTED_INDEX = camera_index
        _ALLOWED_ID = "tracky-native-" + uuid.uuid4().hex
        request_id = _ALLOWED_ID
        try:
            # requires_camera=False because the owner-approved callback probes
            # the actual camera itself; generic hardware inventory is not proof
            # of a currently opened device.
            tracky.register_provider(
                _provider, name="homeserver-native-opencv-owner-test",
                capabilities={
                    "surface": "native_owner_on_demand",
                    "requires_camera": False,
                    "native_detector": MODEL_NAME,
                    "identity_recognition": False,
                    "background_tracking": False,
                    "camera_opens_on_approval_only": True,
                    "timeout_seconds": CAPTURE_SECONDS,
                }, replace=False,
            )
        except Exception:
            _BUSY = False
            _ALLOWED_ID = ""
            raise
    try:
        result = tracky.active_perception(
            "refresh_current_view", request_id=request_id,
            reason="Owner-approved native HomeServer camera test",
            requested_by=CALLER,
        )
        row = result.get("request") or {}
        completed = row.get("status") == "completed"
        with _LOCK:
            _LAST = {
                "status": "native_detector_completed" if completed else "not_verified",
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "camera_index": camera_index,
                "reason": str((row.get("result") or {}).get("reason") or "")[:64],
                "owner_review_required": True,
                "hardware_certified": False,
                "identity_recognition": False,
                "measurement": dict(_MEASUREMENT) if completed and not _privacy() else {},
                "driver_worker_exited": not _INFLIGHT,
            }
        return {
            "request": {"status": row.get("status", "unknown"),
                        "reason": _LAST["reason"]},
            "native": status(),
        }
    finally:
        _CANCEL.set()
        tracky.unregister_provider(expected=_provider)
        with _LOCK:
            _BUSY = False
            _ALLOWED_ID = ""
            # Keep cancellation asserted for any detector thread still inside
            # an uninterruptible OS camera-driver call after provider timeout.
            # Only the next explicit owner-approved test may clear it.


def cancel() -> dict[str, Any]:
    """Owner revocation; prevents late detection results from being accepted."""
    _CANCEL.set()
    return status()
