from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from ..config import settings
from ..database import db
from .remote_identity import load_or_create_remote_identity, remote_identity_metadata
from .https_bridge_session import load_https_session, clear_https_session, clear_https_session_if_matches, https_session_matches, normalize_https_endpoint
from .pairing import authenticate, revoke_paired_app, touch_paired_app
from . import providers, shared_agent_context, work_continuity


class RemoteBridgeError(RuntimeError):
    pass


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_TOOL_KEY = re.compile(r"^[a-z0-9._-]{1,80}$")
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9_-]{8,160}$")
_EVENT_TYPE = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
_REMOTE_OPERATION_ALIASES = {
    "chat": "agent.chat",
    "usage.cloud": "usage.write",
}
_RELOAD_EVENT = threading.Event()
_STATE_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "running": False,
    "stage": "stopped",
    "connected": False,
    "claimed": False,
    "claim_code": None,
    "connection_id": None,
    "last_connected_at": None,
    "last_message_at": None,
    "last_error": None,
    "reconnect_count": 0,
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_broker_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
        raise RemoteBridgeError("Remote bridge URL must use wss:// and include a host.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RemoteBridgeError("Remote bridge URL cannot contain credentials, a query string, or a fragment.")
    if parsed.scheme == "ws" and (parsed.hostname or "").lower() not in _LOOPBACK_HOSTS:
        raise RemoteBridgeError("Remote bridge requires wss:// except for a loopback development broker.")
    return raw


def _broker_metadata(broker_url: str) -> dict[str, Any]:
    parsed = urlparse(broker_url)
    host = (parsed.hostname or "").lower()
    return {"scheme": parsed.scheme, "loopback": host in _LOOPBACK_HOSTS}


def _broker_proxy(broker_url: str):
    """Force loopback fixtures direct; retain normal proxy discovery for remote WSS."""
    return None if _broker_metadata(broker_url)["loopback"] else True


def get_bridge_settings() -> dict:
    with db() as connection:
        row = connection.execute(
            "SELECT enabled, broker_url, transport, https_endpoint, updated_at FROM remote_bridge_settings WHERE id=1 LIMIT 1"
        ).fetchone()
    if row is None:
        return {"enabled": False, "broker_url": "", "transport": "custom_websocket", "https_endpoint": "", "updated_at": None}
    return {
        "enabled": bool(row["enabled"]),
        "broker_url": str(row["broker_url"] or ""),
        "transport": str(row["transport"] or "custom_websocket"),
        "https_endpoint": str(row["https_endpoint"] or ""),
        "updated_at": row["updated_at"],
    }


def save_bridge_settings(enabled: bool, broker_url: str) -> dict:
    normalized = normalize_broker_url(broker_url)
    if enabled and not normalized:
        raise RemoteBridgeError("Configure a remote bridge URL before enabling the bridge.")
    with db() as connection:
        connection.execute(
            """
            INSERT INTO remote_bridge_settings(id, enabled, broker_url, transport, https_endpoint)
            VALUES (1, ?, ?, 'custom_websocket', '')
            ON CONFLICT(id) DO UPDATE SET
                enabled=excluded.enabled,
                broker_url=excluded.broker_url,
                transport='custom_websocket',
                https_endpoint='',
                updated_at=CURRENT_TIMESTAMP
            """,
            (1 if enabled else 0, normalized),
        )
    clear_https_session()
    _RELOAD_EVENT.set()
    return get_bridge_settings()


def save_vp3_https_settings(endpoint: str, enabled: bool = True) -> dict:
    normalized = normalize_https_endpoint(endpoint)
    with db() as connection:
        connection.execute(
            """
            INSERT INTO remote_bridge_settings(id, enabled, broker_url, transport, https_endpoint)
            VALUES (1, ?, '', 'vp3_https', ?)
            ON CONFLICT(id) DO UPDATE SET
                enabled=excluded.enabled,
                transport='vp3_https',
                https_endpoint=excluded.https_endpoint,
                updated_at=CURRENT_TIMESTAMP
            """,
            (1 if enabled else 0, normalized),
        )
    _RELOAD_EVENT.set()
    return get_bridge_settings()


def disable_vp3_https_settings() -> dict:
    with db() as connection:
        connection.execute(
            "UPDATE remote_bridge_settings SET enabled=0, updated_at=CURRENT_TIMESTAMP WHERE id=1 AND transport='vp3_https'"
        )
    _RELOAD_EVENT.set()
    return get_bridge_settings()


def _set_state(**updates: Any) -> None:
    with _STATE_LOCK:
        _STATE.update(updates)


def _increment_reconnect_count() -> int:
    with _STATE_LOCK:
        value = int(_STATE.get("reconnect_count") or 0) + 1
        _STATE["reconnect_count"] = value
        return value


def bridge_status() -> dict:
    configured = get_bridge_settings()
    identity = remote_identity_metadata()
    with _STATE_LOCK:
        runtime = dict(_STATE)
    transport = str(configured.get("transport") or "custom_websocket")
    return {
        "settings": configured,
        "identity": identity,
        "runtime": runtime,
        "transport": transport,
        "trust_model": "vp3-https-outbound" if transport == "vp3_https" else "trusted-wss-relay",
        "end_to_end_payload_encryption": False,
    }


def cloud_connection_status() -> dict:
    """One authoritative owner-facing VP3 Cloud connection projection."""
    bridge = bridge_status()
    configured = bridge["settings"]
    runtime = bridge["runtime"]
    transport = str(bridge.get("transport") or "custom_websocket")
    session = load_https_session() if transport == "vp3_https" else None

    with db() as connection:
        app = connection.execute(
            "SELECT id,app_key,name,status,paired_at,last_seen_at FROM paired_apps WHERE app_key='vp3' LIMIT 1"
        ).fetchone()
    app_item = dict(app) if app is not None else None

    vp3_authorized = bool(app_item and app_item.get("status") == "active")
    paired = bool(
        transport == "vp3_https"
        and configured.get("enabled")
        and session
        and vp3_authorized
    )
    connected = bool(paired and runtime.get("connected"))
    if connected:
        state = "connected"
    elif paired and runtime.get("claimed"):
        state = "reconnecting"
    elif paired:
        state = "offline"
    else:
        state = "not_connected"

    inference = providers.inference_status()
    if inference.get("available"):
        compute_source = str(inference.get("compute_source") or "")
        compute_label = (
            "Local HomeServer"
            if compute_source == "homeserver_local"
            else "User provider"
            if compute_source == "user_provider"
            else "HomeServer provider"
        )
        provider_label = str(inference.get("selected_provider") or "Configured provider")
    elif connected:
        compute_source = "vp3_cloud"
        compute_label = "VP3 Cloud fallback available"
        provider_label = "VP3 Cloud"
    else:
        compute_source = ""
        compute_label = "No inference route ready"
        provider_label = "No local provider configured"

    last_cloud_contact = runtime.get("last_message_at") or (
        app_item.get("last_seen_at") if app_item else None
    )
    return {
        "service": {
            "online": True,
            "version": settings.version,
        },
        "cloud": {
            "state": state,
            "connected": connected,
            "paired": paired,
            "transport": "vp3_https" if transport == "vp3_https" else "custom_websocket",
            "transport_label": "VP3 HTTPS Relay" if transport == "vp3_https" else "Custom WebSocket Relay",
            "last_seen_at": last_cloud_contact,
            "last_error": str(runtime.get("last_error") or ""),
        },
        "compute": {
            "available": bool(inference.get("available")),
            "source": compute_source,
            "source_label": compute_label,
            "provider": provider_label,
            "model": inference.get("model"),
            "cloud_fallback_available": bool(connected and not inference.get("available")),
        },
        "vp3_app": app_item,
        "advanced_relay": {
            "enabled": bool(configured.get("enabled")) if transport != "vp3_https" else False,
            "connected": bool(runtime.get("connected")) if transport != "vp3_https" else False,
            "broker_url": str(configured.get("broker_url") or "") if transport != "vp3_https" else "",
        },
    }


def _event(
    event: str,
    status: str,
    *,
    operation: str | None = None,
    request_id: str | None = None,
    metadata: dict | None = None,
) -> None:
    safe_metadata = metadata or {}
    try:
        with db() as connection:
            connection.execute(
                """
                INSERT INTO remote_bridge_events(event, status, operation, request_id, metadata_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event[:120],
                    status[:40],
                    operation[:80] if operation else None,
                    request_id[:128] if request_id else None,
                    json.dumps(safe_metadata, separators=(",", ":")),
                ),
            )
    except Exception:
        pass


def list_bridge_events(limit: int = 100) -> list[dict]:
    bounded = max(1, min(int(limit), 500))
    with db() as connection:
        rows = connection.execute(
            """
            SELECT id, event, status, operation, request_id, metadata_json, created_at
            FROM remote_bridge_events
            ORDER BY id DESC LIMIT ?
            """,
            (bounded,),
        ).fetchall()
    items: list[dict] = []
    for row in rows:
        item = dict(row)
        try:
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            item["metadata"] = {}
            item.pop("metadata_json", None)
        items.append(item)
    return items


def _payload_size(payload: Any) -> int:
    try:
        return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise RemoteBridgeError("Remote request payload is not valid JSON.") from exc


def _require_token(operation: str, token: str | None) -> str:
    if operation in {"capabilities", "pair.request", "pair.status"}:
        return ""
    candidate = str(token or "").strip()
    if len(candidate) < 20 or len(candidate) > 512:
        raise RemoteBridgeError("A paired-app bearer token is required for this remote operation.")
    return candidate


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int, name: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise RemoteBridgeError(f"{name} must be an integer.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise RemoteBridgeError(f"{name} must be an integer.") from exc
    if parsed < minimum or parsed > maximum:
        raise RemoteBridgeError(f"{name} must be between {minimum} and {maximum}.")
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


def _direct_identity(token: str, required_permissions: set[str] | None = None) -> dict:
    identity = authenticate(token)
    if identity is None:
        raise RemoteBridgeError("HomeServer paired-app authorization is invalid or revoked.")
    granted = set(identity.get("permissions") or [])
    required = set(required_permissions or set())
    missing = sorted(required - granted)
    if missing:
        raise RemoteBridgeError("HomeServer shared context permission is unavailable: " + ", ".join(missing))
    return identity


def dispatch_remote_request(operation: str, payload: dict | None, bearer_token: str | None = None) -> dict:
    requested_op = str(operation or "").strip()
    op = _REMOTE_OPERATION_ALIASES.get(requested_op, requested_op)
    body = payload if isinstance(payload, dict) else {}
    if _payload_size(body) > settings.max_remote_bridge_message_bytes:
        raise RemoteBridgeError("Remote request payload is too large.")
    token = _require_token(op, bearer_token)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    base_url = f"http://{settings.host}:{settings.port}"

    # The worker talks only to HomeServer's loopback API. Every protected route
    # still authenticates the paired-app bearer token and enforces permissions.
    with httpx.Client(base_url=base_url, timeout=125.0, trust_env=False) as client:
        if op == "capabilities":
            return _local_response(client.get("/api/v1/capabilities"))
        if op == "system.ping":
            identity = _direct_identity(token)
            remote = load_or_create_remote_identity()
            return {
                "status": 200,
                "ok": True,
                "payload": {
                    "pong": True,
                    "version": settings.version,
                    "device_id": remote["device_id"],
                    "received_at": _iso_now(),
                    "echo": body.get("nonce") or body.get("echo") or "",
                    "app": identity["app_key"],
                },
            }
        if op == "shared.context.exchange":
            _direct_identity(
                token,
                {"memory.read", "knowledge.search", "contacts.read", "tasks.read", "notifications.read"},
            )
            cloud_snapshot = body.get("cloud_snapshot")
            if not isinstance(cloud_snapshot, dict):
                raise RemoteBridgeError("shared.context.exchange requires cloud_snapshot.")
            query = str(body.get("query") or "")[:240]
            try:
                payload_out = shared_agent_context.exchange(cloud_snapshot, query)
            except shared_agent_context.SharedAgentContextError as exc:
                raise RemoteBridgeError(str(exc)) from exc
            return {"status": 200, "ok": True, "payload": payload_out}
        if op == "capability.registry":
            return _local_response(client.get("/api/v1/capability-registry", headers=headers))
        if op == "vp3.os.status":
            return _local_response(client.get("/api/v1/vp3-os/status", headers=headers))
        if op == "vp3.os.placement":
            return _local_response(client.post("/api/v1/vp3-os/placement", json=body, headers=headers))
        if op == "fleet.device.status":
            return _local_response(client.get("/api/v1/fleet/device/status", headers=headers))
        if op == "fleet.device.diagnostics":
            return _local_response(client.post("/api/v1/fleet/device/diagnostics", headers=headers))
        if op == "fleet.device.support_summary":
            return _local_response(client.post("/api/v1/fleet/device/support-summary", headers=headers))
        if op == "fleet.device.update_request":
            return _local_response(client.post("/api/v1/fleet/device/update-requests", json=body, headers=headers))
        if op == "fleet.checkin":
            return _local_response(client.post("/api/v1/fleet/check-ins", json=body, headers=headers))
        if op == "fleet.inventory":
            limit = _bounded_int(body.get("limit"), default=100, minimum=1, maximum=500, name="limit")
            return _local_response(client.get("/api/v1/fleet/inventory", params={"limit": limit}, headers=headers))
        if op == "fleet.rollouts":
            limit = _bounded_int(body.get("limit"), default=50, minimum=1, maximum=100, name="limit")
            return _local_response(client.get("/api/v1/fleet/rollouts", params={"limit": limit}, headers=headers))
        if op == "fleet.rollout.outcome":
            rollout_id = _bounded_int(body.get("rollout_id"), default=0, minimum=1, maximum=2147483647, name="rollout_id")
            outcome_body = {
                "device_id": body.get("device_id"),
                "outcome": body.get("outcome"),
                "detail_code": body.get("detail_code") or "",
            }
            return _local_response(
                client.post(
                    f"/api/v1/fleet/rollouts/{rollout_id}/outcomes",
                    json=outcome_body,
                    headers=headers,
                )
            )
        if op == "pair.request":
            return _local_response(client.post("/api/v1/pairing/request", json=body))
        if op == "pair.status":
            return _local_response(client.post("/api/v1/pairing/status", json=body))
        if op == "agent.chat":
            continuity = body.get("_continuity")
            if isinstance(continuity, dict):
                identity = _direct_identity(token, {"chat"})
                chat_body = dict(body)
                chat_body.pop("_continuity", None)
                try:
                    payload_out = work_continuity.execute(
                        continuity,
                        "agent.chat",
                        lambda: _local_response(client.post("/api/v1/chat", json=chat_body, headers=headers))["payload"],
                        source_app_key=str(identity.get("app_key") or "vp3"),
                    )
                except work_continuity.WorkContinuityError as exc:
                    raise RemoteBridgeError(str(exc)) from exc
                return {"status": 200, "ok": True, "payload": payload_out}
            return _local_response(client.post("/api/v1/chat", json=body, headers=headers))
        if op == "work.continuity.status":
            _direct_identity(token, {"chat"})
            try:
                payload_out = work_continuity.status(str(body.get("key") or ""))
            except work_continuity.WorkContinuityError as exc:
                raise RemoteBridgeError(str(exc)) from exc
            return {"status": 200, "ok": True, "payload": payload_out}
        if op == "work.continuity.cancel":
            _direct_identity(token, {"chat"})
            try:
                payload_out = work_continuity.cancel(str(body.get("key") or ""))
            except work_continuity.WorkContinuityError as exc:
                raise RemoteBridgeError(str(exc)) from exc
            return {"status": 200, "ok": True, "payload": payload_out}
        if op == "conversations.list":
            return _local_response(client.get("/api/v1/conversations", headers=headers))
        if op == "conversation.get":
            conversation_id = str(body.get("conversation_id") or "").strip()
            if not _OPAQUE_ID.fullmatch(conversation_id):
                raise RemoteBridgeError("conversation_id must be a valid opaque conversation identifier.")
            return _local_response(client.get(f"/api/v1/conversations/{conversation_id}", headers=headers))
        if op == "contacts.search":
            query = str(body.get("query") or "")[:240]
            return _local_response(client.get("/api/v1/contacts", params={"q": query}, headers=headers))
        if op == "knowledge.search":
            query = str(body.get("query") or "")[:240]
            return _local_response(client.get("/api/v1/knowledge", params={"q": query}, headers=headers))
        if op == "memory.read":
            return _local_response(client.get("/api/v1/memory", headers=headers))
        if op == "memory.write":
            return _local_response(client.post("/api/v1/memory", json=body, headers=headers))
        if op == "inference.status":
            return _local_response(client.get("/api/v1/inference/status", headers=headers))
        if op == "events.emit":
            return _local_response(client.post("/api/v1/events", json=body, headers=headers))
        if op == "events.list":
            params: dict[str, Any] = {
                "limit": _bounded_int(body.get("limit"), default=100, minimum=1, maximum=500, name="limit")
            }
            event_type = str(body.get("event_type") or "").strip()
            if event_type:
                if not _EVENT_TYPE.fullmatch(event_type):
                    raise RemoteBridgeError("event_type is invalid.")
                params["event_type"] = event_type
            return _local_response(client.get("/api/v1/events", params=params, headers=headers))
        if op == "awareness.list":
            limit = _bounded_int(body.get("limit"), default=50, minimum=1, maximum=200, name="limit")
            return _local_response(client.get("/api/v1/awareness", params={"limit": limit}, headers=headers))
        if op == "plugins.list":
            return _local_response(client.get("/api/v1/plugins", headers=headers))
        if op == "usage.write":
            return _local_response(client.post("/api/v1/usage/cloud", json=body, headers=headers))
        if op == "usage.read":
            limit = _bounded_int(body.get("limit"), default=200, minimum=1, maximum=500, name="limit")
            return _local_response(client.get("/api/v1/usage", params={"limit": limit}, headers=headers))
        if op == "tools.list":
            return _local_response(client.get("/api/v1/tools", headers=headers))
        if op == "skills.list":
            return _local_response(client.get("/api/v1/skills", headers=headers))
        if op == "tool.execute":
            tool_key = str(body.get("tool_key") or "")
            arguments = body.get("arguments")
            if not _TOOL_KEY.fullmatch(tool_key) or not isinstance(arguments, dict):
                raise RemoteBridgeError("tool.execute requires a valid tool_key and arguments object.")
            return _local_response(
                client.post(
                    f"/api/v1/tools/{tool_key}/execute",
                    json={"arguments": arguments},
                    headers=headers,
                )
            )
        if op == "action.status":
            action_id = str(body.get("request_id") or "")
            if not _OPAQUE_ID.fullmatch(action_id):
                raise RemoteBridgeError("action.status requires a valid request_id.")
            return _local_response(client.get(f"/api/v1/action-requests/{action_id}", headers=headers))
        if op == "action.list":
            params: dict[str, Any] = {
                "limit": _bounded_int(body.get("limit"), default=100, minimum=1, maximum=200, name="limit")
            }
            status = str(body.get("status") or "pending").strip()
            if status:
                if status not in {"pending", "executing", "executed", "denied", "failed", "expired"}:
                    raise RemoteBridgeError("action.list status is invalid.")
                params["status"] = status
            return _local_response(client.get("/api/v1/action-requests", params=params, headers=headers))
        if op in {"action.approve", "action.deny"}:
            action_id = str(body.get("request_id") or "")
            if not _OPAQUE_ID.fullmatch(action_id):
                raise RemoteBridgeError(f"{op} requires a valid request_id.")
            decision = "approve" if op == "action.approve" else "deny"
            return _local_response(client.post(f"/api/v1/action-requests/{action_id}/{decision}", headers=headers))

    raise RemoteBridgeError("Remote operation is not allowlisted by HomeServer.")


def _safe_send(websocket, payload: dict) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > settings.max_remote_bridge_message_bytes:
        raise RemoteBridgeError("Remote bridge response is too large.")
    websocket.send(encoded)


def _parse_message(raw: str | bytes) -> dict:
    if isinstance(raw, bytes):
        if len(raw) > settings.max_remote_bridge_message_bytes:
            raise RemoteBridgeError("Remote bridge message is too large.")
        raw = raw.decode("utf-8")
    elif len(raw.encode("utf-8")) > settings.max_remote_bridge_message_bytes:
        raise RemoteBridgeError("Remote bridge message is too large.")
    try:
        payload = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise RemoteBridgeError("Remote bridge message is invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise RemoteBridgeError("Remote bridge message must be an object.")
    return payload


class RemoteBridgeWorker:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_guarded,
            name="homeserver-remote-bridge",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        _RELOAD_EVENT.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=8)
        _set_state(running=False, stage="stopped", connected=False)

    def _run_guarded(self) -> None:
        try:
            self._run()
        except Exception as exc:
            _set_state(
                running=False,
                stage="crashed",
                connected=False,
                claimed=False,
                claim_code=None,
                connection_id=None,
                last_error=f"{type(exc).__name__}: remote bridge worker crashed",
            )
            _event("bridge.worker", "failed", metadata={"stage": "crashed", "error_type": type(exc).__name__})

    def _wait_local_api(self) -> bool:
        url = f"http://{settings.host}:{settings.port}/api/v1/health"
        _set_state(stage="waiting-local-api")
        _event("bridge.worker", "progress", metadata={"stage": "waiting-local-api"})
        last_error_type: str | None = None
        while not self._stop.is_set():
            try:
                with httpx.Client(timeout=0.6, trust_env=False) as client:
                    response = client.get(url)
                if response.status_code == 200:
                    _set_state(stage="local-api-ready", last_error=None)
                    _event("bridge.worker", "progress", metadata={"stage": "local-api-ready"})
                    return True
                last_error_type = f"http-{response.status_code}"
            except Exception as exc:
                last_error_type = type(exc).__name__
            _set_state(last_error=f"{last_error_type}: local HomeServer API unavailable")
            self._stop.wait(0.3)
        return False

    def _run_https(self, configured: dict) -> None:
        session = load_https_session()
        if not session:
            raise RemoteBridgeError("VP3 HTTPS session is unavailable. Pair HomeServer with VP3 again.")
        identity = load_or_create_remote_identity()
        endpoint = str(session.get("endpoint") or configured.get("https_endpoint") or "").strip()
        token = str(session.get("session_token") or "").strip()
        if not endpoint or len(token) < 32:
            raise RemoteBridgeError("VP3 HTTPS session is incomplete. Pair HomeServer with VP3 again.")

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "X-VP3-HomeServer-Session": token,
            "X-HomeServer-Device": identity["device_id"],
        }
        pending_results: list[dict] = []
        announced = False
        _set_state(stage="connecting", connected=False, claimed=True, claim_code=None, last_error=None)
        _event("bridge.connection", "attempting", metadata={"stage": "connecting", "transport": "vp3_https"})

        with httpx.Client(timeout=20.0, follow_redirects=False) as client:
            while not self._stop.is_set() and not _RELOAD_EVENT.is_set():
                capability_result = dispatch_remote_request("capabilities", {}, None)
                capabilities = capability_result.get("payload") if capability_result.get("ok") else {}
                if not isinstance(capabilities, dict):
                    capabilities = {}

                response = client.post(
                    endpoint,
                    headers=headers,
                    json={
                        "version": settings.version,
                        "capabilities": capabilities,
                        "results": pending_results,
                    },
                )
                if response.status_code == 410:
                    # Only an explicit Gone response means this exact session was
                    # intentionally revoked. Never let a stale worker destroy a
                    # replacement session that was saved after this loop started.
                    if clear_https_session_if_matches(token):
                        disable_vp3_https_settings()
                        revoke_paired_app("vp3")
                        _set_state(
                            stage="revoked",
                            connected=False,
                            claimed=False,
                            last_error="VP3 pairing was explicitly revoked. Save a new pairing key to connect again.",
                        )
                        _event("bridge.disconnected", "revoked", metadata={"transport": "vp3_https"})
                    return
                if response.status_code in {401, 403}:
                    # Authentication failures are retryable and must never erase
                    # the saved pairing. If a newer pairing replaced this token,
                    # stop this stale worker immediately so the reload can take over.
                    if not https_session_matches(token):
                        _event("bridge.worker", "superseded", metadata={"transport": "vp3_https"})
                        return
                    raise RemoteBridgeError(f"VP3 HTTPS authentication returned HTTP {response.status_code}.")
                if response.status_code < 200 or response.status_code >= 300:
                    raise RemoteBridgeError(f"VP3 HTTPS relay returned HTTP {response.status_code}.")
                try:
                    data = response.json()
                except ValueError as exc:
                    raise RemoteBridgeError("VP3 HTTPS relay returned invalid JSON.") from exc
                if not isinstance(data, dict) or not data.get("ok"):
                    raise RemoteBridgeError("VP3 HTTPS relay rejected the exchange.")

                touch_paired_app("vp3")
                now = _iso_now()
                _set_state(
                    stage="ready",
                    connected=True,
                    claimed=True,
                    claim_code=None,
                    connection_id=identity["device_id"],
                    last_connected_at=now if not announced else _STATE.get("last_connected_at"),
                    last_message_at=now,
                    last_error=None,
                )
                if not announced:
                    announced = True
                    _event("bridge.connected", "completed", metadata={"device_id": identity["device_id"], "transport": "vp3_https"})

                pending_results = []
                requests = data.get("requests") if isinstance(data.get("requests"), list) else []
                for message in requests:
                    if not isinstance(message, dict):
                        continue
                    request_id = str(message.get("request_id") or "")
                    operation = str(message.get("operation") or "")
                    if not _REQUEST_ID.fullmatch(request_id):
                        _event("bridge.request", "rejected", operation=operation, metadata={"reason": "invalid-request-id"})
                        continue
                    started = time.monotonic()
                    try:
                        result = dispatch_remote_request(
                            operation,
                            message.get("payload") if isinstance(message.get("payload"), dict) else {},
                            str(message.get("bearer_token") or "") or None,
                        )
                    except RemoteBridgeError as exc:
                        result = {"status": 400, "ok": False, "payload": {"detail": str(exc)}}
                    duration_ms = int((time.monotonic() - started) * 1000)
                    _event(
                        "bridge.request",
                        "completed" if result.get("ok") else "denied",
                        operation=operation,
                        request_id=request_id,
                        metadata={"http_status": int(result.get("status") or 500), "duration_ms": duration_ms, "transport": "vp3_https"},
                    )
                    pending_results.append({"request_id": request_id, **result})

                poll_after = data.get("poll_after_ms", 900)
                try:
                    wait_seconds = max(0.25, min(float(poll_after) / 1000.0, 5.0))
                except (TypeError, ValueError):
                    wait_seconds = 0.9
                self._stop.wait(wait_seconds)

    def _run(self) -> None:
        _set_state(running=True, stage="starting", last_error=None)
        _event("bridge.worker", "started", metadata={"stage": "starting"})
        backoff = 1.0

        while not self._stop.is_set():
            try:
                configured = get_bridge_settings()
            except Exception as exc:
                _set_state(stage="settings-error", last_error=f"settings:{type(exc).__name__}")
                _event("bridge.worker", "failed", metadata={"stage": "settings-error", "error_type": type(exc).__name__})
                self._stop.wait(2.0)
                continue

            transport = str(configured.get("transport") or "custom_websocket")
            missing_transport_endpoint = (
                transport == "vp3_https" and not configured.get("https_endpoint")
            ) or (
                transport != "vp3_https" and not configured.get("broker_url")
            )
            if not configured["enabled"] or missing_transport_endpoint:
                _set_state(
                    stage="disabled",
                    connected=False,
                    claimed=False,
                    claim_code=None,
                    connection_id=None,
                    last_error=None,
                )
                _RELOAD_EVENT.clear()
                self._stop.wait(1.0)
                continue

            if transport == "vp3_https":
                if not self._wait_local_api():
                    break
                _RELOAD_EVENT.clear()
                try:
                    self._run_https(configured)
                    backoff = 1.0
                except (httpx.HTTPError, OSError, TimeoutError, RemoteBridgeError) as exc:
                    reconnect_count = _increment_reconnect_count()
                    _set_state(
                        stage="retrying",
                        connected=False,
                        claimed=True,
                        claim_code=None,
                        connection_id=None,
                        last_error=f"{type(exc).__name__}: VP3 HTTPS relay unavailable",
                        reconnect_count=reconnect_count,
                    )
                    _event("bridge.disconnected", "failed", metadata={"error_type": type(exc).__name__, "stage": "retrying", "transport": "vp3_https"})
                if _RELOAD_EVENT.is_set():
                    backoff = 1.0
                    continue
                self._stop.wait(backoff)
                backoff = min(backoff * 2.0, 30.0)
                continue

            metadata = _broker_metadata(configured["broker_url"])
            _set_state(stage="configured", last_error=None)
            _event("bridge.worker", "progress", metadata={"stage": "configured", **metadata})

            if not self._wait_local_api():
                break

            _RELOAD_EVENT.clear()
            try:
                _set_state(stage="identity")
                identity = load_or_create_remote_identity()
                _set_state(stage="identity-ready", last_error=None)
                _event("bridge.worker", "progress", metadata={"stage": "identity-ready"})

                _set_state(stage="connecting")
                _event("bridge.connection", "attempting", metadata={"stage": "connecting", **metadata})
                with connect(
                    configured["broker_url"],
                    subprotocols=["homeserver.bridge.v1"],
                    additional_headers={
                        "Authorization": f"Bearer {identity['device_secret']}",
                        "X-HomeServer-Device": identity["device_id"],
                    },
                    compression=None,
                    proxy=_broker_proxy(configured["broker_url"]),
                    open_timeout=8,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    max_size=settings.max_remote_bridge_message_bytes,
                ) as websocket:
                    _set_state(
                        stage="connected",
                        connected=True,
                        last_connected_at=_iso_now(),
                        last_error=None,
                    )
                    _event("bridge.connected", "completed", metadata={"device_id": identity["device_id"]})
                    _safe_send(
                        websocket,
                        {
                            "type": "hello",
                            "protocol": "homeserver-relay-v1",
                            "device_id": identity["device_id"],
                            "version": settings.version,
                        },
                    )
                    backoff = 1.0

                    while not self._stop.is_set() and not _RELOAD_EVENT.is_set():
                        try:
                            raw = websocket.recv(timeout=1.0)
                        except TimeoutError:
                            continue
                        if raw is None:
                            break

                        message = _parse_message(raw)
                        _set_state(last_message_at=_iso_now())
                        message_type = str(message.get("type") or "")

                        if message_type == "hello.ok":
                            claim_code = str(message.get("claim_code") or "").strip() or None
                            if claim_code and len(claim_code) > 40:
                                claim_code = None
                            _set_state(
                                stage="ready",
                                claimed=bool(message.get("claimed")),
                                claim_code=claim_code,
                                connection_id=str(message.get("connection_id") or "")[:128] or None,
                            )
                            continue

                        if message_type != "request":
                            _event("bridge.protocol", "rejected", metadata={"message_type": message_type[:60]})
                            continue

                        request_id = str(message.get("request_id") or "")
                        operation = str(message.get("operation") or "")
                        if not _REQUEST_ID.fullmatch(request_id):
                            _event(
                                "bridge.request",
                                "rejected",
                                operation=operation,
                                metadata={"reason": "invalid-request-id"},
                            )
                            continue

                        started = time.monotonic()
                        try:
                            result = dispatch_remote_request(
                                operation,
                                message.get("payload") if isinstance(message.get("payload"), dict) else {},
                                str(message.get("bearer_token") or "") or None,
                            )
                            duration_ms = int((time.monotonic() - started) * 1000)
                            _event(
                                "bridge.request",
                                "completed" if result["ok"] else "denied",
                                operation=operation,
                                request_id=request_id,
                                metadata={"http_status": result["status"], "duration_ms": duration_ms},
                            )
                            _safe_send(
                                websocket,
                                {"type": "response", "request_id": request_id, **result},
                            )
                        except RemoteBridgeError as exc:
                            duration_ms = int((time.monotonic() - started) * 1000)
                            _event(
                                "bridge.request",
                                "rejected",
                                operation=operation,
                                request_id=request_id,
                                metadata={"reason": str(exc)[:120], "duration_ms": duration_ms},
                            )
                            _safe_send(
                                websocket,
                                {
                                    "type": "response",
                                    "request_id": request_id,
                                    "ok": False,
                                    "status": 400,
                                    "payload": {"detail": str(exc)},
                                },
                            )
            except (ConnectionClosed, OSError, TimeoutError, RemoteBridgeError) as exc:
                reconnect_count = _increment_reconnect_count()
                _set_state(
                    stage="retrying",
                    connected=False,
                    claimed=False,
                    claim_code=None,
                    connection_id=None,
                    last_error=f"{type(exc).__name__}: remote bridge unavailable",
                    reconnect_count=reconnect_count,
                )
                _event(
                    "bridge.disconnected",
                    "failed",
                    metadata={"error_type": type(exc).__name__, "stage": "retrying"},
                )
            except Exception as exc:
                reconnect_count = _increment_reconnect_count()
                _set_state(
                    stage="retrying",
                    connected=False,
                    claimed=False,
                    claim_code=None,
                    connection_id=None,
                    last_error=f"{type(exc).__name__}: remote bridge failed",
                    reconnect_count=reconnect_count,
                )
                _event(
                    "bridge.disconnected",
                    "failed",
                    metadata={"error_type": type(exc).__name__, "stage": "retrying"},
                )

            if _RELOAD_EVENT.is_set():
                backoff = 1.0
                continue
            self._stop.wait(backoff)
            backoff = min(backoff * 2.0, 30.0)

        _set_state(running=False, stage="stopped", connected=False)
        _event("bridge.worker", "stopped", metadata={"stage": "stopped"})
