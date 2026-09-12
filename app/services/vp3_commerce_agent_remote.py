from __future__ import annotations

import hashlib
from typing import Callable

from . import pairing, remote_bridge, vp3_commerce_agent_connector

OPERATION = "vp3.commerce.connector.configure"
_ALLOWED_FIELDS = {"endpoint", "token", "contract", "contract_sha256", "capabilities"}


def install() -> None:
    if getattr(remote_bridge, "_vp3_commerce_agent_v061_installed", False):
        return
    original: Callable[[str, dict | None, str | None], dict] = remote_bridge.dispatch_remote_request

    def dispatch_remote_request(operation: str, payload: dict | None, bearer_token: str | None = None) -> dict:
        if str(operation or "").strip() != OPERATION:
            return original(operation, payload, bearer_token)
        token = str(bearer_token or "").strip()
        identity = pairing.authenticate(token) if token else None
        if identity is None or str(identity.get("app_key") or "").strip() != "vp3":
            raise remote_bridge.RemoteBridgeError("Only the paired VP3 application may configure Agent Commerce.")
        granted = set(identity.get("permissions") or [])
        required = {"tools.execute", "commerce.read", "commerce.order", "commerce.fulfill"}
        if not required.issubset(granted):
            raise remote_bridge.RemoteBridgeError("The paired VP3 application is missing Agent Commerce permissions.")
        body = payload if isinstance(payload, dict) else {}
        unknown = set(body) - _ALLOWED_FIELDS
        if unknown:
            raise remote_bridge.RemoteBridgeError(f"VP3 Agent Commerce connector payload contains unsupported field: {sorted(unknown)[0]}")
        if remote_bridge._payload_size(body) > 32768:
            raise remote_bridge.RemoteBridgeError("VP3 Agent Commerce connector payload is too large.")
        capabilities = body.get("capabilities")
        if capabilities is not None and not isinstance(capabilities, list):
            raise remote_bridge.RemoteBridgeError("VP3 Agent Commerce connector capabilities must be a list.")
        pairing_token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        try:
            configured = vp3_commerce_agent_connector.configure(
                str(body.get("endpoint") or ""),
                str(body.get("token") or ""),
                str(body.get("contract") or ""),
                str(body.get("contract_sha256") or ""),
                list(capabilities or []),
                pairing_token_hash=pairing_token_hash,
            )
        except vp3_commerce_agent_connector.VP3CommerceAgentConnectorError as exc:
            raise remote_bridge.RemoteBridgeError(str(exc)) from exc
        return {"status": 200, "ok": True, "payload": configured}

    remote_bridge.dispatch_remote_request = dispatch_remote_request
    remote_bridge._vp3_commerce_agent_v061_installed = True
