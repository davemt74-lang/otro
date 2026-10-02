"""Tracky 1G2: owner-gated Agent Eyes orchestration, not another camera runtime.

Runs the existing supervised HomeServer native worker and canonical Tracky
active-perception request ledger. No background process, startup persistence,
camera duplication, face identification or Cloud media transfer.
"""
from __future__ import annotations

from typing import Any

from . import tracky_native_managed_session as managed
from . import tracky_native_camera as native
from . import tracky_native_certification as cert
from . import tracky_physical_context as physical

CONTRACT = "tracky.agent-eyes.supervised-live.v1g2"
SCOPE = "owner-agent-eyes-supervised-live.v1"
OWNER_SURFACE = "agent_eyes"


class AgentEyesError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def status() -> dict[str, Any]:
    """Passive inspection; never touches a camera or renews owner consent."""
    worker = managed.status()
    belongs = worker.get("owner_surface") == OWNER_SURFACE
    active = bool(belongs and worker.get("active"))
    approval = cert.status()
    exposure = physical.provider_exposure()
    return {
        "contract": CONTRACT,
        "mode": "bounded_owner_supervised_native",
        "active": active,
        "phase": (str(worker.get("phase") or "inactive") if belongs else "inactive"),
        "reason": (str(worker.get("reason") or "")[:50] if belongs else "not_started"),
        "stop_requested": bool(active and worker.get("stop_requested")),
        "completed_observations": int(worker.get("completed_samples") or 0) if belongs else 0,
        "requested_observations": int(worker.get("requested_samples") or 0) if belongs else 0,
        "last_observed_at": str(worker.get("last_observed_at") or "") if belongs else "",
        "started_at": str(worker.get("started_at") or "") if belongs else "",
        "max_observations": managed.MAX_SAMPLES,
        "max_seconds": managed.MAX_SECONDS,
        "resource_budget": (worker.get("resource_budget") if belongs else None),
        "available_wall_budgets": list(managed.AGENT_WALL_OPTIONS),
        "available_cpu_budgets": list(managed.AGENT_CPU_OPTIONS),
        "watchdog_fail_closed": True,
        "owner_heartbeat_ttl_seconds": managed.HEARTBEAT_TTL_SECONDS,
        "owner_review_current": bool(approval["owner_accepted_current_run"]),
        "model_changed_requires_review": bool(
            approval["requires_new_owner_test_due_model_change"]
        ),
        "privacy_engaged": bool(native._privacy()),
        "provider_registered": bool(active and exposure["registered"]),
        "remotely_requestable": False,
        "cloud_media_transfer": False,
        "raw_media_retained": False,
        "identity_recognition": False,
        "continuous_unattended_tracking": False,
        "hardware_certified": False,
        "canonical_request_ledger": True,
        "durable_evidence": (worker.get("durable_evidence") if belongs else None),
        "manual_reapproval_required": True,
        "auto_resume": False,
    }


def start(*, consent: bool, scope: str, camera_index: int,
          sample_count: int = 6, interval_seconds: int = 5,
          max_session_seconds: int = 120, max_cpu_seconds: int = 12) -> dict[str, Any]:
    if consent is not True or scope != SCOPE:
        raise AgentEyesError("Fresh Agent Eyes owner approval is required.", 403)
    # A second endpoint cannot borrow a preexisting generic supervised
    # session or weaken its live certification, driver lock or privacy gates.
    try:
        managed.start(
            consent=True, scope=managed.SCOPE, camera_index=camera_index,
            sample_count=sample_count, interval_seconds=interval_seconds,
            owner_surface=OWNER_SURFACE,
            max_session_seconds=max_session_seconds,
            max_cpu_seconds=max_cpu_seconds,
        )
    except managed.ManagedSessionError as exc:
        raise AgentEyesError(str(exc), exc.status_code) from exc
    return status()


def stop() -> dict[str, Any]:
    running = managed.status()
    if running.get("owner_surface") == OWNER_SURFACE and running.get("active"):
        managed.stop()
    return status()


def heartbeat() -> dict[str, Any]:
    running = managed.status()
    if running.get("owner_surface") != OWNER_SURFACE or not running.get("active"):
        raise AgentEyesError("No owner-approved Agent Eyes lease is active.", 409)
    try:
        managed.heartbeat()
    except managed.ManagedSessionError as exc:
        raise AgentEyesError(str(exc), exc.status_code) from exc
    return status()


def agent_context() -> dict[str, Any]:
    """Non-identifying read-only status for the existing Agent onboarding loop.

    Nothing starts, extends or modifies a perception session when Agent Brain
    reads this projection. No personal recognition or hardware proof implied.
    """
    current = status()
    return {
        "mode": current["mode"], "active": current["active"],
        "phase": current["phase"], "reason": current["reason"],
        "observations": current["completed_observations"],
        "last_observed_at": current["last_observed_at"],
        "owner_presence_required": True,
        "remotely_requestable": False,
        "identity_recognition": False,
        "hardware_certified": False,
        "canonical_request_ledger": True,
        "resource_budget": current["resource_budget"],
        "next_action": (
            "owner_stop_or_continue_visible_lease" if current["active"]
            else "repeat_installed_owner_review" if not current["owner_review_current"]
            else "request_fresh_owner_approval"
        ),
    }
