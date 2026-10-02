"""Agent Eyes 1G2B2: passive, restart-safe operator recovery and acceptance report.

Never starts capture, changes detector certification, or claims physical proof.
The owner must acknowledge a failed/interrupted *Agent Eyes* session only after
the canonical worker and exclusive native capture lock are idle.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any

from . import system_state, tracky_native_session_evidence as evidence

CONTRACT = "tracky.agent-eyes.owner-recovery.v1g2b2"
KEY = CONTRACT
_ABNORMAL = frozenset({
    "watchdog_stall", "observation_unavailable",
    "startup_failed", "interrupted_by_restart",
})


class RecoveryError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _fingerprint(row: dict[str, Any]) -> str:
    # Hash only deliberately whitelisted non-identifying session metadata.
    payload = "|".join((
        str(row.get("started_at") or "")[:40],
        str(row.get("phase") or "")[:20],
        str(row.get("reason") or "")[:50],
        str(row.get("owner_surface") or "")[:30],
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _saved() -> dict[str, Any]:
    row = system_state._read_setting(KEY, {})
    return row if isinstance(row, dict) else {}


def status(*, worker: dict[str, Any] | None = None) -> dict[str, Any]:
    """Non-mutating status; no camera discovery, lease renewal or auto-repair."""
    from . import tracky_native_managed_session as managed
    from . import tracky_native_camera as native
    from . import tracky_physical_context as physical
    state = worker if worker is not None else managed.status()
    row = evidence.latest()
    native_state = native.status()
    # The shared lock proves only whether software owns a capture call,
    # not whether Windows physically disconnected the camera.
    busy = bool(state.get("active") or native_state.get("running")
                or native_state.get("capture_worker_active")
                or native.capture_busy() or physical.provider_exposure()["registered"])
    relevant = row["owner_surface"] == "agent_eyes"
    reason = row["reason"] if relevant else ""
    abnormal = bool(relevant and (reason in _ABNORMAL
                    or (row["phase"] == "failed")))
    ack = _saved()
    fingerprint = _fingerprint(row) if abnormal else ""
    acknowledged = bool(abnormal and ack.get("fingerprint") == fingerprint
                        and ack.get("owner_reported_stopped") is True)
    required = abnormal and not acknowledged
    next_action = (
        "wait_for_worker_and_driver_release" if required and busy else
        "inspect_installed_camera_then_owner_acknowledge" if required else
        "fresh_owner_approval_required" if acknowledged else
        "no_recovery_action"
    )
    return {
        "contract": CONTRACT,
        "requires_acknowledgement": required,
        "acknowledged": acknowledged,
        "last_phase": row["phase"] if relevant else "not_applicable",
        "last_reason": reason if relevant else "",
        "last_started_at": row["started_at"] if relevant else "",
        "last_finished_at": row["finished_at"] if relevant else "",
        "worker_active": bool(state.get("active")),
        "shared_camera_busy": busy,
        "ready_for_owner_acknowledgement": bool(required and not busy),
        "next_action": next_action,
        "manual_owner_action_required": required,
        "installed_owner_review_required_for_new_sessions": True,
        "independently_hardware_certified": False,
        "physical_camera_release_verified": False,
        "automatic_recovery": False,
        "camera_started_by_diagnostics": False,
    }


def acknowledge(*, consent: bool, camera_stopped_observed: bool,
                fresh_consent_understood: bool) -> dict[str, Any]:
    if not (consent is True and camera_stopped_observed is True
            and fresh_consent_understood is True):
        raise RecoveryError("Explicit owner inspection and recovery acknowledgement required.", 403)
    state = status()
    if not state["requires_acknowledgement"]:
        raise RecoveryError("No pending Agent Eyes failure requires recovery acknowledgement.", 409)
    if not state["ready_for_owner_acknowledgement"]:
        raise RecoveryError("Camera worker or shared provider is still occupied; do not rearm.", 409)
    # Restarted sessions are recorded as interrupted only during explicit
    # owner recovery; passive status never alters durable session evidence.
    row = evidence.latest()
    if row["recover_before_new_session"]:
        evidence.recover_prior(worker_active=False)
        row = evidence.latest()
    system_state._write_setting(KEY, {
        "fingerprint": _fingerprint(row), "owner_reported_stopped": True,
        "owner_understands_fresh_consent": True,
        "reported_at": datetime.now(timezone.utc).isoformat(),
        "report_id": secrets.token_hex(8),
        "physical_hardware_certified": False,
    })
    return status()
