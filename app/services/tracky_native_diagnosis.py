"""Tracky Section 1C: installed HomeServer native-camera diagnosis and recovery.

Read-only preflight never enumerates or opens a camera. Only the existing
owner-approved native test can perform capture; results remain strictly
non-biometric and a green CI fixture never constitutes real hardware proof.
Reuses the existing HomeServer diagnostic and owner-only API authorities.
"""
from __future__ import annotations

import importlib
import importlib.util
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import system_state, tracky_native_camera as native, vp3_os

CONTRACT = "tracky.native.live-diagnosis.v1c"
KEY = "tracky.native.live-diagnosis.v1c"
_BOOT = secrets.token_hex(16)


def _saved() -> dict[str, Any]:
    data = system_state._read_setting(KEY, {})
    return data if isinstance(data, dict) else {}


def _save(value: dict[str, Any]) -> None:
    # No raw camera frames, identity labels, descriptors, device paths or tokens.
    system_state._write_setting(KEY, value)


def _model_preflight() -> dict[str, Any]:
    try:
        if importlib.util.find_spec("cv2") is None:
            return {"installed": False, "model_present": False, "runtime_loaded": False,
                    "reason": "opencv_missing"}
        module = importlib.import_module("cv2")
        model = Path(module.data.haarcascades) / "haarcascade_frontalface_default.xml"
        return {
            "installed": True,
            "model_present": model.is_file(),
            "runtime_loaded": True,
            "reason": "ready" if model.is_file() else "face_model_missing",
        }
    except Exception:
        return {"installed": False, "model_present": False, "runtime_loaded": False,
                "reason": "opencv_unavailable"}


def diagnose() -> dict[str, Any]:
    """Read installed readiness without touching any camera driver."""
    model = _model_preflight()
    model["model_integrity_verified"] = bool(
        model.get("installed") and model.get("model_present")
        and native.model_preflight().get("model_integrity_verified")
    )
    run = native.status()
    inventory = vp3_os.hardware_inventory()
    camera = inventory.get("camera") or {}
    privacy = inventory.get("privacy_switch") or {}
    saved = _saved()
    interrupted = saved.get("phase") == "running" and (
        saved.get("boot_id") != _BOOT or not run.get("running")
    )
    previous = dict(run.get("last_test") or {})
    privacy_blocked = bool(run["privacy_engaged"])
    issues = []
    if not model["installed"]:
        issues.append({"code": model["reason"], "severity": "failed", "repair": "reinstall_homeserver"})
    elif not model["model_present"]:
        issues.append({"code": "face_model_missing", "severity": "failed", "repair": "reinstall_homeserver"})
    if model["installed"] and model["model_present"] and not model["model_integrity_verified"]:
        issues.append({"code": "face_model_integrity_mismatch", "severity": "failed",
                       "repair": "reinstall_homeserver"})
    if privacy_blocked:
        issues.append({"code": "privacy_engaged", "severity": "attention", "repair": "owner_physical_action"})
    if run["provider_conflict"]:
        issues.append({"code": "provider_busy", "severity": "attention", "repair": "wait_for_existing_provider"})
    if previous.get("status") == "not_verified":
        issues.append({"code": "last_capture_failed", "severity": "attention", "repair": "check_camera_permissions"})
    if interrupted:
        issues.append({"code": "interrupted_prior_attempt", "severity": "attention", "repair": "new_owner_test"})
    if saved.get("phase") in {"completed", "privacy_reviewed"} and not previous:
        issues.append({"code": "prior_test_requires_repeat_after_restart", "severity": "attention",
                       "repair": "new_owner_test"})
    # Hardware inventory can be absent even if cv2 can open a USB camera.
    hardware_inventory_ready = bool(camera.get("present") and camera.get("ready"))
    if not hardware_inventory_ready:
        issues.append({"code": "camera_inventory_unverified", "severity": "info",
                       "repair": "owner_select_camera_and_test"})
    return {
        "contract": CONTRACT, "model": model, "camera_inventory_ready": hardware_inventory_ready,
        "privacy_switch_reported": bool(privacy.get("present")),
        "privacy_engaged": privacy_blocked,
        "native_running": bool(run["running"]), "provider_conflict": bool(run["provider_conflict"]),
        "last_test_status": str(previous.get("status") or ""),
        "last_test_current_process": bool(previous),
        "interrupted_prior_attempt": interrupted,
        "owner_review_required": True,
        "hardware_certified": False,
        "face_recognition_certified": False,
        "cloud_biometrics": False,
        "issues": issues,
        "recommendations": [
            {"key": "repair_runtime", "when": "opencv_missing_or_model_missing",
             "kind": "reinstall_reviewed_homeserver", "automated": False, "owner_approval": True},
            {"key": "select_camera", "when": "camera_unverified",
             "kind": "owner_choose_camera_index", "automated": False, "owner_approval": True},
            {"key": "run_live_test", "when": "runtime_ready_and_privacy_off",
             "kind": "existing_owner_native_test", "automated": False, "owner_approval": True},
            {"key": "privacy_review", "when": "live_test_complete",
             "kind": "owner_validate_physical_privacy_control", "automated": False, "owner_approval": True},
        ],
    }


def before_test() -> None:
    _save({"phase": "running", "boot_id": _BOOT,
           "started_at": datetime.now(timezone.utc).isoformat()})


def after_test(*, status: str) -> None:
    if status not in {"completed", "failed", "cancelled"}:
        status = "failed"
    _save({"phase": status, "boot_id": _BOOT,
           "finished_at": datetime.now(timezone.utc).isoformat()})
    from . import tracky_native_certification as certification
    certification.record_test(status)


def privacy_review(*, consent: bool) -> dict[str, Any]:
    """Safe live privacy challenge: never opens the camera to verify a block."""
    if consent is not True:
        raise ValueError("Explicit owner approval required for privacy review.")
    hardware = vp3_os.hardware_inventory().get("privacy_switch") or {}
    # VP3 OS physical_disconnect currently describes the microphone power
    # circuit. It does NOT prove the camera is physically disconnected.
    # Validate only the software camera gate controlled by the reported
    # privacy switch. Installed-device physical camera proof is separate.
    engaged = bool(hardware.get("present") and hardware.get("ready")
                   and hardware.get("engaged"))
    if not engaged or not native.status()["privacy_engaged"]:
        return {"privacy_check": "not_verified",
                "instruction": "Engage the hardware-reported privacy switch, then retry. This check never opens the camera.",
                "camera_opened": False, "hardware_certified": False}
    _save({"phase": "privacy_reviewed", "boot_id": _BOOT,
           "privacy_reviewed_at": datetime.now(timezone.utc).isoformat()})
    from . import tracky_native_certification as certification
    certification.record_privacy()
    return {"privacy_check": "reported_software_gate_engaged",
            "camera_opened": False, "hardware_certified": False,
            "instruction": "Reported privacy switch engages HomeServer's camera software gate; physical camera disconnect and on-device acceptance remain unverified."}
