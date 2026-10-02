"""Tracky 1G2B3: guided on-device owner acceptance exercise, never a camera runtime.

Stage receipts require *actual* current-process canonical Agent Eyes terminal
evidence, idle shared driver, current reviewed model and an owner declaration.
They are owner-reported exercises, not independent physical certification.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any

from ..database import db
from . import tracky_native_session_evidence as evidence
from . import tracky_native_certification as cert
from . import tracky_native_camera as native
from . import tracky_physical_context as physical

CONTRACT = "tracky.agent-eyes.installed-exercise.v1g2b3"
TEST_KEY = "tracky_agent_eyes_installed_exercise"
_BOOT = secrets.token_hex(12)
_STEPS = {
    "owner_stop": "owner_stopped",
    "privacy_revocation": "privacy_engaged",
    "presence_lease": "owner_presence_expired",
}


class AcceptanceError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _review(approval: dict[str, Any]) -> str:
    return str((approval.get("latest_owner_review") or {}).get("id") or "")[:48]


def _current(*, approval: dict[str, Any], model: dict[str, Any],
             worker: dict[str, Any], exposure: dict[str, Any]) -> dict[str, Any]:
    from . import tracky_native_managed_session as managed
    current = evidence.latest()
    busy = bool(worker.get("active") or native.capture_busy()
                or native.status().get("running")
                or native.status().get("capture_worker_active")
                or exposure.get("registered"))
    digest = str(model.get("model_sha256") or "")
    ready = bool(approval.get("owner_accepted_current_run")
                 and model.get("model_integrity_verified") is True
                 and len(digest) == 64
                 and _review(approval)
                 and current.get("owner_surface") == "agent_eyes"
                 and current.get("current_process") is True
                 and current.get("phase") == "stopped"
                 and current.get("reason") in _STEPS.values()
                 and not current.get("recover_before_new_session")
                 and not busy)
    return {
        "ready": ready, "busy": busy, "review_id": _review(approval),
        "digest": digest, "last": current, "boot": _BOOT,
        "current_step": next((s for s, reason in _STEPS.items()
                              if reason == current.get("reason")), ""),
    }


def status(*, approval: dict[str, Any] | None = None,
           worker: dict[str, Any] | None = None,
           exposure: dict[str, Any] | None = None) -> dict[str, Any]:
    from . import tracky_native_managed_session as managed
    approved = approval if approval is not None else cert.status()
    worker_state = worker if worker is not None else managed.status()
    provider_state = exposure if exposure is not None else physical.provider_exposure()
    model = native.model_preflight()
    current = _current(approval=approved, model=model, worker=worker_state,
                       exposure=provider_state)
    # Existing redacted local certification ledger; no extra schema, frames,
    # device identifiers, biometrics or Cloud media projection.
    with db() as connection:
        rows = connection.execute(
            "SELECT evidence_json FROM runtime_certification_runs "
            "WHERE test_key=? ORDER BY rowid DESC LIMIT 50", (TEST_KEY,)
        ).fetchall()
    completed: set[str] = set()
    for row in rows:
        try:
            saved = json.loads(row["evidence_json"])
        except (TypeError, ValueError):
            continue
        if (isinstance(saved, dict) and saved.get("boot") == _BOOT
                and saved.get("review_id") == current["review_id"]
                and saved.get("model_sha256") == current["digest"]
                and saved.get("device_fingerprint") == cert._device_fingerprint()
                and bool(current["review_id"]) and len(current["digest"]) == 64):
            if saved.get("step") in _STEPS:
                completed.add(saved["step"])
    readiness = bool(approved.get("owner_accepted_current_run")
                     and model.get("model_integrity_verified") is True
                     and len(current["digest"]) == 64 and current["review_id"])
    pending = [step for step in _STEPS if step not in completed]
    return {
        "contract": CONTRACT,
        "steps": list(_STEPS),
        "completed_steps": sorted(completed),
        "pending_steps": pending,
        "current_session_step": current["current_step"] if current["ready"] else "",
        "ready_to_record": current["ready"],
        "shared_camera_busy": current["busy"],
        "current_review_ready": readiness,
        "owner_exercise_complete": bool(readiness and not pending),
        "last_terminal_phase": current["last"]["phase"],
        "last_terminal_reason": current["last"]["reason"] if
            current["last"]["owner_surface"] == "agent_eyes" else "",
        "owner_reported_exercise_only": True,
        "independent_hardware_certified": False,
        "physical_camera_release_verified": False,
        "extended_mode_enabled": False,
        "unattended_perception_allowed": False,
        "automatic_activation": False,
    }


def record(*, step: str, consent: bool,
           inspected_camera_release: bool) -> dict[str, Any]:
    from . import tracky_native_managed_session as managed
    if step not in _STEPS:
        raise AcceptanceError("Choose a supported installed-device exercise.", 422)
    if consent is not True or inspected_camera_release is not True:
        raise AcceptanceError("Explicit on-device observation and owner approval are required.", 403)
    # Shared start/ack lock prevents another capture starting between validation
    # and the evidence write. Never uses or extends a camera lease.
    with managed._LOCK:
        approved = cert.status()
        model = native.model_preflight()
        worker = managed.status()
        current = _current(approval=approved, model=model, worker=worker,
                           exposure=physical.provider_exposure())
        if not current["ready"] or current["current_step"] != step:
            raise AcceptanceError(
                "Perform the selected exercise on this installed, reviewed device, "
                "then ensure its camera worker and provider have exited.", 409)
        session = current["last"]
        device = cert._device_fingerprint()
        payload = "|".join((
            _BOOT, current["review_id"], current["digest"], device,
            step, session["started_at"], session["finished_at"],
        ))
        fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        clean = {
            "boot": _BOOT,
            "device_fingerprint": device,
            "model_sha256": current["digest"],
            "review_id": current["review_id"],
            "step": step,
            "session_fingerprint": fingerprint,
            "owner_reported_camera_stopped": True,
            "canonical_session_terminal": True,
            "independent_physical_camera_release_verified": False,
            "unattended_perception_allowed": False,
        }
        with db() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO runtime_certification_runs "
                "(id,test_key,status,duration_ms,evidence_json) VALUES(?,?,?,?,?)",
                ("tracky-eyes-exercise-" + fingerprint[:32], TEST_KEY, "not_verified",
                 0, json.dumps(clean, sort_keys=True, separators=(",", ":"))),
            )
    return status()
