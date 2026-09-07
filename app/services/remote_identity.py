from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timezone

from ..config import settings
from .owner_secret import _atomic_write, _protect_windows, _unprotect_windows


class RemoteIdentityError(RuntimeError):
    pass


def _encode(payload: bytes) -> bytes:
    return _protect_windows(payload) if os.name == "nt" else payload


def _decode(payload: bytes) -> bytes:
    return _unprotect_windows(payload) if os.name == "nt" else payload


def _device_id(secret: str) -> str:
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    return f"hs-{digest[:24]}"


def load_or_create_remote_identity() -> dict:
    path = settings.remote_bridge_secret_path
    if path.is_file():
        try:
            decoded = json.loads(_decode(path.read_bytes()).decode("utf-8"))
            secret = str(decoded.get("secret") or "")
            created_at = str(decoded.get("created_at") or "")
            if len(secret) < 40:
                raise ValueError("Remote bridge secret is invalid")
            return {
                "device_id": _device_id(secret),
                "device_secret": secret,
                "created_at": created_at or None,
            }
        except Exception:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            try:
                path.replace(path.with_name(f"{path.name}.invalid-{stamp}"))
            except OSError:
                pass

    secret = secrets.token_urlsafe(48)
    created_at = datetime.now(timezone.utc).isoformat()
    encoded = json.dumps(
        {"secret": secret, "created_at": created_at},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    _atomic_write(path, _encode(encoded))
    return {
        "device_id": _device_id(secret),
        "device_secret": secret,
        "created_at": created_at,
    }


def remote_identity_metadata() -> dict:
    identity = load_or_create_remote_identity()
    return {
        "device_id": identity["device_id"],
        "created_at": identity["created_at"],
        "protection": "windows-dpapi" if os.name == "nt" else "restricted-local-file",
        "path": str(settings.remote_bridge_secret_path),
        "exists": settings.remote_bridge_secret_path.is_file(),
    }
