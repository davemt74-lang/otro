from __future__ import annotations

from typing import Any, Callable

from . import capability_registry, meeting_transcription, remote_bridge
from .pairing import authenticate

_INSTALLED = False


def _identity(bearer_token: str | None) -> dict[str, Any]:
    token = str(bearer_token or "").strip()
    if len(token) < 20 or len(token) > 512:
        raise remote_bridge.RemoteBridgeError(
            "A paired-app bearer token is required for meeting transcription."
        )
    identity = authenticate(token)
    if identity is None:
        raise remote_bridge.RemoteBridgeError("Invalid or revoked paired-app bearer token.")
    if "agent.chat" not in identity.get("permissions", []):
        raise remote_bridge.RemoteBridgeError(
            "Paired app does not have permission to run local meeting transcription."
        )
    return identity


def install() -> None:
    """Install the v18.6 operation into the authenticated relay + registry surfaces."""
    global _INSTALLED
    if _INSTALLED:
        return

    original_dispatch: Callable[[str, dict | None, str | None], dict] = remote_bridge.dispatch_remote_request
    original_operations = capability_registry._operations

    def extended_dispatch(
        operation: str,
        payload: dict | None,
        bearer_token: str | None = None,
    ) -> dict:
        op = str(operation or "").strip()
        if op != meeting_transcription.OPERATION:
            return original_dispatch(operation, payload, bearer_token)

        body = payload if isinstance(payload, dict) else {}
        if remote_bridge._payload_size(body) > remote_bridge.settings.max_remote_bridge_message_bytes:
            raise remote_bridge.RemoteBridgeError("Remote request payload is too large.")
        identity = _identity(bearer_token)
        try:
            result = meeting_transcription.start(body, identity)
        except meeting_transcription.MeetingTranscriptionError as exc:
            return {
                "status": int(exc.status_code),
                "ok": False,
                "payload": {"detail": str(exc), "operation": meeting_transcription.OPERATION},
            }
        return {"status": 200, "ok": True, "payload": result}

    def extended_operations(permissions: set[str], contacts_available: bool) -> list[str]:
        operations = list(original_operations(permissions, contacts_available))
        if "agent.chat" in permissions and meeting_transcription.status().get("available"):
            operations.append(meeting_transcription.OPERATION)
        return sorted(set(operations))

    remote_bridge.dispatch_remote_request = extended_dispatch
    capability_registry._operations = extended_operations
    remote_bridge._meeting_transcription_v1860_installed = True
    _INSTALLED = True
