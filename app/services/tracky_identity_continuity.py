from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any
from urllib.parse import unquote

from ..database import db
from . import tracky_federated_world, tracky_site_topology

TRACKY_IDENTITY_CONTINUITY_VERSION = "2.78"
IDENTITY_CONTINUITY_PROTOCOL = "physical_identity_continuity.v1"
ENTITY_TYPES = {"person", "device", "object", "animal"}
LINK_STATES = {"proposed", "confirmed", "rejected", "revoked", "split"}
IDENTITY_STATES = {"candidate", "active", "split", "revoked"}
SITE_REF_RE = re.compile(r"^site:([0-9a-fA-F-]{36})::(.+)$")
FORBIDDEN_KEY_RE = re.compile(
    r"(?:^|_)(?:raw|frame|frames|image|images|video|videos|audio|recording|recordings|embedding|embeddings|blob|bytes|pixels|file_path|filesystem_path|camera_uri)(?:$|_)",
    re.I,
)

class TrackyIdentityContinuityError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = int(status_code)

def _uuid(value: Any, label: str) -> str:
    try:
        return str(uuid.UUID(str(value or "").strip()))
    except (TypeError, ValueError, AttributeError) as exc:
        raise TrackyIdentityContinuityError(f"{label} must be a UUID.") from exc

def _text(value: Any, limit: int = 240, *, required: bool = False, label: str = "value") -> str:
    text = " ".join(str(value or "").split())[: max(1, int(limit))]
    if required and not text:
        raise TrackyIdentityContinuityError(f"{label} is required.")
    return text

def _confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0

def _assert_semantic(value: Any, path: str = "payload", depth: int = 0) -> None:
    if depth > 8:
        raise TrackyIdentityContinuityError("Identity continuity payload nesting is too deep.")
    if isinstance(value, dict):
        if len(value) > 128:
            raise TrackyIdentityContinuityError("Identity continuity object is too large.")
        for key, child in value.items():
            if FORBIDDEN_KEY_RE.search(str(key)):
                raise TrackyIdentityContinuityError(
                    f"Identity continuity cannot retain raw perception data at {path}.{key}.", 422
                )
            _assert_semantic(child, f"{path}.{key}", depth + 1)
    elif isinstance(value, list):
        if len(value) > 1024:
            raise TrackyIdentityContinuityError("Identity continuity list is too large.")
        for index, child in enumerate(value):
            _assert_semantic(child, f"{path}[{index}]", depth + 1)
    elif isinstance(value, str) and len(value) > 20000:
        raise TrackyIdentityContinuityError("Identity continuity string is too large.")

def _json(value: Any) -> str:
    _assert_semantic(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

def _ref(value: Any, label: str) -> tuple[str, str, str]:
    text = _text(value, 320, required=True, label=label)
    match = SITE_REF_RE.fullmatch(text)
    if match is None:
        raise TrackyIdentityContinuityError(f"{label} must be a site-qualified entity ref.")
    site_id = _uuid(match.group(1), f"{label} site")
    try:
        local_id = unquote(match.group(2))
    except Exception as exc:
        raise TrackyIdentityContinuityError(f"{label} encoding is invalid.") from exc
    if not local_id or len(local_id) > 160:
        raise TrackyIdentityContinuityError(f"{label} local id is invalid.")
    return text, site_id, local_id

def _pair_key(left: str, right: str) -> str:
    refs = sorted([left, right])
    return "|".join(refs)

def _current_authority(site_id: str) -> tuple[str, int]:
    topology = tracky_site_topology.current_topology()
    for site in topology.get("sites", []):
        if str(site.get("id") or "") != site_id:
            continue
        if str(site.get("status") or "active") != "active":
            break
        device = str(site.get("authority_device_id") or "")
        epoch = int(site.get("authority_epoch") or 0)
        if device and epoch > 0:
            return _uuid(device, "authority device id"), epoch
        break
    raise TrackyIdentityContinuityError("Identity governing site has no active authority.", 409)

def _local_site_id() -> str | None:
    try:
        from . import tracky_federation_sync
        return tracky_federation_sync.local_site_id(auto_pin=False)
    except Exception:
        return None

def _known_entity_refs() -> set[str]:
    report = tracky_federated_world.current_report()
    refs: set[str] = set()
    for site in report.get("sites", []):
        for entity in site.get("entities", []):
            ref = str(entity.get("ref") or "")
            if ref:
                refs.add(ref)
    return refs

def _normalize_identity(input: dict[str, Any]) -> dict[str, Any]:
    _assert_semantic(input, "identity")
    canonical_id = _uuid(input.get("canonical_identity_id"), "canonical_identity_id")
    entity_type = _text(input.get("entity_type"), 40).lower()
    if entity_type not in ENTITY_TYPES:
        raise TrackyIdentityContinuityError("Canonical identity entity type is unsupported.")
    status = _text(input.get("status") or "active", 40).lower()
    if status not in IDENTITY_STATES:
        raise TrackyIdentityContinuityError("Canonical identity status is unsupported.")
    members = []
    seen = set()
    for raw in list(input.get("members") or [])[:256]:
        ref, _, _ = _ref(raw, "identity member ref")
        if ref not in seen:
            members.append(ref)
            seen.add(ref)
    aliases = []
    for raw in list(input.get("aliases") or [])[:128]:
        alias = _text(raw, 160)
        if alias and alias not in aliases:
            aliases.append(alias)
    return {
        "canonical_identity_id": canonical_id,
        "entity_type": entity_type,
        "status": status,
        "members": sorted(members),
        "aliases": sorted(aliases),
        "revision": max(1, int(input.get("revision") or 1)),
        "created_at": max(0, int(input.get("created_at") or 0)),
        "updated_at": max(0, int(input.get("updated_at") or 0)),
    }

def _normalize_link(input: dict[str, Any]) -> dict[str, Any]:
    _assert_semantic(input, "identity_link")
    link_id = _uuid(input.get("link_id"), "link_id")
    canonical_id = _uuid(input.get("canonical_identity_id"), "canonical_identity_id")
    entity_type = _text(input.get("entity_type"), 40).lower()
    if entity_type not in ENTITY_TYPES:
        raise TrackyIdentityContinuityError("Identity link entity type is unsupported.")
    left_ref, left_site, _ = _ref(input.get("left_ref"), "left_ref")
    right_ref, right_site, _ = _ref(input.get("right_ref"), "right_ref")
    if left_ref == right_ref:
        raise TrackyIdentityContinuityError("Identity link requires two distinct refs.")
    if left_site == right_site:
        raise TrackyIdentityContinuityError("Identity continuity link must cross sites.")
    status = _text(input.get("status"), 40).lower()
    if status not in LINK_STATES:
        raise TrackyIdentityContinuityError("Identity link status is unsupported.")
    evidence = list(input.get("evidence") or [])
    if len(evidence) > 128:
        raise TrackyIdentityContinuityError("Identity link evidence exceeds the limit.")
    _assert_semantic(evidence, "identity_link.evidence")
    normalized = {
        "link_id": link_id,
        "pair_key": _pair_key(left_ref, right_ref),
        "canonical_identity_id": canonical_id,
        "entity_type": entity_type,
        "left_ref": left_ref,
        "right_ref": right_ref,
        "left_site_id": left_site,
        "right_site_id": right_site,
        "status": status,
        "reason": _text(input.get("reason"), 200),
        "evidence": evidence,
        "confidence": _confidence(input.get("confidence")),
        "auto_confirmed": bool(input.get("auto_confirmed")),
        "revision": max(1, int(input.get("revision") or 1)),
        "created_at": max(0, int(input.get("created_at") or 0)),
        "updated_at": max(0, int(input.get("updated_at") or 0)),
        "confirmed_at": int(input["confirmed_at"]) if input.get("confirmed_at") is not None else None,
        "rejected_at": int(input["rejected_at"]) if input.get("rejected_at") is not None else None,
        "revoked_at": int(input["revoked_at"]) if input.get("revoked_at") is not None else None,
        "split_at": int(input["split_at"]) if input.get("split_at") is not None else None,
        "governing_site_id": _uuid(input.get("governing_site_id"), "governing_site_id") if input.get("governing_site_id") else "",
        "governing_authority_device_id": _uuid(input.get("governing_authority_device_id"), "governing_authority_device_id") if input.get("governing_authority_device_id") else "",
        "governing_authority_epoch": max(0, int(input.get("governing_authority_epoch") or 0)),
    }
    material = dict(normalized)
    material.pop("governing_site_id", None)
    material.pop("governing_authority_device_id", None)
    material.pop("governing_authority_epoch", None)
    normalized["fingerprint"] = hashlib.sha256(_json(material).encode("utf-8")).hexdigest()
    return normalized

def normalize_projection(input: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(input, dict):
        raise TrackyIdentityContinuityError("Identity continuity projection must be an object.")
    _assert_semantic(input, "identity_continuity")
    if str(input.get("protocol") or "") != IDENTITY_CONTINUITY_PROTOCOL:
        raise TrackyIdentityContinuityError("Identity continuity protocol is unsupported.")
    identities_raw = list(input.get("identities") or [])
    links_raw = list(input.get("links") or [])
    blocked_raw = list(input.get("blocked_pairs") or [])
    if len(identities_raw) > 256 or len(links_raw) > 512 or len(blocked_raw) > 512:
        raise TrackyIdentityContinuityError("Identity continuity projection exceeds limits.")
    identities = [_normalize_identity(item) for item in identities_raw if isinstance(item, dict)]
    links = [_normalize_link(item) for item in links_raw if isinstance(item, dict)]
    identity_by_id = {item["canonical_identity_id"]: item for item in identities}
    for link in links:
        identity = identity_by_id.get(link["canonical_identity_id"])
        if identity is None and link["status"] != "confirmed":
            identity = {
                "canonical_identity_id": link["canonical_identity_id"],
                "entity_type": link["entity_type"],
                "status": "candidate",
                "members": [],
                "aliases": [],
                "revision": link["revision"],
                "created_at": link["created_at"],
                "updated_at": link["updated_at"],
            }
            identities.append(identity)
            identity_by_id[identity["canonical_identity_id"]] = identity
        if identity is None:
            raise TrackyIdentityContinuityError("Confirmed identity link references a missing canonical identity.")
        if identity["entity_type"] != link["entity_type"]:
            raise TrackyIdentityContinuityError("Identity link type conflicts with canonical identity.")
        if link["status"] == "confirmed":
            if link["left_ref"] not in identity["members"] or link["right_ref"] not in identity["members"]:
                raise TrackyIdentityContinuityError("Confirmed identity link members are missing from canonical identity.")
    blocked_pairs = []
    for item in blocked_raw:
        if not isinstance(item, dict):
            continue
        pair = _text(item.get("pair"), 700, required=True, label="blocked pair")
        blocked_pairs.append({
            "pair": pair,
            "reason": _text(item.get("reason"), 200),
            "at": max(0, int(item.get("at") or 0)),
        })
    return {
        "protocol": IDENTITY_CONTINUITY_PROTOCOL,
        "schema_version": 1,
        "identities": identities,
        "links": links,
        "blocked_pairs": blocked_pairs,
        "semantic_only": True,
        "cloud_read_only": True,
        "site_local_entities_immutable": True,
        "reversible": True,
    }

def _assert_active_collision(connection: Any, link: dict[str, Any]) -> None:
    if link["status"] != "confirmed":
        return
    for ref in (link["left_ref"], link["right_ref"]):
        rows = connection.execute(
            """
            SELECT DISTINCT canonical_identity_id
            FROM tracky_identity_links
            WHERE status='confirmed' AND (left_ref=? OR right_ref=?)
              AND canonical_identity_id<>?
            """,
            (ref, ref, link["canonical_identity_id"]),
        ).fetchall()
        if rows:
            raise TrackyIdentityContinuityError(
                "Entity ref is already assigned to a different active canonical identity.", 409
            )

def ingest_projection(
    input: dict[str, Any],
    *,
    source: str = "tracky",
    origin_role: str = "local_governed",
    governing_site_id: str | None = None,
    governing_authority_device_id: str | None = None,
    governing_authority_epoch: int | None = None,
) -> dict[str, Any]:
    if origin_role not in {"local_governed", "cloud_mirror"}:
        raise TrackyIdentityContinuityError("Identity continuity origin role is invalid.")
    projection = normalize_projection(input)
    known_refs = _known_entity_refs()
    local_site = _local_site_id()

    if origin_role == "local_governed":
        default_governing_site = _uuid(governing_site_id or local_site, "governing_site_id")
        if local_site and default_governing_site != local_site:
            raise TrackyIdentityContinuityError("Local identity decisions must be governed by the local site.", 409)
        default_authority_device, default_authority_epoch = _current_authority(default_governing_site)
    else:
        default_governing_site = ""
        default_authority_device = ""
        default_authority_epoch = 0

    changed = stale = idempotent = 0
    with db() as connection:
        for identity in projection["identities"]:
            connection.execute(
                """
                INSERT INTO tracky_canonical_identities(
                  canonical_identity_id,entity_type,status,aliases_json,members_json,revision,
                  origin_role,governing_site_id,created_at_ms,observed_updated_at_ms
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(canonical_identity_id) DO NOTHING
                """,
                (
                    identity["canonical_identity_id"], identity["entity_type"], identity["status"],
                    _json(identity["aliases"]), _json(identity["members"]), identity["revision"],
                    origin_role, default_governing_site or None, identity["created_at"], identity["updated_at"],
                ),
            )

        for link in projection["links"]:
            if origin_role == "local_governed":
                link_governing_site = default_governing_site
                authority_device = default_authority_device
                authority_epoch = default_authority_epoch
            else:
                link_governing_site = link["governing_site_id"]
                authority_device = link["governing_authority_device_id"]
                authority_epoch = link["governing_authority_epoch"]
                if not link_governing_site or not authority_device or authority_epoch < 1:
                    raise TrackyIdentityContinuityError("Mirrored identity link authority metadata is required.")
                expected_device, expected_epoch = _current_authority(link_governing_site)
                if expected_device != authority_device or expected_epoch != authority_epoch:
                    raise TrackyIdentityContinuityError("Mirrored identity authority does not match current topology.", 409)
            if link_governing_site not in {link["left_site_id"], link["right_site_id"]}:
                raise TrackyIdentityContinuityError(
                    "Identity governing site must be one of the linked entity sites.", 409
                )
            if origin_role == "cloud_mirror" and local_site and local_site not in {link["left_site_id"], link["right_site_id"]}:
                raise TrackyIdentityContinuityError(
                    "Mirrored identity link is not relevant to this HomeServer site.", 409
                )
            if link["left_ref"] not in known_refs or link["right_ref"] not in known_refs:
                raise TrackyIdentityContinuityError(
                    "Identity link references an entity not present in the current federated world.", 409
                )
            pair_owner = connection.execute(
                "SELECT link_id,governing_site_id FROM tracky_identity_links WHERE pair_key=? LIMIT 1",
                (link["pair_key"],),
            ).fetchone()
            if pair_owner is not None and str(pair_owner["link_id"]) != link["link_id"]:
                raise TrackyIdentityContinuityError(
                    "Identity pair is already governed by a different link.", 409
                )
            prior = connection.execute(
                "SELECT revision,fingerprint,status FROM tracky_identity_links WHERE link_id=?",
                (link["link_id"],),
            ).fetchone()
            if prior is not None and link["revision"] < int(prior["revision"]):
                stale += 1
                continue
            if prior is not None and link["revision"] == int(prior["revision"]):
                if str(prior["fingerprint"]) != link["fingerprint"]:
                    raise TrackyIdentityContinuityError(
                        "Identity link revision conflicts with existing ledger state.", 409
                    )
                idempotent += 1
                continue
            blocked = connection.execute(
                "SELECT reason FROM tracky_identity_blocked_pairs WHERE pair_key=? LIMIT 1",
                (link["pair_key"],),
            ).fetchone()
            if blocked is not None and link["status"] in {"proposed", "confirmed"}:
                raise TrackyIdentityContinuityError(
                    "Identity pair is blocked by a prior rejection or split.", 409
                )

            _assert_active_collision(connection, link)
            identity = next(
                item for item in projection["identities"]
                if item["canonical_identity_id"] == link["canonical_identity_id"]
            )
            connection.execute(
                """
                INSERT INTO tracky_canonical_identities(
                  canonical_identity_id,entity_type,status,aliases_json,members_json,revision,
                  origin_role,governing_site_id,created_at_ms,observed_updated_at_ms
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(canonical_identity_id) DO UPDATE SET
                  entity_type=CASE WHEN excluded.revision>=revision THEN excluded.entity_type ELSE entity_type END,
                  status=CASE WHEN excluded.revision>=revision THEN excluded.status ELSE status END,
                  aliases_json=CASE WHEN excluded.revision>=revision THEN excluded.aliases_json ELSE aliases_json END,
                  members_json=CASE WHEN excluded.revision>=revision THEN excluded.members_json ELSE members_json END,
                  origin_role=CASE WHEN excluded.revision>=revision THEN excluded.origin_role ELSE origin_role END,
                  governing_site_id=CASE WHEN excluded.revision>=revision THEN excluded.governing_site_id ELSE governing_site_id END,
                  created_at_ms=CASE WHEN created_at_ms=0 THEN excluded.created_at_ms ELSE created_at_ms END,
                  observed_updated_at_ms=CASE WHEN excluded.revision>=revision THEN excluded.observed_updated_at_ms ELSE observed_updated_at_ms END,
                  revision=MAX(revision,excluded.revision),
                  updated_at=CURRENT_TIMESTAMP
                """,
                (
                    identity["canonical_identity_id"], identity["entity_type"], identity["status"],
                    _json(identity["aliases"]), _json(identity["members"]), identity["revision"],
                    origin_role, link_governing_site, identity["created_at"], identity["updated_at"],
                ),
            )
            connection.execute(
                """
                INSERT INTO tracky_identity_links(
                  link_id,pair_key,canonical_identity_id,entity_type,left_ref,right_ref,left_site_id,right_site_id,
                  status,reason,evidence_json,confidence,auto_confirmed,revision,origin_role,governing_site_id,
                  governing_authority_device_id,governing_authority_epoch,fingerprint,created_at_ms,
                  observed_updated_at_ms,confirmed_at_ms,rejected_at_ms,revoked_at_ms,split_at_ms
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(link_id) DO UPDATE SET
                  pair_key=excluded.pair_key,canonical_identity_id=excluded.canonical_identity_id,
                  entity_type=excluded.entity_type,left_ref=excluded.left_ref,right_ref=excluded.right_ref,
                  left_site_id=excluded.left_site_id,right_site_id=excluded.right_site_id,status=excluded.status,
                  reason=excluded.reason,evidence_json=excluded.evidence_json,confidence=excluded.confidence,
                  auto_confirmed=excluded.auto_confirmed,revision=excluded.revision,
                  origin_role=excluded.origin_role,governing_site_id=excluded.governing_site_id,
                  governing_authority_device_id=excluded.governing_authority_device_id,
                  governing_authority_epoch=excluded.governing_authority_epoch,
                  fingerprint=excluded.fingerprint,observed_updated_at_ms=excluded.observed_updated_at_ms,
                  confirmed_at_ms=excluded.confirmed_at_ms,rejected_at_ms=excluded.rejected_at_ms,
                  revoked_at_ms=excluded.revoked_at_ms,split_at_ms=excluded.split_at_ms,
                  updated_at=CURRENT_TIMESTAMP
                """,
                (
                    link["link_id"], link["pair_key"], link["canonical_identity_id"], link["entity_type"],
                    link["left_ref"], link["right_ref"], link["left_site_id"], link["right_site_id"],
                    link["status"], link["reason"], _json(link["evidence"]), link["confidence"],
                    1 if link["auto_confirmed"] else 0, link["revision"], origin_role, link_governing_site,
                    authority_device, authority_epoch, link["fingerprint"], link["created_at"],
                    link["updated_at"], link["confirmed_at"], link["rejected_at"], link["revoked_at"],
                    link["split_at"],
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO tracky_identity_history(
                  link_id,revision,status,origin_role,snapshot_json,fingerprint
                ) VALUES (?,?,?,?,?,?)
                """,
                (
                    link["link_id"], link["revision"], link["status"], origin_role,
                    _json(link), link["fingerprint"],
                ),
            )
            changed += 1

        for blocked in projection["blocked_pairs"]:
            connection.execute(
                """
                INSERT INTO tracky_identity_blocked_pairs(pair_key,reason,blocked_at_ms)
                VALUES (?,?,?)
                ON CONFLICT(pair_key) DO UPDATE SET
                  reason=excluded.reason,blocked_at_ms=MAX(blocked_at_ms,excluded.blocked_at_ms),
                  updated_at=CURRENT_TIMESTAMP
                """,
                (blocked["pair"], blocked["reason"], blocked["at"]),
            )

    return {
        "accepted": True,
        "changed": changed,
        "stale": stale,
        "idempotent": idempotent,
        "governing_site_id": default_governing_site,
    }

def _identity_row(row: Any) -> dict[str, Any]:
    try:
        aliases = json.loads(row["aliases_json"] or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        aliases = []
    try:
        members = json.loads(row["members_json"] or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        members = []
    return {
        "canonical_identity_id": str(row["canonical_identity_id"]),
        "entity_type": str(row["entity_type"]),
        "status": str(row["status"]),
        "aliases": aliases if isinstance(aliases, list) else [],
        "members": members if isinstance(members, list) else [],
        "revision": int(row["revision"] or 0),
        "origin_role": str(row["origin_role"] or ""),
        "governing_site_id": str(row["governing_site_id"] or ""),
        "created_at": int(row["created_at_ms"] or 0),
        "updated_at": int(row["observed_updated_at_ms"] or 0),
    }

def _link_row(row: Any) -> dict[str, Any]:
    try:
        evidence = json.loads(row["evidence_json"] or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        evidence = []
    return {
        "link_id": str(row["link_id"]),
        "canonical_identity_id": str(row["canonical_identity_id"]),
        "entity_type": str(row["entity_type"]),
        "left_ref": str(row["left_ref"]),
        "right_ref": str(row["right_ref"]),
        "left_site_id": str(row["left_site_id"]),
        "right_site_id": str(row["right_site_id"]),
        "status": str(row["status"]),
        "reason": str(row["reason"] or ""),
        "evidence": evidence if isinstance(evidence, list) else [],
        "confidence": float(row["confidence"] or 0),
        "auto_confirmed": bool(row["auto_confirmed"]),
        "revision": int(row["revision"] or 0),
        "origin_role": str(row["origin_role"] or ""),
        "governing_site_id": str(row["governing_site_id"]),
        "governing_authority_device_id": str(row["governing_authority_device_id"]),
        "governing_authority_epoch": int(row["governing_authority_epoch"] or 0),
        "fingerprint": str(row["fingerprint"]),
        "created_at": int(row["created_at_ms"] or 0),
        "updated_at": int(row["observed_updated_at_ms"] or 0),
        "confirmed_at": int(row["confirmed_at_ms"]) if row["confirmed_at_ms"] is not None else None,
        "rejected_at": int(row["rejected_at_ms"]) if row["rejected_at_ms"] is not None else None,
        "revoked_at": int(row["revoked_at_ms"]) if row["revoked_at_ms"] is not None else None,
        "split_at": int(row["split_at_ms"]) if row["split_at_ms"] is not None else None,
    }

def current_report() -> dict[str, Any]:
    with db() as connection:
        identity_rows = connection.execute(
            "SELECT * FROM tracky_canonical_identities ORDER BY canonical_identity_id"
        ).fetchall()
        link_rows = connection.execute(
            "SELECT * FROM tracky_identity_links ORDER BY link_id"
        ).fetchall()
        blocked_rows = connection.execute(
            "SELECT pair_key,reason,blocked_at_ms FROM tracky_identity_blocked_pairs ORDER BY pair_key"
        ).fetchall()
    identities = [_identity_row(row) for row in identity_rows]
    links = [_link_row(row) for row in link_rows]
    return {
        "available": bool(links or identities),
        "protocol": IDENTITY_CONTINUITY_PROTOCOL,
        "schema_version": 1,
        "identities": identities,
        "links": links,
        "blocked_pairs": [
            {"pair": str(row["pair_key"]), "reason": str(row["reason"]), "at": int(row["blocked_at_ms"] or 0)}
            for row in blocked_rows
        ],
        "semantic_only": True,
        "cloud_read_only": True,
        "site_local_entities_immutable": True,
        "reversible": True,
    }

def resolve_entity(entity_ref: str) -> dict[str, Any] | None:
    ref, _, _ = _ref(entity_ref, "entity_ref")
    with db() as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT i.*
            FROM tracky_canonical_identities i
            JOIN tracky_identity_links l ON l.canonical_identity_id=i.canonical_identity_id
            WHERE i.status='active' AND l.status='confirmed'
              AND (l.left_ref=? OR l.right_ref=?)
            """,
            (ref, ref),
        ).fetchall()
    if len(rows) > 1:
        raise TrackyIdentityContinuityError("Entity ref resolves to multiple active canonical identities.", 409)
    return _identity_row(rows[0]) if rows else None

def agent_context() -> dict[str, Any]:
    report = current_report()
    active = [item for item in report["identities"] if item["status"] == "active"]
    return {
        "protocol": IDENTITY_CONTINUITY_PROTOCOL,
        "active_identity_count": len(active),
        "identities": [
            {
                "canonical_identity_id": item["canonical_identity_id"],
                "entity_type": item["entity_type"],
                "aliases": item["aliases"],
                "members": item["members"],
                "site_count": len({_ref(ref, "member ref")[1] for ref in item["members"]}),
            }
            for item in active[-24:]
        ],
        "site_local_entities_immutable": True,
        "reversible": True,
    }

def cloud_projection(local_site_id: str | None = None) -> dict[str, Any]:
    report = current_report()
    links = []
    identity_ids = set()
    for item in report["links"]:
        if item["origin_role"] != "local_governed":
            continue
        if local_site_id and item["governing_site_id"] != local_site_id:
            continue
        authority_device, authority_epoch = _current_authority(item["governing_site_id"])
        enriched = dict(item)
        enriched["governing_authority_device_id"] = authority_device
        enriched["governing_authority_epoch"] = authority_epoch
        links.append(enriched)
        identity_ids.add(item["canonical_identity_id"])
    identities = [
        item for item in report["identities"]
        if item["canonical_identity_id"] in identity_ids
    ]
    blocked = []
    linked_pairs = {item["pair_key"] if "pair_key" in item else _pair_key(item["left_ref"], item["right_ref"]) for item in links}
    for item in report["blocked_pairs"]:
        if item["pair"] in linked_pairs:
            blocked.append(item)
    return {
        "protocol": IDENTITY_CONTINUITY_PROTOCOL,
        "schema_version": 1,
        "identities": identities,
        "links": links,
        "blocked_pairs": blocked,
        "semantic_only": True,
        "summary_only": True,
        "cloud_read_only": True,
        "site_local_entities_immutable": True,
        "reversible": True,
        "authority_assignment": "homeserver_governed",
        "origin_scope": "local_governing_site_only" if local_site_id else "unresolved_legacy_scope",
        "cloud_can_confirm_links": False,
        "cloud_can_merge_identities": False,
        "cloud_can_split_identities": False,
    }

def public_capability() -> dict[str, Any]:
    return {
        "version": TRACKY_IDENTITY_CONTINUITY_VERSION,
        "protocol": IDENTITY_CONTINUITY_PROTOCOL,
        "entity_types": sorted(ENTITY_TYPES),
        "states": sorted(LINK_STATES),
        "site_local_entities_immutable": True,
        "reversible": True,
        "authority_assignment": "homeserver_governed",
        "cloud_read_only": True,
        "cloud_can_confirm_links": False,
        "cloud_can_merge_identities": False,
        "cloud_can_split_identities": False,
    }
