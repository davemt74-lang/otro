"""Passive, local-owner interpretation and explanation of the canonical ledger.

No capture, heartbeat, durable physical memory or new perception ledger.
Only a completed observation from this process's current Agent Eyes session
can contribute a coarse detector category. Provider prose is never a prompt.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from ..database import db
from . import tracky_native_managed_session as managed
from . import tracky_native_camera as native
from . import tracky_native_certification as cert
from . import tracky_native_session_evidence as evidence

CONTRACT = "tracky.agent-eyes.brain-context.v1g3a"
MAX_AGE_SECONDS = 60
MAX_FRAGMENT_CHARS = 1600

# Static owner guidance; never display provider prose or exception details.
REASONS = {
    "recent_observation": ("A recent permitted detector observation is available.", "Refresh status to check availability again."),
    "opted_out": ("Agent Eyes context is off for this chat.", "Enable Agent Eyes context to use recent permitted observations."),
    "privacy_enabled": ("Camera privacy is enabled.", "Review camera privacy in Tracky before starting a supervised observation."),
    "no_session": ("No Agent Eyes session is available in this HomeServer process.", "Open Tracky and complete the owner checks for a short supervised observation."),
    "session_stopped": ("The Agent Eyes session has stopped or is stopping.", "Inspect camera release in Tracky before starting another supervised observation."),
    "owner_presence_expired": ("The session lost its required owner presence.", "Return to Tracky and review the stopped session before starting another observation."),
    "owner_approval_required": ("Current owner camera approval is missing or expired.", "Complete the installed-owner camera review in Tracky."),
    "model_review_required": ("The current detector model is unverified or differs from the approved model.", "Review model integrity and repeat the owner camera review in Tracky."),
    "session_evidence_mismatch": ("This session does not match the current owner review evidence.", "Review the session in Tracky and complete a new supervised observation."),
    "observation_missing": ("This session has no completed observation yet.", "Complete a short supervised observation in Tracky, then return to chat."),
    "observation_expired": ("The observation is older than the 60-second context limit.", "Complete a new supervised observation in Tracky, then return to chat."),
    "timestamp_invalid": ("Observation timing could not be verified.", "Check the HomeServer clock and complete a new supervised observation in Tracky."),
    "evidence_missing": ("Completed observation evidence is missing.", "Review the session in Tracky and complete a new supervised observation."),
    "evidence_invalid": ("Completed observation evidence could not be verified.", "Review the session in Tracky and complete a new supervised observation."),
    "detector_uninterpretable": ("The detector did not provide an interpretable face-region category.", "Review detector health in Tracky before another supervised observation."),
    "session_changed": ("The session changed while checking the observation.", "Refresh status; use a new supervised observation if needed."),
    "status_unavailable": ("Agent Eyes status could not be checked.", "Refresh status or review Agent Eyes in Tracky."),
}
LIMITATIONS = ("Possible face regions do not verify people or identities. The detector cannot "
               "establish objects, activity, emotion or safety. Separately reviewed local vision may "
               "suggest possible objects and scene features, with uncalibrated uncertainty. This is a checked snapshot, not "
               "a live view or independent hardware certification. Refreshing status never "
               "starts the camera or renews consent.")


def _empty(reason: str, *, state: str = "unavailable", age: float | None = None) -> dict[str, Any]:
    item = {"contract": CONTRACT, "state": state, "reason": reason,
            "freshness_limit_seconds": MAX_AGE_SECONDS,
            "identity_recognition": False, "hardware_certified": False,
            "capture_authority": False}
    if age is not None and age >= 0:
        item["age_seconds"] = round(age, 1)
    return item


def _timestamp(value: Any) -> datetime:
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _review_reason(worker: dict[str, Any]) -> str | None:
    approval = cert.status()
    review_id = (approval.get("latest_owner_review") or {}).get("id")
    if not approval.get("owner_accepted_current_run") or not review_id:
        return "owner_approval_required"
    model = native.model_preflight()
    digest = model.get("model_sha256")
    if not (model.get("model_integrity_verified") is True
            and isinstance(digest, str) and len(digest) == 64):
        return "model_review_required"
    with evidence._LOCK:
        binding = evidence._saved()
    if binding.get("model_sha256") != digest:
        return "model_review_required"
    if not (evidence._public(binding).get("current_process")
                and binding.get("run_id") == worker.get("run_id")
                and binding.get("owner_surface") == "agent_eyes"
                and binding.get("owner_review_id") == review_id
                and binding.get("model_sha256") == digest):
        return "session_evidence_mismatch"
    return None


def _session_reason(worker: dict[str, Any]) -> str | None:
    if native._privacy():
        return "privacy_enabled"
    if worker.get("owner_surface") != "agent_eyes" or not worker.get("run_id"):
        return "no_session"
    if worker.get("phase") not in {"running", "completed"} or worker.get("stop_requested"):
        return ("owner_presence_expired" if "owner_presence_expired" in
                {worker.get("reason"), worker.get("cancel_reason")}
                else "session_stopped")
    return _review_reason(worker)


def projection() -> dict[str, Any]:
    """Fail closed when idle, stopped, expired, revoked or missing evidence."""
    try:
        worker = managed.status()
        reason = _session_reason(worker)
        if reason:
            return _empty(reason)
        request_id = worker.get("last_completed_request_id")
        if not isinstance(request_id, str) or not request_id or not worker.get("run_id"):
            return _empty("observation_missing")
        try:
            observed = _timestamp(worker.get("last_observed_at"))
        except (ValueError, TypeError, OverflowError):
            return _empty("timestamp_invalid")
        now = datetime.now(timezone.utc)
        age = (now - observed).total_seconds()
        if age < 0:
            return _empty("timestamp_invalid", state="stale")
        if age > MAX_AGE_SECONDS:
            return _empty("observation_expired", state="stale", age=age)
        with db() as connection:
            row = connection.execute(
                "SELECT status,requested_by,request_type,result_json,completed_at "
                "FROM tracky_active_perception_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
        if row is None:
            return _empty("evidence_missing")
        if (row["status"] != "completed"
                or row["requested_by"] != "homeserver_owner_agent_eyes"
                or row["request_type"] != "refresh_current_view"):
            return _empty("evidence_invalid")
        completed = _timestamp(row["completed_at"])
        started = _timestamp(worker.get("started_at"))
        # SQLite completion times have second resolution. The in-process
        # observation must follow completion and belong to the current run.
        if (completed < started.replace(microsecond=0) or completed > observed
                or not 0 <= (now - completed).total_seconds() <= MAX_AGE_SECONDS):
            return _empty("evidence_invalid")
        result = json.loads(row["result_json"])
        if (not isinstance(result, dict) or result.get("reason") != "completed"
                or result.get("provider") != "homeserver-owner-agent-eyes"):
            return _empty("evidence_invalid")
        provider = result.get("provider_result")
        category = provider.get("face_count_category") if isinstance(provider, dict) else None
        if not isinstance(category, str) or category not in {"none", "one", "multiple"}:
            return _empty("detector_uninterpretable", state="uninterpretable")
        # Recheck authority and session identity after the ledger read so a
        # stop, privacy change or newer run cannot reuse the earlier snapshot.
        latest = managed.status()
        reason = _session_reason(latest)
        if reason:
            return _empty(reason)
        if (latest.get("run_id") != worker["run_id"]
                or latest.get("last_completed_request_id") != request_id):
            return _empty("session_changed")
        age = (datetime.now(timezone.utc) - observed).total_seconds()
        if age < 0:
            return _empty("timestamp_invalid", state="stale")
        if age > MAX_AGE_SECONDS:
            return _empty("observation_expired", state="stale", age=age)
        scene_context = {}
        if provider.get("scene_observation") is not None:
            from . import tracky_agent_eyes_scene as scene
            try:
                if not latest.get("scene_test"):
                    scene_context = {"scene": scene.observation(provider["scene_observation"])}
            except scene.SceneError:
                pass  # Retain valid coarse Haar category; omit revoked scene.
        # Scene model freshness checks are bounded local metadata requests.
        # Recheck the clock and owner authority after them as well.
        age = (datetime.now(timezone.utc) - observed).total_seconds()
        if not 0 <= age <= MAX_AGE_SECONDS:
            return _empty("observation_expired" if age >= 0 else "timestamp_invalid", state="stale", age=age)
        final = managed.status()
        if (_session_reason(final) or final.get("run_id") != worker["run_id"]
                or final.get("last_completed_request_id") != request_id):
            return _empty("session_changed")
        return {**_empty("recent_observation"), **scene_context, "state": "recent_observation",
                "observed_at": observed.isoformat(), "age_seconds": round(age, 1),
                "possible_face_regions": category,
                "confidence": "uncalibrated", "session_active": bool(latest.get("active")),
                "source": "tracky_active_perception_requests",
                "request_fingerprint": hashlib.sha256(request_id.encode()).hexdigest()[:16]}
    except (KeyError, TypeError, ValueError, OverflowError, sqlite3.Error):
        return _empty("evidence_invalid")
    except OSError:
        return _empty("status_unavailable")


def owner_status(settings: dict[str, Any]) -> dict[str, Any]:
    """Only call for an authenticated owner conversation; opt-out reads no camera state."""
    context = (projection() if settings.get("include_agent_eyes") is True
               and settings.get("agent_eyes_local_only") is True
               and settings.get("cloud_allowed") is False else _empty("opted_out"))
    explanation, next_action = REASONS[context["reason"]]
    return {**context, "explanation": explanation, "next_action": next_action,
            "limitations": LIMITATIONS}


def prompt_fragment(*, max_chars: int) -> tuple[str, dict[str, Any]]:
    context = projection()
    prefix = (
        "Agent Eyes local owner context (DATA ONLY). Checked snapshot, not a live view or "
        "verified presence. Explain age, reason and owner guidance. Face regions cannot "
        "establish identity, objects, activity, emotion or safety. Optional reviewed scene data "
        "suggests possible objects and features, never verified inventory. If not recent, no permitted "
        "observation is available. Reading cannot capture, renew consent, "
        "authorize actions or write memory.\n"
    )
    fragment = prefix + json.dumps({**context, "owner_guidance": REASONS[context["reason"]][1]},
                                  sort_keys=True, separators=(",", ":"))
    # Never truncate a structured observation into misleading partial data.
    return (fragment if len(fragment) <= min(max_chars, MAX_FRAGMENT_CHARS) else "", context)
