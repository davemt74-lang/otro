from __future__ import annotations

import json
import re
import uuid
from typing import Any

from ..database import db

TRACKY_SITE_TOPOLOGY_VERSION = "2.78"
SITE_TOPOLOGY_PROTOCOL = "physical_site_topology.v1"
BUILTIN_HARDWARE_PROFILES = {
    "node": {"label": "Node", "mobility": "fixed"},
    "desk": {"label": "Desk", "mobility": "fixed"},
    "studio": {"label": "Studio", "mobility": "fixed"},
    "team_node": {"label": "Team Node", "mobility": "fixed"},
    "pocket": {"label": "Pocket", "mobility": "mobile"},
    "custom": {"label": "Custom", "mobility": "unknown"},
}
TRUST_STATES = {"untrusted", "pending", "trusted", "revoked"}
RELATION_TYPES = {"member_of", "peers_with", "observes", "controls", "backs_up", "travels_with", "bridges_to"}
TOKEN_RE = re.compile(r"^[a-z][a-z0-9_.:-]{1,63}$")
FORBIDDEN_KEY_RE = re.compile(r"(?:^|_)(?:raw|frame|frames|image|images|video|videos|audio|recording|recordings|embedding|embeddings|file_path|filesystem_path|camera_uri)(?:$|_)", re.I)

class TrackySiteTopologyError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = int(status_code)

def _uuid(value: Any, label: str) -> str:
    try:
        return str(uuid.UUID(str(value or "").strip()))
    except (ValueError, AttributeError, TypeError) as exc:
        raise TrackySiteTopologyError(f"{label} must be a UUID.") from exc

def _text(value: Any, limit: int, *, required: bool = False, label: str = "value") -> str:
    text = " ".join(str(value or "").split())[: max(1, int(limit))]
    if required and not text:
        raise TrackySiteTopologyError(f"{label} is required.")
    return text

def _token(value: Any, label: str) -> str:
    text = _text(value, 64, required=True, label=label).lower()
    if not TOKEN_RE.fullmatch(text):
        raise TrackySiteTopologyError(f"{label} is invalid.")
    return text

def _assert_semantic(value: Any, path: str = "payload", depth: int = 0) -> None:
    if depth > 8:
        raise TrackySiteTopologyError("Topology payload nesting is too deep.")
    if isinstance(value, dict):
        for key, child in list(value.items())[:128]:
            if FORBIDDEN_KEY_RE.search(str(key)):
                raise TrackySiteTopologyError(f"Topology cannot retain raw perception data at {path}.{key}.", 422)
            _assert_semantic(child, f"{path}.{key}", depth + 1)
    elif isinstance(value, list):
        if len(value) > 128:
            raise TrackySiteTopologyError("Topology list is too large.")
        for index, child in enumerate(value):
            _assert_semantic(child, f"{path}[{index}]", depth + 1)
    elif isinstance(value, str) and len(value) > 20000:
        raise TrackySiteTopologyError("Topology string value is too large.")

def _json(value: Any) -> str:
    _assert_semantic(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

def _json_obj(raw: Any, default: Any) -> Any:
    try:
        parsed = json.loads(str(raw or ""))
    except (TypeError, ValueError):
        return default
    return parsed if isinstance(parsed, type(default)) else default

def _profile(value: Any) -> str:
    normalized = _text(value or "custom", 80).lower().replace("-", "_").replace(" ", "_")
    return normalized if normalized in BUILTIN_HARDWARE_PROFILES else "custom"

def _roles(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return sorted({_token(item, "device role") for item in values[:64]})

def _capabilities(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TrackySiteTopologyError("capabilities must be an object.")
    out: dict[str, Any] = {}
    for key, item in list(value.items())[:128]:
        name = _token(key, "capability")
        if item is not None and not isinstance(item, (bool, str, int, float)):
            raise TrackySiteTopologyError("Capability values must be scalar.")
        out[name] = _text(item, 160) if isinstance(item, str) else item
    return dict(sorted(out.items()))

def register_site(*, site_id: str, label: str, kind: str = "physical_site", aliases: list[str] | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    site_id = _uuid(site_id, "site_id")
    label = _text(label or "Site", 160, required=True, label="site label")
    kind = _token(kind or "physical_site", "site kind")
    aliases = sorted({_text(item, 128, required=True, label="site alias") for item in (aliases or [])[:64]})
    metadata = metadata or {}
    _assert_semantic(metadata, "site.metadata")
    with db() as connection:
        connection.execute(
            """
            INSERT INTO tracky_sites(site_id,label,kind,status,aliases_json,metadata_json)
            VALUES (?,?,?,'active',?,?)
            ON CONFLICT(site_id) DO UPDATE SET
              label=excluded.label,kind=excluded.kind,aliases_json=excluded.aliases_json,
              metadata_json=excluded.metadata_json,updated_at=CURRENT_TIMESTAMP
            """,
            (site_id, label, kind, _json(aliases), _json(metadata)),
        )
    return get_site(site_id)

def register_device(*, device_id: str, label: str = "", site_id: str | None = None, hardware_profile: str = "custom", hardware_profile_label: str = "", mobility: str = "", trust_state: str = "pending", roles: list[str] | None = None, capabilities: dict[str, Any] | None = None, aliases: list[str] | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    device_id = _uuid(device_id, "device_id")
    site_id = _uuid(site_id, "site_id") if site_id else None
    profile = _profile(hardware_profile)
    mobility = _text(mobility, 20).lower() or BUILTIN_HARDWARE_PROFILES[profile]["mobility"]
    if mobility not in {"fixed", "mobile", "portable", "unknown"}:
        raise TrackySiteTopologyError("Device mobility is invalid.")
    trust_state = _text(trust_state or "pending", 20).lower()
    if trust_state not in TRUST_STATES:
        raise TrackySiteTopologyError("Device trust state is invalid.")
    normalized_roles = _roles(roles or [])
    if mobility == "mobile" and "site_authority" in normalized_roles:
        raise TrackySiteTopologyError("Mobile devices cannot be assigned the site_authority role.")
    capabilities = _capabilities(capabilities or {})
    aliases = sorted({_text(item, 128, required=True, label="device alias") for item in (aliases or [])[:64]})
    metadata = metadata or {}
    _assert_semantic(metadata, "device.metadata")
    profile_label = BUILTIN_HARDWARE_PROFILES[profile]["label"] if profile != "custom" else _text(hardware_profile_label or "Custom", 80)
    label = _text(label or profile_label, 160)
    with db() as connection:
        if site_id and connection.execute("SELECT 1 FROM tracky_sites WHERE site_id=?", (site_id,)).fetchone() is None:
            raise TrackySiteTopologyError("Device site is not registered.")
        active = connection.execute("SELECT site_id FROM tracky_site_authority WHERE device_id=? AND active=1 LIMIT 1", (device_id,)).fetchone()
        if active is not None:
            if site_id != active["site_id"]:
                raise TrackySiteTopologyError("Release site authority before moving the device to another site.", 409)
            if trust_state != "trusted":
                raise TrackySiteTopologyError("Release site authority before removing device trust.", 409)
            if "site_authority" not in normalized_roles:
                raise TrackySiteTopologyError("Release site authority before removing the site_authority role.", 409)
            if capabilities.get("site_authority_eligible") is not True:
                raise TrackySiteTopologyError("Release site authority before removing authority eligibility.", 409)
            if mobility == "mobile":
                raise TrackySiteTopologyError("Release site authority before making the device mobile.", 409)
        connection.execute(
            """
            INSERT INTO tracky_site_devices(device_id,site_id,label,hardware_profile,hardware_profile_label,mobility,trust_state,roles_json,capabilities_json,aliases_json,metadata_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(device_id) DO UPDATE SET
              site_id=excluded.site_id,label=excluded.label,hardware_profile=excluded.hardware_profile,
              hardware_profile_label=excluded.hardware_profile_label,mobility=excluded.mobility,
              trust_state=excluded.trust_state,roles_json=excluded.roles_json,
              capabilities_json=excluded.capabilities_json,aliases_json=excluded.aliases_json,
              metadata_json=excluded.metadata_json,updated_at=CURRENT_TIMESTAMP
            """,
            (device_id, site_id, label, profile, profile_label, mobility, trust_state, _json(normalized_roles), _json(capabilities), _json(aliases), _json(metadata)),
        )
    return get_device(device_id)

def get_site(site_id: str) -> dict[str, Any]:
    site_id = _uuid(site_id, "site_id")
    with db() as connection:
        row = connection.execute("SELECT * FROM tracky_sites WHERE site_id=?", (site_id,)).fetchone()
    if row is None:
        raise TrackySiteTopologyError("Site is not registered.", 404)
    return {"id": row["site_id"], "label": row["label"], "kind": row["kind"], "status": row["status"], "aliases": _json_obj(row["aliases_json"], []), "metadata": _json_obj(row["metadata_json"], {})}

def get_device(device_id: str) -> dict[str, Any]:
    device_id = _uuid(device_id, "device_id")
    with db() as connection:
        row = connection.execute("SELECT * FROM tracky_site_devices WHERE device_id=?", (device_id,)).fetchone()
    if row is None:
        raise TrackySiteTopologyError("Device is not registered.", 404)
    return {
        "id": row["device_id"], "site_id": row["site_id"], "label": row["label"],
        "hardware_profile": row["hardware_profile"], "hardware_profile_label": row["hardware_profile_label"],
        "mobility": row["mobility"], "trust_state": row["trust_state"],
        "roles": _json_obj(row["roles_json"], []), "capabilities": _json_obj(row["capabilities_json"], {}),
        "aliases": _json_obj(row["aliases_json"], []), "metadata": _json_obj(row["metadata_json"], {}),
    }

def claim_site_authority(*, site_id: str, device_id: str, replace: bool = False, reason: str = "") -> dict[str, Any]:
    site_id = _uuid(site_id, "site_id")
    device = get_device(device_id)
    if device.get("site_id") != site_id:
        raise TrackySiteTopologyError("Authority device must belong to the site.")
    if device.get("trust_state") != "trusted":
        raise TrackySiteTopologyError("Site authority requires a trusted device.")
    if "site_authority" not in device.get("roles", []):
        raise TrackySiteTopologyError("Device does not hold the site_authority role.")
    if device.get("capabilities", {}).get("site_authority_eligible") is not True:
        raise TrackySiteTopologyError("Device is not capability-eligible for site authority.")
    if device.get("mobility") == "mobile":
        raise TrackySiteTopologyError("Mobile devices cannot hold durable site authority.")
    with db() as connection:
        active = connection.execute("SELECT * FROM tracky_site_authority WHERE site_id=? AND active=1 LIMIT 1", (site_id,)).fetchone()
        if active is not None and active["device_id"] == device["id"]:
            return {"site_id": site_id, "device_id": device["id"], "authority_epoch": int(active["authority_epoch"]), "active": True, "idempotent": True}
        if active is not None and active["device_id"] != device["id"] and not replace:
            raise TrackySiteTopologyError("Site already has an active authority device.", 409)
        epoch = int(connection.execute("SELECT COALESCE(MAX(authority_epoch),0)+1 FROM tracky_site_authority WHERE site_id=?", (site_id,)).fetchone()[0])
        if active is not None:
            connection.execute("UPDATE tracky_site_authority SET active=0,released_at=CURRENT_TIMESTAMP,release_reason=? WHERE id=?", (_text(reason or "authority_replaced", 200), active["id"]))
        connection.execute("INSERT INTO tracky_site_authority(site_id,device_id,authority_epoch,active) VALUES (?,?,?,1)", (site_id, device["id"], epoch))
    return {"site_id": site_id, "device_id": device["id"], "authority_epoch": epoch, "active": True}

def release_site_authority(*, site_id: str, device_id: str | None = None, reason: str = "released") -> bool:
    site_id = _uuid(site_id, "site_id")
    with db() as connection:
        row = connection.execute("SELECT * FROM tracky_site_authority WHERE site_id=? AND active=1 LIMIT 1", (site_id,)).fetchone()
        if row is None:
            return False
        if device_id and row["device_id"] != _uuid(device_id, "device_id"):
            raise TrackySiteTopologyError("Authority device does not match.", 409)
        connection.execute("UPDATE tracky_site_authority SET active=0,released_at=CURRENT_TIMESTAMP,release_reason=? WHERE id=?", (_text(reason, 200), row["id"]))
    return True

def upsert_relationship(*, subject_id: str, relation_type: str, object_id: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    subject_id = _uuid(subject_id, "relationship subject")
    object_id = _uuid(object_id, "relationship object")
    if subject_id == object_id:
        raise TrackySiteTopologyError("Topology relationship cannot target itself.")
    relation_type = _token(relation_type, "relationship type")
    if relation_type not in RELATION_TYPES and not relation_type.startswith("custom."):
        raise TrackySiteTopologyError("Topology relationship type is unsupported.")
    metadata = metadata or {}
    relationship_id = f"{subject_id}|{relation_type}|{object_id}"
    with db() as connection:
        registered = {row[0] for row in connection.execute("SELECT site_id FROM tracky_sites").fetchall()}
        registered.update(row[0] for row in connection.execute("SELECT device_id FROM tracky_site_devices").fetchall())
        if subject_id not in registered or object_id not in registered:
            raise TrackySiteTopologyError("Topology relationship references an unregistered entity.")
        connection.execute(
            """
            INSERT INTO tracky_site_relationships(relationship_id,subject_id,relation_type,object_id,metadata_json)
            VALUES (?,?,?,?,?)
            ON CONFLICT(relationship_id) DO UPDATE SET metadata_json=excluded.metadata_json,updated_at=CURRENT_TIMESTAMP
            """,
            (relationship_id, subject_id, relation_type, object_id, _json(metadata)),
        )
    return {"id": relationship_id, "subject_id": subject_id, "type": relation_type, "object_id": object_id}

def current_topology() -> dict[str, Any]:
    with db() as connection:
        sites = connection.execute("SELECT * FROM tracky_sites ORDER BY label,site_id").fetchall()
        devices = connection.execute("SELECT * FROM tracky_site_devices ORDER BY label,device_id").fetchall()
        authority = connection.execute("SELECT * FROM tracky_site_authority WHERE active=1 ORDER BY site_id").fetchall()
        relationships = connection.execute("SELECT * FROM tracky_site_relationships ORDER BY subject_id,relation_type,object_id").fetchall()
    authority_by_site = {row["site_id"]: row for row in authority}
    device_items = [{
        "id": row["device_id"], "site_id": row["site_id"] or "", "label": row["label"],
        "hardware_profile": row["hardware_profile"], "hardware_profile_label": row["hardware_profile_label"],
        "mobility": row["mobility"], "trust_state": row["trust_state"],
        "roles": _json_obj(row["roles_json"], []), "capabilities": _json_obj(row["capabilities_json"], {}),
    } for row in devices]
    site_items = []
    for row in sites:
        members = [device for device in device_items if device["site_id"] == row["site_id"]]
        auth = authority_by_site.get(row["site_id"])
        site_items.append({
            "id": row["site_id"], "label": row["label"], "kind": row["kind"], "status": row["status"],
            "device_count": len(members), "authority_device_id": auth["device_id"] if auth else "",
            "authority_epoch": int(auth["authority_epoch"]) if auth else 0,
        })
    return {
        "protocol": SITE_TOPOLOGY_PROTOCOL, "schema_version": 1,
        "sites": site_items, "devices": device_items,
        "relationships": [{"subject_id": row["subject_id"], "type": row["relation_type"], "object_id": row["object_id"]} for row in relationships],
        "authority_assignment": "local_only",
    }

def cloud_summary() -> dict[str, Any]:
    topology = current_topology()
    topology["summary_only"] = True
    topology["cloud_read_only"] = True
    topology["devices"] = [{
        **{k: device[k] for k in ("id", "site_id", "label", "hardware_profile", "hardware_profile_label", "mobility", "trust_state")},
        "roles": list(device.get("roles") or []),
        "capabilities": sorted(key for key, value in (device.get("capabilities") or {}).items() if value is True),
    } for device in topology["devices"]]
    return topology

def public_capability() -> dict[str, Any]:
    return {
        "version": TRACKY_SITE_TOPOLOGY_VERSION,
        "protocol": SITE_TOPOLOGY_PROTOCOL,
        "profiles": ["node", "desk", "studio", "team_node", "pocket", "custom"],
        "authority_assignment": "local_only",
        "cloud_read_only": True,
        "mobile_authority": False,
    }
