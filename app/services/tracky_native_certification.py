"""Durable, local-only Tracky native-camera acceptance evidence (Section 1D).

Uses the existing runtime_certification_runs SQLite ledger rather than creating
another certification database. These are owner-reviewed SOFTWARE test outcomes,
not independent proof of physical camera isolation or biometric recognition.
No frames, templates, raw device IDs, paths, or provider results are retained.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any

from ..database import db
from . import tracky_native_camera as native, vp3_os

CONTRACT = "tracky.native.certification.v1d"
TEST = "tracky_native_camera"
PRIVACY = "tracky_native_privacy_gate"
REVIEW = "tracky_native_owner_review"
_KEYS = frozenset({TEST, PRIVACY, REVIEW})


class NativeCertificationError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _device_fingerprint() -> str:
    # Local correlation only; neither raw machine identifiers nor paths stored.
    device = str(vp3_os.manifest(include_hardware=False).get("device_id") or "")
    return hashlib.sha256(device.encode()).hexdigest()[:16] if device else "unpaired_local"


def _record(key: str, status: str, evidence: dict[str, Any]) -> dict[str, Any]:
    if key not in _KEYS or status not in {"not_verified", "failed"}:
        raise ValueError("Unsupported Tracky certification evidence")
    # This service constructs all fields from a fixed allowlist. Do not accept
    # arbitrary provider, browser, or agent payloads in this SQLite record.
    clean = {name: value for name, value in evidence.items()
             if isinstance(value, (bool, int)) or (
                 isinstance(value, str) and name in {
                     "device_fingerprint", "runtime_version", "test_outcome",
                     "privacy_check", "review_state", "model_sha256"
                 } and len(value) <= 64)}
    row_id = secrets.token_hex(12)
    with db() as connection:
        connection.execute(
            "INSERT INTO runtime_certification_runs"
            "(id,test_key,status,duration_ms,evidence_json) VALUES (?,?,?,?,?)",
            (row_id, key, status, max(0, int(clean.get("capture_and_inference_ms") or 0)),
             json.dumps(clean, sort_keys=True, separators=(",", ":"))),
        )
    return {"id": row_id, "test_key": key, "status": status, "evidence": clean}


def record_test(phase: str) -> dict[str, Any]:
    snapshot = native.status()
    last = snapshot.get("last_test") or {}
    measurement = last.get("measurement") or {}
    model = native.model_preflight()
    successful = (
        phase == "completed" and model.get("model_integrity_verified") is True
        and last.get("status") == "native_detector_completed"
        and measurement.get("frame_captured") is True
        and measurement.get("detector_executed") is True
        and measurement.get("camera_release_call_completed") is True
        and last.get("driver_worker_exited") is True
    )
    return _record(TEST, "not_verified" if successful else "failed", {
        "device_fingerprint": _device_fingerprint(),
        "runtime_version": str(model.get("runtime_version") or "unknown")[:64],
        "model_sha256": str(model.get("model_sha256") or "")[:64],
        "test_outcome": "completed_owner_review_pending" if successful else "not_verified",
        "model_present": model.get("model_present") is True,
        "model_integrity_verified": model.get("model_integrity_verified") is True,
        "frame_captured": successful,
        "detector_executed": successful,
        "camera_release_call_completed": successful,
        "physical_camera_release_verified": False,
        "capture_and_inference_ms": min(30000, max(0, int(
            measurement.get("capture_and_inference_ms") or 0
        ))) if successful else 0,
        "inference_ms": min(30000, max(0, int(
            measurement.get("inference_ms") or 0
        ))) if successful else 0,
    })


def record_privacy() -> dict[str, Any]:
    return _record(PRIVACY, "not_verified", {
        "device_fingerprint": _device_fingerprint(),
        "privacy_check": "reported_software_gate_engaged",
        "software_gate_reported": True,
        "physical_camera_disconnect_verified": False,
    })


def _history(limit: int = 20) -> list[dict[str, Any]]:
    with db() as connection:
        rows = connection.execute(
            "SELECT id,test_key,status,duration_ms,evidence_json,created_at "
            "FROM runtime_certification_runs "
            "WHERE test_key IN (?,?,?) ORDER BY rowid DESC LIMIT ?",
            (TEST, PRIVACY, REVIEW, min(50, max(1, int(limit)))),
        ).fetchall()
    return [
        {"id": r["id"], "test_key": r["test_key"], "status": r["status"],
         "duration_ms": r["duration_ms"], "created_at": r["created_at"],
         "evidence": json.loads(r["evidence_json"] or "{}")}
        for r in rows
    ]


def status() -> dict[str, Any]:
    # Imports at call time because diagnosis records into this service.
    from . import tracky_native_diagnosis as diagnosis
    recent = _history()
    last_test = next((r for r in recent if r["test_key"] == TEST), None)
    last_privacy = next((r for r in recent if r["test_key"] == PRIVACY), None)
    last_review = next((r for r in recent if r["test_key"] == REVIEW), None)
    record = diagnosis._saved()
    live = native.status()
    measurement = (live.get("last_test") or {}).get("measurement") or {}
    current_test = (
        record.get("boot_id") == diagnosis._BOOT
        and record.get("phase") in {"completed", "privacy_reviewed"}
        and live.get("last_test", {}).get("status") == "native_detector_completed"
        and measurement.get("camera_release_call_completed") is True
        and not live.get("running") and not live.get("capture_worker_active")
    )
    privacy_reviewed = (
        record.get("boot_id") == diagnosis._BOOT
        and record.get("phase") == "privacy_reviewed"
    )
    current_model = native.model_preflight()
    test_model = (last_test or {}).get("evidence") or {}
    last_digest = str(test_model.get("model_sha256") or "")
    current_digest = str(current_model.get("model_sha256") or "")
    model_binding_matches = bool(
        current_model.get("model_present") is True
        and current_model.get("model_integrity_verified") is True
        and test_model.get("model_integrity_verified") is True
        and len(last_digest) == 64 and len(current_digest) == 64
        and last_digest == current_digest
        and test_model.get("runtime_version") == current_model.get("runtime_version")
        and test_model.get("test_outcome") == "completed_owner_review_pending"
    )
    needs_new_model_test = bool(
        last_test and test_model.get("test_outcome") == "completed_owner_review_pending"
        and not model_binding_matches
    )
    review_ready = bool(current_test and privacy_reviewed and model_binding_matches)
    current_accepted = bool(
        review_ready and last_review and last_test
        and last_review["evidence"].get("review_state") == "owner_attested_current_run"
        and last_review["evidence"].get("device_fingerprint") == _device_fingerprint()
        and (next((i for i,r in enumerate(recent) if r["test_key"] == REVIEW), 100)
             < next((i for i,r in enumerate(recent) if r["test_key"] == TEST), -1))
        and last_privacy
    )
    return {
        "contract": CONTRACT,
        "latest_test": last_test, "latest_privacy": last_privacy,
        "latest_owner_review": last_review,
        "review_ready": review_ready,
        "installed_model_matches_last_test": model_binding_matches,
        "requires_new_owner_test_due_model_change": needs_new_model_test,
        "owner_accepted_current_run": current_accepted,
        "hardware_certified": False,
        "independent_physical_camera_disconnect_verified": False,
        "identity_recognition_certified": False,
        "history": recent,
        "requires_installed_owner_acceptance": not current_accepted,
        "model_integrity_scope": "reviewed_local_sha256_and_last_test_binding_not_installer_signature",
    }


def accept_owner_review(*, consent: bool, installed_device: bool,
                        camera_release_observed: bool,
                        software_privacy_gate_observed: bool) -> dict[str, Any]:
    if not all((consent is True, installed_device is True,
                camera_release_observed is True,
                software_privacy_gate_observed is True)):
        raise NativeCertificationError(
            "Explicit installed-device owner review of capture, release and privacy is required.", 403
        )
    snapshot = status()
    if snapshot["owner_accepted_current_run"]:
        return snapshot
    if not snapshot["review_ready"]:
        raise NativeCertificationError(
            "Complete a current-process native test and reported privacy gate review first.", 409
        )
    _record(REVIEW, "not_verified", {
        "device_fingerprint": _device_fingerprint(),
        "review_state": "owner_attested_current_run",
        "installed_device_owner_attested": True,
        "camera_release_owner_observed": True,
        "software_privacy_gate_owner_observed": True,
        "physical_camera_disconnect_verified": False,
    })
    return status()
