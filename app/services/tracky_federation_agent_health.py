from __future__ import annotations

import json
import threading
import time
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
SETTING_KEY = "tracky.federation_agent_health.v280"
LOOP_SECONDS = 5.0

_STOP = threading.Event()
_THREAD: threading.Thread | None = None
_LOCK = threading.Lock()
_LATEST: dict[str, Any] = {}

_RANK = {
    "current": 0,
    "unknown": 1,
    "degraded": 2,
    "stale": 3,
    "reconciling": 4,
    "recovering": 5,
    "partitioned": 6,
    "offline": 7,
    "failed": 8,
}
_SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}


def _text(value: Any, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[: max(1, int(limit))]


def _number(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _site_id(value: Any) -> str:
    return _text(value, 64).lower()


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _authority_device(operations: dict[str, Any], site: dict[str, Any]) -> dict[str, Any] | None:
    authority = site.get("authority") if isinstance(site.get("authority"), dict) else {}
    device_id = _site_id(authority.get("device_id"))
    for row in operations.get("devices", []):
        if isinstance(row, dict) and _site_id(row.get("device_id") or row.get("id")) == device_id:
            return row
    return None


def _runtime_offline(device: dict[str, Any] | None) -> bool:
    status = _text((device or {}).get("runtime_status") or (device or {}).get("status"), 40).lower()
    return status in {"offline", "disconnected", "unavailable", "failed", "stopped", "missing"}


def _base_state(site: dict[str, Any], sync: dict[str, Any], device: dict[str, Any] | None) -> str:
    authority = site.get("authority") if isinstance(site.get("authority"), dict) else {}
    sync_status = _text(sync.get("status") or (site.get("federation") or {}).get("status") or "unknown", 30).lower()
    if site.get("status") and _text(site.get("status"), 24).lower() != "active":
        return "offline"
    if authority.get("status") in {"missing", "invalid"} or _runtime_offline(device):
        return "failed"
    if sync_status == "failed":
        return "failed"
    if sync_status == "partitioned":
        return "partitioned"
    if sync_status == "stale":
        return "stale"
    if sync_status == "reconciling":
        return "reconciling"
    if sync_status in {"suspect", "unknown"}:
        return "degraded"
    if sync_status == "current":
        return "current"
    return "unknown"


def _cause(site: dict[str, Any], sync: dict[str, Any], device: dict[str, Any] | None) -> str:
    authority = site.get("authority") if isinstance(site.get("authority"), dict) else {}
    sync_status = _text(sync.get("status") or (site.get("federation") or {}).get("status") or "unknown", 30).lower()
    if site.get("status") and _text(site.get("status"), 24).lower() != "active":
        return "site_inactive"
    if authority.get("status") == "missing":
        return "authority_device_missing"
    if authority.get("status") == "invalid":
        return _text(authority.get("reason") or "authority_device_invalid", 120)
    if _runtime_offline(device):
        return "authority_device_offline"
    if sync_status == "failed":
        return _text(sync.get("conflict_code") or sync.get("last_error") or "reconciliation_failed", 120)
    if sync_status == "partitioned":
        return "federation_partition"
    if sync_status == "stale":
        return "federation_stale"
    if sync_status == "reconciling":
        return "federation_reconciling"
    if sync_status == "suspect":
        return "federation_suspect"
    if sync_status == "unknown":
        return "federation_unknown"
    return ""


def _severity(state: str, duration_ms: int, flap_count: int) -> str:
    if state in {"failed", "offline", "partitioned"}:
        return "critical"
    if state in {"recovering", "reconciling", "stale", "degraded"}:
        return "critical" if duration_ms >= 300_000 or flap_count >= 3 else "warning"
    return "info"


def _trust(state: str, sync: dict[str, Any]) -> dict[str, str]:
    if state == "current" and bool(sync.get("fresh")):
        return {
            "semantic_state": "current",
            "agent_use": "current",
            "physical_claims": "current",
            "reason": "authoritative_reconciliation_current",
        }
    if state in {"recovering", "reconciling", "stale", "degraded", "partitioned", "offline"}:
        return {
            "semantic_state": "stale",
            "agent_use": "qualified_stale",
            "physical_claims": "do_not_claim_current",
            "reason": "authoritative_reconciliation_not_current",
        }
    return {
        "semantic_state": "blocked",
        "agent_use": "health_only",
        "physical_claims": "do_not_claim_current",
        "reason": "health_or_authority_failure",
    }


def _message(state: str, previous_state: str | None) -> str:
    if state == "current" and previous_state and previous_state != "current":
        return "Connectivity and authoritative reconciliation are current. Recovery is complete."
    if state == "recovering":
        return "Connectivity has returned, but recovery is not complete until authoritative reconciliation is current."
    if state == "reconciling":
        return "The site is connected but authoritative reconciliation is still running. Treat remote physical state as stale."
    if state == "partitioned":
        return "The site is partitioned. Last known semantic state remains stale and cannot be promoted to current."
    if state == "offline":
        return "The site is offline. Agent may use only qualified last-known state where policy permits."
    if state == "failed":
        return "The site has a health or authority failure. Physical current-state claims are blocked."
    if state == "stale":
        return "Federated semantic state is stale. Agent must qualify it as last known."
    if state == "degraded":
        return "Federation health is degraded. Freshness is not verified current."
    return "Federation state is current and authoritative."


def _title(label: str, state: str, previous_state: str | None) -> str:
    if state == "current" and previous_state and previous_state != "current":
        return f"{label} recovered"
    mapping = {
        "offline": "is offline",
        "partitioned": "is partitioned",
        "failed": "health failure",
        "recovering": "is recovering",
        "reconciling": "is reconciling",
        "stale": "data is stale",
        "degraded": "is degraded",
    }
    return f"{label} {mapping.get(state, 'federation health changed')}"


def _load_state() -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            "SELECT value_json FROM system_settings WHERE setting_key=? LIMIT 1",
            (SETTING_KEY,),
        ).fetchone()
    if row is None:
        return {}
    try:
        parsed = json.loads(row["value_json"] or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _save_state(state: dict[str, Any]) -> None:
    with db() as connection:
        connection.execute(
            """
            INSERT INTO system_settings(setting_key,value_json)
            VALUES (?,?)
            ON CONFLICT(setting_key) DO UPDATE SET
              value_json=excluded.value_json,
              updated_at=CURRENT_TIMESTAMP
            """,
            (SETTING_KEY, json.dumps(state, separators=(",", ":"), sort_keys=True)),
        )


def _delivery_decision(event: dict[str, Any], delivery: dict[str, Any], now_ms: int) -> tuple[bool, str]:
    key = _text(event.get("dedupe_key"), 320)
    prior = delivery.get(key) if isinstance(delivery.get(key), dict) else {}
    last = _number(prior.get("last_delivered_at"))
    prior_severity = _text(prior.get("severity") or "info", 20)
    cooldown = 60_000 if event.get("severity") == "critical" else 120_000
    increased = _SEVERITY_RANK.get(str(event.get("severity")), 0) > _SEVERITY_RANK.get(prior_severity, 0)
    if last and now_ms - last < cooldown and not increased:
        return False, "dedupe_cooldown"
    delivery[key] = {"last_delivered_at": now_ms, "severity": event.get("severity")}
    return True, "severity_escalated" if increased else "state_change"


def build_report(
    operations: dict[str, Any],
    sync_visibility: dict[str, Any],
    access: dict[str, Any],
    relay_status: dict[str, Any],
    *,
    previous: dict[str, Any] | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    previous = previous or {}
    now_ms = int(now_ms if now_ms is not None else _now_ms())
    sync_map = {
        _site_id(row.get("site_id")): row
        for row in sync_visibility.get("sites", [])
        if isinstance(row, dict)
    }
    access_map = {
        _site_id(row.get("site_id")): row
        for row in access.get("peers", [])
        if isinstance(row, dict)
    }
    previous_sites = {
        _site_id(row.get("site_id")): row
        for row in previous.get("sites", [])
        if isinstance(row, dict)
    }
    previous_event_state = previous.get("event_state") if isinstance(previous.get("event_state"), dict) else {}

    cloud = relay_status.get("cloud") if isinstance(relay_status.get("cloud"), dict) else {}
    relay_state = _text(cloud.get("state") or "unknown", 30).lower()
    relay_paired = bool(cloud.get("paired"))
    relay_connected = bool(cloud.get("connected"))
    if relay_paired and relay_state == "offline":
        relay_health_state = "offline"
    elif relay_paired and relay_state == "reconnecting":
        relay_health_state = "recovering"
    elif relay_paired and not relay_connected and relay_state != "not_connected":
        relay_health_state = "degraded"
    else:
        relay_health_state = "current"
    prior_relay = previous.get("relay_health") if isinstance(previous.get("relay_health"), dict) else {}
    relay_health = {
        "component": "vp3_cloud_relay",
        "state": relay_health_state,
        "previous_state": _text(prior_relay.get("state"), 30) or None,
        "severity": _severity(relay_health_state, 0, 0),
        "cause": _text(cloud.get("last_error") or f"cloud_relay_{relay_state}", 240),
        "recovery_complete": relay_health_state == "current",
        "message": (
            "VP3 Cloud relay is connected or not required."
            if relay_health_state == "current"
            else "VP3 Cloud relay is reconnecting; federation recovery is not yet complete."
            if relay_health_state == "recovering"
            else "VP3 Cloud relay is unavailable."
        ),
    }

    sites: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    event_state: dict[str, Any] = {}

    if (
        (relay_health["previous_state"] and relay_health["previous_state"] != relay_health_state)
        or (not relay_health["previous_state"] and relay_health_state != "current")
    ):
        events.append({
            "event_id": f"federation-health:vp3-cloud-relay:{relay_health_state}:{now_ms}",
            "event_type": "relay.recovered" if relay_health_state == "current" else f"relay.{relay_health_state}",
            "component": "vp3_cloud_relay",
            "severity": relay_health["severity"],
            "importance": 0.95 if relay_health["severity"] == "critical" else 0.75,
            "priority": "priority" if relay_health["severity"] == "critical" else "normal",
            "title": "VP3 Cloud relay recovered" if relay_health_state == "current" else f"VP3 Cloud relay {relay_health_state}",
            "summary": relay_health["message"],
            "cause": relay_health["cause"],
            "recovery_complete": relay_health["recovery_complete"],
            "voice_eligible": True,
            "dedupe_key": f"vp3_cloud_relay|{relay_health_state}|{relay_health['cause']}",
            "flap_count_10m": 0,
            "occurred_at_ms": now_ms,
        })

    for site in operations.get("sites", []):
        if not isinstance(site, dict):
            continue
        site_id = _site_id(site.get("id") or site.get("site_id"))
        if not site_id:
            continue
        sync = sync_map.get(site_id) or {}
        device = _authority_device(operations, site)
        prior = previous_sites.get(site_id) or {}
        previous_state = _text(prior.get("state"), 30).lower()
        state = _base_state(site, sync, device)
        transport_returned = (
            bool(previous_state)
            and previous_state in {"offline", "partitioned", "failed"}
            and state not in {"offline", "partitioned", "failed"}
        )
        if transport_returned and state != "current":
            state = "recovering"
        state_since = _number(prior.get("state_since")) if state == previous_state else now_ms
        if not state_since:
            state_since = now_ms
        prior_event = previous_event_state.get(site_id) if isinstance(previous_event_state.get(site_id), dict) else {}
        transition_times = [
            _number(value)
            for value in (prior_event.get("transition_times") if isinstance(prior_event.get("transition_times"), list) else [])
            if _number(value) and now_ms - _number(value) <= 600_000
        ]
        if previous_state and previous_state != state:
            transition_times.append(now_ms)
        flap_count = max(0, len(transition_times) - 1)
        duration_ms = max(0, now_ms - state_since)
        severity = _severity(state, duration_ms, flap_count)
        authority = site.get("authority") if isinstance(site.get("authority"), dict) else {}
        access_peer = access_map.get(site_id)
        local_site = _site_id(operations.get("local_site_id"))
        agent_visible = site_id == local_site or bool((access_peer or {}).get("policy_peer_allowed"))
        trust = _trust(state, sync)
        message = _message(state, previous_state or None)
        label = _text(site.get("label") or site_id, 160)
        row = {
            "site_id": site_id,
            "label": label,
            "state": state,
            "previous_state": previous_state or None,
            "state_since": state_since,
            "duration_ms": duration_ms,
            "severity": severity,
            "cause": _cause(site, sync, device),
            "federation_status": _text(sync.get("status") or (site.get("federation") or {}).get("status") or "unknown", 30).lower(),
            "fresh": bool(sync.get("fresh")),
            "reconciliation_required": bool(sync.get("reconciliation_required")),
            "revision_gap": _number(sync.get("revision_gap")),
            "stale_age_ms": _number(sync.get("stale_age_ms")),
            "authority": {
                "status": _text(authority.get("status") or "unknown", 30),
                "device_id": _site_id(authority.get("device_id")),
                "epoch": _number(authority.get("epoch")),
            },
            "authority_device_runtime": _text((device or {}).get("runtime_status") or (device or {}).get("status") or "unknown", 40).lower(),
            "trust": trust,
            "agent_visible": agent_visible,
            "recovery_complete": state == "current" and bool(sync.get("fresh")) and not bool(sync.get("reconciliation_required")),
            "recovery_gate": "authoritative_reconciliation_current",
            "flap_count_10m": flap_count,
            "title": _title(label, state, previous_state or None),
            "message": message,
        }
        sites.append(row)
        event_state[site_id] = {
            "state": state,
            "state_since": state_since,
            "transition_times": transition_times[-20:],
        }
        changed = not previous_state or previous_state != state
        prior_severity = _text(prior.get("severity") or "info", 20)
        escalated = (
            not changed
            and _SEVERITY_RANK.get(severity, 0) > _SEVERITY_RANK.get(prior_severity, 0)
        )
        if (changed or escalated) and not (state == "current" and not previous_state):
            event_type = "site.escalated" if escalated else ("site.recovered" if state == "current" and previous_state else f"site.{state}")
            events.append({
                "event_id": f"federation-health:{site_id}:{state}:{now_ms}",
                "event_type": event_type,
                "site_id": site_id,
                "label": label,
                "state": state,
                "previous_state": previous_state or None,
                "severity": severity,
                "importance": 0.95 if severity == "critical" else 0.75 if severity == "warning" else 0.55,
                "priority": "priority" if severity == "critical" else "normal",
                "title": f"{label} health escalated" if escalated else row["title"],
                "summary": message,
                "cause": row["cause"],
                "trust": trust,
                "recovery_complete": row["recovery_complete"],
                "voice_eligible": severity != "info" or event_type == "site.recovered",
                "dedupe_key": f"{site_id}|{state}|{row['cause']}",
                "flap_count_10m": flap_count,
                "occurred_at_ms": now_ms,
            })

    overall_state = "current"
    for row in sites:
        if _RANK.get(row["state"], 1) > _RANK.get(overall_state, 0):
            overall_state = row["state"]
    if _RANK.get(relay_health_state, 0) > _RANK.get(overall_state, 0):
        overall_state = relay_health_state
    visible = [row for row in sites if row["agent_visible"]]
    agent_overall_state = "current"
    for row in visible:
        if _RANK.get(row["state"], 1) > _RANK.get(agent_overall_state, 0):
            agent_overall_state = row["state"]
    if _RANK.get(relay_health_state, 0) > _RANK.get(agent_overall_state, 0):
        agent_overall_state = relay_health_state
    relay_issue = (
        {
            "component": "vp3_cloud_relay",
            "state": relay_health_state,
            "severity": relay_health["severity"],
            "cause": relay_health["cause"],
            "message": relay_health["message"],
        }
        if relay_health_state != "current"
        else None
    )
    return {
        "protocol": FEDERATION_AGENT_HEALTH_PROTOCOL,
        "version": TRACKY_FEDERATION_AGENT_HEALTH_VERSION,
        "schema_version": 1,
        "generated_at": now_ms,
        "local_site_id": _site_id(operations.get("local_site_id")),
        "overall_state": overall_state,
        "relay": {
            "state": relay_state,
            "connected": relay_connected,
            "paired": relay_paired,
            "last_seen_at": cloud.get("last_seen_at"),
            "last_error": _text(cloud.get("last_error"), 240),
            "transport": _text(cloud.get("transport"), 40),
        },
        "relay_health": relay_health,
        "sites": sites,
        "events": events,
        "event_state": event_state,
        "counts": {
            "sites": len(sites),
            "current": sum(1 for row in sites if row["state"] == "current"),
            "degraded": sum(1 for row in sites if row["state"] in {"degraded", "stale", "reconciling", "recovering"}),
            "critical": sum(1 for row in sites if row["state"] in {"partitioned", "offline", "failed"}),
            "recovering": sum(1 for row in sites if row["state"] in {"recovering", "reconciling"}),
        },
        "agent_context": {
            "overall_state": agent_overall_state,
            "sites": [
                {
                    "site_id": row["site_id"],
                    "label": row["label"],
                    "state": row["state"],
                    "severity": row["severity"],
                    "cause": row["cause"],
                    "fresh": row["fresh"],
                    "recovery_complete": row["recovery_complete"],
                    "trust": row["trust"],
                }
                for row in visible
            ],
            "active_issues": (
                [
                    {
                        "site_id": row["site_id"],
                        "label": row["label"],
                        "state": row["state"],
                        "severity": row["severity"],
                        "cause": row["cause"],
                        "message": row["message"],
                        "trust": row["trust"],
                    }
                    for row in visible
                    if row["state"] != "current"
                ]
                + ([relay_issue] if relay_issue else [])
            )[:24],
            "summary": (
                "; ".join(
                    [
                        *[f"{row['label']} is {row['state']}" for row in visible if row["state"] != "current"][:6],
                        *([f"VP3 Cloud relay is {relay_health_state}"] if relay_issue else []),
                    ]
                )[:1000]
                or "All authorized federation sites and the Cloud relay are current."
            ),
            "recovery_requires_authoritative_reconciliation": True,
            "connectivity_returned_is_not_recovery": True,
        },
        "boundaries": [
            "connectivity-returned-is-not-recovery",
            "recovery-requires-authoritative-reconciliation-current",
            "stale-state-never-promoted-to-current",
            "health-never-changes-authority",
            "agent-health-context-respects-site-policy",
            "duplicate-and-flap-alerts-are-bounded",
        ],
    }


def _emit(event: dict[str, Any], delivery: dict[str, Any], now_ms: int) -> None:
    cognitive_runtime.emit_event(
        source_app_key="tracky.federation.health",
        source_kind="system",
        plugin_key="tracky",
        event_id=_text(event.get("event_id"), 160),
        event_type="tracky.federation." + _text(event.get("event_type"), 120).replace(" ", "_"),
        summary=_text(event.get("summary"), 2000),
        entity_type="site" if event.get("site_id") else "service",
        entity_key=_text(event.get("site_id") or event.get("component"), 240),
        importance=float(event.get("importance") or 0.75),
        privacy_scope="private",
        payload=event,
    )
    deliver, _ = _delivery_decision(event, delivery, now_ms)
    if not deliver or not bool(event.get("voice_eligible")):
        return
    with db() as connection:
        connection.execute(
            """
            INSERT INTO notifications(source,title,body,level)
            VALUES ('tracky-federation',?,?,?)
            """,
            (
                _text(event.get("title"), 300),
                _text(event.get("summary"), 5000),
                "warning",
            ),
        )


def recent_history(limit: int = 100) -> list[dict[str, Any]]:
    return cognitive_runtime.list_events(
        limit=max(1, min(500, int(limit))),
        source_app_key="tracky.federation.health",
        include_payload=True,
    )


def monitor_once(*, now_ms: int | None = None, emit: bool = True) -> dict[str, Any]:
    now_ms = int(now_ms if now_ms is not None else _now_ms())
    persisted = _load_state()
    operations = tracky_federation_operations.current_report()
    sync = tracky_sync_visibility.current_report()
    access = tracky_federation_access_operations.current_report()
    relay = remote_bridge.cloud_connection_status()
    report = build_report(operations, sync, access, relay, previous=persisted.get("report") or {}, now_ms=now_ms)
    delivery = persisted.get("delivery") if isinstance(persisted.get("delivery"), dict) else {}
    if emit:
        for event in report.get("events", []):
            if isinstance(event, dict):
                _emit(event, delivery, now_ms)
    persisted = {
        "report": {
            "generated_at": report["generated_at"],
            "relay_health": report["relay_health"],
            "sites": report["sites"],
            "event_state": report["event_state"],
        },
        "delivery": delivery,
    }
    _save_state(persisted)
    with _LOCK:
        global _LATEST
        _LATEST = report
    return report


def current_report() -> dict[str, Any]:
    with _LOCK:
        latest = dict(_LATEST)
    if latest:
        return latest
    try:
        return monitor_once(emit=False)
    except Exception:
        return {
            "protocol": FEDERATION_AGENT_HEALTH_PROTOCOL,
            "version": TRACKY_FEDERATION_AGENT_HEALTH_VERSION,
            "schema_version": 1,
            "generated_at": _now_ms(),
            "local_site_id": "",
            "overall_state": "unknown",
            "relay_health": {"component": "vp3_cloud_relay", "state": "unknown", "recovery_complete": False},
            "sites": [],
            "events": [],
            "counts": {"sites": 0, "current": 0, "degraded": 0, "critical": 0, "recovering": 0},
            "agent_context": {
                "overall_state": "unknown",
                "sites": [],
                "active_issues": [],
                "summary": "Federation health is unavailable.",
                "recovery_requires_authoritative_reconciliation": True,
                "connectivity_returned_is_not_recovery": True,
            },
        }


def cloud_projection() -> dict[str, Any]:
    report = current_report()
    return {
        **report,
        "events": [],
        "event_state": {},
        "history": recent_history(100),
        "summary_only": True,
        "cloud_read_only": True,
        "authority_mutation": False,
    }


def _loop() -> None:
    while not _STOP.wait(LOOP_SECONDS):
        try:
            monitor_once()
        except Exception:
            continue


def start() -> None:
    global _THREAD
    if _THREAD and _THREAD.is_alive():
        return
    _STOP.clear()
    try:
        monitor_once()
    except Exception:
        pass
    _THREAD = threading.Thread(target=_loop, name="tracky-federation-health", daemon=True)
    _THREAD.start()


def stop() -> None:
    _STOP.set()
    thread = _THREAD
    if thread and thread.is_alive():
        thread.join(timeout=2.0)


def public_capability() -> dict[str, Any]:
    return {
        "version": TRACKY_FEDERATION_AGENT_HEALTH_VERSION,
        "protocol": FEDERATION_AGENT_HEALTH_PROTOCOL,
        "states": ["current", "degraded", "stale", "partitioned", "reconciling", "recovering", "offline", "failed", "unknown"],
        "recovery_requires_authoritative_reconciliation": True,
        "connectivity_returned_is_not_recovery": True,
        "priority_agent_events": True,
        "voice_eligible_events": True,
        "flap_suppression": True,
        "persistent_operational_history": True,
        "stale_data_qualification": True,
        "permission_filtered_agent_context": True,
        "authority_mutation": False,
    }
