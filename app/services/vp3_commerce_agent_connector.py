from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from ..config import settings
from ..database import db
from .provider_secrets import _atomic_write, _protect_windows, _unprotect_windows

VERSION = "v0.61"
CLOUD_CONTRACT = "commerce-agent-v1"
CONTRACT_SHA256 = "b61b1baea945286fb006ef78f7f798b4a604284145a74f71628d43779173bf77"
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_ALLOWED_OPERATIONS = {
    "vp3.commerce.agent.status",
    "vp3.commerce.catalog.search",
    "vp3.commerce.product.get",
    "vp3.commerce.orders.list",
    "vp3.commerce.order.get",
    "vp3.commerce.checkout.handoff",
    "vp3.commerce.fulfillment.update",
}
_REQUIRED_PAIRING_PERMISSIONS = {"tools.execute", "commerce.read", "commerce.order", "commerce.fulfill"}


class VP3CommerceAgentConnectorError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _path() -> Path:
    return settings.data_dir / "security" / "vp3-commerce-agent-connector.dat"


def _validate_endpoint(value: str) -> str:
    endpoint = str(value or "").strip()
    if len(endpoint) > 2000:
        raise VP3CommerceAgentConnectorError("VP3 Agent Commerce endpoint is too long.")
    parsed = urlparse(endpoint)
    host = (parsed.hostname or "").lower()
    if not parsed.scheme or not host or parsed.username or parsed.password or parsed.fragment or parsed.query:
        raise VP3CommerceAgentConnectorError("VP3 Agent Commerce endpoint is invalid.")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and host in _LOOPBACK):
        raise VP3CommerceAgentConnectorError("VP3 Agent Commerce connector requires HTTPS except for loopback tests.")
    if not parsed.path.endswith("/api/homeserver-commerce-agent-v1000.php"):
        raise VP3CommerceAgentConnectorError("VP3 Agent Commerce endpoint path is not allowlisted.")
    return endpoint


def _encode(data: dict[str, Any]) -> bytes:
    raw = json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _protect_windows(raw) if os.name == "nt" else raw


def _decode(payload: bytes) -> dict[str, Any]:
    raw = _unprotect_windows(payload) if os.name == "nt" else payload
    parsed = json.loads(raw.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise VP3CommerceAgentConnectorError("VP3 Agent Commerce connector store is invalid.", 500)
    return parsed


def _load() -> dict[str, Any]:
    path = _path()
    if not path.is_file():
        return {}
    try:
        return _decode(path.read_bytes())
    except VP3CommerceAgentConnectorError:
        raise
    except Exception as exc:
        raise VP3CommerceAgentConnectorError("VP3 Agent Commerce connector could not be decrypted on this device.", 500) from exc


def _pairing_binding_active(pairing_token_hash: str) -> bool:
    binding = str(pairing_token_hash or "").strip().lower()
    if len(binding) != 64 or any(ch not in "0123456789abcdef" for ch in binding):
        return False
    try:
        with db() as connection:
            app = connection.execute(
                "SELECT id FROM paired_apps WHERE app_key='vp3' AND token_hash=? AND status='active' LIMIT 1",
                (binding,),
            ).fetchone()
            if app is None:
                return False
            rows = connection.execute(
                "SELECT permission FROM app_permissions WHERE paired_app_id=? AND allowed=1",
                (app["id"],),
            ).fetchall()
    except Exception:
        return False
    granted = {str(row["permission"]) for row in rows}
    return _REQUIRED_PAIRING_PERMISSIONS.issubset(granted)


def configure(
    endpoint: str,
    token: str,
    contract: str,
    contract_sha256: str,
    capabilities: list[str] | None = None,
    *,
    pairing_token_hash: str,
) -> dict:
    normalized = _validate_endpoint(endpoint)
    secret = str(token or "").strip()
    if len(secret) < 32 or len(secret) > 512:
        raise VP3CommerceAgentConnectorError("VP3 Agent Commerce credential is invalid.")
    if str(contract or "").strip() != CLOUD_CONTRACT or str(contract_sha256 or "").strip().lower() != CONTRACT_SHA256:
        raise VP3CommerceAgentConnectorError("VP3 Agent Commerce contract revision is not supported.", 409)
    binding = str(pairing_token_hash or "").strip().lower()
    if not _pairing_binding_active(binding):
        raise VP3CommerceAgentConnectorError("VP3 Agent Commerce requires the active paired VP3 identity and Commerce grants.", 403)
    advertised = sorted({str(item).strip() for item in (capabilities or []) if str(item).strip() in _ALLOWED_OPERATIONS})
    data = {
        "endpoint": normalized,
        "token": secret,
        "contract": CLOUD_CONTRACT,
        "contract_sha256": CONTRACT_SHA256,
        "capabilities": advertised,
        "pairing_token_hash": binding,
    }
    _atomic_write(_path(), _encode(data))
    return status()


def clear() -> dict:
    try:
        _path().unlink(missing_ok=True)
    except OSError as exc:
        raise VP3CommerceAgentConnectorError("VP3 Agent Commerce connector could not be cleared.", 500) from exc
    return status()


def status() -> dict:
    data = _load()
    endpoint = str(data.get("endpoint") or "")
    parsed = urlparse(endpoint) if endpoint else None
    token = str(data.get("token") or "")
    binding = str(data.get("pairing_token_hash") or "")
    pairing_bound = _pairing_binding_active(binding)
    contract_ok = str(data.get("contract") or "") == CLOUD_CONTRACT and str(data.get("contract_sha256") or "") == CONTRACT_SHA256
    return {
        "configured": bool(endpoint and token and pairing_bound and contract_ok),
        "pairing_bound": pairing_bound,
        "endpoint_host": (parsed.hostname or "") if parsed else "",
        "contract": str(data.get("contract") or ""),
        "contract_sha256": str(data.get("contract_sha256") or ""),
        "capabilities": list(data.get("capabilities") or []),
        "token_suffix": token[-4:] if token else "",
        "protection": "windows-dpapi" if os.name == "nt" else "restricted-local-file",
        "version": VERSION,
    }


def request(operation: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    op = str(operation or "").strip()
    if op not in _ALLOWED_OPERATIONS:
        raise VP3CommerceAgentConnectorError("VP3 Agent Commerce operation is not allowlisted.")
    data = _load()
    endpoint = _validate_endpoint(str(data.get("endpoint") or ""))
    token = str(data.get("token") or "").strip()
    if not endpoint or not token:
        raise VP3CommerceAgentConnectorError("VP3 Agent Commerce connector is not configured.", 409)
    if not _pairing_binding_active(str(data.get("pairing_token_hash") or "")):
        raise VP3CommerceAgentConnectorError("VP3 Agent Commerce pairing or grants are no longer active.", 409)
    payload = {"operation": op, "arguments": dict(arguments or {})}
    try:
        with httpx.Client(timeout=30.0, follow_redirects=False, trust_env=True) as client:
            response = client.post(endpoint, json=payload, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise VP3CommerceAgentConnectorError("VP3 Agent Commerce service is unreachable.", 503) from exc
    try:
        body = response.json()
    except ValueError as exc:
        raise VP3CommerceAgentConnectorError("VP3 Agent Commerce service returned an invalid response.", 502) from exc
    if not isinstance(body, dict) or not response.is_success or not body.get("ok"):
        message = str(body.get("error") if isinstance(body, dict) else "")[:300] or "VP3 Agent Commerce request failed."
        raise VP3CommerceAgentConnectorError(message, 409 if response.status_code in {409, 422} else 502)
    result = body.get("result")
    if not isinstance(result, dict):
        raise VP3CommerceAgentConnectorError("VP3 Agent Commerce response is missing its result.", 502)
    if result.get("contract") != CLOUD_CONTRACT or result.get("contract_sha256") != CONTRACT_SHA256:
        raise VP3CommerceAgentConnectorError("VP3 Agent Commerce response contract does not match the pinned revision.", 502)
    return result
