from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from ..database import db
from . import tracky_site_topology

TRACKY_FEDERATED_WORLD_VERSION = "2.78"
FEDERATED_WORLD_PROTOCOL = "physical_federated_world.v1"
FORBIDDEN_KEY_RE = re.compile(r"(?:^|_)(?:raw|frame|frames|image|images|video|videos|audio|recording|recordings|embedding|embeddings|blob|bytes|pixels|file_path|filesystem_path|camera_uri)(?:$|_)", re.I)
TOKEN_RE = re.compile(r"^[a-z][a-z0-9_.:-]{1,79}$")
LOCAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")
TEMPORAL = {"current","last_seen","historical","inferred","predicted","unknown"}
STATES = {"observed","inferred","last-known","user-confirmed","contradicted","expired","unknown"}

class TrackyFederatedWorldError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = int(status_code)

def _uuid(value: Any, label: str) -> str:
    try:
        return str(uuid.UUID(str(value or "").strip()))
    except (ValueError, TypeError, AttributeError) as exc:
        raise TrackyFederatedWorldError(f"{label} must be a UUID.") from exc

def _text(value: Any, limit: int, *, required: bool = False, label: str = "value") -> str:
    text = " ".join(str(value or "").split())[: max(1, int(limit))]
    if required and not text:
        raise TrackyFederatedWorldError(f"{label} is required.")
    return text

def _token(value: Any, label: str, fallback: str = "") -> str:
    text = _text(value or fallback, 80, required=True, label=label).lower()
    if not TOKEN_RE.fullmatch(text):
        raise TrackyFederatedWorldError(f"{label} is invalid.")
    return text

def _local_id(value: Any, label: str) -> str:
    text = _text(value, 160, required=True, label=label)
    if not LOCAL_ID_RE.fullmatch(text):
        raise TrackyFederatedWorldError(f"{label} is invalid.")
    return text

def _confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0

def _assert_semantic(value: Any, path: str = "payload", depth: int = 0) -> None:
    if depth > 8:
        raise TrackyFederatedWorldError("Federated world payload nesting is too deep.")
    if isinstance(value, dict):
        if len(value) > 128:
            raise TrackyFederatedWorldError("Federated world object is too large.")
        for key, child in value.items():
            if FORBIDDEN_KEY_RE.search(str(key)):
                raise TrackyFederatedWorldError(f"Federated world cannot retain raw perception data at {path}.{key}.", 422)
            _assert_semantic(child, f"{path}.{key}", depth + 1)
    elif isinstance(value, list):
        if len(value) > 1024:
            raise TrackyFederatedWorldError("Federated world list is too large.")
        for index, child in enumerate(value):
            _assert_semantic(child, f"{path}[{index}]", depth + 1)
    elif isinstance(value, str) and len(value) > 20000:
        raise TrackyFederatedWorldError("Federated world string value is too large.")

def _json(value: Any) -> str:
    _assert_semantic(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

def _qualify(site_id: str, local_id: str) -> str:
    from urllib.parse import quote
    return f"site:{site_id}::{quote(local_id, safe='')}"

def _entity(site_id: str, item: dict[str, Any]) -> dict[str, Any]:
    _assert_semantic(item, "entity")
    local_id = _local_id(item.get("local_id") or item.get("id") or item.get("entity_id"), "entity local id")
    state = _text(item.get("state") or "unknown", 30).lower()
    return {
        "local_id": local_id,
        "ref": _qualify(site_id, local_id),
        "type": _token(item.get("type") or "entity", "entity type", "entity"),
        "label": _text(item.get("label") or item.get("name"), 160),
        "state": state if state in STATES else "unknown",
        "confidence": _confidence(item.get("confidence")),
        "observed_at": max(0, int(item.get("observed_at") or item.get("last_observed_at") or 0)),
    }

def _relation(site_id: str, item: dict[str, Any]) -> dict[str, Any]:
    _assert_semantic(item, "relation")
    subject = _local_id(item.get("subject_local_id") or item.get("subject_id"), "relation subject")
    raw_object = item.get("object_local_id") or item.get("object_id") or ""
    object_id = _local_id(raw_object, "relation object") if raw_object else ""
    temporal = _text(item.get("temporal_state") or "current", 30).lower()
    return {
        "subject_local_id": subject,
        "subject_ref": _qualify(site_id, subject),
        "predicate": _token(item.get("predicate") or "related_to", "relation predicate", "related_to"),
        "object_local_id": object_id,
        "object_ref": _qualify(site_id, object_id) if object_id else "",
        "value": item.get("value") if isinstance(item.get("value"), dict) else {},
        "confidence": _confidence(item.get("confidence")),
        "temporal_state": temporal if temporal in TEMPORAL else "unknown",
        "source_event_id": _text(item.get("source_event_id"), 160),
        "sequence": max(0, int(item.get("sequence") or 0)),
        "as_of": max(0, int(item.get("as_of") or 0)),
    }

def _current_authority(site_id: str) -> tuple[str,int]:
    topology = tracky_site_topology.current_topology()
    for site in topology.get("sites", []):
        if site.get("id") == site_id:
            device = str(site.get("authority_device_id") or "")
            epoch = int(site.get("authority_epoch") or 0)
            if device and epoch > 0:
                return device, epoch
            break
    raise TrackyFederatedWorldError("Federated world site has no active topology authority.", 409)

def normalize_fragment(input: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(input, dict):
        raise TrackyFederatedWorldError("Federated world fragment must be an object.")
    _assert_semantic(input, "fragment")
    if str(input.get("protocol") or "") != FEDERATED_WORLD_PROTOCOL:
        raise TrackyFederatedWorldError("Federated world protocol is unsupported.")
    site_id = _uuid(input.get("site_id"), "site_id")
    authority_device_id = _uuid(input.get("authority_device_id"), "authority_device_id")
    authority_epoch = max(0, int(input.get("authority_epoch") or 0))
    revision = max(0, int(input.get("revision") or 0))
    if authority_epoch < 1 or revision < 1:
        raise TrackyFederatedWorldError("Federated world authority epoch and revision are required.")
    entities_raw = list(input.get("entities") or [])
    relations_raw = list(input.get("relations") or [])
    if len(entities_raw) > 512 or len(relations_raw) > 1024:
        raise TrackyFederatedWorldError("Federated world fragment exceeds semantic limits.")
    entities = [_entity(site_id, item) for item in entities_raw if isinstance(item, dict)]
    relations = [_relation(site_id, item) for item in relations_raw if isinstance(item, dict)]
    entity_ids = {item["local_id"] for item in entities}
    for relation in relations:
        for local_id in (relation["subject_local_id"], relation["object_local_id"]):
            if local_id and local_id not in entity_ids:
                entities.append({
                    "local_id": local_id, "ref": _qualify(site_id, local_id), "type": "entity",
                    "label": "", "state": "unknown", "confidence": 0.0, "observed_at": relation["as_of"],
                })
                entity_ids.add(local_id)
    entities.sort(key=lambda item: item["local_id"])
    fragment = {
        "protocol": FEDERATED_WORLD_PROTOCOL,
        "schema_version": 1,
        "site_id": site_id,
        "authority_device_id": authority_device_id,
        "authority_epoch": authority_epoch,
        "topology_revision": max(0, int(input.get("topology_revision") or 0)),
        "revision": revision,
        "observed_at": _text(input.get("observed_at"), 64),
        "entities": entities,
        "relations": relations,
        "context": input.get("context") if isinstance(input.get("context"), dict) else {},
        "semantic_only": True,
        "identity_scope": "site_local",
    }
    fragment["fingerprint"] = hashlib.sha256(_json(fragment).encode("utf-8")).hexdigest()
    return fragment

def normalize_projection(input: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(input, dict):
        raise TrackyFederatedWorldError("Federated world projection must be an object.")
    _assert_semantic(input, "federated_world")
    if str(input.get("protocol") or "") != FEDERATED_WORLD_PROTOCOL:
        raise TrackyFederatedWorldError("Federated world protocol is unsupported.")
    if str(input.get("identity_scope") or "site_local") != "site_local":
        raise TrackyFederatedWorldError("Cross-site identity linking is not permitted in V2.78 Section 2.")
    links = list(input.get("cross_site_identity_links") or [])
    if links:
        raise TrackyFederatedWorldError("Cross-site identity links are deferred to V2.78 Section 5.")
    sites = list(input.get("sites") or [])
    if len(sites) > 128:
        raise TrackyFederatedWorldError("Federated world site batch exceeds the limit.")
    return {
        "protocol": FEDERATED_WORLD_PROTOCOL,
        "schema_version": 1,
        "sites": [normalize_fragment(item) for item in sites if isinstance(item, dict)],
        "identity_scope": "site_local",
        "cross_site_identity_links": [],
        "semantic_only": True,
        "cloud_read_only": True,
        "authority_assignment": "local_only",
    }

def _privacy_redaction_only(prior: dict[str, Any], incoming: dict[str, Any]) -> bool:
    if (
        str(prior.get("site_id") or "") != str(incoming.get("site_id") or "")
        or int(prior.get("revision") or 0) != int(incoming.get("revision") or 0)
        or str(prior.get("authority_device_id") or "") != str(incoming.get("authority_device_id") or "")
        or int(prior.get("authority_epoch") or 0) != int(incoming.get("authority_epoch") or 0)
        or int(prior.get("topology_revision") or 0) != int(incoming.get("topology_revision") or 0)
        or str(prior.get("observed_at") or "") != str(incoming.get("observed_at") or "")
    ):
        return False
    if incoming.get("context") not in ({}, None):
        return False
    incoming_entities = incoming.get("entities") if isinstance(incoming.get("entities"), list) else []
    prior_entities = prior.get("entities") if isinstance(prior.get("entities"), list) else []
    if any(isinstance(item, dict) and str(item.get("type") or "") == "person" for item in incoming_entities):
        return False
    prior_entity_map = {
        str(item.get("local_id") or ""): item
        for item in prior_entities if isinstance(item, dict) and str(item.get("local_id") or "")
    }
    for item in incoming_entities:
        if not isinstance(item, dict):
            return False
        local_id = str(item.get("local_id") or "")
        if not local_id or prior_entity_map.get(local_id) != item:
            return False

    prior_relations = {
        _json(item)
        for item in (prior.get("relations") if isinstance(prior.get("relations"), list) else [])
        if isinstance(item, dict)
    }
    incoming_relations = incoming.get("relations") if isinstance(incoming.get("relations"), list) else []
    if any(_json(item) not in prior_relations for item in incoming_relations if isinstance(item, dict)):
        return False
    if any(not isinstance(item, dict) for item in incoming_relations):
        return False

    prior_context = prior.get("context") if isinstance(prior.get("context"), dict) else {}
    removed_entity = len(incoming_entities) < len(prior_entities)
    removed_relation = len(incoming_relations) < len(prior_relations)
    removed_context = bool(prior_context)
    return removed_entity or removed_relation or removed_context


def ingest_projection(
    input: dict[str, Any],
    *,
    source: str = "tracky",
    allow_same_revision_redaction: bool = False,
) -> dict[str, Any]:
    projection = normalize_projection(input)
    accepted = changed = stale = 0
    for fragment in projection["sites"]:
        expected_device, expected_epoch = _current_authority(fragment["site_id"])
        if fragment["authority_device_id"] != expected_device or fragment["authority_epoch"] != expected_epoch:
            raise TrackyFederatedWorldError("Federated world fragment authority does not match current site authority.", 409)
        encoded = _json(fragment)
        fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with db() as connection:
            prior = connection.execute(
                "SELECT world_revision,fingerprint,fragment_json FROM tracky_federated_world_fragments WHERE site_id=?",
                (fragment["site_id"],),
            ).fetchone()
            if prior is not None and fragment["revision"] < int(prior["world_revision"]):
                stale += 1
                continue
            if prior is not None and fragment["revision"] == int(prior["world_revision"]):
                if str(prior["fingerprint"]) != fingerprint:
                    prior_fragment = {}
                    try:
                        prior_fragment = json.loads(prior["fragment_json"] or "{}")
                    except (TypeError, ValueError, json.JSONDecodeError):
                        prior_fragment = {}
                    if not (
                        allow_same_revision_redaction
                        and isinstance(prior_fragment, dict)
                        and _privacy_redaction_only(prior_fragment, fragment)
                    ):
                        raise TrackyFederatedWorldError("Federated world revision conflicts with existing site world.", 409)
                    connection.execute(
                        """
                        UPDATE tracky_federated_world_fragments
                        SET authority_device_id=?,authority_epoch=?,topology_revision=?,
                            observed_at=?,fragment_json=?,fingerprint=?,source=?,
                            updated_at=CURRENT_TIMESTAMP
                        WHERE site_id=?
                        """,
                        (
                            fragment["authority_device_id"], fragment["authority_epoch"],
                            fragment["topology_revision"], fragment["observed_at"],
                            encoded, fingerprint, _text(source, 80) or "federation_sync_redaction",
                            fragment["site_id"],
                        ),
                    )
                    accepted += 1
                    changed += 1
                    continue
                accepted += 1
                continue
            connection.execute(
                """
                INSERT INTO tracky_federated_world_fragments(
                  site_id,authority_device_id,authority_epoch,topology_revision,world_revision,
                  observed_at,fragment_json,fingerprint,source
                ) VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(site_id) DO UPDATE SET
                  authority_device_id=excluded.authority_device_id,
                  authority_epoch=excluded.authority_epoch,
                  topology_revision=excluded.topology_revision,
                  world_revision=excluded.world_revision,
                  observed_at=excluded.observed_at,
                  fragment_json=excluded.fragment_json,
                  fingerprint=excluded.fingerprint,
                  source=excluded.source,
                  updated_at=CURRENT_TIMESTAMP
                """,
                (
                    fragment["site_id"], fragment["authority_device_id"], fragment["authority_epoch"],
                    fragment["topology_revision"], fragment["revision"], fragment["observed_at"],
                    encoded, fingerprint, _text(source, 80) or "tracky",
                ),
            )
            accepted += 1
            changed += 1
    return {"accepted": True, "sites": accepted, "changed": changed, "stale": stale}

def current_report(site_id: str | None = None) -> dict[str, Any]:
    with db() as connection:
        if site_id:
            rows = connection.execute(
                "SELECT * FROM tracky_federated_world_fragments WHERE site_id=? ORDER BY site_id",
                (_uuid(site_id, "site_id"),),
            ).fetchall()
        else:
            rows = connection.execute("SELECT * FROM tracky_federated_world_fragments ORDER BY site_id").fetchall()
    sites = []
    for row in rows:
        try:
            fragment = json.loads(row["fragment_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(fragment, dict):
            sites.append(fragment)
    return {
        "available": bool(sites),
        "protocol": FEDERATED_WORLD_PROTOCOL,
        "schema_version": 1,
        "sites": sites,
        "site_count": len(sites),
        "identity_scope": "site_local",
        "cross_site_identity_links": [],
        "semantic_only": True,
        "authority_assignment": "local_only",
        "cloud_read_only": True,
    }

def cloud_projection(local_site_id: str | None = None) -> dict[str, Any]:
    report = current_report(local_site_id) if local_site_id else current_report()
    return {
        "protocol": FEDERATED_WORLD_PROTOCOL,
        "schema_version": 1,
        "sites": report["sites"],
        "site_count": report["site_count"],
        "identity_scope": "site_local",
        "cross_site_identity_links": [],
        "semantic_only": True,
        "summary_only": True,
        "cloud_read_only": True,
        "authority_assignment": "local_only",
        "origin_scope": "local_site_only" if local_site_id else "unresolved_legacy_scope",
    }

def public_capability() -> dict[str, Any]:
    return {
        "version": TRACKY_FEDERATED_WORLD_VERSION,
        "protocol": FEDERATED_WORLD_PROTOCOL,
        "semantic_only": True,
        "identity_scope": "site_local",
        "cross_site_identity_linking": False,
        "authority_assignment": "local_only",
        "cloud_read_only": True,
    }
