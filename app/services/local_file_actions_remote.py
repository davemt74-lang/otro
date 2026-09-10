from __future__ import annotations

import re
from typing import Callable

import httpx

from ..config import settings
from . import remote_bridge


_REF_RE = re.compile(r"^hsf-[0-9]+-[0-9a-f]{16}$")
_MAX_UPDATE_CHARS = 50000


def _token(value: str | None) -> str:
    token = str(value or "").strip()
    if len(token) < 20 or len(token) > 512:
        raise remote_bridge.RemoteBridgeError(
            "A paired-app bearer token is required for governed file actions."
        )
    return token


def _ref(value) -> str:
    ref = str(value or "").strip().lower()
    if not _REF_RE.fullmatch(ref):
        raise remote_bridge.RemoteBridgeError(
            "Governed file actions require a valid HomeServer file reference."
        )
    return ref


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
    if getattr(remote_bridge, "_local_file_actions_v039_installed", False):
        return

    original: Callable[[str, dict | None, str | None], dict] = remote_bridge.dispatch_remote_request

    def extended(operation: str, payload: dict | None, bearer_token: str | None = None) -> dict:
        op = str(operation or "").strip()
        if op not in {"files.update", "files.delete"}:
            return original(operation, payload, bearer_token)

        body = payload if isinstance(payload, dict) else {}
        token = _token(bearer_token)
        file_ref = _ref(body.get("ref"))
        arguments: dict[str, object] = {"ref": file_ref}
        if op == "files.update":
            content = body.get("content")
            if not isinstance(content, str) or not content.strip():
                raise remote_bridge.RemoteBridgeError("files.update requires non-empty text content.")
            if len(content) > _MAX_UPDATE_CHARS:
                raise remote_bridge.RemoteBridgeError(
                    f"files.update content exceeds {_MAX_UPDATE_CHARS:,} characters."
                )
            arguments["content"] = content
        elif set(body) - {"ref"}:
            raise remote_bridge.RemoteBridgeError("files.delete accepts only a HomeServer file reference.")

        if op == "files.update" and set(body) - {"ref", "content"}:
            raise remote_bridge.RemoteBridgeError("files.update accepts only ref and content.")

        base_url = f"http://{settings.host}:{settings.port}"
        headers = {"Authorization": f"Bearer {token}"}
        with httpx.Client(base_url=base_url, timeout=125.0, trust_env=False) as client:
            return _local_response(
                client.post(
                    f"/api/v1/tools/{op}/execute",
                    json={"arguments": arguments},
                    headers=headers,
                )
            )

    remote_bridge.dispatch_remote_request = extended
    remote_bridge._local_file_actions_v039_installed = True
