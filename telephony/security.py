"""Telephone-number, provider-webhook and service-envelope validation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from typing import Any


PHONE = re.compile(r"^\+[1-9][0-9]{7,14}$")


def phone_number(value: str) -> str:
    number = str(value or "").strip()
    if not PHONE.fullmatch(number):
        raise ValueError("A valid E.164 phone number is required")
    return number


def twilio_valid(auth_token: str, callback_url: str, form: dict[str, str], signature: str) -> bool:
    """Twilio's form-encoded signature: URL + sorted name/value pairs."""
    if not auth_token or not callback_url or not signature:
        return False
    canonical = callback_url + "".join(k + str(form[k]) for k in sorted(form))
    digest = hmac.new(auth_token.encode(), canonical.encode(), hashlib.sha1).digest()
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, signature)


def signed_envelope(secret: str, timestamp: int, body: bytes) -> str:
    return hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def valid_envelope(secret: str, timestamp: str, body: bytes, signature: str, *, now: int | None = None) -> bool:
    if not secret or not timestamp.isascii() or not timestamp.isdigit() or not signature:
        return False
    clock = int(time.time() if now is None else now)
    if abs(clock - int(timestamp)) > 300:
        return False
    return hmac.compare_digest(signed_envelope(secret, int(timestamp), body), signature)


def json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, sort_keys=True).encode("utf-8")
