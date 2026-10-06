from __future__ import annotations

import os
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from ..config import settings
from ..database import db
from .pairing import create_pairing_request, approve_pairing_request, revoke_paired_app, authenticate
from .remote_identity import load_or_create_remote_identity
from .https_bridge_session import SESSION_LOCK, save_https_session, load_https_session, clear_https_session
from .owner_secret import _atomic_write, _protect_windows, _unprotect_windows
from .remote_bridge import (
    dispatch_remote_request,
    save_vp3_https_settings,
    disable_vp3_https_settings,
)


class CloudPairingError(RuntimeError):
    pass


_PAIRING_TOKEN = re.compile(r"^VP3-(?:[A-F0-9]{8}-){7}[A-F0-9]{8}$")
_DEVICE_ID = re.compile(r"^hs-[a-f0-9]{24}$")
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_DEFAULT_PAIRING_ENDPOINT = "https://vp3.me/api/homeserver-https-pair-v1300.php"


def _validated_cloud_endpoint(raw: str, label: str) -> str:
    value = str(raw or "").strip()
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"https", "http"} or not host:
        raise CloudPairingError(f"VP3 {label} endpoint is invalid.")
    if parsed.scheme == "http" and host not in _LOOPBACK_HOSTS:
        raise CloudPairingError(f"VP3 {label} endpoint must use HTTPS.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise CloudPairingError(f"VP3 {label} endpoint cannot contain credentials, a query string, or a fragment.")
    return value


def vp3_pairing_endpoint() -> str:
    return _validated_cloud_endpoint(
        str(os.environ.get("HOMESERVER_VP3_PAIRING_URL") or _DEFAULT_PAIRING_ENDPOINT),
        "pairing",
    )


def _resolved_poll_endpoint(pairing_endpoint: str, poll_url: str) -> str:
    resolved = urljoin(pairing_endpoint, str(poll_url or "").strip())
    endpoint = _validated_cloud_endpoint(resolved, "HTTPS relay")
    pairing_host = (urlparse(pairing_endpoint).hostname or "").lower()
    poll_host = (urlparse(endpoint).hostname or "").lower()
    if pairing_host != poll_host:
        raise CloudPairingError("VP3 Cloud returned a relay endpoint on an unexpected host.")
    pairing = urlparse(pairing_endpoint)
    poll = urlparse(endpoint)
    if (pairing.scheme != poll.scheme
            or (pairing.port or (443 if pairing.scheme == "https" else 80))
            != (poll.port or (443 if poll.scheme == "https" else 80))):
        raise CloudPairingError("VP3 Cloud returned a relay endpoint on an unexpected host.")
    return endpoint


def normalize_pairing_token(value: str) -> str:
    token = str(value or "").strip().upper()
    if not _PAIRING_TOKEN.fullmatch(token):
        raise CloudPairingError("Enter the VP3 pairing token generated in your Cloud account.")
    return token


_VP3_PERMISSIONS = [
    "agent.chat",
    "awareness.read",
    "contacts.read",
    "events.read",
    "events.write",
    "knowledge.search",
    "memory.read",
    "memory.write",
    "notifications.read",
    "plugins.read",
    "tasks.read",
    "tasks.write",
    "tools.execute",
    "usage.read",
    "usage.write",
]


def _revoke_local_vp3_pairing() -> None:
    try:
        revoke_paired_app("vp3")
    except Exception:
        pass


_PAIR_LOCK = SESSION_LOCK


def _pending_path():
    return settings.data_dir / "runtime" / "cloud-pairing-pending-v1.json"


def _load_pending() -> dict[str, Any] | None:
    path = _pending_path()
    if not path.is_file():
        return None
    try:
        raw = path.read_bytes()
        state = json.loads((_unprotect_windows(raw) if os.name == "nt" else raw).decode("utf-8"))
        if not isinstance(state, dict):
            raise ValueError("invalid journal")
        return state
    except Exception as exc:
        raise CloudPairingError("Pending Cloud pairing could not be read. Reset pairing and try again.") from exc


def _save_pending(state: dict[str, Any]) -> None:
    raw = json.dumps(state, separators=(",", ":")).encode("utf-8")
    _atomic_write(_pending_path(), _protect_windows(raw) if os.name == "nt" else raw)


def has_pending_pairing() -> bool:
    # A redacted recovery indicator, never journal contents or credentials.
    return _pending_path().is_file()


def clear_pending_pairing() -> None:
    with _PAIR_LOCK:
        # Reset only unfinished authorization; never revoke a saved connection.
        if _pending_path().is_file():
            if load_https_session():
                raise CloudPairingError("A Cloud session is already saved. Retry the same pairing or disconnect before resetting.")
            _revoke_local_vp3_pairing()
        _pending_path().unlink(missing_ok=True)


def disconnect_vp3_pairing() -> None:
    with _PAIR_LOCK:
        clear_https_session()
        disable_vp3_https_settings()
        _revoke_local_vp3_pairing()
        _pending_path().unlink(missing_ok=True)


def redeem_vp3_pairing_token(pairing_token: str) -> dict[str, Any]:
    with _PAIR_LOCK:
        return _redeem_locked(pairing_token)


def _redeem_locked(pairing_token: str) -> dict[str, Any]:
    token = normalize_pairing_token(pairing_token)
    endpoint = vp3_pairing_endpoint()
    identity = load_or_create_remote_identity()
    device_id = str(identity.get("device_id") or "").strip().lower()
    if not _DEVICE_ID.fullmatch(device_id):
        raise CloudPairingError("HomeServer device identity is not ready for pairing.")
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    pending = _load_pending()
    saved = load_https_session()
    if saved:
        # Recover a crash between session persistence and settings persistence.
        if (pending and pending.get("account_token_hash") == token_hash
                and pending.get("device_id") == device_id and pending.get("endpoint") == endpoint
                and pending.get("session_token") == saved["session_token"]
                and pending.get("poll_endpoint") == saved["endpoint"]
                and authenticate(str(pending.get("local_token") or ""))):
            save_vp3_https_settings(saved["endpoint"], True)
            _pending_path().unlink(missing_ok=True)
            return _accepted(device_id, pending.get("permissions") or [])
        raise CloudPairingError("HomeServer already has a saved Cloud connection. Disconnect it before pairing again.")
    if pending:
        if (pending.get("account_token_hash") != token_hash or pending.get("device_id") != device_id
                or pending.get("endpoint") != endpoint):
            raise CloudPairingError("Another Cloud pairing is pending. Reset pairing before using a new token.")
        try:
            expiry = datetime.fromisoformat(str(pending["expires_at"]))
            valid_time = expiry.tzinfo is not None and expiry > datetime.now(timezone.utc)
        except (ValueError, KeyError):
            valid_time = False
        if not valid_time:
            raise CloudPairingError("Pending Cloud pairing expired. Reset pairing and generate a new code.")
        local_token = str(pending.get("local_token") or "")
        if not authenticate(local_token):
            raise CloudPairingError("Local pairing approval was revoked. Reset pairing to approve a new connection.")
    else:
        # Validate endpoint/identity and refuse live replacement before touching authority.
        _revoke_local_vp3_pairing()
        local_pairing = create_pairing_request("vp3", "VP3", _VP3_PERMISSIONS)
        approved = approve_pairing_request(str(local_pairing.get("request_id") or ""))
        local_token = str(local_pairing.get("claim_token") or "").strip()
        if not approved or len(local_token) < 32:
            _revoke_local_vp3_pairing()
            raise CloudPairingError("HomeServer could not establish local VP3 authorization.")
        pending = {"account_token_hash": token_hash, "device_id": device_id, "endpoint": endpoint,
                   "local_token": local_token, "session_token": secrets.token_urlsafe(48),
                   "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
                   "permissions": approved.get("permissions", [])}
        try:
            _save_pending(pending)
        except Exception as exc:
            _revoke_local_vp3_pairing()
            raise CloudPairingError("HomeServer could not save pairing recovery state.") from exc

    capabilities_result = dispatch_remote_request("capabilities", {}, None)
    capabilities = capabilities_result.get("payload") if capabilities_result.get("ok") else {}
    if not isinstance(capabilities, dict):
        capabilities = {}
    try:
        with httpx.Client(timeout=20.0, follow_redirects=False, trust_env=False) as client:
            response = client.post(endpoint, json={
                "pairing_token": token, "device_id": device_id, "homeserver_token": local_token,
                "recovery_session_token": pending["session_token"],
                "version": settings.version, "capabilities": capabilities,
            }, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        # The server may already have committed. Keep the same approved secrets
        # so the next owner poll, including after restart, can recover that commit.
        raise CloudPairingError("VP3 Cloud could not be reached. Retry pairing with the same code or token.") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise CloudPairingError("VP3 Cloud returned an invalid pairing response. Retry the same pairing.") from exc
    if not isinstance(payload, dict) or not 200 <= response.status_code < 300 or payload.get("ok") is not True:
        raise CloudPairingError("VP3 Cloud rejected pairing. Retry the same code or reset pairing to start again.")
    paired_device_id = str(payload.get("device_id") or "").strip().lower()
    session_token = str(payload.get("session_token") or "").strip()
    poll_url = str(payload.get("poll_url") or "").strip()
    if paired_device_id != device_id or payload.get("transport") != "vp3_https" or len(session_token) < 32 or not poll_url:
        raise CloudPairingError("VP3 Cloud returned an invalid HTTPS relay session.")
    try:
        poll_endpoint = _resolved_poll_endpoint(endpoint, poll_url)
        # Older Cloud deployments may generate their own session secret. Preserve
        # the returned value before saving, while upgraded Cloud echoes our proof.
        pending.update({"session_token": session_token, "poll_endpoint": poll_endpoint})
        _save_pending(pending)
        save_https_session(poll_endpoint, session_token)
        save_vp3_https_settings(poll_endpoint, True)
    except Exception as exc:
        raise CloudPairingError("HomeServer could not save the VP3 HTTPS session. Retry the same pairing.") from exc
    _pending_path().unlink(missing_ok=True)
    return _accepted(device_id, pending.get("permissions") or [])


def _accepted(device_id: str, permissions: list) -> dict[str, Any]:
    return {"accepted": True, "device_id": device_id, "transport": "vp3_https",
            "permissions": permissions,
            "next_step": "Connected. HomeServer will maintain the VP3 HTTPS session automatically."}
