from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from ..config import settings
from ..database import db
from .provider_secrets import _atomic_write, _protect_windows, _unprotect_windows

VERSION = "v0.59"
CLOUD_CONTRACT_VERSION = "v6.20"
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_ALLOWED_OPERATIONS = {
    "capabilities", "overview", "availability", "booking.create", "booking.reschedule", "booking.cancel",
    "team.availability", "team.booking.create", "team.booking.cancel",
}
_WRITE_OPERATIONS = {"booking.create", "booking.reschedule", "booking.cancel", "team.booking.create", "team.booking.cancel"}


class VP3SchedulingConnectorError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _path() -> Path:
    return settings.data_dir / "security" / "vp3-scheduling-connector.dat"


def _validate_endpoint(value: str) -> str:
    endpoint = str(value or "").strip()
    if len(endpoint) > 2000:
        raise VP3SchedulingConnectorError("VP3 scheduling endpoint is too long.")
    parsed = urlparse(endpoint)
    host = (parsed.hostname or "").lower()
    if not parsed.scheme or not host or parsed.username or parsed.password or parsed.fragment:
        raise VP3SchedulingConnectorError("VP3 scheduling endpoint is invalid.")
    if parsed.query:
        raise VP3SchedulingConnectorError("VP3 scheduling endpoint may not include a query string.")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and host in _LOOPBACK):
        raise VP3SchedulingConnectorError("VP3 scheduling connector requires HTTPS except for loopback tests.")
    if not parsed.path.endswith("/api/homeserver-scheduling-v620.php"):
        raise VP3SchedulingConnectorError("VP3 scheduling endpoint path is not allowlisted.")
    return endpoint


def _encode(data: dict[str, Any]) -> bytes:
    raw = json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _protect_windows(raw) if os.name == "nt" else raw


def _decode(payload: bytes) -> dict[str, Any]:
    raw = _unprotect_windows(payload) if os.name == "nt" else payload
    parsed = json.loads(raw.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise VP3SchedulingConnectorError("VP3 scheduling connector store is invalid.", 500)
    return parsed


def _load() -> dict[str, Any]:
    path = _path()
    if not path.is_file():
        return {}
    try:
        return _decode(path.read_bytes())
    except VP3SchedulingConnectorError:
        raise
    except Exception as exc:
        raise VP3SchedulingConnectorError("VP3 scheduling connector could not be decrypted on this device.", 500) from exc


def _pairing_binding_active(pairing_token_hash: str) -> bool:
    binding = str(pairing_token_hash or "").strip().lower()
    if len(binding) != 64 or any(ch not in "0123456789abcdef" for ch in binding):
        return False
    try:
        with db() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM paired_apps
                WHERE app_key='vp3' AND token_hash=? AND status='active'
                LIMIT 1
                """,
                (binding,),
            ).fetchone()
    except Exception:
        return False
    return row is not None


def configure(
    endpoint: str,
    token: str,
    version: str = CLOUD_CONTRACT_VERSION,
    capabilities: list[str] | None = None,
    *,
    pairing_token_hash: str,
) -> dict:
    normalized = _validate_endpoint(endpoint)
    secret = str(token or "").strip()
    if len(secret) < 32 or len(secret) > 512:
        raise VP3SchedulingConnectorError("VP3 scheduling credential is invalid.")
    binding = str(pairing_token_hash or "").strip().lower()
    if not _pairing_binding_active(binding):
        raise VP3SchedulingConnectorError("VP3 scheduling connector requires the active paired VP3 identity.", 403)
    advertised = sorted({str(item).strip() for item in (capabilities or []) if str(item).strip() in _ALLOWED_OPERATIONS})
    data = {
        "endpoint": normalized,
        "token": secret,
        "version": str(version or CLOUD_CONTRACT_VERSION)[:40],
        "capabilities": advertised,
        "pairing_token_hash": binding,
    }
    _atomic_write(_path(), _encode(data))
    return status()


def clear() -> dict:
    try:
        _path().unlink(missing_ok=True)
    except OSError as exc:
        raise VP3SchedulingConnectorError("VP3 scheduling connector could not be cleared.", 500) from exc
    return status()


def status() -> dict:
    data = _load()
    endpoint = str(data.get("endpoint") or "")
    parsed = urlparse(endpoint) if endpoint else None
    token = str(data.get("token") or "")
    binding = str(data.get("pairing_token_hash") or "")
    pairing_bound = _pairing_binding_active(binding)
    return {
        "configured": bool(endpoint and token and pairing_bound),
        "pairing_bound": pairing_bound,
        "endpoint_host": (parsed.hostname or "") if parsed else "",
        "cloud_version": str(data.get("version") or ""),
        "capabilities": list(data.get("capabilities") or []),
        "token_suffix": token[-4:] if token else "",
        "protection": "windows-dpapi" if os.name == "nt" else "restricted-local-file",
        "version": VERSION,
    }


def request(operation: str, arguments: dict[str, Any] | None = None, *, idempotency_key: str = "") -> dict[str, Any]:
    op = str(operation or "").strip()
    if op not in _ALLOWED_OPERATIONS:
        raise VP3SchedulingConnectorError("VP3 scheduling operation is not allowlisted.")
    data = _load()
    endpoint = _validate_endpoint(str(data.get("endpoint") or ""))
    token = str(data.get("token") or "").strip()
    if not endpoint or not token:
        raise VP3SchedulingConnectorError("VP3 scheduling connector is not configured.", 409)
    if not _pairing_binding_active(str(data.get("pairing_token_hash") or "")):
        raise VP3SchedulingConnectorError("VP3 scheduling connector pairing is no longer active.", 409)
    key = str(idempotency_key or "").strip()
    if op in _WRITE_OPERATIONS and (not key or len(key) > 160):
        raise VP3SchedulingConnectorError("Scheduling mutations require a stable idempotency key.")
    payload = {"operation": op, "arguments": dict(arguments or {})}
    if key:
        payload["idempotency_key"] = key
    try:
        with httpx.Client(timeout=30.0, follow_redirects=False, trust_env=True) as client:
            response = client.post(
                endpoint,
                json=payload,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        raise VP3SchedulingConnectorError("VP3 scheduling service is unreachable.", 503) from exc
    try:
        body = response.json()
    except ValueError as exc:
        raise VP3SchedulingConnectorError("VP3 scheduling service returned an invalid response.", 502) from exc
    if not isinstance(body, dict) or not response.is_success or not body.get("ok"):
        message = str(body.get("error") if isinstance(body, dict) else "")[:300] or "VP3 scheduling request failed."
        raise VP3SchedulingConnectorError(message, 409 if response.status_code in {409, 422} else 502)
    result = body.get("result")
    if not isinstance(result, dict):
        raise VP3SchedulingConnectorError("VP3 scheduling response is missing its result.", 502)
    return result
