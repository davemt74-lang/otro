from __future__ import annotations

import re
import time
from typing import Any, Callable

from . import capability_registry, meeting_transcription, meeting_transcription_remote, remote_bridge

STATUS_OPERATION = "meeting.transcription.status"
STOP_OPERATION = "meeting.transcription.stop"
_CONTROL_OPERATIONS = (STATUS_OPERATION, STOP_OPERATION)
_TERMINAL_STATES = {"completed", "failed", "stopped", "expired"}
_PUBLIC_ID = re.compile(r"^[a-f0-9]{32}$")
_IDEMPOTENCY = re.compile(r"^vp3-meeting-transcription:[a-f0-9]{32}$")
_INSTALLED = False


def _identity_app_key(identity: dict[str, Any]) -> str:
    return str(identity.get("app_key") or "").strip()[:80]


def _normalize_key(payload: dict[str, Any]) -> tuple[str, str]:
    public_id = str(payload.get("meeting") or payload.get("public_id") or "").strip().lower()
    key = str(payload.get("idempotency_key") or "").strip().lower()

    if public_id:
        if not _PUBLIC_ID.fullmatch(public_id):
            raise meeting_transcription.MeetingTranscriptionError("meeting is invalid.", 422)
        expected = f"vp3-meeting-transcription:{public_id}"
        if key and key != expected:
            raise meeting_transcription.MeetingTranscriptionError(
                "idempotency_key is not bound to this meeting.", 422
            )
        key = expected
    elif key:
        if not _IDEMPOTENCY.fullmatch(key):
            raise meeting_transcription.MeetingTranscriptionError("idempotency_key is invalid.", 422)
        public_id = key.rsplit(":", 1)[1]
    else:
        raise meeting_transcription.MeetingTranscriptionError(
            "meeting or idempotency_key is required.", 422
        )
    return key, public_id


def _safe_job_snapshot(job: Any) -> dict[str, Any]:
    state = str(getattr(job, "status", "unknown") or "unknown")[:40]
    last_error = str(getattr(job, "last_error", "") or "")[:120]
    return {
        "ready": state in {"starting", "connecting", "running", "already_running"},
        "active": state not in _TERMINAL_STATES,
        "status": state,
        "operation": meeting_transcription.OPERATION,
        "contract": meeting_transcription.CONTRACT,
        "meeting": str(getattr(job, "public_id", "") or "")[:32],
        "room_name": str(getattr(job, "room_name", "") or "")[:100],
        "idempotency_key": str(getattr(job, "idempotency_key", "") or "")[:96],
        "callback_failures": max(0, int(getattr(job, "callback_failures", 0) or 0)),
        "transcription_failures": max(0, int(getattr(job, "transcription_failures", 0) or 0)),
        "last_error": last_error,
        "updated_at_unix": int(float(getattr(job, "updated_at", 0.0) or 0.0)),
    }


def runtime_status() -> dict[str, Any]:
    base = dict(meeting_transcription.status())
    counts: dict[str, int] = {}
    active = 0
    with meeting_transcription._JOBS_LOCK:
        jobs = list(meeting_transcription._JOBS.values())
        for job in jobs:
            state = str(getattr(job, "status", "unknown") or "unknown")[:40]
            counts[state] = counts.get(state, 0) + 1
            if state not in _TERMINAL_STATES:
                active += 1
    base.update(
        {
            "production_control_version": "v18.8",
            "status_operation": STATUS_OPERATION,
            "stop_operation": STOP_OPERATION,
            "active_jobs": active,
            "tracked_jobs": len(jobs),
            "state_counts": counts,
        }
    )
    return base


def _owned_job(payload: dict[str, Any], identity: dict[str, Any]) -> Any | None:
    key, _ = _normalize_key(payload)
    app_key = _identity_app_key(identity)
    if not app_key:
        raise meeting_transcription.MeetingTranscriptionError("Paired app identity is invalid.", 403)
    with meeting_transcription._JOBS_LOCK:
        job = meeting_transcription._JOBS.get(key)
        if job is None:
            return None
        if str(getattr(job, "app_key", "") or "") != app_key:
            raise meeting_transcription.MeetingTranscriptionError("Meeting transcription job was not found.", 404)
        return job


def status_for(payload: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    key, public_id = _normalize_key(payload)
    job = _owned_job(payload, identity)
    if job is None:
        return {
            "ready": False,
            "active": False,
            "status": "not_found",
            "operation": meeting_transcription.OPERATION,
            "contract": meeting_transcription.CONTRACT,
            "meeting": public_id,
            "idempotency_key": key,
        }
    return _safe_job_snapshot(job)


def stop_for(payload: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    key, public_id = _normalize_key(payload)
    job = _owned_job(payload, identity)
    if job is None:
        return {
            "ready": False,
            "active": False,
            "status": "not_found",
            "operation": meeting_transcription.OPERATION,
            "contract": meeting_transcription.CONTRACT,
            "meeting": public_id,
            "idempotency_key": key,
            "stopped": False,
        }

    with meeting_transcription._JOBS_LOCK:
        state = str(getattr(job, "status", "unknown") or "unknown")
        if state not in _TERMINAL_STATES:
            job.stop_event.set()
            if state not in {"failed", "expired"}:
                job.status = "stopping"
            job.updated_at = time.time()
        result = _safe_job_snapshot(job)
    result["stopped"] = True
    return result


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_dispatch: Callable[[str, dict | None, str | None], dict] = remote_bridge.dispatch_remote_request
    original_operations = capability_registry._operations

    def extended_dispatch(
        operation: str,
        payload: dict | None,
        bearer_token: str | None = None,
    ) -> dict:
        op = str(operation or "").strip()
        if op not in _CONTROL_OPERATIONS:
            return original_dispatch(operation, payload, bearer_token)

        body = payload if isinstance(payload, dict) else {}
        if remote_bridge._payload_size(body) > remote_bridge.settings.max_remote_bridge_message_bytes:
            raise remote_bridge.RemoteBridgeError("Remote request payload is too large.")
        identity = meeting_transcription_remote._identity(bearer_token)
        try:
            result = status_for(body, identity) if op == STATUS_OPERATION else stop_for(body, identity)
        except meeting_transcription.MeetingTranscriptionError as exc:
            return {
                "status": int(exc.status_code),
                "ok": False,
                "payload": {"detail": str(exc), "operation": op},
            }
        return {"status": 200, "ok": True, "payload": result}

    def extended_operations(permissions: set[str], contacts_available: bool) -> list[str]:
        operations = list(original_operations(permissions, contacts_available))
        if "agent.chat" in permissions and meeting_transcription_remote._claimed_cloud_ready():
            operations.extend(_CONTROL_OPERATIONS)
        return sorted(set(operations))

    remote_bridge.dispatch_remote_request = extended_dispatch
    capability_registry._operations = extended_operations
    remote_bridge._meeting_transcription_control_v1880_installed = True
    _INSTALLED = True
