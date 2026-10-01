"""Agent-chat first-run orchestration, reusing canonical Local Apps and Cloud HTTPS pairing.
Only approved package installs run automatically. Camera/microphone capture is never invoked.
Device-code verifier is local-only, DPAPI protected on Windows, and never sent to the browser.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from ..config import settings
from . import cloud_pairing, local_apps, remote_bridge, system_state, onboarding_visual, tracky_native_camera
from .owner_secret import _atomic_write, _protect_windows, _unprotect_windows
from .remote_identity import load_or_create_remote_identity

CLOUD_DEVICE_ENDPOINT = "https://vp3.me/api/homeserver-device-code-v1.php"
CLOUD_CLAIM_URL = "https://vp3.me/settings-homeserver.php"
VOICE_PACKAGES = ("whisper-stt", "piper-tts")
PROVISION_KEY = "first_run.chat_provision.v1"
_DEVICE_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_LOCK = threading.Lock()
_POLL_LOCK = threading.Lock()
_WORKER: threading.Thread | None = None


class OnboardingError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _provision_state() -> dict[str, Any]:
    result = system_state._read_setting(PROVISION_KEY, {})
    return result if isinstance(result, dict) else {}


def _save_progress(value: dict[str, Any]) -> None:
    system_state._write_setting(PROVISION_KEY, value)


def _voice_catalog() -> list[dict[str, Any]]:
    entries = local_apps.catalog()["packages"]
    return [item for item in entries if item["key"] in VOICE_PACKAGES]


def _healthy(item: dict[str, Any]) -> bool:
    installed = item.get("installed") or {}
    return installed.get("status") == "installed" and installed.get("healthy") is True


def provision_status() -> dict[str, Any]:
    row = _provision_state()
    items = _voice_catalog()
    running = bool(_WORKER and _WORKER.is_alive())
    phase = str(row.get("phase") or "ready")
    if phase == "running" and not running:
        phase = "interrupted"
    supported = [item for item in items if item.get("supported")]
    return {
        "phase": phase, "current": str(row.get("current") or ""),
        "approved": bool(row.get("approved")), "reason": str(row.get("reason") or "")[:140],
        "packages": [
            {"key": p["key"], "name": p["name"], "supported": bool(p["supported"]),
             "download_bytes": int(p.get("download_bytes") or 0), "ready": _healthy(p),
             "status": (p.get("installed") or {}).get("status") or "not_installed"}
            for p in items
        ],
        "all_ready": bool(supported) and all(_healthy(x) for x in supported),
        "unsupported_count": len(items) - len(supported),
        "supported_count": len(supported),
        "updated_at": str(row.get("updated_at") or ""),
    }


def _run_voice() -> None:
    failures = 0
    try:
        for key in VOICE_PACKAGES:
            package = next((p for p in _voice_catalog() if p["key"] == key), None)
            if not package or not package.get("supported") or _healthy(package):
                continue
            _save_progress({"approved": True, "phase": "running", "current": key, "updated_at": _now().isoformat()})
            try:
                # Idempotent: the canonical verified catalog skips healthy packages and
                # rechecks the SHA-pinned package before publishing capabilities.
                local_apps.install(key)
            except Exception:
                failures += 1
                # Preserve only coarse errors; download URLs and filesystem paths
                # must not be echoed into the scripted Agent canvas.
                _save_progress({"approved": True, "phase": "running", "current": key,
                                "reason": "A voice component needs attention.", "updated_at": _now().isoformat()})
        _save_progress({"approved": True, "phase": "attention" if failures else "complete",
                        "current": "", "reason": "One or more voice packages need retry." if failures else "",
                        "updated_at": _now().isoformat()})
    except Exception:
        _save_progress({"approved": True, "phase": "attention", "current": "",
                        "reason": "Voice installation was interrupted. Retry setup.",
                        "updated_at": _now().isoformat()})


def start_voice(*, resume: bool = False) -> dict[str, Any]:
    global _WORKER
    with _LOCK:
        if _WORKER and _WORKER.is_alive():
            return provision_status()
        existing = _provision_state()
        if resume and not (existing.get("approved") and existing.get("phase") == "running"):
            return provision_status()
        if provision_status()["all_ready"]:
            _save_progress({"approved": True, "phase": "complete", "current": "",
                            "updated_at": _now().isoformat()})
            return provision_status()
        _save_progress({"approved": True, "phase": "running", "current": "",
                        "updated_at": _now().isoformat()})
        _WORKER = threading.Thread(target=_run_voice, daemon=True, name="vp3-onboard-voice")
        _WORKER.start()
        return provision_status()


def resume_approved() -> None:
    if _provision_state().get("phase") == "running" and _provision_state().get("approved"):
        start_voice(resume=True)


def _device_file() -> Path:
    return settings.data_dir / "runtime" / "onboarding-device-code-v1.json"


def _remove_device() -> None:
    try:
        _device_file().unlink(missing_ok=True)
    except OSError:
        pass


def _read_device() -> dict[str, Any] | None:
    path = _device_file()
    try:
        state = json.loads((_unprotect_windows(path.read_bytes()) if os.name == "nt" else path.read_bytes()).decode("utf-8"))
        if not isinstance(state, dict):
            return None
        expiry = datetime.fromisoformat(str(state["expires_at"]))
        if expiry.tzinfo is None or expiry <= _now():
            _remove_device()
            return None
        if len(str(state.get("verifier") or "")) < 40:
            return None
        return state
    except Exception:
        return None


def _public_device(state: dict[str, Any] | None) -> dict[str, Any]:
    if not state:
        return {"state": "not_started", "code": None, "expires_at": None}
    return {"state": "pending", "code": str(state["code"]), "expires_at": str(state["expires_at"])}


def device_status() -> dict[str, Any]:
    cloud = remote_bridge.cloud_connection_status()["cloud"]
    paired = bool(cloud.get("paired"))
    if paired:
        _remove_device()
    return {"cloud": {"state": str(cloud.get("state") or "not_connected"),
                      "paired": paired, "connected": bool(cloud.get("connected"))},
            "pairing": {"state": "paired", "code": None, "expires_at": None} if paired
            else _public_device(_read_device()),
            "cloud_url": CLOUD_CLAIM_URL}


def summary() -> dict[str, Any]:
    return {"setup": system_state.first_run_status(),
            "provision": provision_status(), "visual": onboarding_visual.status(), "native_camera": tracky_native_camera.status(), **device_status()}


def _cloud(action: str, device: dict[str, Any]) -> dict[str, Any]:
    # Only one fixed reviewed HTTPS destination; never caller-controlled URLs.
    try:
        with httpx.Client(timeout=12, follow_redirects=False, trust_env=False) as client:
            result = client.post(
                CLOUD_DEVICE_ENDPOINT,
                json={"action": action, "code": device["code"].replace("-", ""),
                      "verifier": device["verifier"], "device_id": device["device_id"]},
                headers={"Accept": "application/json"},
            )
        content = result.json()
        if not isinstance(content, dict) or not result.is_success or content.get("ok") is not True:
            raise OnboardingError("VP3 Cloud could not complete device pairing. Retry or use advanced pairing.")
        return content
    except (httpx.HTTPError, ValueError) as exc:
        raise OnboardingError("VP3 Cloud is unavailable. Check your connection and retry.") from exc


def new_device_code() -> dict[str, Any]:
    with _POLL_LOCK:
        current = device_status()
        if current["cloud"]["paired"]:
            return current
        previous = _read_device()
        if previous:
            return current
        raw = "".join(secrets.choice(_DEVICE_CODE_ALPHABET) for _ in range(12))
        identity = load_or_create_remote_identity()
        code = "-".join(raw[n:n + 4] for n in (0, 4, 8))
        state = {"code": code, "verifier": secrets.token_urlsafe(32),
                 "device_id": identity["device_id"],
                 "expires_at": (_now() + timedelta(minutes=15)).isoformat()}
        _cloud("start", state)
        path = _device_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(state).encode("utf-8")
        _atomic_write(path, _protect_windows(payload) if os.name == "nt" else payload)
        return device_status()


def poll_device_code() -> dict[str, Any]:
    with _POLL_LOCK:
        current = device_status()
        if current["cloud"]["paired"]:
            return current
        state = _read_device()
        if state is None:
            return current
        result = _cloud("poll", state)
        if result.get("state") == "expired":
            _remove_device()
            return device_status()
        if result.get("state") != "claimed":
            return current
        token = str(result.get("pairing_token") or "")
        if not token:
            raise OnboardingError("Cloud authorization is incomplete. Try again.")
        try:
            cloud_pairing.redeem_vp3_pairing_token(token)
        except cloud_pairing.CloudPairingError as exc:
            raise OnboardingError("Cloud authorized the code, but HomeServer could not finish pairing. Retry or start again.") from exc
        # The one-time account token has been redeemed through the existing v1.3
        # Cloud HTTPS endpoint. Erase local verifier before returning to the UI.
        try:
            _cloud("complete", state)
        except OnboardingError:
            pass
        _remove_device()
        return device_status()


def clear_pending_code() -> dict[str, Any]:
    _remove_device()
    return device_status()
