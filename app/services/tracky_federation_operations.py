from __future__ import annotations

import json
from typing import Any

FEDERATION_OPERATIONS_PROTOCOL = "physical_federation_operations.v1"
TRACKY_FEDERATION_OPERATIONS_VERSION = "2.80"
HEALTH_STATES = ("healthy", "degraded", "critical", "unknown")
_PEER_HEALTH = {
    "current": "healthy",
    "unknown": "degraded",
    "suspect": "degraded",
    "reconciling": "degraded",
    "stale": "degraded",
    "partitioned": "critical",
    "failed": "critical",
}
_HEALTH_RANK = {"healthy": 0, "unknown": 1, "degraded": 2, "critical": 3}


def _text(value: Any, limit: int = 160) -> str:
    return " ".join(str(value or "").split())[:limit]


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _peer_for(reconciliation: dict[str, Any], site_id: str, local_site_id: str) -> dict[str, Any]:
    if site_id == local_site_id:
        return {"site_id": site_id, "status": "current", "fresh": True, "reconciliation_required": False, "last_error": ""}
    for raw in reconciliation.get("peers") or []:
        if not isinstance(raw, dict):
            continue
        remote = _text(raw.get("site_id") or raw.get("remote_site_id"), 64).lower()
        if remote != site_id:
            continue
        status = _text(raw.get("status") or "unknown", 24).lower()
        if status not in _PEER_HEALTH:
            status = "unknown"
        return {
            "site_id": site_id,
            "status": status,
            "fresh": status == "current",
            "reconciliation_required": bool(raw.get("reconciliation_required"))
            or status in {"partitioned", "reconciling", "stale", "failed", "unknown"},
            "last_error": _text(raw.get("last_error"), 240),
            "stale_since": raw.get("stale_since") or "",
            "partitioned_at": raw.get("partitioned_at") or "",
            "last_contact_at": raw.get("last_contact_at") or "",
            "retry_count": max(0, int(raw.get("retry_count") or 0)),
            "next_retry_at": raw.get("next_retry_at") or "",
        }
    return {"site_id": site_id, "status": "unknown", "fresh": False, "reconciliation_required": True, "last_error": ""}


def _authority_for(site: dict[str, Any], devices: list[dict[str, Any]]) -> dict[str, Any]:
    device_id = _text(site.get("authority_device_id") or site.get("authorityDeviceId"), 64).lower()
    epoch = max(0, int(site.get("authority_epoch") or site.get("authorityEpoch") or 0))
    if not device_id:
        return {"status": "missing", "device_id": "", "epoch": epoch}
    device = next((row for row in devices if row["id"] == device_id), None)
    if not device:
        return {"status": "invalid", "device_id": device_id, "epoch": epoch, "reason": "authority_device_not_in_inventory"}
    if device["trust_state"] and device["trust_state"] != "trusted":
        return {"status": "invalid", "device_id": device_id, "epoch": epoch, "reason": "authority_device_not_trusted"}
    roles = device.get("roles") or []
    capabilities = device.get("capabilities") or {}
    eligible = bool(capabilities.get("site_authority_eligible")) or "site_authority" in roles
    if not eligible:
        return {"status": "invalid", "device_id": device_id, "epoch": epoch, "reason": "authority_device_not_eligible"}
    return {"status": "current", "device_id": device_id, "epoch": epoch}


def build_snapshot(
    topology: dict[str, Any],
    reconciliation: dict[str, Any],
    *,
    local_site_id: str = "",
    runtime_devices: list[dict[str, Any]] | None = None,
    generated_at: str = "",
) -> dict[str, Any]:
    topology = _copy(topology if isinstance(topology, dict) else {})
    reconciliation = _copy(reconciliation if isinstance(reconciliation, dict) else {})
    local_site_id = _text(local_site_id or reconciliation.get("local_site_id"), 64).lower()
    runtime_by_id = {
        _text(row.get("device_id") or row.get("id"), 64).lower(): row
        for row in (runtime_devices or [])
        if isinstance(row, dict)
    }
    devices: list[dict[str, Any]] = []
    for raw in topology.get("devices") or []:
        if not isinstance(raw, dict):
            continue
        device_id = _text(raw.get("id") or raw.get("device_id"), 64).lower()
        extra = runtime_by_id.get(device_id) or {}
        profile = _text(raw.get("hardware_profile") or raw.get("hardwareProfile") or "custom", 40).lower() or "custom"
        devices.append({
            "id": device_id,
            "label": _text(raw.get("label") or profile),
            "site_id": _text(raw.get("site_id") or raw.get("siteId"), 64).lower(),
            "hardware_profile": profile,
            "hardware_profile_label": _text(raw.get("hardware_profile_label") or raw.get("hardwareProfileLabel") or profile, 80),
            "mobility": _text(raw.get("mobility") or "unknown", 24).lower(),
            "trust_state": _text(raw.get("trust_state") or raw.get("trustState") or "unknown", 24).lower(),
            "roles": sorted(list(raw.get("roles") or [])),
            "capabilities": _copy(raw.get("capabilities") or {}),
            "runtime_status": _text(extra.get("status") or extra.get("runtime_status") or "unknown", 32).lower(),
            "version": _text(extra.get("version"), 64),
            "last_seen_at": extra.get("last_seen_at") or extra.get("lastSeenAt") or "",
        })
    issues: list[dict[str, Any]] = []
    sites: list[dict[str, Any]] = []
    for raw in topology.get("sites") or []:
        if not isinstance(raw, dict):
            continue
        site_id = _text(raw.get("id") or raw.get("site_id"), 64).lower()
        members = [device for device in devices if device["site_id"] == site_id]
        authority = _authority_for(raw, devices)
        peer = _peer_for(reconciliation, site_id, local_site_id)
        active = _text(raw.get("status") or "active", 24).lower() == "active"
        if not active:
            health = "unknown"
        elif authority["status"] != "current" or peer["status"] in {"partitioned", "failed"}:
            health = "critical"
        elif peer["status"] in {"unknown", "suspect", "reconciling", "stale"}:
            health = "degraded"
        else:
            health = "healthy"
        if active and authority["status"] == "missing":
            issues.append({"site_id": site_id, "severity": "critical", "code": "authority_missing", "message": "Active site has no authority device."})
        if active and authority["status"] == "invalid":
            issues.append({"site_id": site_id, "severity": "critical", "code": authority.get("reason") or "authority_invalid", "message": "Site authority is not valid for the current inventory."})
        if peer["status"] in {"partitioned", "failed"}:
            issues.append({"site_id": site_id, "severity": "critical", "code": "federation_" + peer["status"], "message": "Federation peer is " + peer["status"] + "."})
        elif peer["status"] in {"unknown", "suspect", "reconciling", "stale"}:
            issues.append({"site_id": site_id, "severity": "degraded", "code": "federation_" + peer["status"], "message": "Federation peer is " + peer["status"] + "."})
        sites.append({
            "id": site_id,
            "label": _text(raw.get("label") or "Site"),
            "kind": _text(raw.get("kind") or "physical_site", 64),
            "status": _text(raw.get("status") or "active", 24).lower(),
            "device_count": len(members),
            "profiles": sorted({device["hardware_profile"] for device in members}),
            "mobile_device_count": sum(1 for device in members if device["mobility"] == "mobile"),
            "authority": authority,
            "federation": peer,
            "health": health,
        })
    sites.sort(key=lambda row: row["id"])
    devices.sort(key=lambda row: row["id"])
    active_health = [row["health"] for row in sites if row["status"] == "active"]
    health = max(active_health, key=lambda state: _HEALTH_RANK[state]) if active_health else "unknown"
    profile_counts: dict[str, int] = {}
    for device in devices:
        profile_counts[device["hardware_profile"]] = profile_counts.get(device["hardware_profile"], 0) + 1
    issues.sort(key=lambda row: (row["site_id"], row["code"]))
    return {
        "protocol": FEDERATION_OPERATIONS_PROTOCOL,
        "version": TRACKY_FEDERATION_OPERATIONS_VERSION,
        "schema_version": 1,
        "generated_at": generated_at,
        "read_only": True,
        "authority_assignment": "origin_only",
        "cloud_role": "relay_and_mirror_only",
        "topology_revision": max(0, int(topology.get("revision") or 0)),
        "local_site_id": local_site_id,
        "health": health,
        "summary": {
            "site_count": len(sites),
            "active_site_count": sum(1 for row in sites if row["status"] == "active"),
            "device_count": len(devices),
            "authority_count": sum(1 for row in sites if row["authority"]["status"] == "current"),
            "current_site_count": sum(1 for row in sites if row["federation"]["status"] == "current"),
            "degraded_site_count": sum(1 for row in sites if row["health"] == "degraded"),
            "critical_site_count": sum(1 for row in sites if row["health"] == "critical"),
            "profile_counts": dict(sorted(profile_counts.items())),
        },
        "sites": sites,
        "devices": devices,
        "relationships": _copy(topology.get("relationships") or []),
        "issues": issues,
        "boundaries": [
            "operations-snapshot-is-read-only",
            "origin-site-authority-only",
            "cloud-remains-relay-and-mirror-only",
            "stale-and-partitioned-sites-are-explicit",
            "inventory-does-not-merge-site-local-identities",
            "health-never-promotes-authority",
        ],
    }


def current_report() -> dict[str, Any]:
    from . import tracky_federation_reconciliation, tracky_federation_sync, tracky_site_topology
    topology = tracky_site_topology.current_topology()
    reconciliation = tracky_federation_reconciliation.current_report()
    local_site_id = tracky_federation_sync.local_site_id(auto_pin=False) or ""
    reconciliation["local_site_id"] = local_site_id
    return build_snapshot(topology, reconciliation, local_site_id=local_site_id)


def public_capability() -> dict[str, Any]:
    return {
        "version": TRACKY_FEDERATION_OPERATIONS_VERSION,
        "protocol": FEDERATION_OPERATIONS_PROTOCOL,
        "health_states": list(HEALTH_STATES),
        "hardware_profiles": ["node", "desk", "studio", "team_node", "pocket", "custom"],
        "read_only": True,
        "authority_assignment": "origin_only",
        "cloud_role": "relay_and_mirror_only",
        "authority_mutation": False,
        "identity_mutation": False,
    }
