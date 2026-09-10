from __future__ import annotations

import re
from typing import Callable

import httpx

from ..config import settings
from . import remote_bridge


_FILE_REF = re.compile(r"^hsf-\d+-[0-9a-f]{16}$")


def _token(value: str | None) -> str:
    token = str(value or "").strip()
    if len(token) < 20 or len(token) > 512:
        raise remote_bridge.RemoteBridgeError("A paired-app bearer token is required for file access.")
    return token


def _bounded_int(value, *, default: int, minimum: int, maximum: int, label: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise remote_bridge.RemoteBridgeError(f"{label} must be an integer.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise remote_bridge.RemoteBridgeError(f"{label} must be an integer.") from exc
    if parsed < minimum or parsed > maximum:
        raise remote_bridge.RemoteBridgeError(f"{label} must be between {minimum} and {maximum}.")
    return parsed


def _local_response(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except ValueError:
        payload = {"detail": "HomeServer returned a non-JSON response."}
    return {
        "status": int(response.status_code),
        "ok": 200 <= response.status_code < 300,
        "payload": payload,
    }


def install() -> None:
    """Add the bounded v0.38 file operations to the fail-closed relay dispatcher."""
    if getattr(remote_bridge, "_local_files_v038_installed", False):
        return

    original: Callable[[str, dict | None, str | None], dict] = remote_bridge.dispatch_remote_request

    def extended(operation: str, payload: dict | None, bearer_token: str | None = None) -> dict:
        op = str(operation or "").strip()
        if op not in {"files.list", "files.read"}:
            return original(operation, payload, bearer_token)

        body = payload if isinstance(payload, dict) else {}
        token = _token(bearer_token)
        headers = {"Authorization": f"Bearer {token}"}
        base_url = f"http://{settings.host}:{settings.port}"
        with httpx.Client(base_url=base_url, timeout=30.0, trust_env=False) as client:
            if op == "files.list":
                query = str(body.get("query") or "")[:240]
                limit = _bounded_int(body.get("limit"), default=20, minimum=1, maximum=50, label="limit")
                return _local_response(
                    client.get(
                        "/api/v1/files",
                        params={"q": query, "limit": limit},
                        headers=headers,
                    )
                )

            file_ref = str(body.get("ref") or "").strip().lower()
            if not _FILE_REF.fullmatch(file_ref):
                raise remote_bridge.RemoteBridgeError("files.read requires a valid HomeServer file reference.")
            offset = _bounded_int(body.get("offset"), default=0, minimum=0, maximum=10_000_000, label="offset")
            max_chars = _bounded_int(body.get("max_chars"), default=6000, minimum=1, maximum=12000, label="max_chars")
            return _local_response(
                client.get(
                    f"/api/v1/files/{file_ref}",
                    params={"offset": offset, "max_chars": max_chars},
                    headers=headers,
                )
            )

    remote_bridge.dispatch_remote_request = extended
    remote_bridge._local_files_v038_installed = True
