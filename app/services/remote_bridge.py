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


class RemoteBridgeError(RuntimeError):
    pass


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_TOOL_KEY = re.compile(r"^[a-z0-9._-]{1,80}$")
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9_-]{8,160}$")
_EVENT_TYPE = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
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
            "SELECT enabled, broker_url, updated_at FROM remote_bridge_settings WHERE id=1 LIMIT 1"
        ).fetchone()
    if row is None:
        return {"enabled": False, "broker_url": "", "updated_at": None}
    return {
        "enabled": bool(row["enabled"]),
        "broker_url": str(row["broker_url"] or ""),
        "updated_at": row["updated_at"],
    }


def save_bridge_settings(enabled: bool, broker_url: str) -> dict:
    normalized = normalize_broker_url(broker_url)
    if enabled and not normalized:
        raise RemoteBridgeError("Configure a remote bridge URL before enabling the bridge.")
    with db() as connection:
        connection.execute(
            """
            INSERT INTO remote_bridge_settings(id, enabled, broker_url)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                enabled=excluded.enabled,
                broker_url=excluded.broker_url,
                updated_at=CURRENT_TIMESTAMP
            """,
            (1 if enabled else 0, normalized),
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
    return {
        "settings": configured,
        "identity": identity,
        "runtime": runtime,
        "trust_model": "trusted-wss-relay",
        "end_to_end_payload_encryption": False,
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


def dispatch_remote_request(operation: str, payload: dict | None, bearer_token: str | None = None) -> dict:
    op = str(operation or "").strip()
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
        if op == "pair.request":
            return _local_response(client.post("/api/v1/pairing/request", json=body))
        if op == "pair.status":
            return _local_response(client.post("/api/v1/pairing/status", json=body))
        if op == "chat":
            return _local_response(client.post("/api/v1/chat", json=body, headers=headers))
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
        if op == "usage.cloud":
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

            if not configured["enabled"] or not configured["broker_url"]:
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
