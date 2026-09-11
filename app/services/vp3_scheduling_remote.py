from __future__ import annotations

from typing import Callable

from . import pairing, remote_bridge, vp3_scheduling_connector

OPERATION = "vp3.connector.configure"
_ALLOWED_FIELDS = {"endpoint", "token", "version", "capabilities"}


def install() -> None:
    if getattr(remote_bridge, "_vp3_scheduling_v059_installed", False):
        return

    original: Callable[[str, dict | None, str | None], dict] = remote_bridge.dispatch_remote_request

    def dispatch_remote_request(
        operation: str,
        payload: dict | None,
        bearer_token: str | None = None,
    ) -> dict:
        if str(operation or "").strip() != OPERATION:
            return original(operation, payload, bearer_token)

        token = str(bearer_token or "").strip()
        identity = pairing.authenticate(token) if token else None
        if identity is None:
            raise remote_bridge.RemoteBridgeError("A paired VP3 bearer token is required to configure scheduling.")
        if str(identity.get("app_key") or "").strip() != "vp3":
            raise remote_bridge.RemoteBridgeError("Only the paired VP3 application may configure scheduling.")
        if "tools.execute" not in set(identity.get("permissions") or []):
            raise remote_bridge.RemoteBridgeError("The paired VP3 application is not allowed to configure scheduling.")

        body = payload if isinstance(payload, dict) else {}
        unknown = set(body) - _ALLOWED_FIELDS
        if unknown:
            raise remote_bridge.RemoteBridgeError(
                f"VP3 scheduling connector payload contains unsupported field: {sorted(unknown)[0]}"
            )
        if remote_bridge._payload_size(body) > 32768:
            raise remote_bridge.RemoteBridgeError("VP3 scheduling connector payload is too large.")

        endpoint = str(body.get("endpoint") or "")
        connector_token = str(body.get("token") or "")
        version = str(body.get("version") or vp3_scheduling_connector.CLOUD_CONTRACT_VERSION)
        capabilities = body.get("capabilities")
        if capabilities is not None and not isinstance(capabilities, list):
            raise remote_bridge.RemoteBridgeError("VP3 scheduling connector capabilities must be a list.")

        try:
            configured = vp3_scheduling_connector.configure(
                endpoint,
                connector_token,
                version,
                list(capabilities or []),
            )
        except vp3_scheduling_connector.VP3SchedulingConnectorError as exc:
            raise remote_bridge.RemoteBridgeError(str(exc)) from exc

        return {"status": 200, "ok": True, "payload": configured}

    remote_bridge.dispatch_remote_request = dispatch_remote_request
    remote_bridge._vp3_scheduling_v059_installed = True
