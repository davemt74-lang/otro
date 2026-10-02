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
from . import tracky_agent_eyes_recovery as recovery
from . import tracky_agent_eyes_acceptance as acceptance

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
    recovery_state = recovery.status(worker=worker)
    acceptance_state = acceptance.status(approval=approval, worker=worker,
                                         exposure=exposure)
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
        "max_observations": (managed.AGENT_EXTENDED_MAX_SAMPLES
            if acceptance_state["owner_exercise_complete"] else managed.MAX_SAMPLES),
        "max_seconds": (managed.AGENT_EXTENDED_WALL_OPTIONS[-1]
            if acceptance_state["owner_exercise_complete"] else managed.MAX_SECONDS),
        "resource_budget": (worker.get("resource_budget") if belongs else None),
        "recovery": recovery_state,
        "installed_exercise": acceptance_state,
        "available_wall_budgets": list(managed.AGENT_WALL_OPTIONS) + (
            list(managed.AGENT_EXTENDED_WALL_OPTIONS)
            if acceptance_state["owner_exercise_complete"] else []),
        "available_cpu_budgets": list(managed.AGENT_CPU_OPTIONS) + (
            list(managed.AGENT_EXTENDED_CPU_OPTIONS)
            if acceptance_state["owner_exercise_complete"] else []),
        "available_intervals": [5, 10, 15, 20, 30] if
            acceptance_state["owner_exercise_complete"] else [5, 10, 15],
        "extended_supervised_eligible": bool(acceptance_state["owner_exercise_complete"]),
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
          max_session_seconds: int = 120, max_cpu_seconds: int = 12,
          include_scene: bool = False, scene_test: bool = False) -> dict[str, Any]:
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
            include_scene=include_scene, scene_test=scene_test,
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
        "extended_supervised_eligible": current["extended_supervised_eligible"],
        "installed_exercise": {
            "complete": current["installed_exercise"]["owner_exercise_complete"],
            "pending_steps": current["installed_exercise"]["pending_steps"],
            "physical_hardware_certified": False,
        },
        "recovery": {
            "required": current["recovery"]["requires_acknowledgement"],
            "reason": current["recovery"]["last_reason"],
            "next_action": current["recovery"]["next_action"],
        },
        "next_action": (
            "owner_inspect_and_acknowledge_recovery"
            if current["recovery"]["requires_acknowledgement"]
            else
            "owner_stop_or_continue_visible_lease" if current["active"]
            else "repeat_installed_owner_review" if not current["owner_review_current"]
            else "request_fresh_owner_approval"
        ),
    }
