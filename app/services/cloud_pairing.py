from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from .remote_bridge import bridge_status


class CloudPairingError(RuntimeError):
    pass


_PAIRING_TOKEN = re.compile(r"^VP3-(?:[A-F0-9]{8}-){7}[A-F0-9]{8}$")
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_DEFAULT_PAIRING_ENDPOINT = "https://vp3.me/api/homeserver-pair-v1210.php"


def vp3_pairing_endpoint() -> str:
    raw = str(os.environ.get("HOMESERVER_VP3_PAIRING_URL") or _DEFAULT_PAIRING_ENDPOINT).strip()
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"https", "http"} or not host:
        raise CloudPairingError("VP3 pairing endpoint is invalid.")
    if parsed.scheme == "http" and host not in _LOOPBACK_HOSTS:
        raise CloudPairingError("VP3 pairing endpoint must use HTTPS.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise CloudPairingError("VP3 pairing endpoint cannot contain credentials, a query string, or a fragment.")
    return raw


def normalize_pairing_token(value: str) -> str:
    token = str(value or "").strip().upper()
    if not _PAIRING_TOKEN.fullmatch(token):
        raise CloudPairingError("Enter the VP3 pairing token generated in your Cloud account.")
    return token


def redeem_vp3_pairing_token(pairing_token: str) -> dict[str, Any]:
    token = normalize_pairing_token(pairing_token)
    status = bridge_status()
    configured = status.get("settings") if isinstance(status.get("settings"), dict) else {}
    runtime = status.get("runtime") if isinstance(status.get("runtime"), dict) else {}
    identity = status.get("identity") if isinstance(status.get("identity"), dict) else {}

    if not configured.get("enabled"):
        raise CloudPairingError("Enable Remote Bridge before pairing with VP3 Cloud.")
    if not runtime.get("connected"):
        raise CloudPairingError("HomeServer is not connected to the secure relay yet.")
    if runtime.get("claimed"):
        raise CloudPairingError("This HomeServer is already claimed by a Cloud account. Remove the existing Cloud pairing before using a new token.")

    relay_claim = str(runtime.get("claim_code") or "").strip()
    device_id = str(identity.get("device_id") or "").strip()
    if not relay_claim or not device_id:
        raise CloudPairingError("HomeServer relay proof is not ready yet. Refresh Remote Bridge and try again.")

    endpoint = vp3_pairing_endpoint()
    try:
        with httpx.Client(timeout=15.0, follow_redirects=False) as client:
            response = client.post(
                endpoint,
                json={
                    "pairing_token": token,
                    "relay_claim": relay_claim,
                },
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        raise CloudPairingError("VP3 Cloud could not be reached for pairing.") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise CloudPairingError("VP3 Cloud returned an invalid pairing response.") from exc
    if not isinstance(payload, dict):
        raise CloudPairingError("VP3 Cloud returned an invalid pairing response.")
    if response.status_code < 200 or response.status_code >= 300 or not payload.get("ok"):
        detail = str(payload.get("error") or payload.get("detail") or "VP3 Cloud rejected the pairing request.").strip()
        raise CloudPairingError(detail[:300])

    paired_device_id = str(payload.get("device_id") or "").strip()
    if paired_device_id != device_id:
        raise CloudPairingError("VP3 Cloud paired a different HomeServer device. The pairing was not accepted locally.")

    return {
        "accepted": True,
        "device_id": device_id,
        "request_id": str(payload.get("request_id") or "")[:128],
        "expires_at": str(payload.get("expires_at") or "")[:80],
        "permissions": payload.get("permissions") if isinstance(payload.get("permissions"), list) else [],
        "next_step": "Review and approve VP3 in HomeServer Connected Apps.",
    }
