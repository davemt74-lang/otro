from __future__ import annotations

import json
from typing import Any

from ..database import db
from . import tracky_federation_operations, tracky_mobile_transition

TRACKY_CROSS_SITE_PRESENCE_VERSION = "2.80"
CROSS_SITE_PRESENCE_PROTOCOL = "physical_cross_site_presence.v1"
ACTIVE_STATES = {"departing", "in_transit", "arriving", "uncertain", "offline", "temporary_context"}
TERMINAL_STATES = {"arrived", "canceled"}


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _text(value: Any, limit: int = 200) -> str:
    return " ".join(str(value or "").split())[: max(1, int(limit))]


def _confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _site_map(operations: dict[str, Any]) -> dict[str, dict[str, Any]]:
    sites: dict[str, dict[str, Any]] = {}
    for row in operations.get("sites", []):
        if not isinstance(row, dict):
            continue
        site_id = _text(row.get("id") or row.get("site_id"), 64).lower()
        if not site_id:
            continue
        authority = row.get("authority") if isinstance(row.get("authority"), dict) else {}
        federation = row.get("federation") if isinstance(row.get("federation"), dict) else {}
        sites[site_id] = {
            "id": site_id,
            "label": _text(row.get("label") or site_id),
            "authority_device_id": _text(authority.get("device_id") or row.get("authority_device_id"), 64).lower(),
            "authority_epoch": max(0, int(authority.get("epoch") or row.get("authority_epoch") or 0)),
            "health": _text(row.get("health") or "unknown", 24).lower(),
            "federation_status": _text(federation.get("status") or "unknown", 24).lower(),
        }
    return sites


def _subject_catalog(operations: dict[str, Any], agent_context: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for row in operations.get("devices", []):
        if not isinstance(row, dict):
            continue
        subject_id = _text(row.get("id") or row.get("device_id"), 160).lower()
        if not subject_id:
            continue
        catalog[subject_id] = {
            "label": _text(row.get("label") or row.get("hardware_profile_label") or row.get("hardware_profile") or subject_id),
            "subject_type": "mobile_hardware",
            "hardware_profile": _text(row.get("hardware_profile") or "custom", 40).lower(),
        }
    context = agent_context if isinstance(agent_context, dict) else {}
    focus = context.get("focus_identity") if isinstance(context.get("focus_identity"), dict) else {}
    canonical = _text(focus.get("canonical_identity_id"), 160).lower()
    if canonical:
        aliases = focus.get("aliases") if isinstance(focus.get("aliases"), list) else []
        catalog[canonical] = {
            "label": _text(aliases[0] if aliases else canonical),
            "subject_type": _text(focus.get("entity_type") or "person", 40).lower(),
            "hardware_profile": "",
        }
    active = context.get("active_mobile_transition") if isinstance(context.get("active_mobile_transition"), dict) else {}
    active_subject = _text(active.get("subject_id"), 160).lower()
    if active_subject and active_subject not in catalog and canonical:
        catalog[active_subject] = dict(catalog[canonical])
    return catalog


def _site_label(sites: dict[str, dict[str, Any]], site_id: str) -> str:
    return _text((sites.get(site_id) or {}).get("label") or site_id)


def _current_presence(transition: dict[str, Any], sites: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    state = _text(transition.get("state"), 40).lower()
    source = _text(transition.get("source_site_id"), 64).lower()
    destination = _text(transition.get("destination_site_id"), 64).lower()
    temp = transition.get("temporary_context") if isinstance(transition.get("temporary_context"), dict) else None
    if state == "arrived" and destination:
        return {"kind": "site", "site_id": destination, "label": _site_label(sites, destination), "status": "present", "confirmed": True}
    if state == "departing" and source:
        return {"kind": "site", "site_id": source, "label": _site_label(sites, source), "status": "departing", "confirmed": True}
    if state == "temporary_context" and temp:
        return {
            "kind": "temporary_context",
            "context_id": _text(temp.get("id"), 160),
            "label": _text(temp.get("label") or "Temporary context"),
            "status": "temporary_context",
            "confirmed": False,
            "durable_site": False,
            "site_authority": False,
            "confidence": _confidence(temp.get("confidence")),
        }
    if state in {"in_transit", "arriving", "uncertain", "offline"}:
        return {"kind": "transition", "site_id": "", "label": "", "status": state, "confirmed": False}
    return None


def _last_confirmed_site(transition: dict[str, Any], sites: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    state = _text(transition.get("state"), 40).lower()
    source = _text(transition.get("source_site_id"), 64).lower()
    destination = _text(transition.get("destination_site_id"), 64).lower()
    if state == "arrived" and destination:
        return {
            "site_id": destination,
            "label": _site_label(sites, destination),
            "basis": "destination_arrival_confirmed",
            "confirmed_at": int(transition.get("arrived_at") or transition.get("updated_at") or 0),
        }
    if source:
        return {
            "site_id": source,
            "label": _site_label(sites, source),
            "basis": "source_site_before_transition",
            "confirmed_at": int(transition.get("started_at") or 0),
        }
    return None


def _transition_summary(
    transition: dict[str, Any],
    sites: dict[str, dict[str, Any]],
    catalog: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    state = _text(transition.get("state"), 40).lower()
    subject_kind = _text(transition.get("subject_kind") or "mobile_device", 40).lower()
    subject_id = _text(transition.get("subject_id"), 160)
    meta = catalog.get(subject_id.lower()) or {}
    source = _text(transition.get("source_site_id"), 64).lower()
    destination = _text(transition.get("destination_site_id"), 64).lower()
    source_authority = _text((sites.get(source) or {}).get("authority_device_id"), 64).lower()
    is_authority = subject_kind == "mobile_device" and bool(source_authority) and source_authority == subject_id.lower()
    evidence = []
    for item in (transition.get("evidence") if isinstance(transition.get("evidence"), list) else [])[-8:]:
        if not isinstance(item, dict):
            continue
        evidence.append({
            "type": _text(item.get("type"), 80),
            "site_id": _text(item.get("site_id"), 64).lower(),
            "confidence": _confidence(item.get("confidence")),
            "observed_at": max(0, int(item.get("observed_at") or 0)),
            "context_id": _text(item.get("context_id"), 160),
            "context_label": _text(item.get("context_label"), 160),
        })
    return {
        "transition_id": _text(transition.get("transition_id"), 160),
        "subject_kind": subject_kind,
        "subject_id": subject_id,
        "subject_type": _text(meta.get("subject_type") or ("mobile_hardware" if subject_kind == "mobile_device" else "continuity_subject"), 40).lower(),
        "subject_label": _text(meta.get("label") or subject_id),
        "source_site": {"site_id": source, "label": _site_label(sites, source)},
        "destination_site": {"site_id": destination, "label": _site_label(sites, destination)} if destination else None,
        "state": state,
        "active": state in ACTIVE_STATES,
        "confidence": _confidence(transition.get("confidence")),
        "destination_confidence": _confidence(transition.get("destination_confidence")),
        "state_reason": _text(transition.get("state_reason"), 200),
        "started_at": max(0, int(transition.get("started_at") or 0)),
        "state_changed_at": max(0, int(transition.get("state_changed_at") or 0)),
        "updated_at": max(0, int(transition.get("updated_at") or 0)),
        "arrived_at": int(transition["arrived_at"]) if transition.get("arrived_at") is not None else None,
        "offline_since": int(transition["offline_since"]) if transition.get("offline_since") is not None else None,
        "temporary_context": _copy(transition.get("temporary_context")) if isinstance(transition.get("temporary_context"), dict) else None,
        "evidence_count": len(transition.get("evidence") or []),
        "evidence": evidence,
        "last_confirmed_site": _last_confirmed_site(transition, sites),
        "current_presence": _current_presence(transition, sites),
        "destination_presence_confirmed": state == "arrived" and bool(destination),
        "may_claim_present_at_destination": state == "arrived" and bool(destination),
        "authority": {
            "subject_is_source_authority": is_authority,
            "source_authority_device_id": source_authority,
            "authority_transfer": False,
            "authority_unchanged": True,
        },
        "identity": {
            "scope": "stable_mobile_device" if subject_kind == "mobile_device" else "explicit_continuity_subject",
            "cross_site_merge": False,
            "identity_linking": False,
        },
    }


def _history_rows(limit: int = 200) -> list[dict[str, Any]]:
    try:
        with db() as connection:
            rows = connection.execute(
                """
                SELECT transition_id,revision,state,snapshot_json,fingerprint
                FROM tracky_mobile_transition_history
                ORDER BY rowid DESC LIMIT ?
                """,
                (max(1, min(1000, int(limit))),),
            ).fetchall()
    except Exception:
        return []
    out = []
    for row in rows:
        try:
            snapshot = json.loads(row["snapshot_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(snapshot, dict):
            continue
        out.append({
            "transition_id": str(row["transition_id"] or ""),
            "revision": int(row["revision"] or 0),
            "state": str(row["state"] or ""),
            "snapshot": snapshot,
            "fingerprint": str(row["fingerprint"] or ""),
        })
    return out


def build_report(
    operations: dict[str, Any],
    mobile_report: dict[str, Any],
    *,
    history: list[dict[str, Any]] | None = None,
    agent_context: dict[str, Any] | None = None,
    generated_at: str = "",
) -> dict[str, Any]:
    operations = _copy(operations if isinstance(operations, dict) else {})
    mobile_report = _copy(mobile_report if isinstance(mobile_report, dict) else {})
    sites = _site_map(operations)
    catalog = _subject_catalog(operations, agent_context)
    transitions = [
        _transition_summary(row, sites, catalog)
        for row in mobile_report.get("transitions", [])
        if isinstance(row, dict)
    ]
    transitions.sort(key=lambda row: (not row["active"], -(row["state_changed_at"] or row["updated_at"]), row["transition_id"]))
    active = [row for row in transitions if row["active"]]

    timeline = []
    for row in history or []:
        if not isinstance(row, dict):
            continue
        snapshot = row.get("snapshot") if isinstance(row.get("snapshot"), dict) else row
        if not isinstance(snapshot, dict):
            continue
        summary = _transition_summary(snapshot, sites, catalog)
        timeline.append({
            "event_id": _text(row.get("event_id") or f"transition:{summary['transition_id']}:revision:{int(row.get('revision') or snapshot.get('revision') or 0)}", 220),
            "transition_id": summary["transition_id"],
            "revision": max(0, int(row.get("revision") or snapshot.get("revision") or 0)),
            "state": summary["state"],
            "subject_id": summary["subject_id"],
            "subject_label": summary["subject_label"],
            "source_site": summary["source_site"],
            "destination_site": summary["destination_site"],
            "confidence": summary["confidence"],
            "state_reason": summary["state_reason"],
            "occurred_at": max(0, int(snapshot.get("state_changed_at") or snapshot.get("updated_at") or snapshot.get("started_at") or 0)),
            "immutable": True,
            "fingerprint": _text(row.get("fingerprint") or snapshot.get("fingerprint"), 128),
        })
    if not timeline:
        for summary in transitions:
            timeline.append({
                "event_id": f"transition:{summary['transition_id']}:current",
                "transition_id": summary["transition_id"],
                "revision": 0,
                "state": summary["state"],
                "subject_id": summary["subject_id"],
                "subject_label": summary["subject_label"],
                "source_site": summary["source_site"],
                "destination_site": summary["destination_site"],
                "confidence": summary["confidence"],
                "state_reason": summary["state_reason"],
                "occurred_at": summary["state_changed_at"] or summary["updated_at"] or summary["started_at"],
                "immutable": False,
                "fingerprint": "",
            })
    timeline.sort(key=lambda row: (-row["occurred_at"], -row["revision"], row["transition_id"]))

    state_counts = {state: 0 for state in ["departing","in_transit","arriving","uncertain","offline","temporary_context","arrived","canceled"]}
    for row in transitions:
        if row["state"] in state_counts:
            state_counts[row["state"]] += 1

    site_presence = []
    for site in sorted(sites.values(), key=lambda row: row["label"]):
        related = [row for row in transitions if row["source_site"]["site_id"] == site["id"] or (row["destination_site"] or {}).get("site_id") == site["id"]]
        site_presence.append({
            "site_id": site["id"],
            "label": site["label"],
            "health": site["health"],
            "federation_status": site["federation_status"],
            "departing": sum(1 for row in related if row["active"] and row["source_site"]["site_id"] == site["id"] and row["state"] == "departing"),
            "arriving": sum(1 for row in related if row["active"] and (row["destination_site"] or {}).get("site_id") == site["id"] and row["state"] == "arriving"),
            "in_transit_from": sum(1 for row in related if row["active"] and row["source_site"]["site_id"] == site["id"] and row["state"] == "in_transit"),
            "in_transit_to": sum(1 for row in related if row["active"] and (row["destination_site"] or {}).get("site_id") == site["id"] and row["state"] == "in_transit"),
            "offline": sum(1 for row in related if row["active"] and row["state"] == "offline"),
            "recent_arrivals": sum(1 for row in related if row["state"] == "arrived" and (row["destination_site"] or {}).get("site_id") == site["id"]),
        })

    return {
        "protocol": CROSS_SITE_PRESENCE_PROTOCOL,
        "version": TRACKY_CROSS_SITE_PRESENCE_VERSION,
        "schema_version": 1,
        "generated_at": generated_at,
        "active_count": len(active),
        "transition_count": len(transitions),
        "state_counts": state_counts,
        "active_transitions": active,
        "transitions": transitions,
        "site_presence": site_presence,
        "timeline": timeline[:200],
        "history_source": "immutable_transition_revision_history" if history else "current_transition_snapshots",
        "agent_context": {
            "active_count": len(active),
            "transitions": [
                {
                    "transition_id": row["transition_id"],
                    "subject_label": row["subject_label"],
                    "subject_type": row["subject_type"],
                    "state": row["state"],
                    "source_site": row["source_site"],
                    "destination_site": row["destination_site"],
                    "confidence": row["confidence"],
                    "last_confirmed_site": row["last_confirmed_site"],
                    "current_presence": row["current_presence"],
                    "may_claim_present_at_destination": row["may_claim_present_at_destination"],
                    "authority_transfer": False,
                    "cross_site_identity_merge": False,
                }
                for row in active[:12]
            ],
            "destination_claim_rule": "present_at_destination_only_after_arrived",
            "physical_location_invention": False,
        },
        "boundaries": [
            "source-site-transition-authority-remains-authoritative",
            "arrival-is-never-invented",
            "destination-presence-requires-arrived-state",
            "offline-and-temporary-context-are-not-durable-sites",
            "moving-authority-device-does-not-transfer-authority",
            "cross-site-identity-merge-remains-disabled",
            "semantic-only-no-raw-perception",
        ],
        "read_only": True,
        "authority_mutation": False,
        "physical_location_mutation": False,
        "cross_site_identity_merge": False,
        "cloud_role": "relay_and_mirror_only",
    }


def current_report() -> dict[str, Any]:
    operations = tracky_federation_operations.current_report()
    mobile = tracky_mobile_transition.current_report()
    try:
        from . import tracky_federated_agent_context
        agent_context = tracky_federated_agent_context.current_context(refresh=True)
    except Exception:
        agent_context = {}
    history = _history_rows(200)
    return build_report(operations, mobile, history=history, agent_context=agent_context)


def public_capability() -> dict[str, Any]:
    return {
        "version": TRACKY_CROSS_SITE_PRESENCE_VERSION,
        "protocol": CROSS_SITE_PRESENCE_PROTOCOL,
        "states": ["departing","in_transit","arriving","arrived","uncertain","offline","temporary_context","canceled"],
        "transition_history": True,
        "immutable_history_when_available": True,
        "destination_claim_requires_arrived": True,
        "agent_context": True,
        "authority_mutation": False,
        "physical_location_mutation": False,
        "cross_site_identity_merge": False,
        "temporary_context_site_authority": False,
        "read_only": True,
    }
