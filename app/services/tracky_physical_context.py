from __future__ import annotations

import hashlib
import json
import queue
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlparse, urlunparse

import httpx

from ..database import db
from . import federated_data, room_device_automation, tracky_federated_agent_context, tracky_federated_query, tracky_federated_world, tracky_federation_agent_health, tracky_federation_fleet_health, tracky_federation_policy, tracky_federation_reconciliation, tracky_federation_sync, tracky_forecast_calibration, tracky_governed_actions, tracky_identity_continuity, tracky_mobile_transition, tracky_model_lifecycle, tracky_site_topology, tracky_sync_visibility, vp3_os
from .https_bridge_session import load_https_session
from .remote_identity import remote_identity_metadata


TRACKY_PHYSICAL_VERSION = "2.78"
PHYSICAL_CONTEXT_PROTOCOL = "physical_context.v1"
ACTIVE_PERCEPTION_PROTOCOL = "active_perception.v1"
CLOUD_SYNC_PATH = "/api/tracky-sync-v270.php"
TRACKY_SYNC_BACKOFF_BASE_SECONDS = 5
TRACKY_SYNC_BACKOFF_MAX_SECONDS = 300
TRACKY_PROVIDER_TIMEOUT_SECONDS = 12
TRACKY_STALE_REQUEST_SECONDS = 120
TRACKY_BACKLOG_WARN_EVENTS = 500
TRACKY_BACKLOG_CRITICAL_EVENTS = 2000

_ALLOWED_REQUEST_TYPES = {
    "refresh_current_view",
    "check_room",
    "find_entity",
    "re_evaluate",
}
_ALLOWED_EVENT_PREFIXES = {
    "room", "person", "object", "environment", "routine", "gesture",
    "sensor", "camera", "tracky", "physical_context", "safety",
}
_ALLOWED_PRIVACY = {"cloud_derived", "system_health", "user_approved"}
_ALLOWED_TEMPORAL = {"current", "last_seen", "historical", "inferred", "predicted", "unknown"}
_SINGLE_VALUED_RELATIONS = {"located_in", "located_on", "present_in", "moving_between", "belongs_to"}
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_ENTITY_ID = re.compile(r"^[A-Za-z0-9._:-]{2,128}$")
_PREDICATE = re.compile(r"^[a-z0-9_.:-]{2,80}$")
_FORBIDDEN_KEY = re.compile(
    r"(?:^|_)(?:raw|frame|frames|image|images|video|videos|audio|recording|recordings|"
    r"embedding|embeddings|face_embedding|transcript|filesystem_path|file_path|"
    r"source_uri|camera_uri)(?:$|_)",
    re.IGNORECASE,
)


class TrackyPhysicalError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = int(status_code)


_PROVIDER_LOCK = threading.RLock()
_PROVIDER: Callable[[dict[str, Any]], dict[str, Any]] | None = None
_PROVIDER_CAPABILITIES: dict[str, Any] = {}
_PROVIDER_NAME = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise TrackyPhysicalError("Tracky semantic payload is not valid JSON.") from exc


def _bounded_text(value: Any, limit: int, *, required: bool = False, label: str = "value") -> str:
    text = str(value or "").strip()
    if required and not text:
        raise TrackyPhysicalError(f"{label} is required.")
    return text[: max(1, int(limit))]


def _confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0

def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _sync_backoff_seconds(failures: int) -> int:
    failures = max(1, int(failures))
    return min(TRACKY_SYNC_BACKOFF_MAX_SECONDS, TRACKY_SYNC_BACKOFF_BASE_SECONDS * (2 ** min(failures - 1, 8)))


def _record_sync_failure(error: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    with db() as connection:
        row = connection.execute(
            "SELECT consecutive_failures FROM tracky_cloud_sync_state WHERE id=1"
        ).fetchone()
        failures = int(row["consecutive_failures"] or 0) + 1 if row is not None else 1
        delay = _sync_backoff_seconds(failures)
        retry_at = (now + timedelta(seconds=delay)).isoformat()
        pending = int(connection.execute(
            "SELECT COUNT(*) FROM tracky_physical_events WHERE cloud_synced=0"
        ).fetchone()[0])
        connection.execute(
            """
            UPDATE tracky_cloud_sync_state
            SET consecutive_failures=?,next_retry_at=?,last_failure_at=?,last_error=?,
                last_backlog_count=?,updated_at=CURRENT_TIMESTAMP
            WHERE id=1
            """,
            (failures, retry_at, now.isoformat(), error[:300], pending),
        )
    tracky_federation_reconciliation.mark_all_remote_partitioned(error or "cloud_transport_failure")
    return {"failures": failures, "delay_seconds": delay, "next_retry_at": retry_at}


def _record_sync_success(last_sequence: int, cursor: str, synced_events: int) -> None:
    now = _now_iso()
    with db() as connection:
        pending = int(connection.execute(
            "SELECT COUNT(*) FROM tracky_physical_events WHERE cloud_synced=0"
        ).fetchone()[0])
        connection.execute(
            """
            UPDATE tracky_cloud_sync_state
            SET last_sequence=?,sync_cursor=?,last_success_at=?,last_error='',
                consecutive_failures=0,next_retry_at=NULL,last_backlog_count=?,
                last_success_sequence=?,updated_at=CURRENT_TIMESTAMP
            WHERE id=1
            """,
            (max(0, int(last_sequence)), str(cursor or ""), now, pending, max(0, int(last_sequence))),
        )


def recover_stale_requests(max_age_seconds: int = TRACKY_STALE_REQUEST_SECONDS) -> int:
    max_age_seconds = max(30, min(int(max_age_seconds), 3600))
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
    with db() as connection:
        rows = connection.execute(
            """
            SELECT request_id FROM tracky_active_perception_requests
            WHERE status IN ('requested','accepted','observing')
              AND updated_at < ?
            """,
            (cutoff.strftime("%Y-%m-%d %H:%M:%S"),),
        ).fetchall()
    for row in rows:
        _set_request_status(
            str(row["request_id"]),
            "failed",
            result={"reason": "request_recovered_after_interruption"},
            error="Active perception request expired after a process interruption.",
            completed=True,
        )
    return len(rows)


def _invoke_provider_with_timeout(
    provider: Callable[[dict[str, Any]], dict[str, Any]],
    request: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    timeout_seconds = max(0.25, min(float(timeout_seconds), 30.0))
    outcome: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

    def runner() -> None:
        try:
            outcome.put(("ok", provider(request)))
        except Exception as exc:
            outcome.put(("error", exc))

    thread = threading.Thread(target=runner, name="tracky-perception-provider", daemon=True)
    thread.start()
    thread.join(timeout_seconds)
    if thread.is_alive():
        raise TrackyPhysicalError("Tracky perception provider timed out.", 504)
    try:
        kind, value = outcome.get_nowait()
    except queue.Empty as exc:
        raise TrackyPhysicalError("Tracky perception provider returned no result.", 502) from exc
    if kind == "error":
        raise value
    return value


def sync_due(now: datetime | None = None) -> bool:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    status = sync_status()
    if int(status.get("pending_events") or 0) < 1:
        return False
    retry_at = _parse_datetime(status.get("next_retry_at"))
    return retry_at is None or retry_at <= now


def resilience_status() -> dict[str, Any]:
    recover_stale_requests()
    status = sync_status()
    pending = int(status.get("pending_events") or 0)
    failures = int(status.get("consecutive_failures") or 0)
    retry_at = _parse_datetime(status.get("next_retry_at"))
    now = datetime.now(timezone.utc)
    backoff_active = retry_at is not None and retry_at > now
    with db() as connection:
        active = int(connection.execute(
            "SELECT COUNT(*) FROM tracky_active_perception_requests WHERE status IN ('requested','accepted','observing')"
        ).fetchone()[0])
        oldest = connection.execute(
            "SELECT MIN(created_at) AS oldest FROM tracky_physical_events WHERE cloud_synced=0"
        ).fetchone()
    oldest_at = _parse_datetime(oldest["oldest"] if oldest is not None else None)
    oldest_age = max(0, int((now - oldest_at).total_seconds())) if oldest_at else 0
    if pending >= TRACKY_BACKLOG_CRITICAL_EVENTS:
        state = "critical"
    elif pending >= TRACKY_BACKLOG_WARN_EVENTS or failures >= 3:
        state = "degraded"
    elif backoff_active or pending > 0:
        state = "recovering"
    else:
        state = "healthy"
    return {
        "state": state,
        "pending_events": pending,
        "oldest_pending_age_seconds": oldest_age,
        "consecutive_sync_failures": failures,
        "next_retry_at": status.get("next_retry_at"),
        "backoff_active": backoff_active,
        "active_requests": active,
        "last_success_at": status.get("last_success_at"),
        "last_error": status.get("last_error"),
        "provider_timeout_seconds": TRACKY_PROVIDER_TIMEOUT_SECONDS,
        "backlog_warn_events": TRACKY_BACKLOG_WARN_EVENTS,
        "backlog_critical_events": TRACKY_BACKLOG_CRITICAL_EVENTS,
    }



def _assert_governed(value: Any, path: str = "payload", depth: int = 0) -> None:
    if depth > 8:
        raise TrackyPhysicalError("Tracky semantic payload nesting is too deep.")
    if isinstance(value, dict):
        for key, child in value.items():
            name = str(key)
            if _FORBIDDEN_KEY.search(name):
                raise TrackyPhysicalError(
                    f"Tracky semantic payload contains local-only perception data at {path}.{name}.",
                    422,
                )
            _assert_governed(child, f"{path}.{name}", depth + 1)
        return
    if isinstance(value, list):
        if len(value) > 500:
            raise TrackyPhysicalError("Tracky semantic payload list is too large.")
        for index, child in enumerate(value):
            _assert_governed(child, f"{path}[{index}]", depth + 1)
        return
    if isinstance(value, str) and len(value) > 20_000:
        raise TrackyPhysicalError("Tracky semantic payload contains an oversized value.")


def _normalize_entity_ref(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    entity_id = _bounded_text(value.get("entity_id"), 128)
    entity_type = _bounded_text(value.get("type"), 60).lower()
    if not entity_id and not entity_type:
        return {}
    if not _ENTITY_ID.fullmatch(entity_id) or not re.fullmatch(r"[a-z0-9_.:-]{2,60}", entity_type):
        raise TrackyPhysicalError("Tracky entity reference is invalid.")
    return {"entity_id": entity_id, "type": entity_type}


def _normalize_event(value: dict[str, Any]) -> dict[str, Any]:
    _assert_governed(value, "event")
    event_id = _bounded_text(value.get("event_id"), 128, required=True, label="event_id")
    if not _REQUEST_ID.fullmatch(event_id):
        raise TrackyPhysicalError("Tracky event_id is invalid.")
    try:
        sequence = int(value.get("sequence") or 0)
    except (TypeError, ValueError) as exc:
        raise TrackyPhysicalError("Tracky event sequence is invalid.") from exc
    if sequence < 1:
        raise TrackyPhysicalError("Tracky event sequence is required.")
    event_type = _bounded_text(value.get("event_type"), 100, required=True, label="event_type").lower()
    parts = event_type.split(".", 1)
    if len(parts) != 2 or parts[0] not in _ALLOWED_EVENT_PREFIXES or not re.fullmatch(r"[a-z0-9_]{2,70}", parts[1]):
        raise TrackyPhysicalError("Tracky event type is unsupported.")
    severity = _bounded_text(value.get("severity") or "informational", 30).lower()
    if severity not in {"informational", "notable", "actionable", "urgent", "system-health"}:
        severity = "informational"
    privacy = _bounded_text(value.get("privacy_class"), 40, required=True, label="privacy_class").lower()
    if privacy not in _ALLOWED_PRIVACY:
        raise TrackyPhysicalError("Tracky event is not eligible for Cloud semantic projection.", 422)
    occurred_at = _bounded_text(value.get("occurred_at"), 64, required=True, label="occurred_at")
    try:
        datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TrackyPhysicalError("Tracky event timestamp is invalid.") from exc

    event: dict[str, Any] = {
        "event_id": event_id,
        "sequence": sequence,
        "event_type": event_type,
        "severity": severity,
        "confidence": _confidence(value.get("confidence")),
        "privacy_class": privacy,
        "occurred_at": occurred_at,
    }
    for key in ("room_id", "environment_id"):
        item = _bounded_text(value.get(key), 128)
        if item:
            event[key] = item
    for key in ("summary", "state", "previous_state"):
        if key in value:
            event[key] = _bounded_text(value.get(key), 300)
    for key in ("subject", "object"):
        ref = _normalize_entity_ref(value.get(key))
        if ref:
            event[key] = ref
    return event


def _normalize_relation(value: dict[str, Any]) -> dict[str, Any]:
    _assert_governed(value, "world_state")
    subject = _bounded_text(value.get("subject_id"), 128, required=True, label="subject_id")
    predicate = _bounded_text(value.get("predicate"), 80, required=True, label="predicate").lower()
    object_id = _bounded_text(value.get("object_id"), 128)
    if not _ENTITY_ID.fullmatch(subject) or not _PREDICATE.fullmatch(predicate):
        raise TrackyPhysicalError("Tracky world-state relation is invalid.")
    temporal = _bounded_text(value.get("temporal_state") or "current", 30).lower()
    if temporal not in _ALLOWED_TEMPORAL:
        temporal = "unknown"
    try:
        sequence = max(0, int(value.get("sequence") or 0))
    except (TypeError, ValueError):
        sequence = 0
    as_of = _bounded_text(value.get("as_of") or _now_iso(), 64)
    raw_value = value.get("value")
    normalized_value: dict[str, Any] = {}
    if raw_value is not None:
        if not isinstance(raw_value, dict):
            raise TrackyPhysicalError("Tracky relation value must be an object.")
        for key in ("state", "previous_state", "label", "unit", "value"):
            if key in raw_value and isinstance(raw_value[key], (str, int, float, bool, type(None))):
                normalized_value[key] = raw_value[key] if not isinstance(raw_value[key], str) else raw_value[key][:300]
    return {
        "subject_id": subject,
        "predicate": predicate,
        "object_id": object_id,
        "value": normalized_value,
        "confidence": _confidence(value.get("confidence")),
        "temporal_state": temporal,
        "source_event_id": _bounded_text(value.get("source_event_id"), 128),
        "sequence": sequence,
        "as_of": as_of,
    }


def _relation_key(relation: dict[str, Any]) -> str:
    suffix = "" if relation["predicate"] in _SINGLE_VALUED_RELATIONS else "\0" + relation["object_id"]
    material = relation["subject_id"] + "\0" + relation["predicate"] + suffix
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _normalize_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    _assert_governed(value, "context")
    context: dict[str, Any] = {
        "current_room": _bounded_text(value.get("current_room"), 160),
        "environment_status": _bounded_text(value.get("environment_status"), 120),
        "confidence": _confidence(value.get("confidence")),
        "people_present": [],
        "recent_changes": [],
        "exceptions": [],
    }
    for item in list(value.get("people_present") or [])[:30]:
        if isinstance(item, (str, int, float)):
            text = _bounded_text(item, 160)
            if text:
                context["people_present"].append(text)
    for key in ("recent_changes", "exceptions"):
        for item in list(value.get(key) or [])[:20]:
            if isinstance(item, (str, int, float)):
                text = _bounded_text(item, 300)
                if text:
                    context[key].append(text)
    return context


def _provider_snapshot() -> tuple[Callable[[dict[str, Any]], dict[str, Any]] | None, dict[str, Any], str]:
    with _PROVIDER_LOCK:
        return _PROVIDER, dict(_PROVIDER_CAPABILITIES), _PROVIDER_NAME


def register_provider(
    callback: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    name: str,
    capabilities: dict[str, Any] | None = None,
) -> None:
    if not callable(callback):
        raise TrackyPhysicalError("Tracky perception provider must be callable.")
    safe_name = _bounded_text(name, 100, required=True, label="provider name")
    safe_caps = capabilities if isinstance(capabilities, dict) else {}
    _assert_governed(safe_caps, "provider_capabilities")
    with _PROVIDER_LOCK:
        global _PROVIDER, _PROVIDER_CAPABILITIES, _PROVIDER_NAME
        _PROVIDER = callback
        _PROVIDER_CAPABILITIES = json.loads(_json(safe_caps))
        _PROVIDER_NAME = safe_name


def unregister_provider() -> None:
    with _PROVIDER_LOCK:
        global _PROVIDER, _PROVIDER_CAPABILITIES, _PROVIDER_NAME
        _PROVIDER = None
        _PROVIDER_CAPABILITIES = {}
        _PROVIDER_NAME = ""


def canonical_rooms() -> list[dict[str, Any]]:
    try:
        rows = room_device_automation.list_rooms(enabled_only=True)
    except Exception:
        rows = []
    return [
        {
            "room_id": str(row.get("room_key") or ""),
            "name": str(row.get("name") or ""),
            "description": str(row.get("description") or ""),
        }
        for row in rows
        if str(row.get("room_key") or "")
    ]


def public_capability() -> dict[str, Any]:
    provider, provider_caps, provider_name = _provider_snapshot()
    inventory = vp3_os.hardware_inventory()
    camera = inventory.get("camera") if isinstance(inventory.get("camera"), dict) else {}
    reconciliation = federated_data.reconciliation_state("vp3_cloud")
    return {
        "version": TRACKY_PHYSICAL_VERSION,
        "protocol": PHYSICAL_CONTEXT_PROTOCOL,
        "active_perception_protocol": ACTIVE_PERCEPTION_PROTOCOL,
        "authority": "homeserver_local_tracky_semantics",
        "room_authority": "vp3_os_room_device_registry",
        "transport": "existing_vp3_https_session",
        "provider": {
            "available": provider is not None,
            "name": provider_name,
            "capabilities": provider_caps,
        },
        "camera": {
            "present": bool(camera.get("present")),
            "ready": bool(camera.get("ready")),
        },
        "room_count": len(canonical_rooms()),
        "active_perception": {
            "supported": True,
            "provider_required": True,
            "request_types": sorted(_ALLOWED_REQUEST_TYPES),
            "physical_actions": False,
        },
        "privacy": {
            "raw_frames_cloud_default": False,
            "raw_video_cloud_default": False,
            "raw_audio_cloud_default": False,
            "semantic_projection_only": True,
        },
        "continuity": {
            "foundation": "homeserver_v2.4",
            "reconciliation_required": bool(reconciliation.get("needs_reconciliation")),
            "active_perception_blocked_during_reconciliation": True,
        },
        "reliability": resilience_status(),
        "governed_actions": tracky_governed_actions.public_capability(),
        "forecast_calibration": tracky_forecast_calibration.public_capability(),
        "model_lifecycle": tracky_model_lifecycle.public_capability(),
        "site_topology": tracky_site_topology.public_capability(),
        "federated_world": tracky_federated_world.public_capability(),
        "federation_sync": tracky_federation_sync.public_capability(),
        "mobile_transitions": tracky_mobile_transition.public_capability(),
        "identity_continuity": tracky_identity_continuity.public_capability(),
        "federated_agent_context": tracky_federated_agent_context.public_capability(),
        "federation_policy": tracky_federation_policy.public_capability(),
        "federated_query": tracky_federated_query.public_capability(),
    }


def current_context() -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            "SELECT sequence_no,context_json,observed_at,updated_at FROM tracky_physical_context WHERE id=1"
        ).fetchone()
        relations = connection.execute(
            """
            SELECT subject_id,predicate,object_id,value_json,confidence,temporal_state,
                   source_event_id,sequence_no,as_of
            FROM tracky_physical_world_state
            ORDER BY as_of DESC,predicate,subject_id
            LIMIT 250
            """
        ).fetchall()
    context = {}
    sequence = 0
    observed_at = None
    updated_at = None
    if row is not None:
        try:
            context = json.loads(row["context_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            context = {}
        sequence = int(row["sequence_no"] or 0)
        observed_at = row["observed_at"]
        updated_at = row["updated_at"]
    world: list[dict[str, Any]] = []
    for rel in relations:
        item = dict(rel)
        try:
            item["value"] = json.loads(item.pop("value_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            item["value"] = {}
            item.pop("value_json", None)
        world.append(item)
    return {
        "protocol": PHYSICAL_CONTEXT_PROTOCOL,
        "sequence": sequence,
        "observed_at": observed_at,
        "updated_at": updated_at,
        "context": context,
        "world_state": world,
        "rooms": canonical_rooms(),
        "forecast_calibration": tracky_forecast_calibration.current_report(),
        "model_lifecycle": tracky_model_lifecycle.current_report(),
        "site_topology": tracky_site_topology.current_topology(),
        "federated_world": tracky_federation_policy.filter_world_report_for_local(
            tracky_federated_world.current_report()
        ),
        "federation_sync": tracky_federation_sync.status(),
        "mobile_transitions": tracky_mobile_transition.agent_context(
            tracky_federation_policy.filter_mobile_report_for_local(
                tracky_mobile_transition.current_report()
            )
        ),
        "identity_continuity": tracky_identity_continuity.agent_context(
            tracky_federation_policy.filter_identity_report_for_local(
                tracky_identity_continuity.current_report()
            )
        ),
        "federated_agent_context": tracky_federated_agent_context.current_context(refresh=True),
        "federation_policy": tracky_federation_policy.current_report(),
        "federation_agent_health": tracky_federation_agent_health.current_report().get("agent_context", {}),
        "federation_fleet_health": tracky_federation_fleet_health.current_report().get("agent_context", {}),
    }


def ingest_semantic_projection(payload: dict[str, Any], *, source: str = "provider") -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise TrackyPhysicalError("Tracky semantic projection must be an object.")
    _assert_governed(payload)
    events_raw = payload.get("events") if isinstance(payload.get("events"), list) else []
    relations_raw = payload.get("world_state") if isinstance(payload.get("world_state"), list) else []
    if len(events_raw) > 100 or len(relations_raw) > 250:
        raise TrackyPhysicalError("Tracky semantic projection exceeds batch limits.")
    events = [_normalize_event(item) for item in events_raw if isinstance(item, dict)]
    relations = [_normalize_relation(item) for item in relations_raw if isinstance(item, dict)]
    context = _normalize_context(payload.get("context"))
    calibration_raw = payload.get("forecast_calibration")
    calibration_report = (
        tracky_forecast_calibration.normalize_report(calibration_raw)
        if isinstance(calibration_raw, dict)
        else None
    )
    lifecycle_raw = payload.get("model_lifecycle")
    lifecycle_report = (
        tracky_model_lifecycle.normalize_report(lifecycle_raw)
        if isinstance(lifecycle_raw, dict)
        else None
    )
    federated_world_raw = payload.get("federated_world")
    federated_world_projection = (
        tracky_federated_world.normalize_projection(federated_world_raw)
        if isinstance(federated_world_raw, dict)
        else None
    )
    mobile_transitions_raw = payload.get("mobile_transitions")
    mobile_transitions_projection = (
        tracky_mobile_transition.normalize_projection(mobile_transitions_raw)
        if isinstance(mobile_transitions_raw, dict)
        else None
    )
    identity_continuity_raw = payload.get("identity_continuity")
    identity_continuity_projection = (
        tracky_identity_continuity.normalize_projection(identity_continuity_raw)
        if isinstance(identity_continuity_raw, dict)
        else None
    )
    try:
        context_sequence = max(0, int(payload.get("context_sequence") or max([e["sequence"] for e in events] + [0])))
    except (TypeError, ValueError):
        context_sequence = max([e["sequence"] for e in events] + [0])
    observed_at = _bounded_text(payload.get("context_observed_at") or _now_iso(), 64)
    inserted = 0
    duplicates = 0
    max_sequence = context_sequence

    with db() as connection:
        for event in events:
            existing_sequence = connection.execute(
                "SELECT event_id,event_json FROM tracky_physical_events WHERE sequence_no=? LIMIT 1",
                (event["sequence"],),
            ).fetchone()
            encoded = _json(event)
            if existing_sequence is not None and str(existing_sequence["event_id"]) != event["event_id"]:
                raise TrackyPhysicalError("Tracky event sequence conflicts with an existing event.", 409)
            existing_id = connection.execute(
                "SELECT sequence_no,event_json FROM tracky_physical_events WHERE event_id=? LIMIT 1",
                (event["event_id"],),
            ).fetchone()
            if existing_id is not None:
                if int(existing_id["sequence_no"]) != event["sequence"] or str(existing_id["event_json"]) != encoded:
                    raise TrackyPhysicalError("Tracky event idempotency conflict detected.", 409)
                duplicates += 1
                max_sequence = max(max_sequence, event["sequence"])
                continue
            connection.execute(
                """
                INSERT INTO tracky_physical_events(
                    event_id,sequence_no,event_type,severity,confidence,privacy_class,occurred_at,event_json
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    event["event_id"], event["sequence"], event["event_type"], event["severity"],
                    event["confidence"], event["privacy_class"], event["occurred_at"], encoded,
                ),
            )
            inserted += 1
            max_sequence = max(max_sequence, event["sequence"])

        for relation in relations:
            key = _relation_key(relation)
            existing = connection.execute(
                "SELECT sequence_no FROM tracky_physical_world_state WHERE relation_key=? LIMIT 1",
                (key,),
            ).fetchone()
            if existing is not None and int(existing["sequence_no"] or 0) > relation["sequence"]:
                continue
            connection.execute(
                """
                INSERT INTO tracky_physical_world_state(
                    relation_key,subject_id,predicate,object_id,value_json,confidence,
                    temporal_state,source_event_id,sequence_no,as_of
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(relation_key) DO UPDATE SET
                    subject_id=excluded.subject_id,
                    predicate=excluded.predicate,
                    object_id=excluded.object_id,
                    value_json=excluded.value_json,
                    confidence=excluded.confidence,
                    temporal_state=excluded.temporal_state,
                    source_event_id=excluded.source_event_id,
                    sequence_no=excluded.sequence_no,
                    as_of=excluded.as_of,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    key, relation["subject_id"], relation["predicate"], relation["object_id"],
                    _json(relation["value"]), relation["confidence"], relation["temporal_state"],
                    relation["source_event_id"], relation["sequence"], relation["as_of"],
                ),
            )
            max_sequence = max(max_sequence, relation["sequence"])

        if context and context_sequence >= 0:
            prior = connection.execute(
                "SELECT sequence_no FROM tracky_physical_context WHERE id=1"
            ).fetchone()
            if prior is None or context_sequence >= int(prior["sequence_no"] or 0):
                connection.execute(
                    """
                    INSERT INTO tracky_physical_context(id,sequence_no,context_json,observed_at,updated_at)
                    VALUES (1,?,?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(id) DO UPDATE SET
                        sequence_no=excluded.sequence_no,
                        context_json=excluded.context_json,
                        observed_at=excluded.observed_at,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (context_sequence, _json(context), observed_at),
                )

        calibration_result = None
        if calibration_report is not None:
            calibration_result = tracky_forecast_calibration.ingest_report(
                calibration_report,
                observed_at=observed_at,
                source=source,
                connection=connection,
            )

        lifecycle_result = None
        if lifecycle_report is not None:
            lifecycle_result = tracky_model_lifecycle.ingest_report(
                lifecycle_report,
                observed_at=observed_at,
                source=source,
                connection=connection,
            )

        connection.execute(
            """
            INSERT INTO activity_log(actor_type,actor_key,action,resource_type,resource_key,metadata_json)
            VALUES ('system',?,'tracky.semantic_projection.ingested','physical_context',?,?)
            """,
            (
                source[:80],
                str(max_sequence),
                _json({
                    "inserted": inserted,
                    "duplicates": duplicates,
                    "relations": len(relations),
                    "forecast_calibration": bool(calibration_result),
                    "forecast_calibration_changed": bool(
                        calibration_result and calibration_result.get("changed")
                    ),
                    "model_lifecycle": bool(lifecycle_result),
                    "model_lifecycle_changed": bool(
                        lifecycle_result and lifecycle_result.get("changed")
                    ),
                    "federated_world": bool(federated_world_projection),
                    "mobile_transitions": bool(mobile_transitions_projection),
                    "identity_continuity": bool(identity_continuity_projection),
                }),
            ),
        )

    federated_world_result = None
    if federated_world_projection is not None:
        federated_world_result = tracky_federated_world.ingest_projection(
            federated_world_projection,
            source=source,
        )

    mobile_transition_result = None
    if mobile_transitions_projection is not None:
        mobile_transition_result = tracky_mobile_transition.ingest_projection(
            mobile_transitions_projection,
            source=source,
            origin_role="local_authority",
        )

    identity_continuity_result = None
    if identity_continuity_projection is not None:
        identity_continuity_result = tracky_identity_continuity.ingest_projection(
            identity_continuity_projection,
            source=source,
            origin_role="local_governed",
        )

    automation_results = []
    if events:
        try:
            automation_results = tracky_governed_actions.process_event_automations(
                [str(event["event_id"]) for event in events]
            )
        except Exception:
            automation_results = []

    return {
        "accepted": True,
        "inserted_events": inserted,
        "duplicate_events": duplicates,
        "relations": len(relations),
        "last_sequence": max_sequence,
        "automation_results": automation_results,
        "forecast_calibration": {
            "accepted": calibration_report is not None,
            "changed": bool(calibration_result and calibration_result.get("changed")),
        },
        "model_lifecycle": {
            "accepted": lifecycle_report is not None,
            "changed": bool(lifecycle_result and lifecycle_result.get("changed")),
        },
        "federated_world": {
            "accepted": federated_world_projection is not None,
            "changed": bool(federated_world_result and federated_world_result.get("changed")),
            "stale": int(federated_world_result.get("stale") or 0) if federated_world_result else 0,
        },
        "mobile_transitions": {
            "accepted": mobile_transitions_projection is not None,
            "changed": int(mobile_transition_result.get("changed") or 0) if mobile_transition_result else 0,
            "stale": int(mobile_transition_result.get("stale") or 0) if mobile_transition_result else 0,
            "idempotent": int(mobile_transition_result.get("idempotent") or 0) if mobile_transition_result else 0,
        },
        "identity_continuity": {
            "accepted": identity_continuity_projection is not None,
            "changed": int(identity_continuity_result.get("changed") or 0) if identity_continuity_result else 0,
            "stale": int(identity_continuity_result.get("stale") or 0) if identity_continuity_result else 0,
            "idempotent": int(identity_continuity_result.get("idempotent") or 0) if identity_continuity_result else 0,
        },
    }


def _cloud_sync_url(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise TrackyPhysicalError("VP3 HTTPS session endpoint is invalid.", 503)
    return urlunparse((parsed.scheme, parsed.netloc, CLOUD_SYNC_PATH, "", "", ""))


def _cloud_payload(limit: int = 100) -> dict[str, Any]:
    identity = remote_identity_metadata()
    device_id = _bounded_text(identity.get("device_id"), 100, required=True, label="HomeServer device identity")
    with db() as connection:
        rows = connection.execute(
            """
            SELECT event_id,sequence_no,event_json
            FROM tracky_physical_events
            WHERE cloud_synced=0
            ORDER BY sequence_no ASC,id ASC
            LIMIT ?
            """,
            (max(1, min(int(limit), 100)),),
        ).fetchall()
        relations = connection.execute(
            """
            SELECT subject_id,predicate,object_id,value_json,confidence,temporal_state,
                   source_event_id,sequence_no,as_of
            FROM tracky_physical_world_state
            ORDER BY sequence_no DESC,relation_key
            LIMIT 250
            """
        ).fetchall()
        context_row = connection.execute(
            "SELECT sequence_no,context_json,observed_at FROM tracky_physical_context WHERE id=1"
        ).fetchone()
        sync_row = connection.execute(
            "SELECT sync_cursor FROM tracky_cloud_sync_state WHERE id=1"
        ).fetchone()

    events: list[dict[str, Any]] = []
    event_ids: list[str] = []
    max_sequence = 0
    for row in rows:
        try:
            event = json.loads(row["event_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(event, dict):
            continue
        events.append(event)
        event_ids.append(str(row["event_id"]))
        max_sequence = max(max_sequence, int(row["sequence_no"] or 0))

    world: list[dict[str, Any]] = []
    for row in relations:
        try:
            value = json.loads(row["value_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            value = {}
        world.append({
            "subject_id": str(row["subject_id"]),
            "predicate": str(row["predicate"]),
            "object_id": str(row["object_id"] or ""),
            "value": value if isinstance(value, dict) else {},
            "confidence": float(row["confidence"] or 0),
            "temporal_state": str(row["temporal_state"] or "unknown"),
            "source_event_id": str(row["source_event_id"] or ""),
            "sequence": int(row["sequence_no"] or 0),
            "as_of": str(row["as_of"]),
        })
        max_sequence = max(max_sequence, int(row["sequence_no"] or 0))

    context: dict[str, Any] = {}
    context_sequence = 0
    context_observed_at = None
    if context_row is not None:
        try:
            decoded = json.loads(context_row["context_json"] or "{}")
            context = decoded if isinstance(decoded, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            context = {}
        context_sequence = int(context_row["sequence_no"] or 0)
        context_observed_at = context_row["observed_at"]
        max_sequence = max(max_sequence, context_sequence)

    capability = public_capability()
    provider = capability["provider"]
    camera = capability["camera"]
    status = "healthy" if provider["available"] and (camera["ready"] or not provider["capabilities"].get("requires_camera", True)) else "degraded"
    cursor = f"hs-{max_sequence}"
    federation_request = tracky_federation_sync.cloud_sync_request()
    local_federation_site = str(federation_request.get("local_site_id") or "")
    federation_ready = bool(local_federation_site)
    federated_projection = (
        tracky_federated_world.cloud_projection(local_federation_site)
        if federation_ready
        else None
    )
    mobile_transition_projection = (
        tracky_mobile_transition.cloud_projection(local_federation_site)
        if federation_ready
        else None
    )
    identity_continuity_projection = (
        tracky_identity_continuity.cloud_projection(local_federation_site)
        if federation_ready
        else None
    )
    federated_agent_context_projection = (
        tracky_federated_agent_context.cloud_projection()
        if federation_ready
        else None
    )
    federation_policy_projection = (
        tracky_federation_policy.cloud_projection(local_federation_site)
        if federation_ready
        else None
    )
    sync_visibility_projection = (
        tracky_sync_visibility.cloud_projection()
        if federation_ready
        else None
    )
    federation_agent_health_projection = (
        tracky_federation_agent_health.cloud_projection()
        if federation_ready
        else None
    )
    federation_fleet_health_projection = (
        tracky_federation_fleet_health.cloud_projection()
        if federation_ready
        else None
    )
    return {
        "payload": {
            "protocol": PHYSICAL_CONTEXT_PROTOCOL,
            "site": {"id": device_id, "label": "HomeServer"},
            "status": status,
            "cursor": cursor,
            "capabilities": {
                "camera_count": 1 if camera["present"] else 0,
                "scene_graph": True,
                "active_perception": bool(provider["available"]),
                "recognition": bool(provider["capabilities"].get("recognition")),
                "object_tracking": bool(provider["capabilities"].get("object_tracking")),
                "gesture_support": bool(provider["capabilities"].get("gesture_support")),
                "acceleration": _bounded_text(provider["capabilities"].get("acceleration"), 120),
                "protocol": PHYSICAL_CONTEXT_PROTOCOL,
                "world_state_version": "1",
                "event_schema_version": "1",
                "physical_context_version": "1",
                "forecast_calibration": bool(tracky_forecast_calibration.current_report().get("available")),
                "forecast_calibration_protocol": tracky_forecast_calibration.FORECAST_CALIBRATION_PROTOCOL,
                "model_lifecycle": bool(tracky_model_lifecycle.current_report().get("available")),
                "model_lifecycle_protocol": tracky_model_lifecycle.MODEL_LIFECYCLE_PROTOCOL,
                "site_topology": True,
                "site_topology_protocol": tracky_site_topology.SITE_TOPOLOGY_PROTOCOL,
                "federated_world": bool(tracky_federated_world.current_report().get("available")),
                "federated_world_protocol": tracky_federated_world.FEDERATED_WORLD_PROTOCOL,
                "federation_sync": True,
                "federation_sync_protocol": tracky_federation_sync.FEDERATION_SYNC_PROTOCOL,
                "mobile_transitions": bool(tracky_mobile_transition.current_report().get("available")),
                "mobile_transition_protocol": tracky_mobile_transition.MOBILE_TRANSITION_PROTOCOL,
                "identity_continuity": bool(tracky_identity_continuity.current_report().get("available")),
                "identity_continuity_protocol": tracky_identity_continuity.IDENTITY_CONTINUITY_PROTOCOL,
                "federated_agent_context": True,
                "federated_agent_context_protocol": tracky_federated_agent_context.FEDERATED_AGENT_CONTEXT_PROTOCOL,
                "federation_policy": bool(tracky_federation_policy.current_report().get("available")),
                "federation_policy_protocol": tracky_federation_policy.FEDERATION_POLICY_PROTOCOL,
                "federated_query": bool(local_federation_site),
                "federated_query_protocol": tracky_federated_query.FEDERATED_QUERY_PROTOCOL,
                "federation_sync_visibility": bool(federation_ready),
                "federation_sync_visibility_protocol": tracky_sync_visibility.FEDERATION_SYNC_VISIBILITY_PROTOCOL,
                "federation_agent_health": bool(federation_ready),
                "federation_agent_health_protocol": tracky_federation_agent_health.FEDERATION_AGENT_HEALTH_PROTOCOL,
                "federation_fleet_health": bool(federation_ready),
                "federation_fleet_health_protocol": tracky_federation_fleet_health.FEDERATION_FLEET_HEALTH_PROTOCOL,
            },
            "health": {
                "runtime": "healthy",
                "camera": "healthy" if camera["ready"] else ("available" if camera["present"] else "unavailable"),
                "world_state": "fresh" if context else "empty",
                "inference": "available" if provider["available"] else "unavailable",
                "forecast_calibration": (
                    "available"
                    if tracky_forecast_calibration.current_report().get("available")
                    else "empty"
                ),
                "model_lifecycle": tracky_model_lifecycle.health_summary()["state"],
                "site_topology": "available" if tracky_site_topology.current_topology()["sites"] else "empty",
                "federated_world": "available" if tracky_federated_world.current_report().get("available") else "empty",
                "federation_sync": "available" if tracky_federation_sync.local_site_id(auto_pin=False) else "unresolved",
                "mobile_transitions": "active" if tracky_mobile_transition.current_report(active_only=True).get("active_count") else "idle",
                "identity_continuity": "available" if tracky_identity_continuity.current_report().get("available") else "empty",
                "federated_agent_context": tracky_federated_agent_context.current_context(refresh=True).get("agent_state", "current"),
                "federation_policy": "configured" if tracky_federation_policy.current_report().get("available") else "default_deny",
                "federated_query": "available" if local_federation_site else "unresolved",
                "federation_sync_visibility": (
                    sync_visibility_projection.get("overall_state", "unknown")
                    if sync_visibility_projection
                    else "unresolved"
                ),
                "federation_agent_health": (
                    federation_agent_health_projection.get("overall_state", "unknown")
                    if federation_agent_health_projection
                    else "unresolved"
                ),
                "federation_fleet_health": (
                    federation_fleet_health_projection.get("overall_state", "unknown")
                    if federation_fleet_health_projection
                    else "unresolved"
                ),
            },
            "forecast_calibration": tracky_forecast_calibration.cloud_projection(),
            "model_lifecycle": tracky_model_lifecycle.cloud_projection(),
            "site_topology": tracky_site_topology.cloud_summary(),
            "federated_world": federated_projection,
            "federation_sync": federation_request,
            "mobile_transitions": mobile_transition_projection,
            "identity_continuity": identity_continuity_projection,
            "federated_agent_context": federated_agent_context_projection,
            "federation_policy": federation_policy_projection,
            "federation_sync_visibility": sync_visibility_projection,
            "federation_agent_health": federation_agent_health_projection,
            "federation_fleet_health": federation_fleet_health_projection,
            "events": events,
            "world_state": world,
            "context": context,
            "context_sequence": context_sequence,
            "context_observed_at": context_observed_at or _now_iso(),
        },
        "event_ids": event_ids,
        "max_sequence": max_sequence,
        "cursor": cursor,
        "previous_cursor": str(sync_row["sync_cursor"] or "") if sync_row else "",
    }


def sync_cloud(*, timeout: float = 12.0, force: bool = False) -> dict[str, Any]:
    if not force and not sync_due():
        status = sync_status()
        return {
            "ok": True,
            "protocol": PHYSICAL_CONTEXT_PROTOCOL,
            "deferred": True,
            "reason": "retry_backoff" if int(status.get("pending_events") or 0) else "nothing_pending",
            "pending_events": int(status.get("pending_events") or 0),
            "next_retry_at": status.get("next_retry_at"),
        }
    session = load_https_session()
    if not session:
        raise TrackyPhysicalError("VP3 Cloud is not paired.", 503)
    identity = remote_identity_metadata()
    device_id = _bounded_text(identity.get("device_id"), 100, required=True, label="HomeServer device identity")
    package = _cloud_payload()
    endpoint = _cloud_sync_url(str(session.get("endpoint") or ""))
    now = _now_iso()
    with db() as connection:
        connection.execute(
            "UPDATE tracky_cloud_sync_state SET last_attempt_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=1",
            (now,),
        )
    try:
        with httpx.Client(timeout=max(2.0, min(float(timeout), 20.0)), follow_redirects=False, trust_env=True) as client:
            response = client.post(
                endpoint,
                json=package["payload"],
                headers={
                    "Authorization": f"Bearer {session['session_token']}",
                    "X-HomeServer-Device": device_id,
                    "Accept": "application/json",
                },
            )
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code < 200 or response.status_code >= 300 or not isinstance(body, dict) or not body.get("ok"):
            detail = str((body or {}).get("error") or (body or {}).get("detail") or f"HTTP {response.status_code}")[:300]
            raise TrackyPhysicalError(f"VP3 Tracky sync failed: {detail}", 503)
    except (httpx.HTTPError, OSError) as exc:
        _record_sync_failure(f"{type(exc).__name__}: Cloud sync unavailable")
        raise TrackyPhysicalError("VP3 Tracky sync is temporarily unavailable.", 503) from exc
    except TrackyPhysicalError as exc:
        _record_sync_failure(str(exc))
        raise

    with db() as connection:
        for event_id in package["event_ids"]:
            connection.execute(
                "UPDATE tracky_physical_events SET cloud_synced=1,cloud_synced_at=? WHERE event_id=?",
                (now, event_id),
            )
    federation_policy_result = None
    federation_policy_mirror = body.get("federation_policy")
    if isinstance(federation_policy_mirror, dict):
        try:
            federation_policy_result = tracky_federation_policy.ingest_cloud_mirror(
                federation_policy_mirror
            )
        except tracky_federation_policy.TrackyFederationPolicyError as exc:
            _record_sync_failure(f"Federation policy mirror rejected: {exc}")
            raise TrackyPhysicalError(f"VP3 Tracky federation policy sync failed: {exc}", 503) from exc

    federation_result = None
    federation_batch = body.get("federation_sync")
    if isinstance(federation_batch, dict):
        try:
            federation_result = tracky_federation_sync.ingest_cloud_batch(federation_batch)
        except tracky_federation_sync.TrackyFederationSyncError as exc:
            _record_sync_failure(f"Federation relay rejected: {exc}")
            raise TrackyPhysicalError(f"VP3 Tracky federation sync failed: {exc}", 503) from exc

    mobile_transition_result = None
    mobile_transition_mirror = body.get("mobile_transitions")
    if isinstance(mobile_transition_mirror, dict):
        try:
            mobile_transition_result = tracky_mobile_transition.ingest_projection(
                mobile_transition_mirror,
                source="vp3_cloud",
                origin_role="cloud_mirror",
            )
        except tracky_mobile_transition.TrackyMobileTransitionError as exc:
            _record_sync_failure(f"Mobile transition mirror rejected: {exc}")
            raise TrackyPhysicalError(f"VP3 Tracky mobile transition sync failed: {exc}", 503) from exc

    identity_continuity_result = None
    identity_continuity_mirror = body.get("identity_continuity")
    if isinstance(identity_continuity_mirror, dict):
        try:
            identity_continuity_result = tracky_identity_continuity.ingest_projection(
                identity_continuity_mirror,
                source="vp3_cloud",
                origin_role="cloud_mirror",
            )
        except tracky_identity_continuity.TrackyIdentityContinuityError as exc:
            _record_sync_failure(f"Identity continuity mirror rejected: {exc}")
            raise TrackyPhysicalError(f"VP3 Tracky identity continuity sync failed: {exc}", 503) from exc

    last_sequence = int(body.get("last_sequence") or package["max_sequence"])
    cursor = str(body.get("cursor") or package["cursor"])
    _record_sync_success(last_sequence, cursor, len(package["event_ids"]))
    return {
        "ok": True,
        "protocol": PHYSICAL_CONTEXT_PROTOCOL,
        "cloud": body,
        "synced_events": len(package["event_ids"]),
        "cursor": cursor,
        "federation_sync": federation_result,
        "mobile_transitions": mobile_transition_result,
        "identity_continuity": identity_continuity_result,
        "federation_policy": federation_policy_result,
        "resilience": resilience_status(),
    }


def sync_status() -> dict[str, Any]:
    with db() as connection:
        row = connection.execute("SELECT * FROM tracky_cloud_sync_state WHERE id=1").fetchone()
        pending = int(connection.execute("SELECT COUNT(*) FROM tracky_physical_events WHERE cloud_synced=0").fetchone()[0])
    return {
        **(dict(row) if row is not None else {}),
        "pending_events": pending,
        "protocol": PHYSICAL_CONTEXT_PROTOCOL,
    }


def _request_row(request_id: str) -> dict[str, Any] | None:
    with db() as connection:
        row = connection.execute(
            "SELECT * FROM tracky_active_perception_requests WHERE request_id=? LIMIT 1",
            (request_id,),
        ).fetchone()
    if row is None:
        return None
    item = dict(row)
    for key in ("target_json", "result_json"):
        try:
            item[key.removesuffix("_json")] = json.loads(item.pop(key) or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            item[key.removesuffix("_json")] = {}
            item.pop(key, None)
    return item


def request_status(request_id: str) -> dict[str, Any]:
    recover_stale_requests()
    request_id = _bounded_text(request_id, 128, required=True, label="request_id")
    if not _REQUEST_ID.fullmatch(request_id):
        raise TrackyPhysicalError("Active perception request_id is invalid.")
    row = _request_row(request_id)
    if row is None:
        raise TrackyPhysicalError("Active perception request was not found.", 404)
    return {"protocol": ACTIVE_PERCEPTION_PROTOCOL, "request": row}


def _set_request_status(
    request_id: str,
    status: str,
    *,
    result: dict[str, Any] | None = None,
    error: str = "",
    started: bool = False,
    completed: bool = False,
) -> None:
    parts = ["status=?", "result_json=?", "error=?", "updated_at=CURRENT_TIMESTAMP"]
    params: list[Any] = [status, _json(result or {}), error[:500]]
    if started:
        parts.append("started_at=COALESCE(started_at,CURRENT_TIMESTAMP)")
    if completed:
        parts.append("completed_at=COALESCE(completed_at,CURRENT_TIMESTAMP)")
    params.append(request_id)
    with db() as connection:
        connection.execute(
            f"UPDATE tracky_active_perception_requests SET {','.join(parts)} WHERE request_id=?",
            params,
        )


def active_perception(
    request_type: str,
    *,
    request_id: str | None = None,
    correlation_id: str | None = None,
    site_id: str | None = None,
    target: dict[str, Any] | None = None,
    reason: str = "",
    requested_by: str = "vp3_cloud",
) -> dict[str, Any]:
    recover_stale_requests()
    request_type = _bounded_text(request_type, 60, required=True, label="request_type").lower()
    if request_type not in _ALLOWED_REQUEST_TYPES:
        raise TrackyPhysicalError("Active perception request_type is unsupported.")
    request_id = _bounded_text(request_id or f"tracky-{uuid.uuid4().hex}", 128)
    correlation_id = _bounded_text(correlation_id or request_id, 128)
    if not _REQUEST_ID.fullmatch(request_id) or not _REQUEST_ID.fullmatch(correlation_id):
        raise TrackyPhysicalError("Active perception request identity is invalid.")
    identity = remote_identity_metadata()
    canonical_site = _bounded_text(identity.get("device_id"), 100, required=True, label="HomeServer device identity")
    requested_site = _bounded_text(site_id or canonical_site, 100)
    if requested_site != canonical_site:
        raise TrackyPhysicalError("Active perception request is routed to a different HomeServer site.", 409)
    target = target if isinstance(target, dict) else {}
    _assert_governed(target, "active_perception.target")
    safe_target: dict[str, Any] = {}
    for key in ("room_id", "entity_id", "environment_id", "query"):
        if key in target:
            safe_target[key] = _bounded_text(target.get(key), 240 if key == "query" else 128)
    if safe_target.get("room_id"):
        room_keys = {room["room_id"] for room in canonical_rooms()}
        if safe_target["room_id"] not in room_keys:
            raise TrackyPhysicalError("Requested room is not in the canonical HomeServer room registry.", 404)

    existing = _request_row(request_id)
    if existing is not None:
        same = (
            existing["request_type"] == request_type
            and existing["correlation_id"] == correlation_id
            and existing["site_id"] == canonical_site
            and existing.get("target") == safe_target
        )
        if not same:
            raise TrackyPhysicalError("Active perception request_id conflicts with an existing request.", 409)
        return {"protocol": ACTIVE_PERCEPTION_PROTOCOL, "request": existing, "idempotent": True}

    with db() as connection:
        prior = connection.execute(
            """
            SELECT request_id FROM tracky_active_perception_requests
            WHERE correlation_id=? AND request_id<>?
              AND status IN ('requested','accepted','observing')
            ORDER BY created_at DESC
            """,
            (correlation_id, request_id),
        ).fetchall()
        for row in prior:
            connection.execute(
                """
                UPDATE tracky_active_perception_requests
                SET status='superseded',superseded_by=?,result_json=?,error='',
                    completed_at=COALESCE(completed_at,CURRENT_TIMESTAMP),updated_at=CURRENT_TIMESTAMP
                WHERE request_id=?
                """,
                (
                    request_id,
                    _json({"reason": "superseded_by_new_request", "superseded_by": request_id}),
                    str(row["request_id"]),
                ),
            )
        deadline = (datetime.now(timezone.utc) + timedelta(seconds=TRACKY_PROVIDER_TIMEOUT_SECONDS)).isoformat()
        connection.execute(
            """
            INSERT INTO tracky_active_perception_requests(
                request_id,correlation_id,request_type,site_id,target_json,reason,status,requested_by,deadline_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                request_id, correlation_id, request_type, canonical_site, _json(safe_target),
                _bounded_text(reason, 500), "requested", _bounded_text(requested_by, 80) or "vp3_cloud",
                deadline,
            ),
        )

    reconciliation = federated_data.reconciliation_state("vp3_cloud")
    if reconciliation.get("needs_reconciliation"):
        result = {
            "reason": "homeserver_reconciliation_pending",
            "continuity_foundation": "v2.4",
            "reconciliation": {
                "needs_reconciliation": True,
                "last_error": str(reconciliation.get("last_error") or "")[:300],
            },
        }
        _set_request_status(request_id, "unable", result=result, error="v2.4 reconciliation pending", completed=True)
        return request_status(request_id)

    privacy = vp3_os.manifest(include_hardware=False, include_device_id=False).get("privacy", {})
    if bool(privacy.get("privacy_switch_engaged")):
        result = {"reason": "privacy_engaged", "privacy_switch_engaged": True}
        _set_request_status(request_id, "denied", result=result, error="Physical privacy is engaged.", completed=True)
        return request_status(request_id)

    provider, provider_caps, provider_name = _provider_snapshot()
    if provider is None:
        result = {
            "reason": "provider_unavailable",
            "provider_available": False,
            "camera": public_capability()["camera"],
        }
        _set_request_status(request_id, "unable", result=result, error="No local Tracky perception provider is attached.", completed=True)
        return request_status(request_id)

    if bool(provider_caps.get("requires_camera", True)):
        camera = vp3_os.hardware_inventory().get("camera") or {}
        if not bool(camera.get("ready")):
            result = {"reason": "camera_unavailable", "provider": provider_name}
            _set_request_status(request_id, "unable", result=result, error="Camera hardware is unavailable.", completed=True)
            return request_status(request_id)

    _set_request_status(request_id, "accepted", started=True)
    _set_request_status(request_id, "observing", started=True)
    provider_request = {
        "protocol": ACTIVE_PERCEPTION_PROTOCOL,
        "request_id": request_id,
        "correlation_id": correlation_id,
        "request_type": request_type,
        "site_id": canonical_site,
        "target": safe_target,
        "reason": _bounded_text(reason, 500),
        "rooms": canonical_rooms(),
        "deadline_at": (datetime.now(timezone.utc) + timedelta(seconds=TRACKY_PROVIDER_TIMEOUT_SECONDS)).isoformat(),
    }
    try:
        timeout_seconds = provider_caps.get("timeout_seconds", TRACKY_PROVIDER_TIMEOUT_SECONDS)
        provider_result = _invoke_provider_with_timeout(provider, provider_request, timeout_seconds)
        if not isinstance(provider_result, dict):
            raise TrackyPhysicalError("Tracky perception provider returned an invalid result.", 502)
        _assert_governed(provider_result, "provider_result")
        semantic = provider_result.get("semantic_projection")
        ingest_result = None
        if semantic is not None:
            if not isinstance(semantic, dict):
                raise TrackyPhysicalError("Tracky provider semantic_projection must be an object.", 502)
            ingest_result = ingest_semantic_projection(semantic, source=f"provider:{provider_name}")
        semantic_projection = _cloud_payload()["payload"] if semantic is not None else None
        result = {
            "reason": "completed",
            "provider": provider_name,
            "provider_result": {
                "summary": _bounded_text(provider_result.get("summary"), 500),
                "confidence": _confidence(provider_result.get("confidence")),
            },
            "semantic_ingest": ingest_result,
            "semantic_projection": semantic_projection,
            "cloud_sync": {"deferred": semantic is not None, "via": "existing_https_worker"},
            "cloud_sync_error": "",
            "current_context": current_context(),
        }
        _set_request_status(request_id, "completed", result=result, completed=True)
    except TrackyPhysicalError as exc:
        reason = "provider_timeout" if exc.status_code == 504 else "provider_failed"
        _set_request_status(
            request_id,
            "failed",
            result={"reason": reason},
            error=str(exc),
            completed=True,
        )
    except Exception as exc:
        _set_request_status(
            request_id,
            "failed",
            result={"reason": "provider_failed"},
            error=f"{type(exc).__name__}: local perception provider failed",
            completed=True,
        )

    return request_status(request_id)
