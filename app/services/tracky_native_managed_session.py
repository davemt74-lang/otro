"""Tracky 1E/1: owner-armed, bounded native perception sessions.

This is supervised, non-identifying sampling. It reuses the canonical Tracky
provider/request ledger and native camera capture; it does not start at boot,
auto-enroll participants, track people, or store frames/biometrics. Production
unattended observation requires distinct physical acceptance and policy work.
"""
from __future__ import annotations

import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any

from . import tracky_native_camera as native, tracky_native_certification as cert
from . import tracky_physical_context as tracky
from . import tracky_native_session_evidence as evidence

CONTRACT = "tracky.native.managed-session.v1e1"
SCOPE = "owner-supervised-native-sampling.v1"
OWNER_SURFACES = frozenset({"native_supervised", "agent_eyes"})
MAX_SAMPLES = 12
MIN_INTERVAL_SECONDS = 5
MAX_SECONDS = 120
HEARTBEAT_TTL_SECONDS = 15
# 1G2B1: deliberate owner-selected *supervised* budgets. Longer sessions
# remain physically uncertified and disabled until installed-device acceptance.
AGENT_WALL_OPTIONS = (60, 120)
AGENT_CPU_OPTIONS = (4, 8, 12)
WATCHDOG_STALL_SECONDS = 16
_LOCK = threading.RLock()
_STOP = threading.Event()
_WORKER: threading.Thread | None = None
_STATE: dict[str, Any] = {
    "phase": "inactive", "reason": "not_started", "completed_samples": 0,
    "requested_samples": 0, "last_observed_at": "",
}
_ALLOWED_REQUEST = ""
_CAMERA_INDEX = 0
_LAST_HEARTBEAT = 0.0
_SESSION_STARTED = 0.0
_ATTEMPT_STARTED = 0.0
_WATCHDOG: threading.Thread | None = None


class ManagedSessionError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _snapshot() -> dict[str, Any]:
    with _LOCK:
        state = dict(_STATE)
        active = bool(_WORKER and _WORKER.is_alive())
        state.update({
            "contract": CONTRACT,
            "active": active,
            "stop_requested": _STOP.is_set() if active else False,
            "hardware_certified": False,
            "identity_recognition": False,
            "continuous_unattended_tracking": False,
            "raw_media_retained": False,
            "max_samples": MAX_SAMPLES,
            "max_session_seconds": MAX_SECONDS,
            "owner_presence_required": True,
            "heartbeat_ttl_seconds": HEARTBEAT_TTL_SECONDS,
            "requires_new_owner_consent_per_session": True,
        })
        if state.get("owner_surface") == "agent_eyes":
            elapsed = max(0.0, time.monotonic() - _SESSION_STARTED) if active else 0.0
            limit = int(state.get("wall_limit_seconds") or MAX_SECONDS)
            state["resource_budget"] = {
                "wall_limit_seconds": limit,
                "wall_elapsed_seconds": round(min(elapsed, limit), 2) if active
                    else round(float(state.get("final_elapsed_seconds") or 0), 2),
                "cpu_limit_seconds": int(state.get("cpu_limit_seconds") or AGENT_CPU_OPTIONS[-1]),
                "cpu_used_seconds": round(float(state.get("cpu_used_seconds") or 0), 3),
                "observations_remaining": max(0, int(state.get("requested_samples") or 0)
                                              - int(state.get("completed_samples") or 0)),
                "watchdog_running": bool(active and _WATCHDOG and _WATCHDOG.is_alive()),
                "automatic_restart": False,
            }
        state["durable_evidence"] = evidence.latest()
        return state


def status() -> dict[str, Any]:
    state = _snapshot()
    # This check is read-only; a privacy change should immediately request
    # cancellation even if the worker is blocked in a camera driver call.
    if state["active"]:
        if native._privacy():
            _cancel("privacy_engaged")
            state["stop_requested"] = True
        elif state.get("phase") == "running" and _heartbeat_expired():
            _cancel("owner_presence_expired")
            state["stop_requested"] = True
    return state


def _heartbeat_expired() -> bool:
    with _LOCK:
        return bool(_LAST_HEARTBEAT and time.monotonic() - _LAST_HEARTBEAT > HEARTBEAT_TTL_SECONDS)


def _cancel(reason: str) -> None:
    with _LOCK:
        if _WORKER is not None and _WORKER.is_alive() and _STATE.get("phase") == "running":
            _STATE["cancel_reason"] = reason
            _STATE["phase"] = "stopping"
            _STOP.set()


def heartbeat() -> dict[str, Any]:
    global _LAST_HEARTBEAT
    with _LOCK:
        if (not _WORKER or not _WORKER.is_alive()
                or _STATE.get("phase") != "running"
                or _STOP.is_set() or native._privacy() or _heartbeat_expired()):
            raise ManagedSessionError("Supervised session inactive or consent lease expired.", 409)
        _LAST_HEARTBEAT = time.monotonic()
    return status()


def _stopping_reason(default: str = "owner_stopped") -> str:
    with _LOCK:
        return str(_STATE.get("cancel_reason") or default)


def _provider(request: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        if (not _WORKER or not _WORKER.is_alive()
                or request.get("request_id") != _ALLOWED_REQUEST
                or request.get("request_type") != "refresh_current_view"
                or _STOP.is_set()):
            raise tracky.TrackyPhysicalError("Session has no authorized observation.", 403)
        index = _CAMERA_INDEX
    # A detector update or revoked owner review must immediately invalidate
    # observations even after the supervised session was initially armed.
    if not cert.status().get("owner_accepted_current_run"):
        _cancel("acceptance_revoked")
        raise tracky.TrackyPhysicalError("Installed camera approval expired.", 403)
    # Existing shared native capture path enforces driver exclusivity and
    # checks revocation before capture, inference and result acceptance.
    cpu_at = time.thread_time()
    try:
        result = native._observe(index, _STOP)
    finally:
        used = max(0.0, time.thread_time() - cpu_at)
        with _LOCK:
            if _STATE.get("owner_surface") == "agent_eyes":
                _STATE["cpu_used_seconds"] = float(_STATE.get("cpu_used_seconds") or 0) + used
    with _LOCK:
        cpu_exceeded = (_STATE.get("owner_surface") == "agent_eyes"
                        and float(_STATE.get("cpu_used_seconds") or 0)
                        >= float(_STATE.get("cpu_limit_seconds") or AGENT_CPU_OPTIONS[-1]))
        wall_exceeded = (_STATE.get("owner_surface") == "agent_eyes"
                         and time.monotonic() - _SESSION_STARTED
                         >= float(_STATE.get("wall_limit_seconds") or MAX_SECONDS))
    if cpu_exceeded or wall_exceeded:
        _cancel("cpu_budget_exhausted" if cpu_exceeded else "time_limit")
        raise tracky.TrackyPhysicalError("Agent Eyes session resource budget exhausted.", 403)
    if _heartbeat_expired():
        _cancel("owner_presence_expired")
        raise tracky.TrackyPhysicalError("Owner presence lease expired.", 403)
    if not cert.status().get("owner_accepted_current_run"):
        _cancel("acceptance_revoked")
        raise tracky.TrackyPhysicalError("Detector approval changed during capture.", 403)
    if _STOP.is_set() or native._privacy():
        raise tracky.TrackyPhysicalError("Session stopped or privacy engaged.", 403)
    return {
        "summary": result["summary"],
        "confidence": 0.0,  # Haar regions do not provide calibrated confidence.
    }


def _watchdog(worker: threading.Thread, run_id: str) -> None:
    """Independent fail-closed supervisor: never launches or resumes a camera."""
    while worker.is_alive() and not _STOP.wait(0.25):
        with _LOCK:
            if _STATE.get("run_id") != run_id or _STATE.get("phase") != "running":
                return
            elapsed = time.monotonic() - _SESSION_STARTED
            limit = int(_STATE.get("wall_limit_seconds") or MAX_SECONDS)
            attempt = _ATTEMPT_STARTED
            cpu_used = float(_STATE.get("cpu_used_seconds") or 0)
            cpu_limit = int(_STATE.get("cpu_limit_seconds") or AGENT_CPU_OPTIONS[-1])
        # Do not hold the session lock around calls into privacy/certification.
        if native._privacy():
            _cancel("privacy_engaged")
        elif not cert.status().get("owner_accepted_current_run"):
            _cancel("acceptance_revoked")
        elif _heartbeat_expired():
            _cancel("owner_presence_expired")
        elif elapsed >= limit:
            _cancel("time_limit")
        elif cpu_used >= cpu_limit:
            _cancel("cpu_budget_exhausted")
        elif attempt and time.monotonic() - attempt >= WATCHDOG_STALL_SECONDS:
            _cancel("watchdog_stall")


def _run(sample_count: int, interval: int, started: float, run_id: str,
         owner_surface: str) -> None:
    global _ALLOWED_REQUEST, _WORKER, _ATTEMPT_STARTED
    reason = "completed"
    phase = "completed"
    try:
        for index in range(sample_count):
            if native._privacy():
                _cancel("privacy_engaged")
            if not cert.status().get("owner_accepted_current_run"):
                _cancel("acceptance_revoked")
            if _heartbeat_expired():
                _cancel("owner_presence_expired")
            if _STOP.is_set():
                reason, phase = _stopping_reason(), "stopped"
                break
            # Leave room for the native provider's bounded call. An OS driver
            # that ignores cancellation remains separately reported as busy.
            with _LOCK:
                wall_limit = int(_STATE.get("wall_limit_seconds") or MAX_SECONDS)
            if time.monotonic() - started >= wall_limit - native.CAPTURE_SECONDS:
                reason, phase = "time_limit", "stopped"
                break
            request_id = "tracky-managed-" + secrets.token_hex(16)
            with _LOCK:
                _ALLOWED_REQUEST = request_id
                _ATTEMPT_STARTED = time.monotonic()
            try:
                response = tracky.active_perception(
                    "refresh_current_view", request_id=request_id,
                    requested_by=("homeserver_owner_agent_eyes" if owner_surface == "agent_eyes"
                                  else "homeserver_owner_managed_native"),
                    reason=("Owner-approved bounded Agent Eyes live observation"
                            if owner_surface == "agent_eyes"
                            else "Owner-armed bounded native sampling"),
                )
                row = response.get("request") or {}
                if native._privacy():
                    _cancel("privacy_engaged")
                if not cert.status().get("owner_accepted_current_run"):
                    _cancel("acceptance_revoked")
                if _heartbeat_expired():
                    _cancel("owner_presence_expired")
                if row.get("status") != "completed" or _STOP.is_set():
                    reason = _stopping_reason() if _STOP.is_set() else "observation_unavailable"
                    phase = "stopped" if _STOP.is_set() else "failed"
                    break
                with _LOCK:
                    _STATE["completed_samples"] += 1
                    _STATE["last_observed_at"] = _now()
            finally:
                with _LOCK:
                    _ALLOWED_REQUEST = ""
                    _ATTEMPT_STARTED = 0.0
            if index + 1 < sample_count and _STOP.wait(interval):
                reason, phase = _stopping_reason(), "stopped"
                break
        if _STOP.is_set() and reason == "completed":
            reason, phase = _stopping_reason(), "stopped"
    except Exception:
        # Preserve watchdog/privacy/owner-revocation evidence when a driver
        # raises during cancellation. Never publish driver exception text.
        if _STOP.is_set():
            reason, phase = _stopping_reason(), "stopped"
        else:
            reason, phase = "observation_unavailable", "failed"
    finally:
        _STOP.set()
        tracky.unregister_provider(expected=_provider)
        with _LOCK:
            _ALLOWED_REQUEST = ""
            _ATTEMPT_STARTED = 0.0
            _STATE.update({"phase": phase, "reason": reason, "finished_at": _now(),
                           "final_elapsed_seconds": round(time.monotonic() - started, 2)})
            completed = int(_STATE["completed_samples"])
        try:
            evidence.finish(run_id=run_id, phase=phase, reason=reason,
                            completed_samples=completed)
        except Exception:
            with _LOCK:
                _STATE["evidence_write_status"] = "unavailable"
            # Do not null _WORKER until it has actually exited: racing starts
            # still see a live worker and fail closed.


def start(*, consent: bool, scope: str, camera_index: int,
          sample_count: int = 3, interval_seconds: int = MIN_INTERVAL_SECONDS,
          owner_surface: str = "native_supervised",
          max_session_seconds: int = MAX_SECONDS,
          max_cpu_seconds: int = AGENT_CPU_OPTIONS[-1]) -> dict[str, Any]:
    global _WORKER, _CAMERA_INDEX, _LAST_HEARTBEAT, _SESSION_STARTED, _ATTEMPT_STARTED, _WATCHDOG
    if consent is not True or scope != SCOPE:
        raise ManagedSessionError("Explicit fresh owner consent is required.", 403)
    if type(owner_surface) is not str or owner_surface not in OWNER_SURFACES:
        raise ManagedSessionError("Unsupported owner session surface.", 422)
    if type(camera_index) is not int or camera_index not in native.CAMERA_INDICES:
        raise ManagedSessionError("Select a supported camera.", 422)
    if (type(max_session_seconds) is not int or type(max_cpu_seconds) is not int
            or (owner_surface == "agent_eyes"
                and (max_session_seconds not in AGENT_WALL_OPTIONS
                     or max_cpu_seconds not in AGENT_CPU_OPTIONS))
            or (owner_surface != "agent_eyes"
                and (max_session_seconds != MAX_SECONDS
                     or max_cpu_seconds != AGENT_CPU_OPTIONS[-1]))):
        raise ManagedSessionError("Unsupported supervised resource budget.", 422)
    if (type(sample_count) is not int or not 1 <= sample_count <= MAX_SAMPLES
            or type(interval_seconds) is not int
            or not MIN_INTERVAL_SECONDS <= interval_seconds <= 15
            or (sample_count - 1) * interval_seconds
               > max_session_seconds - native.CAPTURE_SECONDS):
        raise ManagedSessionError("Session limits exceeded.", 422)
    if native._privacy():
        raise ManagedSessionError("Privacy is engaged.", 403)
    if not cert.status()["owner_accepted_current_run"]:
        raise ManagedSessionError("Complete current-process owner camera acceptance first.", 409)
    if not native.model_preflight()["model_integrity_verified"]:
        raise ManagedSessionError("Installed native detector does not match the reviewed offline model.", 503)
    with _LOCK:
        # Shared hardware must never let a generic supervised session
        # overwrite the last unresolved Agent Eyes failure evidence.
        from . import tracky_agent_eyes_recovery as recovery
        if recovery.status()["requires_acknowledgement"]:
            raise ManagedSessionError("Inspect the stopped camera and acknowledge Agent Eyes recovery before a new session.", 409)
        if _WORKER is not None and _WORKER.is_alive():
            raise ManagedSessionError("A supervised session is already running.", 409)
        if native.status()["running"] or native.status()["capture_worker_active"] or native.capture_busy():
            raise ManagedSessionError("Native camera driver is occupied.", 409)
        existing, _, _ = tracky._provider_snapshot()
        if existing is not None:
            raise ManagedSessionError("Another Tracky perception provider owns the session.", 409)
        # Close an old process's interrupted session without resuming capture.
        evidence.recover_prior(worker_active=False)
        _STOP.clear()
        _LAST_HEARTBEAT = time.monotonic()
        _SESSION_STARTED = _LAST_HEARTBEAT
        _ATTEMPT_STARTED = 0.0
        _CAMERA_INDEX = camera_index
        _STATE.clear()
        _STATE.update({
            "phase": "running", "reason": "owner_approved",
            "completed_samples": 0, "requested_samples": sample_count,
            "started_at": _now(), "last_observed_at": "",
            "capture_interval_seconds": interval_seconds,
            "owner_surface": owner_surface,
            "wall_limit_seconds": max_session_seconds,
            "cpu_limit_seconds": max_cpu_seconds,
            "cpu_used_seconds": 0.0,
        })
        run_id = ""
        try:
            tracky.register_provider(
                _provider, name=("homeserver-owner-agent-eyes"
                                 if owner_surface == "agent_eyes"
                                 else "homeserver-supervised-native-sampling"),
                capabilities={
                    "surface": ("native_agent_eyes" if owner_surface == "agent_eyes"
                                else "native_supervised"),
                    "requires_camera": False,  # native shared path verifies actual device
                    "identity_recognition": False,
                    "background_tracking": False,
                    "owner_consent_required": True,
                    "timeout_seconds": native.CAPTURE_SECONDS,
                }, replace=False,
            )
            run_id = evidence.begin(sample_count=sample_count, owner_surface=owner_surface)
            _STATE["run_id"] = run_id
            _WORKER = threading.Thread(
                target=_run, args=(sample_count, interval_seconds, time.monotonic(), run_id,
                                   owner_surface),
                name="tracky-supervised-native", daemon=True,
            )
            _WORKER.start()
            # Only Agent Eyes gets the owner-presence and resource watchdog;
            # it never opens or restarts the camera itself.
            if owner_surface == "agent_eyes":
                _WATCHDOG = threading.Thread(
                    target=_watchdog, args=(_WORKER, run_id),
                    name="tracky-agent-eyes-watchdog", daemon=True)
                _WATCHDOG.start()
            else:
                _WATCHDOG = None
        except Exception:
            _STOP.set()
            tracky.unregister_provider(expected=_provider)
            if run_id:
                evidence.finish(run_id=run_id, phase="failed", reason="startup_failed",
                                completed_samples=0)
            _STATE.update({"phase": "failed", "reason": "startup_failed"})
            raise
    return status()


def stop() -> dict[str, Any]:
    _cancel("owner_stopped")
    # Revocation is immediate even if OS VideoCapture.read cannot be interrupted.
    # The worker must exit and release its driver before any new session starts.
    return status()
