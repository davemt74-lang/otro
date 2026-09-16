from __future__ import annotations

from typing import Callable

from . import capability_registry, meeting_intelligence, meeting_transcription_remote, remote_bridge

_INSTALLED = False


def install() -> None:
    """Install private meeting intelligence on the authenticated VP3 relay."""
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
        if op != meeting_intelligence.OPERATION:
            return original_dispatch(operation, payload, bearer_token)

        body = payload if isinstance(payload, dict) else {}
        if remote_bridge._payload_size(body) > remote_bridge.settings.max_remote_bridge_message_bytes:
            raise remote_bridge.RemoteBridgeError("Remote request payload is too large.")
        identity = meeting_transcription_remote._identity(bearer_token)
        try:
            result = meeting_intelligence.analyze(body, identity)
        except meeting_intelligence.MeetingIntelligenceError as exc:
            return {
                "status": int(exc.status_code),
                "ok": False,
                "payload": {"detail": str(exc), "operation": meeting_intelligence.OPERATION},
            }
        return {"status": 200, "ok": True, "payload": result}

    def extended_operations(permissions: set[str], contacts_available: bool) -> list[str]:
        operations = list(original_operations(permissions, contacts_available))
        if (
            "agent.chat" in permissions
            and meeting_transcription_remote._claimed_cloud_ready()
            and meeting_intelligence.status().get("available")
        ):
            operations.append(meeting_intelligence.OPERATION)
        return sorted(set(operations))

    remote_bridge.dispatch_remote_request = extended_dispatch
    capability_registry._operations = extended_operations
    remote_bridge._meeting_intelligence_v1890_installed = True
    _INSTALLED = True
