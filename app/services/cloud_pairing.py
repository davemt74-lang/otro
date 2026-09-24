from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from ..config import settings
from ..database import db
from .pairing import create_pairing_request, approve_pairing_request
from .remote_identity import load_or_create_remote_identity
from .https_bridge_session import save_https_session
from .remote_bridge import (
    bridge_status,
    dispatch_remote_request,
    normalize_broker_url,
    save_bridge_settings,
    save_vp3_https_settings,
)


class CloudPairingError(RuntimeError):
    pass


_PAIRING_TOKEN = re.compile(r"^VP3-(?:[A-F0-9]{8}-){7}[A-F0-9]{8}$")
_DEVICE_ID = re.compile(r"^hs-[a-f0-9]{24}$")
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_DEFAULT_PAIRING_ENDPOINT = "https://vp3.me/api/homeserver-https-pair-v1300.php"
_DEFAULT_BOOTSTRAP_ENDPOINT = "https://vp3.me/api/homeserver-relay-bootstrap-v1210.php"


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


def vp3_bootstrap_endpoint() -> str:
    return _validated_cloud_endpoint(
        str(os.environ.get("HOMESERVER_VP3_BOOTSTRAP_URL") or _DEFAULT_BOOTSTRAP_ENDPOINT),
        "relay bootstrap",
    )


def normalize_pairing_token(value: str) -> str:
    token = str(value or "").strip().upper()
    if not _PAIRING_TOKEN.fullmatch(token):
        raise CloudPairingError("Enter the VP3 pairing token generated in your Cloud account.")
    return token


def bootstrap_vp3_remote_bridge() -> dict[str, Any]:
    endpoint = vp3_bootstrap_endpoint()
    try:
        with httpx.Client(timeout=10.0, follow_redirects=False) as client:
            response = client.get(endpoint, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise CloudPairingError("VP3 Cloud could not provide the relay configuration.") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise CloudPairingError("VP3 Cloud returned an invalid relay bootstrap response.") from exc
    if not isinstance(payload, dict) or response.status_code < 200 or response.status_code >= 300 or not payload.get("ok"):
        raise CloudPairingError("VP3 Cloud relay bootstrap is unavailable.")
    if str(payload.get("pairing_protocol") or "") != "account-token-v1":
        raise CloudPairingError("VP3 Cloud returned an unsupported pairing protocol.")

    try:
        broker_url = normalize_broker_url(str(payload.get("relay_websocket_url") or ""))
        settings = save_bridge_settings(True, broker_url)
    except Exception as exc:
        raise CloudPairingError("VP3 Cloud returned an invalid relay WebSocket endpoint.") from exc
    return {"configured": True, "settings": settings}


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
        with db() as connection:
            app = connection.execute("SELECT id FROM paired_apps WHERE app_key='vp3' LIMIT 1").fetchone()
            if app is not None:
                connection.execute("UPDATE paired_apps SET status='revoked' WHERE id=?", (app["id"],))
                connection.execute("UPDATE app_permissions SET allowed=0, updated_at=CURRENT_TIMESTAMP WHERE paired_app_id=?", (app["id"],))
    except Exception:
        pass


def redeem_vp3_pairing_token(pairing_token: str) -> dict[str, Any]:
    token = normalize_pairing_token(pairing_token)
    identity = load_or_create_remote_identity()
    device_id = str(identity.get("device_id") or "").strip().lower()
    if not _DEVICE_ID.fullmatch(device_id):
        raise CloudPairingError("HomeServer device identity is not ready for pairing.")

    # Clicking Pair on the local HomeServer is the local owner consent boundary.
    # Create the same least-privilege VP3 paired-app credential behind the scenes.
    _revoke_local_vp3_pairing()
    local_pairing = create_pairing_request("vp3", "VP3", _VP3_PERMISSIONS)
    approved = approve_pairing_request(str(local_pairing.get("request_id") or ""))
    local_token = str(local_pairing.get("claim_token") or "").strip()
    if not approved or len(local_token) < 32:
        _revoke_local_vp3_pairing()
        raise CloudPairingError("HomeServer could not establish local VP3 authorization.")

    capabilities_result = dispatch_remote_request("capabilities", {}, None)
    capabilities = capabilities_result.get("payload") if capabilities_result.get("ok") else {}
    if not isinstance(capabilities, dict):
        capabilities = {}

    endpoint = vp3_pairing_endpoint()
    try:
        with httpx.Client(timeout=20.0, follow_redirects=False) as client:
            response = client.post(
                endpoint,
                json={
                    "pairing_token": token,
                    "device_id": device_id,
                    "homeserver_token": local_token,
                    "version": settings.version,
                    "capabilities": capabilities,
                },
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        _revoke_local_vp3_pairing()
        raise CloudPairingError("VP3 Cloud could not be reached for pairing.") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        _revoke_local_vp3_pairing()
        raise CloudPairingError("VP3 Cloud returned an invalid pairing response.") from exc
    if not isinstance(payload, dict) or response.status_code < 200 or response.status_code >= 300 or not payload.get("ok"):
        _revoke_local_vp3_pairing()
        detail = str(payload.get("error") or payload.get("detail") or "VP3 Cloud rejected the pairing request.").strip() if isinstance(payload, dict) else ""
        raise CloudPairingError((detail or "VP3 Cloud rejected the pairing request.")[:300])

    paired_device_id = str(payload.get("device_id") or "").strip().lower()
    session_token = str(payload.get("session_token") or "").strip()
    poll_url = str(payload.get("poll_url") or "").strip()
    transport = str(payload.get("transport") or "").strip()
    if paired_device_id != device_id or transport != "vp3_https" or len(session_token) < 32 or not poll_url:
        _revoke_local_vp3_pairing()
        raise CloudPairingError("VP3 Cloud returned an invalid HTTPS relay session.")

    try:
        save_https_session(poll_url, session_token)
        save_vp3_https_settings(poll_url, True)
    except Exception as exc:
        _revoke_local_vp3_pairing()
        raise CloudPairingError("HomeServer could not save the VP3 HTTPS session.") from exc

    return {
        "accepted": True,
        "device_id": device_id,
        "transport": "vp3_https",
        "permissions": approved.get("permissions") if isinstance(approved, dict) else _VP3_PERMISSIONS,
        "next_step": "Connected. HomeServer will maintain the VP3 HTTPS session automatically.",
    }
