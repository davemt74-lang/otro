from __future__ import annotations

from typing import Callable

import httpx

from ..config import settings
from . import remote_bridge


def _token(value: str | None) -> str:
    token = str(value or "").strip()
    if len(token) < 20 or len(token) > 512:
        raise remote_bridge.RemoteBridgeError("A paired-app bearer token is required for knowledge.search.")
    return token


def _bounded_limit(value) -> int:
    if value is None:
        return 20
    if isinstance(value, bool):
        raise remote_bridge.RemoteBridgeError("limit must be an integer.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise remote_bridge.RemoteBridgeError("limit must be an integer.") from exc
    if parsed < 1 or parsed > 50:
        raise remote_bridge.RemoteBridgeError("limit must be between 1 and 50.")
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
    """Install the scoped search contract, then chain newer knowledge operations.

    v0.37 owns the canonical citation-safe knowledge.search route. v0.62 layers
    collection/folder mapping and collection-scoped writes on top of that same
    fail-closed dispatcher so older paired clients remain compatible.
    """
    if not getattr(remote_bridge, "_knowledge_collections_v037_installed", False):
        original: Callable[[str, dict | None, str | None], dict] = remote_bridge.dispatch_remote_request

        def extended(operation: str, payload: dict | None, bearer_token: str | None = None) -> dict:
            op = str(operation or "").strip()
            if op != "knowledge.search":
                return original(operation, payload, bearer_token)

            body = payload if isinstance(payload, dict) else {}
            query = str(body.get("query") or "")[:240]
            limit = _bounded_limit(body.get("limit"))
            token = _token(bearer_token)
            headers = {"Authorization": f"Bearer {token}"}
            base_url = f"http://{settings.host}:{settings.port}"
            with httpx.Client(base_url=base_url, timeout=30.0, trust_env=False) as client:
                response = client.get(
                    "/api/v1/knowledge/search-v037",
                    params={"q": query, "limit": limit},
                    headers=headers,
                )
                return _local_response(response)

        remote_bridge.dispatch_remote_request = extended
        remote_bridge._knowledge_collections_v037_installed = True

    # Imported lazily to avoid a module cycle while this v0.37 wrapper is being
    # initialized. The v0.62 installer is itself idempotent.
    from .knowledge_folder_mapping_remote import install as install_v062

    install_v062()
