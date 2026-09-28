from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote, unquote

from . import (
    tracky_federated_agent_context,
    tracky_federated_world,
    tracky_federation_operations,
    tracky_federation_policy,
)

TRACKY_PHYSICAL_WORLD_DASHBOARD_VERSION = "2.80"
PHYSICAL_WORLD_DASHBOARD_PROTOCOL = "physical_world_dashboard.v1"
ROOM_TYPES = {"room", "area", "zone", "space", "environment"}
PERSON_TYPES = {"person"}
DEVICE_TYPES = {"device", "sensor", "camera", "appliance", "hardware"}
LOCATION_PREDICATES = {"located_in", "present_in", "located_on", "inside", "in_room"}


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _text(value: Any, limit: int = 160) -> str:
    return " ".join(str(value or "").split())[: max(1, int(limit))]


def _confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _site_id(row: dict[str, Any] | None) -> str:
    row = row or {}
    return _text(row.get("site_id") or row.get("id"), 64).lower()


def _category(entity_type: str) -> str:
    if entity_type in ROOM_TYPES:
        return "rooms"
    if entity_type in PERSON_TYPES:
        return "people"
    if entity_type in DEVICE_TYPES:
        return "devices"
    return "objects"


def _entity_ref(site_id: str, entity: dict[str, Any]) -> str:
    existing = _text(entity.get("ref"), 360)
    if existing:
        return existing
    local_id = _text(entity.get("local_id") or entity.get("id"), 160)
    return f"site:{site_id}::{quote(local_id, safe='')}"


def _ref_local_id(ref: str) -> str:
    marker = "::"
    if marker not in ref:
        return ""
    try:
        return unquote(ref.split(marker, 1)[1])
    except Exception:
        return ref.split(marker, 1)[1]


def _location(
    site_id: str,
    entity: dict[str, Any],
    relations: list[dict[str, Any]],
    entity_by_local: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    ref = _entity_ref(site_id, entity)
    local_id = _text(entity.get("local_id") or entity.get("id"), 160)
    candidates = []
    for relation in relations:
        if not isinstance(relation, dict):
            continue
        predicate = _text(relation.get("predicate"), 60).lower()
        if predicate not in LOCATION_PREDICATES:
            continue
        subject_ref = _text(relation.get("subject_ref"), 360)
        if not subject_ref:
            subject_local = _text(relation.get("subject_local_id"), 160)
            subject_ref = f"site:{site_id}::{quote(subject_local, safe='')}"
        if subject_ref != ref and _text(relation.get("subject_local_id"), 160) != local_id:
            continue
        object_ref = _text(relation.get("object_ref"), 360)
        object_local = _text(relation.get("object_local_id"), 160)
        if not object_ref and object_local:
            object_ref = f"site:{site_id}::{quote(object_local, safe='')}"
        if not object_local:
            object_local = _ref_local_id(object_ref)
        if not object_ref:
            continue
        target = entity_by_local.get(object_local) or {}
        temporal = _text(relation.get("temporal_state") or "unknown", 30).lower()
        candidates.append({
            "location_ref": object_ref,
            "location_local_id": object_local,
            "location_label": _text(target.get("label") or target.get("name") or object_local),
            "predicate": predicate,
            "confidence": _confidence(relation.get("confidence")),
            "temporal_state": temporal,
            "as_of": max(0, int(relation.get("as_of") or 0)),
        })
    candidates.sort(
        key=lambda row: (
            1 if row["temporal_state"] in {"current", "inferred"} else 0,
            row["confidence"],
            row["as_of"],
        ),
        reverse=True,
    )
    return candidates[0] if candidates else None


def _entity(
    site_id: str,
    raw: dict[str, Any],
    relations: list[dict[str, Any]],
    entity_by_local: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    entity_type = _text(raw.get("type") or "entity", 40).lower() or "entity"
    location = _location(site_id, raw, relations, entity_by_local)
    state = _text(raw.get("state") or "unknown", 30).lower()
    observed_at = max(0, int(raw.get("observed_at") or 0))
    entity_confidence = _confidence(raw.get("confidence"))
    confidence = (
        _confidence((entity_confidence * 0.4) + (location["confidence"] * 0.6))
        if location else entity_confidence
    )
    return {
        "ref": _entity_ref(site_id, raw),
        "local_id": _text(raw.get("local_id") or raw.get("id"), 160),
        "site_id": site_id,
        "type": entity_type,
        "category": _category(entity_type),
        "label": _text(raw.get("label") or raw.get("name") or raw.get("local_id") or raw.get("id") or entity_type),
        "state": state,
        "confidence": confidence,
        "observed_at": observed_at,
        "current": state in {"observed", "user-confirmed"} and (
            location is None or location["temporal_state"] in {"current", "inferred"}
        ),
        "last_known": state == "last-known" or observed_at > 0,
        "location": location,
        "identity_scope": "site_local",
    }


def _select_site(
    requested: str,
    operations: dict[str, Any],
    agent_context: dict[str, Any],
    site_ids: list[str],
) -> tuple[str, str, bool]:
    requested = _text(requested, 64).lower()
    available = set(site_ids)
    current = _text((agent_context.get("current_site") or {}).get("site_id"), 64).lower()
    local = _text(operations.get("local_site_id"), 64).lower()
    if requested and requested in available:
        return requested, "explicit_user_selection", True
    if current and current in available:
        return current, "current_agent_site", not bool(requested)
    if local and local in available:
        return local, "local_site_fallback", not bool(requested)
    if site_ids:
        return site_ids[0], "first_authorized_site", not bool(requested)
    return "", "none", not bool(requested)


def build_dashboard(
    operations: dict[str, Any],
    federated_world: dict[str, Any],
    agent_context: dict[str, Any],
    *,
    selected_site_id: str = "",
    generated_at: str = "",
) -> dict[str, Any]:
    operations = _copy(operations if isinstance(operations, dict) else {})
    federated_world = _copy(federated_world if isinstance(federated_world, dict) else {})
    agent_context = _copy(agent_context if isinstance(agent_context, dict) else {})

    op_sites = [row for row in operations.get("sites", []) if isinstance(row, dict)]
    world_sites = [row for row in federated_world.get("sites", []) if isinstance(row, dict)]
    op_by_id = {_site_id(row): row for row in op_sites if _site_id(row)}
    world_by_id = {_site_id(row): row for row in world_sites if _site_id(row)}
    site_ids = sorted(set(op_by_id) | set(world_by_id))
    selected, basis, request_available = _select_site(
        selected_site_id, operations, agent_context, site_ids
    )

    fragment = world_by_id.get(selected) or {}
    relations = [row for row in fragment.get("relations", []) if isinstance(row, dict)]
    raw_entities = [row for row in fragment.get("entities", []) if isinstance(row, dict)]
    entity_by_local = {
        _text(row.get("local_id") or row.get("id"), 160): row
        for row in raw_entities
        if _text(row.get("local_id") or row.get("id"), 160)
    }
    normalized = [_entity(selected, row, relations, entity_by_local) for row in raw_entities]
    groups: dict[str, list[dict[str, Any]]] = {
        "rooms": [], "people": [], "objects": [], "devices": []
    }
    for row in normalized:
        groups[row["category"]].append(row)
    for items in groups.values():
        items.sort(key=lambda row: (-int(bool(row["current"])), -float(row["confidence"]), row["label"]))

    hardware_units = []
    selected_op = op_by_id.get(selected) or {}
    authority_device_id = _text(
        (selected_op.get("authority") or {}).get("device_id")
        or selected_op.get("authority_device_id"),
        64,
    ).lower()
    for device in operations.get("devices", []):
        if not isinstance(device, dict) or _site_id(device) != selected:
            continue
        hardware_units.append({
            "id": _text(device.get("id") or device.get("device_id"), 64).lower(),
            "label": _text(device.get("label") or device.get("hardware_profile_label") or device.get("hardware_profile") or "Device"),
            "hardware_profile": _text(device.get("hardware_profile") or "custom", 40).lower(),
            "hardware_profile_label": _text(device.get("hardware_profile_label") or device.get("hardware_profile") or "Custom", 80),
            "trust_state": _text(device.get("trust_state") or "unknown", 24).lower(),
            "runtime_status": _text(device.get("runtime_status") or "unknown", 32).lower(),
            "version": _text(device.get("version"), 64),
            "is_authority": _text(device.get("id") or device.get("device_id"), 64).lower() == authority_device_id,
        })
    hardware_units.sort(key=lambda row: row["label"])

    site_options = []
    for site_id in site_ids:
        op = op_by_id.get(site_id) or {}
        world = world_by_id.get(site_id) or {}
        counts = {"rooms": 0, "people": 0, "objects": 0, "devices": 0}
        for entity in world.get("entities", []):
            if isinstance(entity, dict):
                counts[_category(_text(entity.get("type") or "entity", 40).lower())] += 1
        site_options.append({
            "site_id": site_id,
            "label": _text(op.get("label") or (world.get("context") or {}).get("site") or site_id),
            "selected": site_id == selected,
            "health": _text(op.get("health") or "unknown", 24).lower(),
            "federation_status": _text((op.get("federation") or {}).get("status") or "unknown", 24).lower(),
            "authority_device_id": _text((op.get("authority") or {}).get("device_id") or op.get("authority_device_id"), 64).lower(),
            "authority_epoch": max(0, int((op.get("authority") or {}).get("epoch") or op.get("authority_epoch") or 0)),
            "world_revision": max(0, int(world.get("revision") or 0)),
            "counts": counts,
        })

    physical_current_site = _text((agent_context.get("current_site") or {}).get("site_id"), 64).lower()
    issues = []
    if selected_site_id and not request_available:
        issues.append({"severity": "degraded", "code": "requested_site_not_available"})

    return {
        "protocol": PHYSICAL_WORLD_DASHBOARD_PROTOCOL,
        "version": TRACKY_PHYSICAL_WORLD_DASHBOARD_VERSION,
        "schema_version": 1,
        "generated_at": generated_at,
        "selected_site": {
            "site_id": selected,
            "label": _text(selected_op.get("label") or (fragment.get("context") or {}).get("site")),
            "basis": basis,
            "health": _text(selected_op.get("health") or "unknown", 24).lower(),
            "federation_status": _text((selected_op.get("federation") or {}).get("status") or "unknown", 24).lower(),
            "world_revision": max(0, int(fragment.get("revision") or 0)),
            "observed_at": fragment.get("observed_at") or "",
        },
        "site_options": site_options,
        "counts": {
            "rooms": len(groups["rooms"]),
            "people": len(groups["people"]),
            "objects": len(groups["objects"]),
            "world_devices": len(groups["devices"]),
            "hardware_units": len(hardware_units),
        },
        "rooms": groups["rooms"],
        "people": groups["people"],
        "objects": groups["objects"],
        "world_devices": groups["devices"],
        "hardware_units": hardware_units,
        "agent_context": {
            "view_site_id": selected,
            "view_basis": basis,
            "physical_current_site_id": physical_current_site,
            "physical_state": _text(agent_context.get("physical_state") or "unknown", 40).lower(),
            "agent_state": _text(agent_context.get("agent_state") or "unknown", 40).lower(),
            "follows_selected_site": True,
            "view_only": True,
            "changes_physical_authority": False,
            "changes_physical_location": False,
        },
        "issues": issues,
        "semantic_only": True,
        "permission_filtered_input": True,
        "identity_scope": "site_local",
        "cross_site_identity_merge": False,
        "authority_assignment": "origin_only",
        "cloud_role": "relay_and_mirror_only",
    }


def current_report(selected_site_id: str = "") -> dict[str, Any]:
    operations = tracky_federation_operations.current_report()
    world = tracky_federation_policy.filter_world_report_for_local(
        tracky_federated_world.current_report()
    )
    try:
        agent_context = tracky_federated_agent_context.current_context(refresh=True)
    except Exception:
        agent_context = {}
    dashboard = build_dashboard(
        operations,
        world,
        agent_context,
        selected_site_id=selected_site_id,
    )
    try:
        from . import tracky_sync_visibility
        dashboard = tracky_sync_visibility.annotate_dashboard(
            dashboard,
            tracky_sync_visibility.current_report(),
        )
    except Exception:
        pass
    return dashboard


def public_capability() -> dict[str, Any]:
    return {
        "version": TRACKY_PHYSICAL_WORLD_DASHBOARD_VERSION,
        "protocol": PHYSICAL_WORLD_DASHBOARD_PROTOCOL,
        "categories": ["rooms", "people", "objects", "world_devices", "hardware_units"],
        "site_switching": True,
        "agent_context_follows_selected_site": True,
        "view_only": True,
        "authority_mutation": False,
        "physical_location_mutation": False,
        "cross_site_identity_merge": False,
        "semantic_only": True,
    }
