from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote

from ..database import db
from . import (
    federated_data,
    tracky_federated_world,
    tracky_federation_policy,
    tracky_federation_sync,
    tracky_identity_continuity,
    tracky_mobile_transition,
    tracky_site_topology,
)

TRACKY_FEDERATED_AGENT_CONTEXT_VERSION = "2.78"
FEDERATED_AGENT_CONTEXT_PROTOCOL = "physical_federated_agent_context.v1"

class TrackyFederatedAgentContextError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = int(status_code)

def _text(value: Any, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[: max(1, int(limit))]

def _uuid(value: Any, label: str) -> str:
    try:
        return str(uuid.UUID(str(value or "").strip()))
    except (TypeError, ValueError, AttributeError) as exc:
        raise TrackyFederatedAgentContextError(f"{label} must be a UUID.") from exc

def _confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0

def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise TrackyFederatedAgentContextError("Federated Agent context is not valid JSON.") from exc

def _time_ms(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        number = float(value)
        if number > 0:
            return int(number)
    except (TypeError, ValueError):
        pass
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)

def _now_ms() -> int:
    return int(time.time() * 1000)

def _site_ref_parts(ref: str) -> tuple[str, str]:
    text = str(ref or "")
    if not text.startswith("site:") or "::" not in text:
        return "", ""
    site_id, local = text[5:].split("::", 1)
    try:
        return _uuid(site_id, "member site id"), unquote(local)
    except (TrackyFederatedAgentContextError, ValueError):
        return "", ""

def _entity_ref(site_id: str, entity: dict[str, Any]) -> str:
    ref = str(entity.get("ref") or "")
    if ref:
        return ref
    from urllib.parse import quote
    local_id = str(entity.get("local_id") or entity.get("id") or "")
    return f"site:{site_id}::{quote(local_id, safe='')}" if local_id else ""

def _relation_subject_ref(site_id: str, relation: dict[str, Any]) -> str:
    ref = str(relation.get("subject_ref") or "")
    if ref:
        return ref
    from urllib.parse import quote
    local_id = str(relation.get("subject_local_id") or relation.get("subject_id") or "")
    return f"site:{site_id}::{quote(local_id, safe='')}" if local_id else ""

def _relation_object_ref(site_id: str, relation: dict[str, Any]) -> str:
    ref = str(relation.get("object_ref") or "")
    if ref:
        return ref
    from urllib.parse import quote
    local_id = str(relation.get("object_local_id") or relation.get("object_id") or "")
    return f"site:{site_id}::{quote(local_id, safe='')}" if local_id else ""

def _reconciliation_state() -> str:
    state = federated_data.reconciliation_state("vp3_cloud")
    needs = bool(state.get("needs_reconciliation"))
    error = _text(state.get("last_error"), 500)
    if needs and error:
        return "failed"
    if needs and (state.get("last_disconnect_at") or state.get("last_connected_at") or state.get("last_run_id")):
        return "reconciling"
    return "current"

def _focus_identity(report: dict[str, Any], explicit_id: str | None = None) -> dict[str, Any] | None:
    active_people = [
        item for item in report.get("identities", [])
        if isinstance(item, dict) and item.get("status") == "active" and item.get("entity_type") == "person"
    ]
    if explicit_id:
        target = _uuid(explicit_id, "focus canonical identity id")
        for item in active_people:
            if str(item.get("canonical_identity_id") or "") == target:
                return item
        return None
    return active_people[0] if len(active_people) == 1 else None

def _focus_mobile_subject_ids(focus: dict[str, Any] | None, report: dict[str, Any]) -> list[str]:
    if not focus:
        return []
    focus_id = str(focus.get("canonical_identity_id") or "")
    ids = []
    for item in report.get("transitions", []):
        if not isinstance(item, dict):
            continue
        if str(item.get("subject_kind") or "") != "explicit_continuity_subject":
            continue
        if str(item.get("subject_id") or "") == focus_id:
            ids.append(focus_id)
    return ids

def _peer_status(sync: dict[str, Any], site_id: str, local_site_id: str) -> str:
    if site_id == local_site_id:
        return "local"
    for peer in sync.get("peers", []):
        if not isinstance(peer, dict):
            continue
        if str(peer.get("remote_site_id") or peer.get("site_id") or "") == site_id:
            return _text(peer.get("status") or "unknown", 40).lower()
    return "unknown"

def _site_summary(
    topology_site: dict[str, Any],
    fragment: dict[str, Any] | None,
    sync: dict[str, Any],
    local_site_id: str,
    now_ms: int,
    stale_age_ms: int,
) -> dict[str, Any]:
    site_id = str(topology_site.get("id") or (fragment or {}).get("site_id") or "")
    observed_at = _time_ms((fragment or {}).get("observed_at"))
    age = max(0, now_ms - observed_at) if observed_at else None
    recent = (fragment or {}).get("context", {}).get("recent_changes", [])
    if not isinstance(recent, list):
        recent = []
    return {
        "site_id": site_id,
        "label": _text(topology_site.get("label") or (fragment or {}).get("context", {}).get("site"), 120),
        "local": site_id == local_site_id,
        "status": _text(topology_site.get("status") or "active", 30).lower(),
        "authority_device_id": str(topology_site.get("authority_device_id") or ""),
        "authority_epoch": int(topology_site.get("authority_epoch") or 0),
        "world_revision": int((fragment or {}).get("revision") or 0),
        "world_observed_at": (fragment or {}).get("observed_at"),
        "freshness": "unknown" if age is None else ("stale" if age > stale_age_ms else "current"),
        "age_ms": age,
        "sync_status": _peer_status(sync, site_id, local_site_id),
        "recent_changes": [_text(item, 240) for item in recent[-5:] if _text(item, 240)],
    }

def _location_candidates(
    focus: dict[str, Any] | None,
    world: dict[str, Any],
    now_ms: int,
    current_age_ms: int,
) -> list[dict[str, Any]]:
    if not focus:
        return []
    members = {str(ref) for ref in focus.get("members", [])}
    out: list[dict[str, Any]] = []
    for fragment in world.get("sites", []):
        if not isinstance(fragment, dict):
            continue
        site_id = str(fragment.get("site_id") or "")
        member_refs = [ref for ref in members if _site_ref_parts(ref)[0] == site_id]
        if not member_refs:
            continue
        best: dict[str, Any] | None = None
        for ref in member_refs:
            entity = next(
                (item for item in fragment.get("entities", []) if isinstance(item, dict) and _entity_ref(site_id, item) == ref),
                None,
            )
            entity_at = _time_ms((entity or {}).get("observed_at") or fragment.get("observed_at"))
            entity_age = max(0, now_ms - entity_at) if entity_at else None
            entity_state = _text((entity or {}).get("state") or "unknown", 40).lower()
            entity_conf = _confidence((entity or {}).get("confidence"))
            entity_current = (
                entity_age is not None
                and entity_age <= current_age_ms
                and entity_state in {"observed", "user-confirmed"}
            )
            relations = [
                item for item in fragment.get("relations", [])
                if isinstance(item, dict)
                and _relation_subject_ref(site_id, item) == ref
                and _text(item.get("predicate"), 60).lower() in {"located_in", "present_in", "located_on"}
                and _text(item.get("temporal_state") or "current", 30).lower() in {"current", "inferred"}
            ]
            for relation in relations:
                at = _time_ms(relation.get("as_of") or entity_at)
                age = max(0, now_ms - at) if at else None
                score = (
                    _confidence(relation.get("confidence")) * 0.65
                    + entity_conf * 0.35
                    - (0.35 if age is not None and age > current_age_ms else 0.0)
                )
                candidate = {
                    "site_id": site_id,
                    "member_ref": ref,
                    "local_entity_id": _site_ref_parts(ref)[1],
                    "object_ref": _relation_object_ref(site_id, relation),
                    "predicate": _text(relation.get("predicate"), 60),
                    "confidence": _confidence(score),
                    "observed_at": at or entity_at,
                    "age_ms": age,
                    "evidence": "current_relation",
                }
                if best is None or candidate["confidence"] > best["confidence"] or candidate["observed_at"] > best["observed_at"]:
                    best = candidate
            if not relations and entity_current:
                candidate = {
                    "site_id": site_id,
                    "member_ref": ref,
                    "local_entity_id": _site_ref_parts(ref)[1],
                    "object_ref": "",
                    "predicate": "observed_at_site",
                    "confidence": entity_conf,
                    "observed_at": entity_at,
                    "age_ms": entity_age,
                    "evidence": "current_entity_observation",
                }
                if best is None or candidate["confidence"] > best["confidence"] or candidate["observed_at"] > best["observed_at"]:
                    best = candidate
        if best is not None:
            out.append(best)
    return sorted(out, key=lambda item: (-float(item["confidence"]), -int(item["observed_at"] or 0)))

def _select_location(
    candidates: list[dict[str, Any]],
    threshold: float = 0.65,
    conflict_delta: float = 0.12,
) -> dict[str, Any]:
    viable = [item for item in candidates if float(item.get("confidence") or 0) >= threshold]
    if not viable:
        return {"state": "unknown", "current": None, "conflicts": []}
    if len(viable) == 1:
        return {"state": "present", "current": viable[0], "conflicts": []}
    top, second = viable[0], viable[1]
    if second["site_id"] != top["site_id"] and abs(float(top["confidence"]) - float(second["confidence"])) <= conflict_delta:
        return {"state": "uncertain", "current": None, "conflicts": viable[:4]}
    return {
        "state": "present",
        "current": top,
        "conflicts": [
            item for item in viable
            if item["site_id"] != top["site_id"]
            and float(item["confidence"]) >= float(top["confidence"]) - conflict_delta
        ],
    }

def _active_focus_transition(
    mobile: dict[str, Any],
    focus_subject_ids: list[str],
) -> dict[str, Any] | None:
    ids = set(focus_subject_ids)
    if not ids:
        return None
    active = [
        item for item in mobile.get("transitions", [])
        if isinstance(item, dict)
        and str(item.get("subject_id") or "") in ids
        and str(item.get("state") or "") not in {"arrived", "canceled"}
    ]
    active.sort(key=lambda item: int(item.get("updated_at") or item.get("state_changed_at") or 0), reverse=True)
    return active[0] if active else None

def _source_material(
    topology: dict[str, Any],
    world: dict[str, Any],
    sync: dict[str, Any],
    mobile: dict[str, Any],
    identity: dict[str, Any],
    reconciliation: str,
    focus: dict[str, Any] | None,
    now_ms: int,
    current_age_ms: int,
    stale_age_ms: int,
) -> dict[str, Any]:
    return {
        "topology_revision": int(topology.get("revision") or 0),
        "sites": [
            {
                "site_id": str(item.get("site_id") or ""),
                "revision": int(item.get("revision") or 0),
                "authority_device_id": str(item.get("authority_device_id") or ""),
                "authority_epoch": int(item.get("authority_epoch") or 0),
                "fingerprint": str(item.get("fingerprint") or ""),
            }
            for item in world.get("sites", [])
            if isinstance(item, dict)
        ],
        "sync": [
            {
                "site_id": str(item.get("remote_site_id") or ""),
                "status": str(item.get("status") or ""),
                "revision": int(item.get("last_received_revision") or 0),
                "authority_epoch": int(item.get("last_received_authority_epoch") or 0),
                "quarantined_count": int(item.get("quarantined_count") or 0),
            }
            for item in sync.get("peers", [])
            if isinstance(item, dict)
        ],
        "mobile": [
            {
                "transition_id": str(item.get("transition_id") or ""),
                "state": str(item.get("state") or ""),
                "revision": int(item.get("revision") or 0),
                "updated_at": int(item.get("updated_at") or item.get("state_changed_at") or 0),
            }
            for item in mobile.get("transitions", [])
            if isinstance(item, dict)
        ],
        "identities": [
            {
                "id": str(item.get("canonical_identity_id") or ""),
                "status": str(item.get("status") or ""),
                "revision": int(item.get("revision") or 0),
                "members": list(item.get("members") or []),
            }
            for item in identity.get("identities", [])
            if isinstance(item, dict)
        ],
        "reconciliation": reconciliation,
        "focus_identity_id": str((focus or {}).get("canonical_identity_id") or ""),
        "freshness_boundaries": {
            "sites": [
                {
                    "site_id": str(item.get("site_id") or ""),
                    "stale": (
                        bool(_time_ms(item.get("observed_at")))
                        and now_ms - _time_ms(item.get("observed_at")) > stale_age_ms
                    ),
                }
                for item in world.get("sites", [])
                if isinstance(item, dict)
            ],
            "focus_members": [
                {
                    "ref": str(ref),
                    "site_id": _site_ref_parts(str(ref))[0],
                    "entity_current": any(
                        isinstance(entity, dict)
                        and _entity_ref(str(site.get("site_id") or ""), entity) == str(ref)
                        and bool(_time_ms(entity.get("observed_at") or site.get("observed_at")))
                        and now_ms - _time_ms(entity.get("observed_at") or site.get("observed_at")) <= current_age_ms
                        and _text(entity.get("state") or "unknown", 40).lower() in {"observed", "user-confirmed"}
                        for site in world.get("sites", [])
                        if isinstance(site, dict)
                        for entity in site.get("entities", [])
                    ),
                    "relation_current": any(
                        isinstance(relation, dict)
                        and _relation_subject_ref(str(site.get("site_id") or ""), relation) == str(ref)
                        and _text(relation.get("predicate"), 60).lower() in {"located_in", "present_in", "located_on"}
                        and _text(relation.get("temporal_state") or "current", 30).lower() in {"current", "inferred"}
                        and bool(_time_ms(relation.get("as_of")))
                        and now_ms - _time_ms(relation.get("as_of")) <= current_age_ms
                        for site in world.get("sites", [])
                        if isinstance(site, dict)
                        for relation in site.get("relations", [])
                    ),
                }
                for ref in ((focus or {}).get("members") or [])
            ],
        },
    }

def _prior_snapshot() -> dict[str, Any] | None:
    with db() as connection:
        row = connection.execute(
            "SELECT revision,fingerprint,context_json FROM tracky_federated_agent_context WHERE id=1"
        ).fetchone()
    if row is None or int(row["revision"] or 0) < 1:
        return None
    try:
        context = json.loads(row["context_json"] or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        context = {}
    return context if isinstance(context, dict) else None

def _changed_elsewhere(
    sites: list[dict[str, Any]],
    current_site_id: str,
    previous: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    previous_revisions = previous.get("site_revisions") if isinstance(previous, dict) else None
    if not isinstance(previous_revisions, dict) or not previous_revisions:
        return []
    out = []
    for site in sites:
        if site["site_id"] == current_site_id:
            continue
        previous_revision = int(previous_revisions.get(site["site_id"], site["world_revision"]) or 0)
        if previous_revision == int(site["world_revision"]):
            continue
        out.append({
            "site_id": site["site_id"],
            "label": site["label"],
            "from_revision": previous_revision,
            "to_revision": int(site["world_revision"]),
            "freshness": site["freshness"],
            "sync_status": site["sync_status"],
            "recent_changes": site["recent_changes"],
        })
    return out[:12]

def _agent_state(
    reconciliation: str,
    location: dict[str, Any],
    sites: list[dict[str, Any]],
    authority_site_id: str,
) -> str:
    if reconciliation == "reconciling":
        return "reconciling"
    if reconciliation == "failed":
        return "failed"
    authority_site = next((item for item in sites if item["site_id"] == authority_site_id), None)
    if authority_site_id and (
        authority_site is None
        or not authority_site.get("authority_device_id")
        or int(authority_site.get("authority_epoch") or 0) < 1
    ):
        return "failed"
    if location["state"] == "uncertain":
        return "stale"
    if authority_site and authority_site.get("freshness") == "stale":
        return "stale"
    if authority_site and authority_site.get("sync_status") in {"quarantined", "failed"}:
        return "stale"
    return "current"

def _build_context(
    *,
    focus_identity_id: str | None = None,
    now_ms: int | None = None,
    current_age_ms: int = 120000,
    stale_age_ms: int = 300000,
    previous: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    now = int(now_ms if now_ms is not None else _now_ms())
    topology = tracky_site_topology.current_topology()
    world = tracky_federation_policy.filter_world_report_for_local(
        tracky_federated_world.current_report()
    )
    sync = tracky_federation_sync.status()
    mobile = tracky_federation_policy.filter_mobile_report_for_local(
        tracky_mobile_transition.current_report()
    )
    identity = tracky_federation_policy.filter_identity_report_for_local(
        tracky_identity_continuity.current_report()
    )
    local_site_id = str(sync.get("local_site_id") or "")
    reconciliation = _reconciliation_state()
    focus = _focus_identity(identity, focus_identity_id)
    focus_id = str((focus or {}).get("canonical_identity_id") or "")
    focus_mobile_ids = _focus_mobile_subject_ids(focus, mobile)

    source_material = _source_material(
        topology, world, sync, mobile, identity, reconciliation, focus,
        now, max(1000, current_age_ms), max(1000, stale_age_ms),
    )
    source_fingerprint = hashlib.sha256(_json(source_material).encode("utf-8")).hexdigest()

    topology_by_id = {
        str(item.get("id") or ""): item for item in topology.get("sites", []) if isinstance(item, dict)
    }
    world_by_id = {
        str(item.get("site_id") or ""): item for item in world.get("sites", []) if isinstance(item, dict)
    }
    site_ids = sorted(set(topology_by_id) | set(world_by_id))
    sites = [
        _site_summary(
            topology_by_id.get(site_id, {"id": site_id}),
            world_by_id.get(site_id),
            sync,
            local_site_id,
            now,
            max(1000, stale_age_ms),
        )
        for site_id in site_ids
    ]

    candidates = _location_candidates(focus, world, now, max(1000, current_age_ms))
    location = _select_location(candidates)
    transition = _active_focus_transition(mobile, focus_mobile_ids)

    physical_state = str(location["state"])
    authority_site_id = str((location.get("current") or {}).get("site_id") or local_site_id or "")
    transition_summary = None
    if transition is not None:
        state = _text(transition.get("state"), 40).lower()
        authority_site_id = str(transition.get("source_site_id") or authority_site_id)
        temporary = transition.get("temporary_context") if isinstance(transition.get("temporary_context"), dict) else None
        transition_summary = {
            "transition_id": _text(transition.get("transition_id"), 160),
            "subject_kind": _text(transition.get("subject_kind"), 40),
            "subject_id": _text(transition.get("subject_id"), 160),
            "source_site_id": _text(transition.get("source_site_id"), 64),
            "destination_site_id": _text(transition.get("destination_site_id"), 64),
            "state": state,
            "confidence": _confidence(transition.get("confidence")),
            "temporary_context": {
                "id": _text(temporary.get("id"), 160),
                "label": _text(temporary.get("label"), 160),
                "confidence": _confidence(temporary.get("confidence")),
                "durable_site": False,
                "site_authority": False,
            } if temporary else None,
            "offline_since": transition.get("offline_since"),
        }
        if state in {"departing", "in_transit", "arriving", "offline", "temporary_context", "uncertain"}:
            physical_state = state
            if state != "arriving":
                location["current"] = None

    authority_site = next((item for item in sites if item["site_id"] == authority_site_id), None)
    current_site_id = str((location.get("current") or {}).get("site_id") or "")
    changed_elsewhere = _changed_elsewhere(sites, current_site_id, previous)
    site_revisions = {item["site_id"]: int(item["world_revision"]) for item in sites}
    agent_state = _agent_state(reconciliation, location, sites, authority_site_id)

    current = location.get("current")
    context = {
        "protocol": FEDERATED_AGENT_CONTEXT_PROTOCOL,
        "schema_version": 1,
        "generated_at": now,
        "agent_state": agent_state,
        "physical_state": physical_state,
        "local_site_id": local_site_id,
        "current_site": {
            "site_id": current["site_id"],
            "label": next((item["label"] for item in sites if item["site_id"] == current["site_id"]), ""),
            "member_ref": current["member_ref"],
            "location_ref": current["object_ref"],
            "confidence": current["confidence"],
            "observed_at": current["observed_at"],
            "age_ms": current["age_ms"],
            "why": current["evidence"],
        } if current else None,
        "location_conflicts": [
            {
                "site_id": item["site_id"],
                "member_ref": item["member_ref"],
                "location_ref": item["object_ref"],
                "confidence": item["confidence"],
                "observed_at": item["observed_at"],
                "why": item["evidence"],
            }
            for item in location["conflicts"]
        ],
        "authority": {
            "site_id": authority_site_id,
            "device_id": str((authority_site or {}).get("authority_device_id") or ""),
            "epoch": int((authority_site or {}).get("authority_epoch") or 0),
            "basis": "active_mobile_transition" if transition else (
                "current_site" if current else "local_site_fallback"
            ),
        },
        "focus_identity": {
            "canonical_identity_id": focus_id,
            "entity_type": str(focus.get("entity_type") or ""),
            "aliases": list(focus.get("aliases") or [])[:8],
            "member_site_ids": sorted({
                _site_ref_parts(str(ref))[0]
                for ref in focus.get("members", [])
                if _site_ref_parts(str(ref))[0]
            }),
        } if focus else None,
        "active_mobile_transition": transition_summary,
        "changed_elsewhere": changed_elsewhere,
        "sites": sites[:32],
        "site_revisions": site_revisions,
        "explainability": {
            "location_candidate_count": len(candidates),
            "current_location_selected": bool(current),
            "conflict_count": len(location["conflicts"]),
            "reconciliation_state": reconciliation,
            "no_location_invention": (
                not current
                and physical_state in {"unknown", "uncertain", "in_transit", "offline", "temporary_context"}
            ),
            "focus_selection": "explicit" if focus_identity_id and focus else (
                "single_active_person" if focus else "unresolved"
            ),
            "mobile_transition_binding": (
                "explicit_continuity_subject" if transition else "none"
            ),
        },
        "semantic_only": True,
        "authority_assignment": "local_only",
        "cloud_read_only": True,
    }
    return context, source_fingerprint

def refresh_context(
    *,
    focus_identity_id: str | None = None,
    now_ms: int | None = None,
    current_age_ms: int = 120000,
    stale_age_ms: int = 300000,
) -> dict[str, Any]:
    previous = _prior_snapshot()
    context, fingerprint = _build_context(
        focus_identity_id=focus_identity_id,
        now_ms=now_ms,
        current_age_ms=current_age_ms,
        stale_age_ms=stale_age_ms,
        previous=previous,
    )
    with db() as connection:
        row = connection.execute(
            "SELECT revision,fingerprint,context_json FROM tracky_federated_agent_context WHERE id=1"
        ).fetchone()
        prior_revision = int(row["revision"] or 0) if row else 0
        prior_fingerprint = str(row["fingerprint"] or "") if row else ""
        if prior_revision > 0 and prior_fingerprint == fingerprint:
            try:
                stored = json.loads(row["context_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                stored = {}
            if isinstance(stored, dict) and stored:
                stored["revision"] = prior_revision
                stored["fingerprint"] = fingerprint
                return stored

        revision = prior_revision + 1
        context["revision"] = revision
        context["fingerprint"] = fingerprint
        encoded = _json(context)
        authority = context["authority"]
        current_site = context.get("current_site") or {}
        focus = context.get("focus_identity") or {}
        connection.execute(
            """
            UPDATE tracky_federated_agent_context
            SET revision=?,fingerprint=?,agent_state=?,physical_state=?,focus_identity_id=?,
                authority_site_id=?,authority_device_id=?,authority_epoch=?,current_site_id=?,
                context_json=?,generated_at_ms=?,updated_at=CURRENT_TIMESTAMP
            WHERE id=1
            """,
            (
                revision, fingerprint, context["agent_state"], context["physical_state"],
                focus.get("canonical_identity_id") or None,
                authority.get("site_id") or None, authority.get("device_id") or None,
                int(authority.get("epoch") or 0), current_site.get("site_id") or None,
                encoded, int(context["generated_at"]),
            ),
        )
        connection.execute(
            """
            INSERT INTO tracky_federated_agent_context_history(
                revision,fingerprint,agent_state,physical_state,authority_site_id,
                current_site_id,context_json,generated_at_ms
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                revision, fingerprint, context["agent_state"], context["physical_state"],
                authority.get("site_id") or None, current_site.get("site_id") or None,
                encoded, int(context["generated_at"]),
            ),
        )
        connection.execute(
            """
            DELETE FROM tracky_federated_agent_context_history
            WHERE id NOT IN (
              SELECT id FROM tracky_federated_agent_context_history
              ORDER BY revision DESC LIMIT 100
            )
            """
        )
    return context

def _attach_sync_visibility(context: dict[str, Any]) -> dict[str, Any]:
    output = dict(context if isinstance(context, dict) else {})
    try:
        from . import tracky_sync_visibility
        visibility = tracky_sync_visibility.current_report()
        agent = visibility.get("agent_context") if isinstance(visibility.get("agent_context"), dict) else {}
        output["federation_sync_visibility"] = {
            "protocol": visibility.get("protocol") or "",
            "state": agent.get("state") or visibility.get("overall_state") or "unknown",
            "summary": agent.get("summary") or "",
            "alerts": list(agent.get("alerts") or [])[:16],
            "cloud_can_mark_destination_current": False,
            "no_remote_authority_promotion": True,
        }
    except Exception:
        output["federation_sync_visibility"] = {
            "protocol": "",
            "state": "unknown",
            "summary": "Federation sync visibility is unavailable.",
            "alerts": [],
            "cloud_can_mark_destination_current": False,
            "no_remote_authority_promotion": True,
        }
    try:
        from . import tracky_federation_access_operations
        access = tracky_federation_access_operations.current_report()
        agent_access = access.get("agent_context") if isinstance(access.get("agent_context"), dict) else {}
        output["federation_access_operations"] = {
            "protocol": access.get("protocol") or "",
            "policy_revision": int(agent_access.get("policy_revision") or 0),
            "revocation_epoch": int(agent_access.get("revocation_epoch") or 0),
            "active_revocations": int(agent_access.get("active_revocations") or 0),
            "stale_grants_suppressed": int(agent_access.get("stale_grants_suppressed") or 0),
            "summary": agent_access.get("summary") or "",
            "revocation_wins": True,
            "cloud_read_only": True,
        }
    except Exception:
        output["federation_access_operations"] = {
            "protocol": "",
            "policy_revision": 0,
            "revocation_epoch": 0,
            "active_revocations": 0,
            "stale_grants_suppressed": 0,
            "summary": "Federation access policy is unavailable.",
            "revocation_wins": True,
            "cloud_read_only": True,
        }
    try:
        from . import tracky_federation_agent_health
        health = tracky_federation_agent_health.current_report()
        agent_health = health.get("agent_context") if isinstance(health.get("agent_context"), dict) else {}
        output["federation_agent_health"] = {
            "protocol": health.get("protocol") or "",
            "overall_state": agent_health.get("overall_state") or health.get("overall_state") or "unknown",
            "sites": list(agent_health.get("sites") or [])[:32],
            "active_issues": list(agent_health.get("active_issues") or [])[:24],
            "summary": agent_health.get("summary") or "",
            "relay_health": health.get("relay_health") if isinstance(health.get("relay_health"), dict) else {},
            "recovery_requires_authoritative_reconciliation": True,
            "connectivity_returned_is_not_recovery": True,
        }
    except Exception:
        output["federation_agent_health"] = {
            "protocol": "",
            "overall_state": "unknown",
            "sites": [],
            "active_issues": [],
            "summary": "Federation health is unavailable.",
            "relay_health": {},
            "recovery_requires_authoritative_reconciliation": True,
            "connectivity_returned_is_not_recovery": True,
        }
    return output


def current_context(*, refresh: bool = True) -> dict[str, Any]:
    if refresh:
        return _attach_sync_visibility(refresh_context())
    with db() as connection:
        row = connection.execute(
            "SELECT revision,fingerprint,context_json FROM tracky_federated_agent_context WHERE id=1"
        ).fetchone()
    if row is None or int(row["revision"] or 0) < 1:
        return refresh_context()
    try:
        context = json.loads(row["context_json"] or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        context = {}
    if not isinstance(context, dict) or not context:
        return refresh_context()
    context["revision"] = int(row["revision"] or 0)
    context["fingerprint"] = str(row["fingerprint"] or "")
    return _attach_sync_visibility(context)

def cloud_projection() -> dict[str, Any]:
    context = current_context(refresh=True)
    sites = [
        {
            "site_id": item["site_id"],
            "label": item["label"],
            "status": item["status"],
            "authority_device_id": item["authority_device_id"],
            "authority_epoch": item["authority_epoch"],
            "world_revision": item["world_revision"],
            "freshness": item["freshness"],
            "sync_status": item["sync_status"],
            "recent_changes": item["recent_changes"],
        }
        for item in context.get("sites", [])
    ]
    return {
        **{key: value for key, value in context.items() if key != "sites"},
        "sites": sites,
        "summary_only": True,
        "cloud_read_only": True,
        "context_mutation_authority": False,
        "site_authority_mutation": False,
    }

def public_capability() -> dict[str, Any]:
    return {
        "version": TRACKY_FEDERATED_AGENT_CONTEXT_VERSION,
        "protocol": FEDERATED_AGENT_CONTEXT_PROTOCOL,
        "derived_only": True,
        "states": ["current", "reconciling", "stale", "failed"],
        "physical_states": [
            "present", "unknown", "uncertain", "departing", "in_transit",
            "arriving", "offline", "temporary_context",
        ],
        "no_location_invention": True,
        "context_mutation_authority": False,
        "site_authority_mutation": False,
        "cloud_read_only": True,
    }
