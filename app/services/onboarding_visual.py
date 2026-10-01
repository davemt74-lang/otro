"""Owner-gated embedded Tracky self-enrollment coordination.

The camera, model and existing Tracky participant IndexedDB operate only inside
the authorized HomeServer Agent Chat origin. This module persists explicit local
consent and *client-reported* progress, never face images, embeddings or media.
A browser report is NOT a trusted local perception-provider attestation.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import system_state, tracky_physical_context, vp3_os

STATE_KEY = "tracky.owner_visual_chat.v1"
SCOPE = "owner-self-local-recognition-v1"
SESSION_MINUTES = 20
_LOCK = threading.RLock()
_UI_ROOT = Path(__file__).resolve().parents[2] / "ui" / "tracky"
_BUNDLE = ("owner-visual.js", "src/auto-enrollment-core.js",
           "src/participant-core.js", "src/participant-store.js", "src/model-config.js")


class VisualOnboardingError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read() -> dict[str, Any]:
    raw = system_state._read_setting(STATE_KEY, {})
    return raw if isinstance(raw, dict) else {}


def _write(value: dict[str, Any]) -> None:
    system_state._write_setting(STATE_KEY, value)


def _expired(record: dict[str, Any]) -> bool:
    try:
        ts = datetime.fromisoformat(str(record["expires_at"]))
        return ts.tzinfo is None or ts <= _now()
    except (KeyError, TypeError, ValueError):
        return True


def _privacy_engaged() -> bool:
    state = vp3_os.manifest(include_hardware=False, include_device_id=False)
    return bool((state.get("privacy") or {}).get("privacy_switch_engaged"))


def status() -> dict[str, Any]:
    with _LOCK:
        row = _read()
        try:
            provider, _, _ = tracky_physical_context._provider_snapshot()
        except Exception:
            provider = None
        phase = str(row.get("phase") or "not_started")
        if phase == "awaiting_capture" and _expired(row):
            phase = "interrupted"
        return {
            "phase": phase,
            "scope": row.get("scope") if row.get("consent") else "",
            "consented": bool(row.get("consent") and phase != "interrupted"),
            "expires_at": str(row.get("expires_at") or "") if phase == "awaiting_capture" else "",
            "client_reported_at": str(row.get("client_reported_at") or ""),
            "local_participant_id": str(row.get("local_participant_id") or ""),
            "sample_count": int(row.get("sample_count") or 0),
            "browser_enrollment_assets": all((_UI_ROOT / name).is_file() for name in _BUNDLE),
            "trusted_perception_provider_registered": provider is not None,
            "provider_certified": False,  # Registration alone is never live hardware certification.
            "enrollment_verified": False,  # Browser-only IndexedDB has no trusted server attestation.
            "evidence": "browser_reported_unverified" if phase == "browser_reported" else "not_enrolled",
            "camera_activation": "owner_gesture_and_browser_permission_only",
            "cloud_biometrics": False,
            "tracking_enabled": False,
            "contact_creation_enabled": False,
            "local_deletion_required": phase == "browser_reported",
        }


def start(*, consent: bool, scope: str) -> dict[str, Any]:
    if consent is not True or scope != SCOPE:
        raise VisualOnboardingError("Explicit consent for local self-enrollment is required.", 403)
    if _privacy_engaged():
        raise VisualOnboardingError("Physical privacy is engaged. Enrollment is unavailable.", 403)
    if not all((_UI_ROOT / name).is_file() for name in _BUNDLE):
        raise VisualOnboardingError("The integrated Tracky enrollment assets are missing.", 503)
    with _LOCK:
        prior = _read()
        if prior.get("phase") == "browser_reported":
            raise VisualOnboardingError(
                "A local visual profile is already reported. Review or delete it before starting again.", 409
            )
        token = secrets.token_urlsafe(32)
        until = _now() + timedelta(minutes=SESSION_MINUTES)
        _write({
            "phase": "awaiting_capture", "scope": SCOPE, "consent": True,
            "started_at": _now().isoformat(), "expires_at": until.isoformat(),
            "session_hash": hashlib.sha256(token.encode()).hexdigest(),
            "client_reported_at": "", "local_participant_id": "", "sample_count": 0,
        })
        return {"session": token, "visual": status()}


def report(*, session: str, participant_id: str, samples: int) -> dict[str, Any]:
    if not isinstance(session, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", session):
        raise VisualOnboardingError("Invalid local enrollment session.", 403)
    if not isinstance(participant_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", participant_id):
        raise VisualOnboardingError("Invalid local participant reference.", 422)
    if not isinstance(samples, int) or isinstance(samples, bool) or not 3 <= samples <= 5:
        raise VisualOnboardingError("Tracky requires three to five local face samples.", 422)
    if _privacy_engaged():
        raise VisualOnboardingError("Physical privacy is engaged.", 403)
    with _LOCK:
        row = _read()
        proof = hashlib.sha256(session.encode()).hexdigest()
        if row.get("phase") != "awaiting_capture" or _expired(row) or not row.get("consent") \
           or not hmac.compare_digest(str(row.get("session_hash") or ""), proof):
            raise VisualOnboardingError("Local consent expired or the enrollment session was cancelled.", 403)
        row.update({
            "phase": "browser_reported", "client_reported_at": _now().isoformat(),
            "local_participant_id": participant_id, "sample_count": samples,
            "session_hash": "", "expires_at": "",
        })
        _write(row)
        return status()


def cancel() -> dict[str, Any]:
    with _LOCK:
        row = _read()
        # Cancelling never pretends that independently persisted browser
        # biometrics have been deleted. The UI offers explicit local deletion.
        if row.get("phase") == "browser_reported":
            row["consent"] = False
            row["scope"] = ""
            row["phase"] = "browser_reported"
        else:
            row = {"phase": "cancelled", "consent": False, "scope": "",
                   "session_hash": "", "expires_at": ""}
        _write(row)
        return status()


def delete_report(*, participant_id: str) -> dict[str, Any]:
    with _LOCK:
        row = _read()
        if row.get("phase") != "browser_reported" or not hmac.compare_digest(
            str(row.get("local_participant_id") or ""), str(participant_id)
        ):
            raise VisualOnboardingError("No matching local enrollment report exists.", 404)
        # Caller MUST first delete the browser's canonical IndexedDB participant.
        # A response here confirms only that the HomeServer report was cleared.
        _write({"phase": "deleted", "consent": False, "scope": "", "session_hash": "",
                "expires_at": "", "local_participant_id": "", "sample_count": 0})
        return status()
