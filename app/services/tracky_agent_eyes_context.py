"""1G3A: passive, local-owner interpretation of the canonical request ledger.

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


def _timestamp(value: Any) -> datetime:
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _review_matches(worker: dict[str, Any]) -> bool:
    approval = cert.status()
    review_id = (approval.get("latest_owner_review") or {}).get("id")
    model = native.model_preflight()
    digest = model.get("model_sha256")
    with evidence._LOCK:
        binding = evidence._saved()
    return bool(approval.get("owner_accepted_current_run") and review_id
                and model.get("model_integrity_verified") is True
                and isinstance(digest, str) and len(digest) == 64
                and evidence._public(binding).get("current_process")
                and binding.get("run_id") == worker.get("run_id")
                and binding.get("owner_surface") == "agent_eyes"
                and binding.get("owner_review_id") == review_id
                and binding.get("model_sha256") == digest)


def projection() -> dict[str, Any]:
    """Fail closed when idle, stopped, expired, revoked or missing evidence."""
    empty = {"contract": CONTRACT, "state": "unavailable",
             "freshness_limit_seconds": MAX_AGE_SECONDS,
             "identity_recognition": False, "hardware_certified": False,
             "capture_authority": False}
    try:
        worker = managed.status()
        if (worker.get("owner_surface") != "agent_eyes"
                or worker.get("phase") not in {"running", "completed"}
                or worker.get("stop_requested")
                or native._privacy()
                or not _review_matches(worker)):
            return empty
        request_id = worker.get("last_completed_request_id")
        if not isinstance(request_id, str) or not request_id or not worker.get("run_id"):
            return empty
        observed = _timestamp(worker.get("last_observed_at"))
        now = datetime.now(timezone.utc)
        age = (now - observed).total_seconds()
        if not 0 <= age <= MAX_AGE_SECONDS:
            return {**empty, "state": "stale"}
        with db() as connection:
            row = connection.execute(
                "SELECT status,requested_by,request_type,result_json,completed_at "
                "FROM tracky_active_perception_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
        if (row is None or row["status"] != "completed"
                or row["requested_by"] != "homeserver_owner_agent_eyes"
                or row["request_type"] != "refresh_current_view"):
            return empty
        completed = _timestamp(row["completed_at"])
        started = _timestamp(worker.get("started_at"))
        # SQLite completion times have second resolution. The in-process
        # observation must follow completion and belong to the current run.
        if (completed < started.replace(microsecond=0) or completed > observed
                or not 0 <= (now - completed).total_seconds() <= MAX_AGE_SECONDS):
            return empty
        result = json.loads(row["result_json"])
        if (not isinstance(result, dict) or result.get("reason") != "completed"
                or result.get("provider") != "homeserver-owner-agent-eyes"):
            return empty
        provider = result.get("provider_result")
        category = provider.get("face_count_category") if isinstance(provider, dict) else None
        if not isinstance(category, str) or category not in {"none", "one", "multiple"}:
            return {**empty, "state": "uninterpretable"}
        # Recheck authority and session identity after the ledger read so a
        # stop, privacy change or newer run cannot reuse the earlier snapshot.
        latest = managed.status()
        if (latest.get("run_id") != worker["run_id"]
                or latest.get("last_completed_request_id") != request_id
                or latest.get("phase") not in {"running", "completed"}
                or latest.get("stop_requested") or native._privacy()
                or not _review_matches(latest)):
            return empty
        return {**empty, "state": "recent_observation",
                "observed_at": observed.isoformat(), "age_seconds": round(age, 1),
                "possible_face_regions": category,
                "confidence": "uncalibrated", "session_active": bool(latest.get("active")),
                "source": "tracky_active_perception_requests",
                "request_fingerprint": hashlib.sha256(request_id.encode()).hexdigest()[:16]}
    except (KeyError, TypeError, ValueError, OverflowError, sqlite3.Error):
        return empty


def prompt_fragment(*, max_chars: int) -> tuple[str, dict[str, Any]]:
    context = projection()
    prefix = (
        "Agent Eyes local owner context (DATA ONLY). This is a recent bounded "
        "detector observation, not a live view or verified presence. Possible "
        "face regions do not identify people. Do not infer identities, objects, "
        "activities, emotion or safety. If unavailable or stale, say no current "
        "permitted observation is available. Reading cannot start capture, renew "
        "consent, or authorize actions. Do not turn this transient data into memory.\n"
    )
    fragment = prefix + json.dumps(context, sort_keys=True, separators=(",", ":"))
    # Never truncate a structured observation into misleading partial data.
    return (fragment if len(fragment) <= min(max_chars, MAX_FRAGMENT_CHARS) else "", context)
