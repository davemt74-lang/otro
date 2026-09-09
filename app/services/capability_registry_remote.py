from __future__ import annotations

from typing import Callable

import httpx

from ..config import settings
from . import remote_bridge


def install() -> None:
    """Add the authenticated v0.33 registry operation without widening legacy dispatch."""
    if getattr(remote_bridge, "_capability_registry_v033_installed", False):
        return

    original: Callable[[str, dict | None, str | None], dict] = remote_bridge.dispatch_remote_request

    def extended(operation: str, payload: dict | None, bearer_token: str | None = None) -> dict:
        op = str(operation or "").strip()
        if op != "capability.registry":
            return original(operation, payload, bearer_token)
        token = str(bearer_token or "").strip()
        if len(token) < 20 or len(token) > 512:
            raise remote_bridge.RemoteBridgeError("A paired-app bearer token is required for this remote operation.")
        headers = {"Authorization": f"Bearer {token}"}
        base_url = f"http://{settings.host}:{settings.port}"
        with httpx.Client(base_url=base_url, timeout=10.0, trust_env=False) as client:
            response = client.get("/api/v1/capability-registry", headers=headers)
        try:
            body = response.json()
        except ValueError:
            body = {"detail": "HomeServer returned a non-JSON response."}
        return {"status": int(response.status_code), "ok": 200 <= response.status_code < 300, "payload": body}

    remote_bridge.dispatch_remote_request = extended
    remote_bridge._capability_registry_v033_installed = True
