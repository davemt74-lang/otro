from __future__ import annotations

import json
import os
from pathlib import Path

from ..config import settings
from .provider_secrets import _atomic_write, _protect_windows, _unprotect_windows

VERSION = "v0.60"


class StripePaymentSecretError(RuntimeError):
    pass


def _path() -> Path:
    return settings.data_dir / "security" / "appointment-payment-stripe.dat"


def _encode(data: dict[str, str]) -> bytes:
    raw = json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _protect_windows(raw) if os.name == "nt" else raw


def _decode(payload: bytes) -> dict[str, str]:
    raw = _unprotect_windows(payload) if os.name == "nt" else payload
    parsed = json.loads(raw.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise StripePaymentSecretError("Stripe appointment payment credential store is invalid.")
    result: dict[str, str] = {}
    for key in ("secret_key", "webhook_secret"):
        value = str(parsed.get(key) or "").strip()
        if value:
            result[key] = value
    return result


def load_credentials() -> dict[str, str]:
    path = _path()
    if not path.is_file():
        return {}
    try:
        return _decode(path.read_bytes())
    except Exception as exc:
        raise StripePaymentSecretError("Stripe appointment payment credentials could not be decrypted on this device.") from exc


def status() -> dict:
    data = load_credentials()
    secret = data.get("secret_key", "")
    webhook = data.get("webhook_secret", "")
    return {
        "provider": "stripe",
        "configured": bool(secret),
        "webhook_configured": bool(webhook),
        "secret_key_suffix": secret[-4:] if secret else "",
        "webhook_secret_suffix": webhook[-4:] if webhook else "",
        "protection": "windows-dpapi" if os.name == "nt" else "restricted-local-file",
        "version": VERSION,
    }


def save(secret_key: str | None = None, webhook_secret: str | None = None, *, clear: bool = False) -> dict:
    data = {} if clear else load_credentials()
    if secret_key is not None:
        value = str(secret_key).strip()
        if value and not (value.startswith("sk_live_") or value.startswith("sk_test_")):
            raise StripePaymentSecretError("Stripe secret key must be an sk_live_ or sk_test_ key.")
        if len(value) > 512:
            raise StripePaymentSecretError("Stripe secret key is too long.")
        if value:
            data["secret_key"] = value
        else:
            data.pop("secret_key", None)
    if webhook_secret is not None:
        value = str(webhook_secret).strip()
        if value and not value.startswith("whsec_"):
            raise StripePaymentSecretError("Stripe webhook secret must be a whsec_ secret.")
        if len(value) > 512:
            raise StripePaymentSecretError("Stripe webhook secret is too long.")
        if value:
            data["webhook_secret"] = value
        else:
            data.pop("webhook_secret", None)
    if data:
        _atomic_write(_path(), _encode(data))
    else:
        _path().unlink(missing_ok=True)
    return status()


def secret_key() -> str:
    value = load_credentials().get("secret_key", "")
    if not value:
        raise StripePaymentSecretError("Local Stripe appointment payments are not configured.")
    return value


def webhook_secret() -> str:
    value = load_credentials().get("webhook_secret", "")
    if not value:
        raise StripePaymentSecretError("Local Stripe webhook verification is not configured.")
    return value
