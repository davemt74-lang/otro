from __future__ import annotations

import json
from typing import Any, Callable

import httpx

from ..config import settings
from . import remote_bridge

_CUSTOM_ROUTES = {
    "knowledge.upsert": "/api/v1/knowledge/external",
    "knowledge.backup.status": "/api/v1/knowledge/external/status",
    "knowledge.asset.begin": "/api/v1/knowledge/external/assets/begin",
    "knowledge.asset.chunk": "/api/v1/knowledge/external/assets/chunk",
    "knowledge.asset.commit": "/api/v1/knowledge/external/assets/commit",
}


def _payload_size(payload: Any) -> int:
    try:
        return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise remote_bridge.RemoteBridgeError("Remote request payload is not valid JSON.") from exc


def _token(value: str | None) -> str:
    token = str(value or "").strip()
    if len(token) < 20 or len(token) > 512:
        raise remote_bridge.RemoteBridgeError("A paired-app bearer token is required for this remote operation.")
    return token


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
    """Install v0.32 operations without widening the legacy bridge dispatcher.

    Keeping the extension separate makes the large, security-sensitive v0.18
    bridge dispatcher stable while preserving its fail-closed default for every
    operation not explicitly handled here or by the original allowlist.
    """
    if getattr(remote_bridge, "_knowledge_backup_v032_installed", False):
        return

    original: Callable[[str, dict | None, str | None], dict] = remote_bridge.dispatch_remote_request

    def extended(operation: str, payload: dict | None, bearer_token: str | None = None) -> dict:
        op = str(operation or "").strip()
        route = _CUSTOM_ROUTES.get(op)
        if route is None:
            return original(operation, payload, bearer_token)

        body = payload if isinstance(payload, dict) else {}
        if _payload_size(body) > settings.max_remote_bridge_message_bytes:
            raise remote_bridge.RemoteBridgeError("Remote request payload is too large.")
        token = _token(bearer_token)
        headers = {"Authorization": f"Bearer {token}"}
        base_url = f"http://{settings.host}:{settings.port}"
        with httpx.Client(base_url=base_url, timeout=125.0, trust_env=False) as client:
            return _local_response(client.post(route, json=body, headers=headers))

    remote_bridge.dispatch_remote_request = extended
    remote_bridge._knowledge_backup_v032_installed = True
