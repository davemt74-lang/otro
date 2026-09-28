from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from ..database import db
from . import (
    cognitive_runtime,
    remote_bridge,
    tracky_federation_access_operations,
    tracky_federation_operations,
    tracky_sync_visibility,
)

TRACKY_FEDERATION_AGENT_HEALTH_VERSION = "2.80"
FEDERATION_AGENT_HEALTH_PROTOCOL = "physical_federation_agent_health.v1"
_STATE_KEY = "tracky.federation_agent_health.v280"
_CONVERSATION_KEY = "tracky.federation_health_conversation.v280"
_HEALTH_RANK = {
    "connected": 0,
    "degraded": 1,
    "stale": 2,
    "reconciling": 3,
    "recovering": 4,
    "partitioned": 5,
    "offline": 6,
    "failed": 7,
}
_FAILURE_STATES = {"degraded", "stale", "reconciling", "recovering", "partitioned", "offline", "failed"}
_HARD_FAILURE_STATES = {"partitioned", "offline", "failed"}
_OFFLINE_RUNTIME = {"offline", "disconnected", "unreachable", "failed", "stopped"}


def _text(value: Any, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[: max(1, int(limit))]


def _number(value: Any) -> int:
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _load_setting(key: str, default: Any) -> Any:
    with db() as connection:
        row = connection.execute(
            "SELECT value_json FROM system_settings WHERE setting_key=? LIMIT 1",
            (key,),
        ).fetchone()
    if row is None:
        return default
    try:
        return json.loads(str(row["value_json"] or "null"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _save_setting(key: str, value: Any) -> None:
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True)
    with db() as connection:
        connection.execute(
            """
            INSERT INTO system_settings(setting_key,value_json)
            VALUES (?,?)
            ON CONFLICT(setting_key) DO UPDATE SET
              value_json=excluded.value_json,
              updated_at=CURRENT_TIMESTAMP
            """,
            (key, encoded),
        )


def _site_id(row: dict[str, Any] | None) -> str:
    row = row or {}
    return _text(row.get("id") or row.get("site_id"), 64).lower()


def _state_for(
    site: dict[str, Any],
    sync: dict[str, Any],
    authority_device: dict[str, Any] | None,
    previous: dict[str, Any],
) -> str:
    site_status = _text(site.get("status") or "active", 24).lower()
    ops_health = _text(site.get("health") or "unknown", 24).lower()
    federation = site.get("federation") if isinstance(site.get("federation"), dict) else {}
    sync_status = _text(sync.get("status") or federation.get("status") or "unknown", 24).lower()
    authority = site.get("authority") if isinstance(site.get("authority"), dict) else {}
    if site_status != "active":
        return "offline"
    if authority.get("status") and authority.get("status") != "current":
        return "failed"
    if authority_device and _text(authority_device.get("runtime_status"), 40).lower() in _OFFLINE_RUNTIME:
        return "offline"
    if sync_status == "failed":
        return "failed"
    if sync_status == "partitioned":
        return "partitioned"
    if sync_status == "reconciling":
        prior = _text(previous.get("state"), 24).lower()
        if prior in _HARD_FAILURE_STATES or prior == "stale" or previous.get("recovery_pending"):
            return "recovering"
        return "reconciling"
    if sync_status in {"stale", "suspect", "unknown"}:
        return "stale"
    if ops_health in {"critical", "degraded"}:
        return "degraded"
    return "connected"


def _issue_code(
    state: str,
    site: dict[str, Any],
    sync: dict[str, Any],
    authority_device: dict[str, Any] | None,
) -> str:
    authority = site.get("authority") if isinstance(site.get("authority"), dict) else {}
    if state == "failed" and authority.get("status") and authority.get("status") != "current":
        return _text(authority.get("reason") or "authority_invalid", 120)
    if state == "offline" and authority_device and _text(authority_device.get("runtime_status"), 40).lower() in _OFFLINE_RUNTIME:
        return "authority_device_offline"
    if state == "offline":
        return "site_offline"
    if state == "partitioned":
        return "federation_partitioned"
    if state == "recovering":
        return "reconciliation_recovery_pending"
    if state == "reconciling":
        return "federation_reconciling"
    if state == "stale":
        return "federation_stale"
    if state == "failed":
        return _text(sync.get("last_error"), 120) or "federation_failed"
    if state == "degraded":
        return "site_degraded"
    return ""


def _severity(state: str, age_ms: int) -> str:
    if state in {"offline", "failed", "partitioned"}:
        return "critical" if age_ms >= 15 * 60 * 1000 else "high"
    if state in {"recovering", "reconciling", "stale"}:
        return "high" if age_ms >= 30 * 60 * 1000 else "warning"
    if state == "degraded":
        return "warning" if age_ms >= 30 * 60 * 1000 else "notice"
    return "info"


def _escalation_level(state: str, age_ms: int) -> int:
    if state == "connected":
        return 0
    if age_ms >= 60 * 60 * 1000:
        return 3
    if age_ms >= 15 * 60 * 1000:
        return 2
    if age_ms >= 2 * 60 * 1000:
        return 1
    return 0


def _trust_scope(state: str, is_local: bool, bridge: dict[str, Any]) -> dict[str, Any]:
    local_current = bool(is_local and state not in {"failed", "offline"})
    federation_current = state == "connected"
    cloud_connected = bool(bridge.get("connected"))
    return {
        "local_physical_truth_current": local_current,
        "remote_federation_truth_current": federation_current,
        "cloud_transport_connected": cloud_connected,
        "agent_may_treat_remote_state_as_current": federation_current,
        "agent_may_treat_local_state_as_current": local_current,
        "note": (
            "Authoritative federation state is current."
            if federation_current
            else "Local physical truth may remain current, but remote federation truth is not current."
            if local_current
            else "Physical truth is not verified current."
        ),
    }


def _state_message(site: dict[str, Any], state: str, sync: dict[str, Any]) -> str:
    label = _text(site.get("label") or _site_id(site), 160) or "Site"
    if state == "connected":
        return f"{label} is connected and authoritative federation state is current."
    if state == "recovering":
        return f"{label} has connectivity again but is still reconciling. Recovery is not complete."
    if state == "reconciling":
        return f"{label} is reconciling authoritative federation state."
    if state == "partitioned":
        return f"{label} is partitioned. Remote physical data must be treated as stale."
    if state == "offline":
        return f"{label} is offline."
    if state == "failed":
        suffix = f": {_text(sync.get('last_error'), 180)}" if sync.get("last_error") else "."
        return f"{label} federation health failed closed{suffix}"
    if state == "stale":
        return f"{label} federation state is stale and must not be treated as current."
    return f"{label} is degraded."


def _event_for(
    current: dict[str, Any],
    previous: dict[str, Any] | None,
    now_ms: int,
) -> dict[str, Any] | None:
    previous = previous or {}
    changed = not previous or previous.get("state") != current["state"]
    escalated = (
        bool(previous)
        and previous.get("state") == current["state"]
        and _number(previous.get("escalation_level")) < current["escalation_level"]
    )
    if not changed and not escalated:
        return None
    label = current["label"]
    event_type = "site_" + current["state"]
    title = f"{label} {current['state']}"
    body = current["message"]
    if previous and previous.get("state") in _FAILURE_STATES and current["state"] == "connected":
        event_type = "site_recovered"
        title = f"{label} recovered"
        body = (
            f"{label} is recovered. Authoritative reconciliation is complete "
            "and federation state is current."
        )
    elif current["state"] == "recovering":
        event_type = "site_recovering"
        title = f"{label} reconnecting"
        body = (
            f"{label} reconnected, but recovery remains pending until "
            "authoritative reconciliation is current."
        )
    elif escalated:
        event_type = "site_health_escalated"
        title = f"{label} health escalation"
        body = current["message"] + " The condition has persisted and was escalated."
    priority = "info" if event_type == "site_recovered" else _severity(current["state"], current["state_age_ms"])
    return {
        "event_type": event_type,
        "site_id": current["site_id"],
        "site_label": label,
        "previous_state": previous.get("state") or "unknown",
        "state": current["state"],
        "priority": priority,
        "title": title,
        "body": body,
        "issue_code": current["issue_code"],
        "occurred_at": now_ms,
        "chat": True,
        "notification": priority in {"high", "critical"} or event_type in {"site_recovered", "site_recovering"},
        "voice_eligible": priority in {"high", "critical"} or event_type == "site_recovered",
        "recovery_complete": event_type == "site_recovered",
        "reconciliation_required": current["reconciliation_required"],
        "dedupe_key": f"site:{current['site_id']}:{event_type}:{current['escalation_level']}",
    }


def _bridge_state(raw: dict[str, Any], previous: dict[str, Any], now_ms: int) -> dict[str, Any]:
    state_raw = _text(
        raw.get("state") or raw.get("stage") or ("connected" if raw.get("connected") else "offline"),
        40,
    ).lower()
    connected = bool(raw.get("connected")) or state_raw == "connected"
    if connected:
        state = "connected"
    elif state_raw == "reconnecting":
        state = "reconnecting"
    elif state_raw == "not_connected":
        state = "not_connected"
    else:
        state = "offline"
    state_since = (
        _number(previous.get("state_since"))
        if previous.get("state") == state
        else now_ms
    ) or now_ms
    age_ms = max(0, now_ms - state_since)
    return {
        "state": state,
        "connected": connected,
        "paired": bool(raw.get("paired", True)),
        "state_since": state_since,
        "state_age_ms": age_ms,
        "last_error": _text(raw.get("last_error"), 240),
        "last_connected_at": raw.get("last_connected_at"),
        "reconnect_count": _number(raw.get("reconnect_count")),
        "transport": _text(raw.get("transport") or raw.get("transport_label"), 80),
        "impacts_local_physical_truth": False,
        "impacts_cloud_federation_delivery": not connected,
        "escalation_level": _escalation_level(state, age_ms),
    }


def _bridge_event(
    current: dict[str, Any],
    previous: dict[str, Any] | None,
    now_ms: int,
) -> dict[str, Any] | None:
    previous = previous or {}
    if current["state"] == "not_connected" and not current.get("paired"):
        return None
    changed = not previous or previous.get("state") != current["state"]
    escalated = (
        bool(previous)
        and previous.get("state") == current["state"]
        and _number(previous.get("escalation_level")) < current["escalation_level"]
    )
    if not changed and not escalated:
        return None
    if current["connected"]:
        event_type = "relay_recovered"
    elif current["state"] == "reconnecting":
        event_type = "relay_reconnecting"
    else:
        event_type = "relay_disconnected"
    priority = "info" if current["connected"] else ("high" if current["state_age_ms"] >= 15 * 60 * 1000 else "warning")
    return {
        "event_type": event_type,
        "site_id": "",
        "site_label": "VP3 Cloud Relay",
        "previous_state": previous.get("state") or "unknown",
        "state": current["state"],
        "priority": priority,
        "title": (
            "VP3 Cloud connection restored"
            if current["connected"]
            else "VP3 Cloud connection " + ("reconnecting" if current["state"] == "reconnecting" else "offline")
        ),
        "body": (
            "The HomeServer Cloud transport is connected. Site recovery still depends on federation reconciliation state."
            if current["connected"]
            else f"The VP3 Cloud transport is {current['state']}. Local physical truth may remain available on HomeServer."
        ),
        "issue_code": "" if current["connected"] else "cloud_relay_" + current["state"],
        "occurred_at": now_ms,
        "chat": True,
        "notification": True,
        "voice_eligible": True,
        "recovery_complete": current["connected"],
        "reconciliation_required": False,
        "dedupe_key": f"relay:{event_type}:{current['escalation_level']}",
    }


def build_report(
    operations: dict[str, Any],
    sync_visibility: dict[str, Any],
    access_operations: dict[str, Any],
    bridge: dict[str, Any],
    *,
    previous: dict[str, Any] | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    now_ms = int(now_ms if now_ms is not None else _now_ms())
    previous = previous if isinstance(previous, dict) else {}
    local_site = _text(sync_visibility.get("local_site_id") or operations.get("local_site_id"), 64).lower()
    devices = {
        _text(row.get("id") or row.get("device_id"), 64).lower(): row
        for row in operations.get("devices", [])
        if isinstance(row, dict)
    }
    sync_map = {
        _text(row.get("site_id"), 64).lower(): row
        for row in sync_visibility.get("sites", [])
        if isinstance(row, dict)
    }
    prior_map = {
        _text(row.get("site_id"), 64).lower(): row
        for row in previous.get("sites", [])
        if isinstance(row, dict)
    }
    access_map = {
        _text(row.get("site_id"), 64).lower(): row
        for row in access_operations.get("peers", [])
        if isinstance(row, dict)
    }
    sites: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for site in operations.get("sites", []):
        if not isinstance(site, dict):
            continue
        site_id = _site_id(site)
        sync_row = sync_map.get(site_id) or (site.get("federation") if isinstance(site.get("federation"), dict) else {})
        prior = prior_map.get(site_id) or {}
        authority = site.get("authority") if isinstance(site.get("authority"), dict) else {}
        authority_id = _text(authority.get("device_id"), 64).lower()
        authority_device = devices.get(authority_id)
        state = _state_for(site, sync_row, authority_device, prior)
        state_since = (
            _number(prior.get("state_since"))
            if prior.get("state") == state
            else now_ms
        ) or now_ms
        age_ms = max(0, now_ms - state_since)
        escalation = _escalation_level(state, age_ms)
        access = access_map.get(site_id)
        row = {
            "site_id": site_id,
            "label": _text(site.get("label") or site_id, 160),
            "is_local": site_id == local_site,
            "state": state,
            "state_since": state_since,
            "state_age_ms": age_ms,
            "escalation_level": escalation,
            "priority": _severity(state, age_ms),
            "health": _text(site.get("health") or "unknown", 24),
            "sync_status": _text(sync_row.get("status") or (site.get("federation") or {}).get("status") or "unknown", 24),
            "reconciliation_required": bool(sync_row.get("reconciliation_required"))
            or state in {"recovering", "reconciling", "partitioned", "stale", "failed"},
            "fresh": state == "connected",
            "issue_code": _issue_code(state, site, sync_row, authority_device),
            "message": "",
            "authority": authority,
            "authority_device": (
                {
                    "id": _text(authority_device.get("id"), 64),
                    "label": _text(authority_device.get("label"), 160),
                    "hardware_profile": _text(authority_device.get("hardware_profile"), 40),
                    "runtime_status": _text(authority_device.get("runtime_status") or "unknown", 40),
                    "last_seen_at": authority_device.get("last_seen_at") or 0,
                }
                if authority_device
                else None
            ),
            "trust": _trust_scope(state, site_id == local_site, bridge),
            "access": {
                "policy_peer_allowed": bool(access.get("policy_peer_allowed")) if access else None,
                "federation_enabled": bool(access.get("federation_enabled")) if access else None,
                "revocation_wins": True,
            },
            "recovery_pending": state in {"recovering", "reconciling"},
        }
        row["message"] = _state_message(site, state, sync_row)
        sites.append(row)
        event = _event_for(row, prior, now_ms)
        if event:
            events.append(event)
    sites.sort(key=lambda row: row["site_id"])

    previous_bridge = previous.get("bridge") if isinstance(previous.get("bridge"), dict) else {}
    bridge_row = _bridge_state(bridge, previous_bridge, now_ms)
    bridge_event = _bridge_event(bridge_row, previous_bridge, now_ms)
    if bridge_event:
        events.append(bridge_event)

    overall = "connected"
    for row in sites:
        if _HEALTH_RANK.get(row["state"], 1) > _HEALTH_RANK.get(overall, 0):
            overall = row["state"]

    return {
        "protocol": FEDERATION_AGENT_HEALTH_PROTOCOL,
        "version": TRACKY_FEDERATION_AGENT_HEALTH_VERSION,
        "schema_version": 1,
        "generated_at": now_ms,
        "local_site_id": local_site,
        "overall_state": overall,
        "sites": sites,
        "bridge": bridge_row,
        "events": events,
        "counts": {
            state: sum(1 for row in sites if row["state"] == state)
            for state in _HEALTH_RANK
        } | {"sites": len(sites)},
        "agent_context": {
            "state": overall,
            "site_health": [
                {
                    "site_id": row["site_id"],
                    "label": row["label"],
                    "state": row["state"],
                    "priority": row["priority"],
                    "message": row["message"],
                    "fresh": row["fresh"],
                    "reconciliation_required": row["reconciliation_required"],
                    "issue_code": row["issue_code"],
                    "trust": row["trust"],
                }
                for row in sites
            ],
            "bridge": bridge_row,
            "recovery_rule": (
                "Connectivity returning does not equal recovery. "
                "Recovery completes only after authoritative reconciliation is current."
            ),
            "local_truth_survives_cloud_relay_failure": True,
            "no_remote_authority_promotion": True,
        },
        "delivery": {
            "chat_events": True,
            "priority_notifications": True,
            "voice_respects_existing_settings": True,
            "duplicate_state_events_suppressed": True,
            "escalation_requires_persistent_duration": True,
        },
        "boundaries": [
            "reconnect-is-not-recovery",
            "authoritative-reconciliation-required-for-recovery",
            "local-physical-truth-separated-from-cloud-transport",
            "stale-remote-data-never-current",
            "agent-health-does-not-promote-authority",
            "permissions-and-consent-remain-enforced",
            "duplicate-transient-alerts-suppressed",
        ],
    }


def _bridge_report() -> dict[str, Any]:
    cloud = remote_bridge.cloud_connection_status().get("cloud") or {}
    runtime = remote_bridge.bridge_status().get("runtime") or {}
    return {
        **cloud,
        "last_connected_at": runtime.get("last_connected_at"),
        "reconnect_count": runtime.get("reconnect_count") or 0,
    }


def _live_report(*, previous: dict[str, Any] | None = None, now_ms: int | None = None) -> dict[str, Any]:
    return build_report(
        tracky_federation_operations.current_report(),
        tracky_sync_visibility.current_report(),
        tracky_federation_access_operations.current_report(),
        _bridge_report(),
        previous=previous,
        now_ms=now_ms,
    )


def _health_conversation_id() -> str:
    saved = _load_setting(_CONVERSATION_KEY, "")
    conversation_id = str(saved or "")
    with db() as connection:
        if conversation_id:
            existing = connection.execute(
                """
                SELECT id FROM conversations
                WHERE id=? AND source_app_key='owner' AND status='active'
                LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
            if existing is not None:
                return conversation_id
        agent = connection.execute(
            "SELECT id FROM agents WHERE is_primary=1 LIMIT 1"
        ).fetchone()
        if agent is None:
            raise RuntimeError("Primary Agent is not configured.")
        conversation_id = uuid.uuid4().hex
        connection.execute(
            """
            INSERT INTO conversations(id,agent_id,source_app_key,title)
            VALUES (?,?,'owner','Federation Health')
            """,
            (conversation_id, int(agent["id"])),
        )
    _save_setting(_CONVERSATION_KEY, conversation_id)
    return conversation_id


def _chat_event(event: dict[str, Any]) -> None:
    conversation_id = _health_conversation_id()
    metadata = {
        "card_type": "federation_health",
        "version": "2.80",
        "event_type": event["event_type"],
        "priority": event["priority"],
        "site_id": event.get("site_id") or "",
        "state": event.get("state") or "",
        "issue_code": event.get("issue_code") or "",
        "recovery_complete": bool(event.get("recovery_complete")),
        "reconciliation_required": bool(event.get("reconciliation_required")),
    }
    with db() as connection:
        connection.execute(
            """
            INSERT INTO conversation_messages(
              conversation_id,role,content,source_app_key,model,metadata_json
            ) VALUES (?,'assistant',?,'owner','system',?)
            """,
            (
                conversation_id,
                _text(event.get("body"), 2000),
                json.dumps(metadata, separators=(",", ":")),
            ),
        )
        connection.execute(
            "UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (conversation_id,),
        )


def _notification_event(event: dict[str, Any]) -> None:
    level = "warning" if event.get("priority") in {"high", "critical"} else "info"
    with db() as connection:
        connection.execute(
            """
            INSERT INTO notifications(source,title,body,level,task_id)
            VALUES ('tracky',?,?,?,NULL)
            """,
            (
                _text(event.get("title"), 300),
                _text(event.get("body"), 5000),
                level,
            ),
        )


def _cognitive_event(event: dict[str, Any]) -> None:
    event_id = "federation-health:" + _text(event.get("dedupe_key"), 120) + ":" + str(_number(event.get("occurred_at")))
    cognitive_runtime.emit_event(
        source_app_key="tracky",
        source_kind="system",
        event_id=event_id,
        event_type="tracky.federation_health." + _text(event.get("event_type"), 80),
        entity_type="site" if event.get("site_id") else "transport",
        entity_key=_text(event.get("site_id") or "vp3_cloud_relay", 160),
        summary=_text(event.get("body"), 1000),
        importance=0.95 if event.get("priority") in {"high", "critical"} else 0.7,
        privacy_scope="private",
        payload={
            "protocol": FEDERATION_AGENT_HEALTH_PROTOCOL,
            **event,
            "voice_eligible": bool(event.get("voice_eligible")),
            "reconnect_is_not_recovery": True,
        },
    )


def _deliver(event: dict[str, Any]) -> None:
    _cognitive_event(event)
    if event.get("chat"):
        _chat_event(event)
    if event.get("notification"):
        _notification_event(event)


def refresh(*, now_ms: int | None = None, deliver: bool = True) -> dict[str, Any]:
    previous = _load_setting(_STATE_KEY, {})
    if not isinstance(previous, dict):
        previous = {}
    report = _live_report(previous=previous, now_ms=now_ms)
    # Persist before delivery so a process restart cannot repeatedly emit the
    # same state transition. Durable cognitive history remains the audit trail.
    _save_setting(_STATE_KEY, {**report, "events": []})
    if deliver:
        for event in report.get("events", []):
            _deliver(event)
    return report


def current_report() -> dict[str, Any]:
    saved = _load_setting(_STATE_KEY, {})
    if isinstance(saved, dict) and saved.get("protocol") == FEDERATION_AGENT_HEALTH_PROTOCOL:
        return {**saved, "events": []}
    return _live_report(previous={})


def history(limit: int = 100) -> list[dict[str, Any]]:
    bounded = max(1, min(500, int(limit)))
    with db() as connection:
        rows = connection.execute(
            """
            SELECT event_id,event_type,entity_key,summary,importance,payload_json,occurred_at,created_at
            FROM cognitive_events
            WHERE source_app_key='tracky'
              AND event_type LIKE 'tracky.federation_health.%'
            ORDER BY id DESC LIMIT ?
            """,
            (bounded,),
        ).fetchall()
    out = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        out.append({
            "event_id": row["event_id"],
            "event_type": row["event_type"],
            "entity_key": row["entity_key"],
            "summary": row["summary"],
            "importance": float(row["importance"] or 0),
            "payload": payload if isinstance(payload, dict) else {},
            "occurred_at": row["occurred_at"],
            "created_at": row["created_at"],
            "immutable": True,
        })
    return out


def cloud_projection() -> dict[str, Any]:
    report = current_report()
    return {
        **report,
        "history": history(100),
        "events": [],
        "cloud_read_only": True,
        "cloud_can_mark_recovered": False,
        "authority_mutation": False,
    }


def public_capability() -> dict[str, Any]:
    return {
        "version": TRACKY_FEDERATION_AGENT_HEALTH_VERSION,
        "protocol": FEDERATION_AGENT_HEALTH_PROTOCOL,
        "states": list(_HEALTH_RANK),
        "relay_states": ["connected", "reconnecting", "offline", "not_connected"],
        "recovery_requires_current_reconciliation": True,
        "chat_events": True,
        "priority_notifications": True,
        "voice_eligible_events": True,
        "voice_respects_existing_settings": True,
        "persistent_history": True,
        "duplicate_state_suppression": True,
        "duration_escalation": True,
        "cloud_mirror_only": True,
        "authority_mutation": False,
    }


class FederationHealthRuntime:
    def __init__(self, interval_seconds: float = 5.0) -> None:
        self.interval_seconds = max(2.0, float(interval_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        # Establish a baseline without generating startup chatter. Real state
        # transitions after this point are delivered.
        try:
            refresh(deliver=False)
        except Exception:
            pass
        self._thread = threading.Thread(
            target=self._run,
            name="tracky-federation-health",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3.0)
        self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                refresh(deliver=True)
            except Exception:
                # Health reporting must never take the HomeServer runtime down.
                continue

    def status(self) -> dict[str, Any]:
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "interval_seconds": self.interval_seconds,
            "report": current_report(),
        }


runtime = FederationHealthRuntime()


def start() -> None:
    runtime.start()


def stop() -> None:
    runtime.stop()


def status() -> dict[str, Any]:
    return runtime.status()
