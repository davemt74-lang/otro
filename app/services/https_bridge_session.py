from __future__ import annotations

import json
import os
from urllib.parse import urlparse

from ..config import settings
from .owner_secret import _atomic_write, _protect_windows, _unprotect_windows


class HttpsBridgeSessionError(RuntimeError):
    pass


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def normalize_https_endpoint(value: str) -> str:
    raw = str(value or "").strip()
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"https", "http"} or not host:
        raise HttpsBridgeSessionError("VP3 HTTPS relay endpoint is invalid.")
    if parsed.scheme == "http" and host not in _LOOPBACK_HOSTS:
        raise HttpsBridgeSessionError("VP3 HTTPS relay requires HTTPS.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HttpsBridgeSessionError("VP3 HTTPS relay endpoint cannot contain credentials, a query string, or a fragment.")
    return raw


def _encode(payload: bytes) -> bytes:
    return _protect_windows(payload) if os.name == "nt" else payload


def _decode(payload: bytes) -> bytes:
    return _unprotect_windows(payload) if os.name == "nt" else payload


def save_https_session(endpoint: str, session_token: str) -> dict:
    endpoint = normalize_https_endpoint(endpoint)
    token = str(session_token or "").strip()
    if len(token) < 32 or len(token) > 512:
        raise HttpsBridgeSessionError("VP3 HTTPS session token is invalid.")
    body = json.dumps({"endpoint": endpoint, "session_token": token}, separators=(",", ":")).encode("utf-8")
    _atomic_write(settings.remote_https_session_path, _encode(body))
    return {"endpoint": endpoint, "configured": True}


def load_https_session() -> dict | None:
    path = settings.remote_https_session_path
    if not path.is_file():
        return None
    try:
        data = json.loads(_decode(path.read_bytes()).decode("utf-8"))
        endpoint = normalize_https_endpoint(str(data.get("endpoint") or ""))
        token = str(data.get("session_token") or "").strip()
        if len(token) < 32 or len(token) > 512:
            raise ValueError("invalid token")
        return {"endpoint": endpoint, "session_token": token}
    except Exception:
        return None


def https_session_matches(session_token: str) -> bool:
    current = load_https_session()
    if not current:
        return False
    return str(current.get("session_token") or "") == str(session_token or "").strip()


def clear_https_session_if_matches(session_token: str) -> bool:
    if not https_session_matches(session_token):
        return False
    clear_https_session()
    return True


def clear_https_session() -> None:
    try:
        settings.remote_https_session_path.unlink(missing_ok=True)
    except OSError:
        pass
