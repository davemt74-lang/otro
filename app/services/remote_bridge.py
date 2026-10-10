from __future__ import annotations

import base64
import binascii
import json
import re
import sqlite3
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
from .https_bridge_session import SESSION_LOCK, load_https_session, clear_https_session, clear_https_session_if_matches, https_session_matches, normalize_https_endpoint
from .pairing import authenticate, revoke_paired_app, touch_paired_app
from . import agent_mission_cloud_v1, agent_voice_profiles, local_transcription_sessions, federated_data, homeserver_app_agent, homeserver_app_control, homeserver_app_data_lifecycle, homeserver_app_distribution, homeserver_app_manager, homeserver_app_packages, homeserver_app_prebuilt, homeserver_app_releases, homeserver_app_security, homeserver_app_workspace, homeserver_apps, homeserver_media_server, homeserver_video_editor, hosting_cloud_control, hosting_cloud_deployment, hosting_diagnostics, hosting_entitlements, hosting_health_recovery, hosting_operations, hosting_public, hosting_runtime, local_voice, providers, shared_agent_context, tracky_physical_context


class RemoteBridgeError(RuntimeError):
    pass


def _database_error_details(exc: sqlite3.OperationalError) -> dict[str, Any]:
    # Report stable codes and owner guidance, never SQL, paths or credentials.
    code = getattr(exc, "sqlite_errorcode", None)
    primary = (code & 255) if isinstance(code, int) else None
    explanations = {
        sqlite3.SQLITE_BUSY: ("SQLITE_BUSY", "The HomeServer database is busy. Retrying automatically."),
        sqlite3.SQLITE_LOCKED: ("SQLITE_LOCKED", "The HomeServer database is locked. Retrying automatically."),
        sqlite3.SQLITE_FULL: ("SQLITE_FULL", "The database drive or database size limit is full. Free space or review the database limit."),
        sqlite3.SQLITE_READONLY: ("SQLITE_READONLY", "The HomeServer database is read-only. Check write access to the data folder."),
        sqlite3.SQLITE_CANTOPEN: ("SQLITE_CANTOPEN", "HomeServer cannot open its database. Check the data folder and drive access."),
        sqlite3.SQLITE_IOERR: ("SQLITE_IOERR", "The database reported a disk I/O error. Check the drive and data folder access."),
        sqlite3.SQLITE_ERROR: ("SQLITE_ERROR", "A database query failed. Check database schema and installed-version diagnostics."),
        sqlite3.SQLITE_SCHEMA: ("SQLITE_SCHEMA", "The database schema changed. Retrying automatically."),
    }
    name, message = explanations.get(primary, ("SQLITE_OPERATIONAL_ERROR", "The database operation failed. Check database diagnostics."))
    return {"sqlite_code": code, "sqlite_name": name, "message": f"{name}: {message}"}


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_TOOL_KEY = re.compile(r"^[a-z0-9._-]{1,80}$")
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9_-]{8,160}$")
_EVENT_TYPE = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
_REMOTE_OPERATION_ALIASES = {
    "chat": "agent.chat",
    "usage.cloud": "usage.write",
    "tools.execute": "tool.execute",
}

_REMOTE_VOICE_MAX_AUDIO_BYTES = 150 * 1024
_REMOTE_VOICE_MAX_TTS_CHARS = 220
_REMOTE_INFER_MAX_MESSAGES = 24
_REMOTE_INFER_MAX_CONTENT_CHARS = 28000


def _bounded_remote_messages(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value or len(value) > _REMOTE_INFER_MAX_MESSAGES:
        raise RemoteBridgeError("agent.infer.local requires a bounded messages array.")
    out: list[dict[str, str]] = []
    total = 0
    for item in value:
        if not isinstance(item, dict):
            raise RemoteBridgeError("agent.infer.local messages must be objects.")
        role = str(item.get("role") or "").strip().lower()
        if role not in {"system", "user", "assistant"}:
            raise RemoteBridgeError("agent.infer.local message role is invalid.")
        content = str(item.get("content") or "").strip()
        if not content:
            raise RemoteBridgeError("agent.infer.local message content is required.")
        if len(content) > 8000:
            raise RemoteBridgeError("agent.infer.local message is too large.")
        total += len(content)
        if total > _REMOTE_INFER_MAX_CONTENT_CHARS:
            raise RemoteBridgeError("agent.infer.local context is too large.")
        out.append({"role": role, "content": content})
    return out


def _decode_remote_wav(value: Any) -> bytes:
    encoded = str(value or "").strip()
    if not encoded or len(encoded) > ((_REMOTE_VOICE_MAX_AUDIO_BYTES * 4 // 3) + 16):
        raise RemoteBridgeError("speech.transcribe audio payload is too large.")
    try:
        audio = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RemoteBridgeError("speech.transcribe audio must be valid base64 WAV data.") from exc
    if not audio or len(audio) > _REMOTE_VOICE_MAX_AUDIO_BYTES:
        raise RemoteBridgeError("speech.transcribe audio payload is too large.")
    return audio


def _remote_voice_profile() -> dict[str, Any]:
    try:
        agent_id = agent_voice_profiles.primary_agent_id()
        profile = agent_voice_profiles.get_profile(agent_id)
    except agent_voice_profiles.AgentVoiceProfileError as exc:
        raise RemoteBridgeError(str(exc)) from exc
    effective = profile.get("effective") if isinstance(profile.get("effective"), dict) else {}
    return {
        "agent_id": int(agent_id),
        "voice": str(effective.get("voice") or ""),
        "voice_source": str(effective.get("voice_source") or ""),
        "speaking_rate": effective.get("speaking_rate"),
        "sentence_silence": effective.get("sentence_silence"),
        "ready": bool(effective.get("ready")),
        "fallback": bool(effective.get("fallback")),
        "warning": str(effective.get("warning") or ""),
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
    "feature_sync_error": None,
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
            "feature_sync_error": str(runtime.get("feature_sync_error") or ""),
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
        "reconciliation": federated_data.reconciliation_state("vp3_cloud"),
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


def _direct_identity(token: str, required_permissions: set[str] | None = None, *, optional_permissions: set[str] | None = None) -> dict:
    identity = authenticate(token)
    if identity is None:
        raise RemoteBridgeError("HomeServer paired-app authorization is invalid or revoked.")
    granted = set(identity.get("permissions") or [])
    required = set(required_permissions or set())
    missing = sorted(required - granted - set(optional_permissions or set()))
    if missing:
        raise RemoteBridgeError("HomeServer shared context permission is unavailable: " + ", ".join(missing))
    return identity


def _vp3_hosting_identity(token: str) -> dict:
    identity=_direct_identity(token,{"hosting.manage"})
    if str(identity.get("app_key") or "")!="vp3":
        raise RemoteBridgeError("Hosting control is restricted to the paired VP3 Cloud app.")
    return identity


def _vp3_system_apps_identity(token: str) -> dict:
    # System-app distribution is restricted to the canonical paired VP3 Cloud
    # identity. No generic paired app can install or inspect protected VP3 apps.
    # We intentionally do not introduce a new coarse permission here so existing
    # paired VP3 accounts do not require a permission-upgrade/re-pair cycle.
    identity=_direct_identity(token)
    if str(identity.get("app_key") or "")!="vp3":
        raise RemoteBridgeError("System Apps control is restricted to the paired VP3 Cloud app.")
    return identity


def _system_app_status(app_key: str) -> dict[str, Any]:
    key=str(app_key or "").strip().lower()
    catalog=homeserver_app_prebuilt.catalog()
    package=next((item for item in catalog.get("packages",[]) if str(item.get("key") or "")==key),None)
    if package is None:
        raise homeserver_apps.HomeServerAppError("VP3 system app not found.",404)
    installed_package_sha256=""
    active_release_id=""
    previous_release_id=""
    if bool(package.get("installed")):
        try:
            app=homeserver_apps.get(key)
            metadata=app.get("metadata") or {}
            installed_package_sha256=str(metadata.get("package_sha256") or "")
            active_release_id=str(metadata.get("active_release_id") or "")
            previous_release_id=str(metadata.get("previous_release_id") or "")
        except homeserver_apps.HomeServerAppError:
            pass
    package=dict(package)
    package["installed_package_sha256"]=installed_package_sha256 or None
    package["active_release_id"]=active_release_id or None
    package["previous_release_id"]=previous_release_id or None
    data_status=None
    if bool(package.get("installed")):
        try:
            from . import homeserver_app_data_lifecycle
            data_status=homeserver_app_data_lifecycle.status(key)
        except Exception:
            data_status=None
    return {
        "contract":"vp3.system-app-installation.v1",
        "catalog_version":catalog.get("catalog_version"),
        "package":package,
        "installed":bool(package.get("installed")),
        "current":bool(package.get("current")),
        "update_available":bool(package.get("update_available")),
        "state":str(package.get("state") or "available"),
        "data":data_status,
        "permissions":homeserver_app_security.permission_status(key) if bool(package.get("installed")) else None,
    }


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
        if op == "apps.system.catalog":
            _vp3_system_apps_identity(token)
            return {"status":200,"ok":True,"payload":homeserver_app_prebuilt.catalog()}
        if op == "apps.system.status":
            _vp3_system_apps_identity(token)
            try:
                payload_out=_system_app_status(str(body.get("app_key") or ""))
            except homeserver_apps.HomeServerAppError as exc:
                return {"status":int(exc.status_code),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op == "apps.system.install":
            _vp3_system_apps_identity(token)
            try:
                result=homeserver_app_prebuilt.install(
                    str(body.get("app_key") or ""),
                    expected_version=str(body.get("expected_version") or "") or None,
                    expected_sha256=str(body.get("expected_sha256") or "") or None,
                    release_channel=str(body.get("release_channel") or "") or None,
                )
                payload_out=_system_app_status(str(body.get("app_key") or ""))
                payload_out["changed"]=bool(result.get("changed"))
                payload_out["reason"]=str(result.get("reason") or "")
                payload_out["release"]=result.get("release")
                payload_out["verification"]=result.get("verification")
                payload_out["rolled_back"]=bool(result.get("rolled_back"))
                payload_out["rollback"]=result.get("rollback")
                payload_out["error"]=str(result.get("error") or "")
            except homeserver_apps.HomeServerAppError as exc:
                return {"status":int(exc.status_code),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op == "apps.media.status":
            _vp3_system_apps_identity(token)
            try:
                state=homeserver_media_server.status()
            except homeserver_media_server.MediaServerError as exc:
                return {"status":int(exc.status_code),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":{
                "contract":"vp3.media-server-projection.v1",
                "app_key":state.get("app_key"),
                "installed_version":state.get("installed_version"),
                "lifecycle_state":state.get("lifecycle_state"),
                "library_count":int(state.get("library_count") or 0),
                "roots":int(state.get("roots") or 0),
                "types":state.get("types") or {},
                "recently_played":list(state.get("recently_played") or [])[:12],
                "remote":state.get("remote") or {},
                "transcoding":False,
                "absolute_paths_exposed":False,
                "source_media_exposed":False,
            }}
        if op == "apps.media.search":
            _vp3_system_apps_identity(token)
            try:
                result=homeserver_media_server.library(
                    str(body.get("query") or ""),
                    str(body.get("media_type") or ""),
                    min(50,max(1,int(body.get("limit") or 20))),
                    0,
                )
            except (homeserver_media_server.MediaServerError,ValueError) as exc:
                return {"status":int(getattr(exc,"status_code",400)),"ok":False,"payload":{"detail":str(exc)}}
            items=[{
                "media_id":row.get("media_id"),"title":row.get("title"),"media_type":row.get("media_type"),
                "mime_type":row.get("mime_type"),"size_bytes":int(row.get("size_bytes") or 0),
                "absolute_path_exposed":False,
            } for row in result.get("items",[])[:50]]
            return {"status":200,"ok":True,"payload":{
                "contract":"vp3.media-server-search-projection.v1",
                "query":result.get("query") or "","media_type":result.get("media_type") or "",
                "items":items,"count":len(items),"source_media_exposed":False,
            }}
        if op == "apps.control.status":
            _vp3_system_apps_identity(token)
            key=str(body.get("app_key") or "").strip().lower()
            if not key:
                return {"status":400,"ok":False,"payload":{"detail":"app_key is required."}}
            try:
                payload_out={
                    "contract":"vp3.app-control-projection.v1",
                    "app_key":key,
                    "compatibility":homeserver_app_control.compatibility(key),
                    "manifest":homeserver_app_control.manifest(key),
                    "settings":homeserver_app_control.settings(key),
                    "hosting":homeserver_app_agent.hosting_status({"app_key":key}),
                    "homeserver_execution_authority":True,
                    "cloud_execution_authority":False,
                    "secret_values_exposed":False,
                }
            except (homeserver_apps.HomeServerAppError,homeserver_app_control.AppControlError,homeserver_app_agent.AppAgentError) as exc:
                return {"status":int(getattr(exc,"status_code",400)),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op == "apps.control.actions":
            _vp3_system_apps_identity(token)
            key=str(body.get("app_key") or "").strip().lower()
            try:
                payload_out=homeserver_app_control.manifest(key)
            except (homeserver_apps.HomeServerAppError,homeserver_app_control.AppControlError) as exc:
                return {"status":int(getattr(exc,"status_code",400)),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op == "apps.control.settings":
            _vp3_system_apps_identity(token)
            key=str(body.get("app_key") or "").strip().lower()
            try:
                payload_out=homeserver_app_control.settings(key)
            except (homeserver_apps.HomeServerAppError,homeserver_app_control.AppControlError) as exc:
                return {"status":int(getattr(exc,"status_code",400)),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op == "apps.control.hosting":
            _vp3_system_apps_identity(token)
            key=str(body.get("app_key") or "").strip().lower()
            try:
                payload_out=homeserver_app_agent.hosting_status({"app_key":key})
            except (homeserver_apps.HomeServerAppError,homeserver_app_agent.AppAgentError) as exc:
                return {"status":int(getattr(exc,"status_code",400)),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op == "apps.control.invoke":
            _vp3_system_apps_identity(token)
            key=str(body.get("app_key") or "").strip().lower()
            action=str(body.get("action") or "").strip()
            arguments=body.get("arguments") if isinstance(body.get("arguments"),dict) else {}
            confirmed=body.get("confirmed") is True
            try:
                spec=homeserver_app_control.action_spec(key,action)
                if (str(spec.get("risk") or "")!="read" or bool(spec.get("requires_confirmation"))) and not confirmed:
                    return {"status":409,"ok":False,"payload":{"detail":"Cloud-orchestrated app writes require owner confirmation.","confirmation_required":True}}
                result=homeserver_app_control.invoke(key,action,arguments,confirmed=confirmed,read_only=not confirmed)
            except (homeserver_apps.HomeServerAppError,homeserver_app_control.AppControlError) as exc:
                return {"status":int(getattr(exc,"status_code",400)),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":result}
        if op == "apps.control.settings.set":
            _vp3_system_apps_identity(token)
            key=str(body.get("app_key") or "").strip().lower()
            values=body.get("values") if isinstance(body.get("values"),dict) else None
            if body.get("confirmed") is not True:
                return {"status":409,"ok":False,"payload":{"detail":"App settings changes require owner confirmation.","confirmation_required":True}}
            try:
                result=homeserver_app_control.update_settings(key,values or {})
            except (homeserver_apps.HomeServerAppError,homeserver_app_control.AppControlError) as exc:
                return {"status":int(getattr(exc,"status_code",400)),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":result}
        if op == "apps.video.status":
            _vp3_system_apps_identity(token)
            try:
                state=homeserver_video_editor.status()
            except homeserver_video_editor.VideoEditorError as exc:
                return {"status":int(exc.status_code),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":state}
        if op == "apps.video.projects":
            _vp3_system_apps_identity(token)
            try:
                state=homeserver_video_editor.list_projects(min(100,max(1,int(body.get("limit") or 50))))
            except (homeserver_video_editor.VideoEditorError,ValueError) as exc:
                return {"status":int(getattr(exc,"status_code",400)),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":state}
        if op == "apps.video.project":
            _vp3_system_apps_identity(token)
            try:
                state=homeserver_video_editor.project(str(body.get("project_id") or ""))
            except homeserver_video_editor.VideoEditorError as exc:
                return {"status":int(exc.status_code),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":state}
        if op == "apps.video.agent.actions":
            _vp3_system_apps_identity(token)
            return {"status":200,"ok":True,"payload":homeserver_video_editor.agent_actions()}
        if op == "apps.video.agent.invoke":
            _vp3_system_apps_identity(token)
            action=str(body.get("action") or "")
            arguments=body.get("arguments") if isinstance(body.get("arguments"),dict) else {}
            confirmed=body.get("confirmed") is True
            spec={row["key"]:row for row in homeserver_video_editor.agent_actions()["actions"]}.get(action)
            if spec is None:
                return {"status":404,"ok":False,"payload":{"detail":"Video Editor action not found."}}
            if bool(spec.get("requires_confirmation")) and not confirmed:
                return {"status":409,"ok":False,"payload":{"detail":"This Video Editor action requires owner confirmation.","confirmation_required":True}}
            try:
                result=homeserver_video_editor.invoke(action,arguments)
            except homeserver_video_editor.VideoEditorError as exc:
                return {"status":int(exc.status_code),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":{"contract":"vp3.app-agent-invoke.v1","app_key":homeserver_video_editor.APP_KEY,"action":action,"result":result}}
        if op == "apps.manager.status":
            _vp3_system_apps_identity(token)
            state=homeserver_app_manager.inventory()
            items=[]
            for row in state.get("items",[])[:100]:
                items.append({
                    "app_key":row.get("app_key"),
                    "name":row.get("name"),
                    "app_class":row.get("app_class"),
                    "installed":bool(row.get("installed")),
                    "available":bool(row.get("available")),
                    "lifecycle_state":row.get("lifecycle_state"),
                    "installed_version":row.get("installed_version"),
                    "available_version":row.get("available_version"),
                    "update_available":bool(row.get("update_available")),
                    "category":row.get("category"),
                    "shared":bool(row.get("distribution")),
                    "hosted":bool((row.get("hosting") or {}).get("bound")),
                    "public":bool((row.get("hosting") or {}).get("public")),
                    "permission_counts":{
                        "declared":int(((row.get("permissions") or {}).get("declared_count") or 0)),
                        "allowed":int(((row.get("permissions") or {}).get("allowed_count") or 0)),
                    },
                    "storage_used_bytes":int(((row.get("resources") or {}).get("storage_used_bytes") or 0)),
                })
            return {"status":200,"ok":True,"payload":{
                "contract":"vp3.app-manager-projection.v1",
                "counts":state.get("counts") or {},
                "catalog_version":state.get("catalog_version"),
                "items":items,
                "count":len(items),
                "homeserver_authority":True,
                "source_content_exposed":False,
                "package_content_exposed":False,
            }}
        if op == "apps.user.distribution.describe":
            _vp3_system_apps_identity(token)
            key=str(body.get("app_key") or "").strip().lower()
            if not key:
                return {"status":400,"ok":False,"payload":{"detail":"app_key is required."}}
            try:
                descriptor=homeserver_app_distribution.distribution_descriptor(key)["descriptor"]
            except (homeserver_app_distribution.AppDistributionError,homeserver_apps.HomeServerAppError) as exc:
                return {"status":int(getattr(exc,"status_code",400)),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":{
                "contract":"vp3.user-app-distribution-projection.v1",
                "descriptor":descriptor,
                "package_content_exposed":False,
                "app_data_exposed":False,
                "secrets_exposed":False,
            }}
        if op == "apps.user.workspace.status":
            _vp3_system_apps_identity(token)
            key=str(body.get("app_key") or "").strip().lower()
            if not key:
                return {"status":400,"ok":False,"payload":{"detail":"app_key is required."}}
            try:
                state=homeserver_app_workspace.status(key)
            except homeserver_app_workspace.AppWorkspaceError as exc:
                return {"status":exc.status_code,"ok":False,"payload":{"detail":str(exc)}}
            project=dict(state.get("project") or {})
            files=[
                {
                    "path":str(row.get("path") or ""),
                    "type":str(row.get("type") or ""),
                    "bytes":int(row.get("bytes") or 0),
                    "editable":bool(row.get("editable")),
                    "protected":bool(row.get("protected")),
                }
                for row in list(project.get("files") or [])[:500]
                if isinstance(row,dict)
            ]
            return {"status":200,"ok":True,"payload":{
                "contract":"vp3.user-app-workspace-projection.v1",
                "app_key":key,
                "validation":state.get("validation"),
                "validation_error":str(state.get("validation_error") or ""),
                "project":{"files":files,"count":len(files)},
                "permissions":state.get("permissions"),
                "resources":state.get("resources"),
                "runtime":state.get("runtime"),
                "releases":state.get("releases"),
                "source":state.get("source"),
                "source_content_exposed":False,
            }}
        if op in {"apps.user.list","apps.user.status"}:
            _vp3_system_apps_identity(token)
            requested=str(body.get("app_key") or "").strip().lower()
            items=[]
            for app_row in homeserver_apps.list_apps().get("apps",[]):
                if not isinstance(app_row,dict) or app_row.get("app_class")!="user":
                    continue
                key=str(app_row.get("app_key") or "")
                if requested and key!=requested:
                    continue
                meta=dict(app_row.get("metadata") or {})
                try:
                    permission_state=homeserver_app_security.permission_status(key)
                except Exception:
                    permission_state=None
                try:
                    release_state=homeserver_app_packages.runtime_status(key)
                except Exception:
                    release_state=None
                try:
                    data_state=homeserver_app_data_lifecycle.status(key)
                except Exception:
                    data_state=None
                try:
                    distribution_state=homeserver_app_distribution.installed_provenance(key)
                except Exception:
                    distribution_state=None
                items.append({
                    "app_key":key,
                    "name":str(app_row.get("name") or key),
                    "source_type":str(app_row.get("source_type") or ""),
                    "lifecycle_state":str(app_row.get("lifecycle_state") or ""),
                    "installed_version":app_row.get("installed_version"),
                    "runtime":str(meta.get("runtime") or ""),
                    "sdk_version":str(meta.get("sdk_version") or ""),
                    "permissions":permission_state,
                    "release":release_state,
                    "data":data_state,
                    "distribution":distribution_state,
                })
            if op=="apps.user.status":
                if not requested:
                    return {"status":400,"ok":False,"payload":{"detail":"app_key is required."}}
                if not items:
                    return {"status":404,"ok":False,"payload":{"detail":"User app was not found."}}
                return {"status":200,"ok":True,"payload":{"contract":"vp3.user-app-status.v1","app":items[0]}}
            return {"status":200,"ok":True,"payload":{"contract":"vp3.user-app-list.v1","items":items,"count":len(items)}}
        if op == "apps.system.permissions.status":
            _vp3_system_apps_identity(token)
            key=str(body.get("app_key") or "").strip().lower()
            try:
                app=homeserver_apps.get(key)
                if app["app_class"]!="system" or not app["protected_system_app"]:
                    raise homeserver_apps.HomeServerAppError("Permission governance is restricted to protected VP3 System Apps.",409)
                payload_out=homeserver_app_security.permission_status(key)
            except (homeserver_apps.HomeServerAppError, homeserver_app_security.AppSecurityError) as exc:
                return {"status":int(getattr(exc,"status_code",400)),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op == "apps.system.permissions.set":
            _vp3_system_apps_identity(token)
            key=str(body.get("app_key") or "").strip().lower()
            permission=str(body.get("permission") or "").strip()
            allowed=bool(body.get("allowed"))
            try:
                app=homeserver_apps.get(key)
                if app["app_class"]!="system" or not app["protected_system_app"]:
                    raise homeserver_apps.HomeServerAppError("Permission governance is restricted to protected VP3 System Apps.",409)
                payload_out=homeserver_app_security.set_permission(
                    key,permission,allowed,
                    actor_type="owner",actor_key="vp3_cloud_confirmed",
                    reason=str(body.get("reason") or "cloud_confirmed")[:160],
                )
            except (homeserver_apps.HomeServerAppError, homeserver_app_security.AppSecurityError) as exc:
                return {"status":int(getattr(exc,"status_code",400)),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op == "apps.system.release.status":
            _vp3_system_apps_identity(token)
            try:
                payload_out=homeserver_app_prebuilt.release_status(str(body.get("app_key") or ""))
            except homeserver_apps.HomeServerAppError as exc:
                return {"status":int(exc.status_code),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op == "apps.system.rollback":
            _vp3_system_apps_identity(token)
            try:
                payload_out=homeserver_app_prebuilt.rollback(
                    str(body.get("app_key") or ""),
                    expected_active_release_id=str(body.get("expected_active_release_id") or "") or None,
                    reason=str(body.get("reason") or "owner_requested"),
                )
            except (homeserver_apps.HomeServerAppError, homeserver_app_releases.AppReleaseError, RuntimeError) as exc:
                return {"status":int(getattr(exc,"status_code",400)),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op == "apps.system.deactivate":
            _vp3_system_apps_identity(token)
            key=str(body.get("app_key") or "").strip().lower()
            try:
                status=_system_app_status(key)
                if not status.get("installed"):
                    return {"status":200,"ok":True,"payload":{**status,"changed":False,"reason":"not_installed"}}
                app=homeserver_apps.get(key)
                current=str(app.get("lifecycle_state") or "")
                changed=False
                if current in {"running","degraded","installed"}:
                    homeserver_apps.transition(
                        key,
                        "stopped",
                        actor_type="system",
                        actor_key="vp3_cloud",
                        metadata={"reason":"ownership_revoked"},
                    )
                    changed=True
                payload_out=_system_app_status(key)
                payload_out["changed"]=changed
                payload_out["reason"]="ownership_revoked" if changed else "already_inactive"
            except homeserver_apps.HomeServerAppError as exc:
                return {"status":int(exc.status_code),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op == "apps.system.reconcile":
            _vp3_system_apps_identity(token)
            requested=body.get("app_keys")
            if not isinstance(requested,list) or len(requested)>100:
                raise RemoteBridgeError("apps.system.reconcile requires a bounded app_keys array.")
            items=[]
            for raw_key in requested:
                try:
                    items.append(_system_app_status(str(raw_key or "")))
                except homeserver_apps.HomeServerAppError as exc:
                    items.append({"contract":"vp3.system-app-installation.v1","app_key":str(raw_key or ""),"error":str(exc),"status":int(exc.status_code)})
            return {"status":200,"ok":True,"payload":{"contract":"vp3.system-app-reconciliation.v1","runtime_authority":"homeserver","items":items}}
        if op == "hosting.inventory":
            _vp3_hosting_identity(token)
            return {"status":200,"ok":True,"payload":hosting_cloud_control.inventory()}
        if op == "hosting.site.status":
            _vp3_hosting_identity(token)
            cloud_site_id=str(body.get("cloud_site_id") or "")
            try:
                payload_out=hosting_cloud_control.status(cloud_site_id)
            except hosting_cloud_control.CloudHostingError as exc:
                return {"status":int(exc.status_code),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op == "hosting.site.reconcile":
            _vp3_hosting_identity(token)
            try:
                payload_out=hosting_cloud_control.reconcile(body)
            except hosting_cloud_control.CloudHostingError as exc:
                return {"status":int(exc.status_code),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op == "hosting.deployment.begin":
            _vp3_hosting_identity(token)
            try:
                payload_out=hosting_cloud_deployment.begin(
                    str(body.get("cloud_site_id") or ""),
                    revision=int(body.get("revision")),
                    package_sha256=str(body.get("package_sha256") or ""),
                    package_bytes=int(body.get("package_bytes")),
                    request_key=str(body.get("request_key") or ""),
                )
            except (TypeError,ValueError,hosting_cloud_deployment.CloudDeploymentError) as exc:
                status=int(getattr(exc,"status_code",422))
                return {"status":status,"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op == "hosting.deployment.chunk":
            _vp3_hosting_identity(token)
            try:
                payload_out=hosting_cloud_deployment.append_chunk(
                    str(body.get("cloud_site_id") or ""),
                    str(body.get("transfer_id") or ""),
                    int(body.get("chunk_index")),
                    str(body.get("data_b64") or ""),
                )
            except (TypeError,ValueError,hosting_cloud_deployment.CloudDeploymentError) as exc:
                status=int(getattr(exc,"status_code",422))
                return {"status":status,"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op == "hosting.deployment.commit":
            _vp3_hosting_identity(token)
            try:
                payload_out=hosting_cloud_deployment.commit(
                    str(body.get("cloud_site_id") or ""),
                    str(body.get("transfer_id") or ""),
                )
            except hosting_cloud_deployment.CloudDeploymentError as exc:
                return {"status":int(exc.status_code),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op == "hosting.deployment.status":
            _vp3_hosting_identity(token)
            try:
                payload_out=hosting_cloud_deployment.status(
                    str(body.get("cloud_site_id") or ""),
                    str(body.get("transfer_id") or "") or None,
                )
            except hosting_cloud_deployment.CloudDeploymentError as exc:
                return {"status":int(exc.status_code),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op == "hosting.deployment.rollback":
            _vp3_hosting_identity(token)
            try:
                payload_out=hosting_cloud_deployment.rollback(
                    str(body.get("cloud_site_id") or ""),
                    request_key=str(body.get("request_key") or ""),
                )
            except hosting_cloud_deployment.CloudDeploymentError as exc:
                return {"status":int(exc.status_code),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op == "hosting.diagnostics.summary":
            _vp3_hosting_identity(token)
            try:
                payload_out=hosting_diagnostics.cloud_summary(
                    str(body.get("cloud_site_id") or ""),
                    window_minutes=_bounded_int(body.get("window_minutes"),default=60,minimum=1,maximum=1440,name="window_minutes"),
                    recent_limit=_bounded_int(body.get("recent_limit"),default=30,minimum=0,maximum=100,name="recent_limit"),
                )
            except hosting_runtime.HostingError as exc:
                return {"status":int(exc.status_code),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op == "hosting.health.status":
            _vp3_hosting_identity(token)
            try:
                projection=hosting_cloud_control.status(str(body.get("cloud_site_id") or ""))
                payload_out=hosting_health_recovery.status(str(projection["site_id"]))
                payload_out["cloud_site_id"]=str(projection["cloud_site_id"])
            except hosting_runtime.HostingError as exc:
                return {"status":int(exc.status_code),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op == "hosting.health.check":
            _vp3_hosting_identity(token)
            try:
                projection=hosting_cloud_control.status(str(body.get("cloud_site_id") or ""))
                payload_out=hosting_health_recovery.evaluate(
                    str(projection["site_id"]),
                    execute_recovery=bool(body.get("execute_recovery",False)),
                )
                payload_out["cloud_site_id"]=str(projection["cloud_site_id"])
            except hosting_runtime.HostingError as exc:
                return {"status":int(exc.status_code),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op == "hosting.health.policy.update":
            _vp3_hosting_identity(token)
            try:
                projection=hosting_cloud_control.status(str(body.get("cloud_site_id") or ""))
                changes=body.get("policy") if isinstance(body.get("policy"),dict) else {}
                payload_out=hosting_health_recovery.update_policy(str(projection["site_id"]),changes)
                payload_out["cloud_site_id"]=str(projection["cloud_site_id"])
            except hosting_runtime.HostingError as exc:
                return {"status":int(exc.status_code),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op == "hosting.deployment.releases":
            _vp3_hosting_identity(token)
            try:
                payload_out=hosting_cloud_deployment.releases(
                    str(body.get("cloud_site_id") or ""),
                )
            except hosting_cloud_deployment.CloudDeploymentError as exc:
                return {"status":int(exc.status_code),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op == "hosting.deployment.promote":
            _vp3_hosting_identity(token)
            try:
                payload_out=hosting_cloud_deployment.promote(
                    str(body.get("cloud_site_id") or ""),
                    str(body.get("release_id") or ""),
                    request_key=str(body.get("request_key") or ""),
                )
            except hosting_cloud_deployment.CloudDeploymentError as exc:
                return {"status":int(exc.status_code),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op == "hosting.deployment.prune":
            _vp3_hosting_identity(token)
            try:
                payload_out=hosting_cloud_deployment.prune(
                    str(body.get("cloud_site_id") or ""),
                    int(body.get("keep") or 0),
                    request_key=str(body.get("request_key") or ""),
                )
            except (TypeError,ValueError,hosting_cloud_deployment.CloudDeploymentError) as exc:
                return {"status":int(getattr(exc,"status_code",422)),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op == "hosting.route.reconcile":
            _vp3_hosting_identity(token)
            try:
                payload_out=hosting_public.reconcile(
                    str(body.get("cloud_site_id") or ""),
                    revision=int(body.get("revision")),
                    hostname=str(body.get("hostname") or ""),
                    desired_state=str(body.get("desired_state") or ""),
                    hostname_verified=bool(body.get("hostname_verified")),
                    tls_state=str(body.get("tls_state") or ""),
                    certificate_not_after=str(body.get("certificate_not_after") or "") or None,
                    rotate_token=bool(body.get("rotate_token",False)),
                )
            except (TypeError,ValueError,hosting_public.PublicRoutingError) as exc:
                status=int(getattr(exc,"status_code",422))
                return {"status":status,"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op == "hosting.route.status":
            _vp3_hosting_identity(token)
            try:
                payload_out=hosting_public.status(str(body.get("cloud_site_id") or ""))
            except hosting_public.PublicRoutingError as exc:
                return {"status":int(exc.status_code),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op == "hosting.entitlements.reconcile":
            _vp3_hosting_identity(token)
            try:
                payload_out=hosting_entitlements.reconcile(body)
            except hosting_entitlements.EntitlementError as exc:
                return {"status":int(exc.status_code),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op == "hosting.entitlements.status":
            _vp3_hosting_identity(token)
            try:
                payload_out=hosting_entitlements.status()
            except hosting_entitlements.EntitlementError as exc:
                return {"status":int(exc.status_code),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op == "hosting.dashboard":
            _vp3_hosting_identity(token)
            try:
                payload_out=hosting_operations.dashboard()
            except hosting_operations.HostingOperationsError as exc:
                return {"status":int(exc.status_code),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op == "hosting.operation.execute":
            _vp3_hosting_identity(token)
            try:
                payload_out=hosting_operations.execute(
                    str(body.get("site_id") or ""),
                    str(body.get("action") or ""),
                    str(body.get("idempotency_key") or ""),
                    confirmed=bool(body.get("confirmed",False)),
                )
            except hosting_runtime.HostingError as exc:
                return {"status":int(exc.status_code),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op.startswith("agent.missions."):
            # Cloud command surface is reserved for the canonical paired VP3 app
            # with a current agent.chat grant. Never route through owner endpoints.
            identity = _direct_identity(token, {"agent.chat"})
            if str(identity.get("app_key") or "") != "vp3":
                raise RemoteBridgeError("Cloud missions require a paired VP3 identity.")
            action = op.removeprefix("agent.missions.")
            if action not in {"list", "get", "create", "task.prepare", "start", "cancel", "pause", "resume", "retry", "events", "evaluate", "decisions", "approve", "reject", "execution", "bind_provider", "tools.get", "tools.configure", "tools.start", "tools.status", "actions.list", "actions.review", "browser.grant", "browser.get", "browser.capture", "browser.revoke", "browser.live.start", "browser.live.get", "browser.live.refresh", "browser.live.propose", "browser.live.approve", "browser.live.stop", "browser.live.plan", "browser.action.propose", "browser.action.approve", "browser.owner.takeover", "browser.owner.release", "browser.owner.control", "browser.owner.search.review", "browser.owner.search.submit"}:
                raise RemoteBridgeError("Cloud mission operation is not allowed.")
            try:
                result = agent_mission_cloud_v1.execute(action, body)
            except agent_mission_cloud_v1.runtime.MissionError as exc:
                return {"status": int(exc.status_code), "ok": False,
                        "payload": {"detail": str(exc)}}
            except agent_mission_cloud_v1.agent_routing.AgentRoutingError as exc:
                return {"status": int(exc.status_code), "ok": False,
                        "payload": {"detail": str(exc)}}
            return {"status": 200, "ok": True, "payload": result}
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
            identity = _direct_identity(
                token,
                {"memory.read", "knowledge.search", "contacts.read", "tasks.read", "events.read", "files.read", "notifications.read"},
                # Saved VP3 pairings predate the native file-read grant. Keep
                # their authorized context live without granting file access.
                optional_permissions={"files.read"},
            )
            cloud_snapshot = body.get("cloud_snapshot")
            if not isinstance(cloud_snapshot, dict):
                raise RemoteBridgeError("shared.context.exchange requires cloud_snapshot.")
            query = str(body.get("query") or "")[:240]
            try:
                payload_out = shared_agent_context.exchange(
                    cloud_snapshot, query, include_files="files.read" in identity["permissions"]
                )
            except shared_agent_context.SharedAgentContextError as exc:
                raise RemoteBridgeError(str(exc)) from exc
            return {"status": 200, "ok": True, "payload": payload_out}
        if op == "physical_context.capabilities":
            return _local_response(client.get("/api/v1/tracky/capabilities", headers=headers))
        if op == "physical_context.current":
            return _local_response(client.get("/api/v1/tracky/context", headers=headers))
        if op == "physical_context.active_perception":
            return _local_response(client.post("/api/v1/tracky/active-perception", json=body, headers=headers))
        if op == "physical_context.request_status":
            request_id = str(body.get("request_id") or "")
            if not _REQUEST_ID.fullmatch(request_id):
                raise RemoteBridgeError("physical_context.request_status requires a valid request_id.")
            return _local_response(client.get(f"/api/v1/tracky/active-perception/{request_id}", headers=headers))
        if op == "physical_context.calibration":
            return _local_response(client.get("/api/v1/tracky/calibration", headers=headers))
        if op == "physical_context.model_lifecycle":
            return _local_response(client.get("/api/v1/tracky/model-lifecycle", headers=headers))
        if op == "physical_context.action.propose":
            return _local_response(client.post("/api/v1/tracky/actions/propose", json=body, headers=headers))
        if op == "physical_context.action.status":
            intent_id = str(body.get("intent_id") or "")
            if not _REQUEST_ID.fullmatch(intent_id):
                raise RemoteBridgeError("physical_context.action.status requires a valid intent_id.")
            return _local_response(client.get(f"/api/v1/tracky/actions/{intent_id}", headers=headers))
        if op == "physical_context.sync":
            return _local_response(client.post("/api/v1/tracky/cloud-sync", headers=headers))
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
            return _local_response(client.post("/api/v1/chat", json=body, headers=headers))
        if op == "federation.registry":
            _direct_identity(token, {"agent.chat"})
            return {"status": 200, "ok": True, "payload": federated_data.registry()}
        if op == "agent.infer.local":
            _direct_identity(token, {"agent.chat"})
            messages = _bounded_remote_messages(body.get("messages"))
            try:
                generated = providers.generate_ollama(messages)
            except providers.ProviderError as exc:
                return {"status": 503, "ok": False, "payload": {"detail": str(exc)}}
            return {
                "status": 200,
                "ok": True,
                "payload": {
                    "reply": str(generated.get("content") or "").strip(),
                    "provider": str(generated.get("provider") or "ollama"),
                    "model": str(generated.get("model") or ""),
                    "compute_source": "homeserver_local",
                    "usage": generated.get("usage") if isinstance(generated.get("usage"), dict) else {},
                    "stateless": True,
                    "tools_enabled": False,
                },
            }
        if op in {"transcription.shared.list","transcription.shared.fetch"}:
            # Cloud can only pull completed transcript text the HomeServer owner
            # explicitly marked shareable. No raw audio or private sessions.
            _vp3_system_apps_identity(token)
            _direct_identity(token,{"knowledge.search"})
            try:
                if op == "transcription.shared.list":
                    payload_out=local_transcription_sessions.list_sessions(
                        paired=True,limit=_bounded_int(body.get("limit"),default=25,minimum=1,maximum=50,name="limit"),
                    )
                else:
                    payload_out=local_transcription_sessions.get(
                        str(body.get("session_id") or ""),paired=True,
                    )
            except local_transcription_sessions.TranscriptError as exc:
                return {"status":int(exc.status_code),"ok":False,"payload":{"detail":str(exc)}}
            return {"status":200,"ok":True,"payload":payload_out}
        if op == "speech.status":
            _direct_identity(token, {"agent.chat"})
            profile = _remote_voice_profile()
            status = local_voice.status()
            return {
                "status": 200,
                "ok": True,
                "payload": {
                    "available": bool(status.get("tts", {}).get("available")) if isinstance(status.get("tts"), dict) else False,
                    "transcription_available": bool(status.get("stt", {}).get("available")) if isinstance(status.get("stt"), dict) else False,
                    "provider": "piper",
                    "transcription_provider": "whisper.cpp",
                    "voice_profile": profile,
                    "max_text_chars": _REMOTE_VOICE_MAX_TTS_CHARS,
                    "max_audio_bytes": _REMOTE_VOICE_MAX_AUDIO_BYTES,
                    "local": True,
                },
            }
        if op == "speech.synthesize":
            _direct_identity(token, {"agent.chat"})
            text = str(body.get("text") or "").strip()
            if not text:
                raise RemoteBridgeError("speech.synthesize text is required.")
            if len(text) > _REMOTE_VOICE_MAX_TTS_CHARS:
                raise RemoteBridgeError(
                    f"speech.synthesize text exceeds the {_REMOTE_VOICE_MAX_TTS_CHARS}-character relay limit."
                )
            profile = _remote_voice_profile()
            if not profile["ready"]:
                return {"status": 503, "ok": False, "payload": {"detail": profile["warning"] or "Local voice is unavailable."}}
            try:
                audio = local_voice.synthesize(
                    text,
                    voice_key=profile["voice"] or None,
                    speaking_rate=profile["speaking_rate"],
                    sentence_silence=profile["sentence_silence"],
                )
            except local_voice.LocalVoiceError as exc:
                return {"status": int(exc.status_code), "ok": False, "payload": {"detail": str(exc)}}
            if len(audio) > _REMOTE_VOICE_MAX_AUDIO_BYTES:
                return {
                    "status": 413,
                    "ok": False,
                    "payload": {"detail": "Local voice audio exceeds the relay chunk limit. Use a shorter speech chunk."},
                }
            return {
                "status": 200,
                "ok": True,
                "payload": {
                    "audio_base64": base64.b64encode(audio).decode("ascii"),
                    "content_type": "audio/wav",
                    "bytes": len(audio),
                    "provider": "piper",
                    "voice": profile["voice"],
                    "voice_source": profile["voice_source"],
                    "local": True,
                },
            }
        if op == "speech.transcribe":
            _direct_identity(token, {"agent.chat"})
            audio = _decode_remote_wav(body.get("audio_base64"))
            try:
                transcript = local_voice.transcribe(audio)
            except local_voice.LocalVoiceError as exc:
                return {"status": int(exc.status_code), "ok": False, "payload": {"detail": str(exc)}}
            return {
                "status": 200,
                "ok": True,
                "payload": {
                    "text": str(transcript.get("text") or ""),
                    "provider": str(transcript.get("provider") or "whisper.cpp"),
                    "model": str(transcript.get("model") or ""),
                    "local": True,
                },
            }
        if op == "conversations.list":
            return _local_response(client.get("/api/v1/conversations", headers=headers))
        if op == "conversation.get":
            conversation_id = str(body.get("conversation_id") or "").strip()
            if not _OPAQUE_ID.fullmatch(conversation_id):
                raise RemoteBridgeError("conversation_id must be a valid opaque conversation identifier.")
            return _local_response(client.get(f"/api/v1/conversations/{conversation_id}", headers=headers))
        if op in {"contacts.search", "contacts.list"}:
            query = str(body.get("query") or "")[:240]
            limit = _bounded_int(body.get("limit"), default=100, minimum=1, maximum=250, name="limit")
            return _local_response(client.get("/api/v1/contacts", params={"q": query, "limit": limit}, headers=headers))
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
        self._https_session_token: str | None = None
        self._https_pending_results: list[dict] = []
        self._https_pending_exchange: dict | None = None
        self._feature_sync_retry_at = 0.0
        self._feature_sync_backoff = 1.0

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
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._run()
                return
            except Exception as exc:
                database_error = isinstance(exc, sqlite3.OperationalError)
                details = _database_error_details(exc) if database_error else {}
                message = str(details.get("message") or f"{type(exc).__name__}: remote bridge worker crashed")
                stage = "database-retrying" if database_error else "crashed-retrying"
                _set_state(running=True, stage=stage, connected=False,
                           claimed=False, claim_code=None, connection_id=None,
                           last_error=message)
                if database_error:
                    _increment_reconnect_count()
                # A second database failure while reporting must not kill recovery.
                try:
                    federated_data.note_peer_disconnected("vp3_cloud", message)
                except sqlite3.Error:
                    pass
                _event("bridge.worker", "failed", metadata={"stage": stage, "error_type": type(exc).__name__, **details})
                if self._stop.wait(backoff):
                    return
                backoff = min(backoff * 2.0, 30.0)

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
        if self._https_session_token != token:
            # Accepted requests and receipts belong to this exact Cloud session.
            self._https_session_token = token
            self._https_pending_results.clear()
            self._https_pending_exchange = None
            self._feature_sync_retry_at = 0.0
            self._feature_sync_backoff = 1.0
            _set_state(feature_sync_error=None)
        pending_results = self._https_pending_results
        announced = False
        _set_state(stage="connecting", connected=False, claimed=True, claim_code=None, last_error=None)
        _event("bridge.connection", "attempting", metadata={"stage": "connecting", "transport": "vp3_https"})

        with httpx.Client(timeout=20.0, follow_redirects=False) as client:
            while not self._stop.is_set() and not _RELOAD_EVENT.is_set():
                capability_result = dispatch_remote_request("capabilities", {}, None)
                capabilities = capability_result.get("payload") if capability_result.get("ok") else {}
                if not isinstance(capabilities, dict):
                    capabilities = {}

                if self._https_pending_exchange is None:
                    self._receive_https_exchange(client, endpoint, headers, token, capabilities, pending_results)
                    if self._https_pending_exchange is None:
                        return
                data = self._https_pending_exchange

                if self._stop.is_set() or _RELOAD_EVENT.is_set() or not https_session_matches(token):
                    return
                touch_paired_app("vp3")
                first_contact = not announced
                if first_contact:
                    peer_state = federated_data.note_peer_connected("vp3_cloud")
                    announced = True
                    _event(
                        "bridge.reconnected" if peer_state.get("reconnected") else "bridge.connected",
                        "completed",
                        metadata={"device_id": identity["device_id"], "transport": "vp3_https",
                                  "reconciliation_required": bool(peer_state.get("needs_reconciliation"))},
                    )
                now = _iso_now()
                _set_state(stage="ready", connected=True, claimed=True, claim_code=None,
                           connection_id=identity["device_id"],
                           last_connected_at=now if first_contact else _STATE.get("last_connected_at"),
                           last_message_at=now, last_error=None)

                # Keep an accepted response across SQLite failures before dispatch.
                requests = data.get("requests") if isinstance(data.get("requests"), list) else []
                while requests:
                    if self._stop.is_set() or _RELOAD_EVENT.is_set() or not https_session_matches(token):
                        return
                    # Consume before dispatch: an interrupted action has an unknown
                    # outcome and must never replay earlier commands in this batch.
                    message = requests.pop(0)
                    if not isinstance(message, dict):
                        continue
                    request_id = str(message.get("request_id") or "")
                    operation = str(message.get("operation") or "")
                    if not _REQUEST_ID.fullmatch(request_id):
                        _event("bridge.request", "rejected", operation=operation, metadata={"reason": "invalid-request-id"})
                        continue
                    started = time.monotonic()
                    try:
                        with SESSION_LOCK:
                            if self._stop.is_set() or _RELOAD_EVENT.is_set() or not https_session_matches(token):
                                return
                            result = dispatch_remote_request(
                                operation,
                                message.get("payload") if isinstance(message.get("payload"), dict) else {},
                                str(message.get("bearer_token") or "") or None,
                            )
                    except RemoteBridgeError as exc:
                        result = {"status": 400, "ok": False, "payload": {"detail": str(exc)}}
                    except sqlite3.OperationalError as exc:
                        # Execution may have started. Report failure without
                        # automatically replaying this action during recovery.
                        result = {"status": 503, "ok": False,
                                  "payload": {"detail": _database_error_details(exc)["message"]}}
                    except Exception as exc:
                        result = {"status": 503, "ok": False,
                                  "payload": {"detail": f"{type(exc).__name__}: remote operation interrupted; verify its outcome before retrying."}}
                    duration_ms = int((time.monotonic() - started) * 1000)
                    _event(
                        "bridge.request",
                        "completed" if result.get("ok") else "denied",
                        operation=operation,
                        request_id=request_id,
                        metadata={"http_status": int(result.get("status") or 500), "duration_ms": duration_ms, "transport": "vp3_https"},
                    )
                    pending_results.append({"request_id": request_id, **result})
                self._https_pending_exchange = None

                # Complete accepted commands before supplementary synchronization.
                # A feature database fault must not strand their receipts or the
                # next heartbeat on an already accepted relay response.
                self._sync_features()

                poll_after = data.get("poll_after_ms", 900)
                try:
                    wait_seconds = max(0.25, min(float(poll_after) / 1000.0, 5.0))
                except (TypeError, ValueError):
                    wait_seconds = 0.9
                self._stop.wait(wait_seconds)

    def _sync_features(self) -> None:
        if time.monotonic() < self._feature_sync_retry_at:
            return
        try:
            if tracky_physical_context.sync_due():
                tracky_physical_context.sync_cloud(timeout=8.0)
            elif self._feature_sync_backoff > 1.0 and tracky_physical_context.sync_status().get("last_error"):
                # An existing feature transport backoff is not a successful sync.
                return
        except Exception as exc:
            details = _database_error_details(exc) if isinstance(exc, sqlite3.OperationalError) else {}
            message = str(details.get("message") or "Feature synchronization is temporarily unavailable.")
            _set_state(feature_sync_error=message)
            _event("bridge.feature-sync", "failed", metadata={"error_type": type(exc).__name__, **details})
            self._feature_sync_retry_at = time.monotonic() + self._feature_sync_backoff
            self._feature_sync_backoff = min(self._feature_sync_backoff * 2.0, 30.0)
        else:
            self._feature_sync_retry_at = 0.0
            self._feature_sync_backoff = 1.0
            _set_state(feature_sync_error=None)

    def _receive_https_exchange(self, client, endpoint, headers, token, capabilities, pending_results) -> None:
        response = client.post(
            endpoint,
            headers=headers,
            json={
                "version": settings.version,
                "capabilities": capabilities,
                "results": pending_results,
            },
        )
        with SESSION_LOCK:
            if self._stop.is_set() or _RELOAD_EVENT.is_set() or not https_session_matches(token):
                _event("bridge.worker", "superseded", metadata={"transport": "vp3_https"})
                return
            if response.status_code == 410:
                # Hold the pairing lock through all revocation writes, so a
                # replacement cannot be disabled after this token was checked.
                if clear_https_session_if_matches(token):
                    disable_vp3_https_settings()
                    revoke_paired_app("vp3")
                    _set_state(stage="revoked", connected=False, claimed=False,
                               last_error="VP3 pairing was explicitly revoked. Save a new pairing key to connect again.")
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

        # A successful validated exchange acknowledges previous receipts.
        pending_results.clear()
        self._https_pending_exchange = data

    def _run(self) -> None:
        _set_state(running=True, stage="starting", last_error=None)
        _event("bridge.worker", "started", metadata={"stage": "starting"})
        backoff = 1.0

        while not self._stop.is_set():
            try:
                configured = get_bridge_settings()
            except sqlite3.OperationalError:
                raise
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
                    federated_data.note_peer_disconnected(
                        "vp3_cloud", f"{type(exc).__name__}: VP3 HTTPS relay unavailable"
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
                    peer_state = federated_data.note_peer_connected("vp3_cloud")
                    _event(
                        "bridge.reconnected" if peer_state.get("reconnected") else "bridge.connected",
                        "completed",
                        metadata={
                            "device_id": identity["device_id"],
                            "reconciliation_required": bool(peer_state.get("needs_reconciliation")),
                        },
                    )
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
                federated_data.note_peer_disconnected(
                    "vp3_cloud", f"{type(exc).__name__}: remote bridge unavailable"
                )
                federated_data.note_peer_disconnected(
                    "vp3_cloud", f"{type(exc).__name__}: remote bridge failed"
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

