from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from ..database import db
from . import tracky_federated_world, tracky_site_topology

TRACKY_FEDERATION_SYNC_VERSION = "2.78"
FEDERATION_SYNC_PROTOCOL = "physical_federation_sync.v1"
QUARANTINE_REASONS = {
    "source_not_federated",
    "topology_ahead",
    "authority_mismatch",
    "revision_conflict",
    "invalid_fragment",
}

class TrackyFederationSyncError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = int(status_code)

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _uuid(value: Any, label: str) -> str:
    try:
        return str(uuid.UUID(str(value or "").strip()))
    except (ValueError, TypeError, AttributeError) as exc:
        raise TrackyFederationSyncError(f"{label} must be a UUID.") from exc

def _text(value: Any, limit: int = 200) -> str:
    return " ".join(str(value or "").split())[: max(1, int(limit))]

def _topology() -> dict[str, Any]:
    return tracky_site_topology.current_topology()

def _topology_revision(topology: dict[str, Any]) -> int:
    return max(0, int(topology.get("revision") or 0))

def _authority(topology: dict[str, Any], site_id: str) -> tuple[str, int] | None:
    for site in topology.get("sites", []):
        if str(site.get("id") or "") != site_id:
            continue
        device = str(site.get("authority_device_id") or "")
        epoch = int(site.get("authority_epoch") or 0)
        if device and epoch > 0 and str(site.get("status") or "active") == "active":
            return _uuid(device, "authority device id"), epoch
        return None
    return None

def _peer_allowed(topology: dict[str, Any], left: str, right: str) -> bool:
    if left == right:
        return True
    for raw in topology.get("relationships", []):
        if not isinstance(raw, dict):
            continue
        relation_type = str(raw.get("type") or raw.get("relation_type") or "").lower()
        if relation_type not in {"peers_with", "bridges_to"}:
            continue
        subject = str(raw.get("subject_id") or raw.get("subjectId") or "")
        obj = str(raw.get("object_id") or raw.get("objectId") or "")
        if (subject == left and obj == right) or (subject == right and obj == left):
            return True
    return False

def set_local_site_id(site_id: str) -> str:
    site_id = _uuid(site_id, "local_site_id")
    topology = _topology()
    if _authority(topology, site_id) is None:
        raise TrackyFederationSyncError("Local federation site must have an active site authority.", 409)
    with db() as connection:
        connection.execute(
            """
            UPDATE tracky_federation_identity
            SET local_site_id=?,pinned_at=COALESCE(pinned_at,CURRENT_TIMESTAMP),updated_at=CURRENT_TIMESTAMP
            WHERE id=1
            """,
            (site_id,),
        )
    return site_id

def local_site_id(*, auto_pin: bool = True) -> str | None:
    with db() as connection:
        row = connection.execute("SELECT local_site_id FROM tracky_federation_identity WHERE id=1").fetchone()
    pinned = str(row["local_site_id"] or "") if row is not None else ""
    if pinned:
        return _uuid(pinned, "local_site_id")
    if not auto_pin:
        return None
    topology = _topology()
    candidates = [
        str(site.get("id"))
        for site in topology.get("sites", [])
        if _authority(topology, str(site.get("id") or "")) is not None
    ]
    if len(candidates) == 1:
        return set_local_site_id(candidates[0])
    return None

def _normalize_envelope(input: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(input, dict):
        raise TrackyFederationSyncError("Federation envelope must be an object.")
    if str(input.get("protocol") or "") != FEDERATION_SYNC_PROTOCOL:
        raise TrackyFederationSyncError("Federation sync protocol is unsupported.")
    source_site_id = _uuid(input.get("source_site_id"), "source_site_id")
    destination_site_id = _uuid(input.get("destination_site_id"), "destination_site_id") if input.get("destination_site_id") else ""
    authority_device_id = _uuid(input.get("source_authority_device_id"), "source_authority_device_id")
    authority_epoch = max(0, int(input.get("source_authority_epoch") or 0))
    revision = max(0, int(input.get("source_world_revision") or 0))
    topology_revision = max(0, int(input.get("topology_revision") or 0))
    fragment_raw = input.get("fragment")
    if authority_epoch < 1 or revision < 1 or not isinstance(fragment_raw, dict):
        raise TrackyFederationSyncError("Federation envelope authority, revision and fragment are required.")
    fragment = tracky_federated_world.normalize_fragment(fragment_raw)
    if fragment["site_id"] != source_site_id:
        raise TrackyFederationSyncError("Federation envelope source does not match fragment site.")
    if fragment["authority_device_id"] != authority_device_id or int(fragment["authority_epoch"]) != authority_epoch:
        raise TrackyFederationSyncError("Federation envelope authority does not match fragment authority.")
    if int(fragment["revision"]) != revision:
        raise TrackyFederationSyncError("Federation envelope revision does not match fragment revision.")
    fingerprint = _text(input.get("source_fingerprint"), 128)
    if not fingerprint:
        raise TrackyFederationSyncError("Federation envelope source fingerprint is required.")
    return {
        "protocol": FEDERATION_SYNC_PROTOCOL,
        "schema_version": 1,
        "envelope_id": _text(input.get("envelope_id"), 180),
        "source_site_id": source_site_id,
        "destination_site_id": destination_site_id,
        "source_authority_device_id": authority_device_id,
        "source_authority_epoch": authority_epoch,
        "source_world_revision": revision,
        "source_fingerprint": fingerprint,
        "topology_revision": topology_revision,
        "emitted_at": _text(input.get("emitted_at"), 64),
        "fragment": fragment,
    }

def _quarantine(envelope: dict[str, Any], reason: str, message: str) -> dict[str, Any]:
    if reason not in QUARANTINE_REASONS:
        reason = "invalid_fragment"
    safe = {
        "protocol": FEDERATION_SYNC_PROTOCOL,
        "envelope_id": _text(envelope.get("envelope_id"), 180),
        "source_site_id": _text(envelope.get("source_site_id"), 64),
        "destination_site_id": _text(envelope.get("destination_site_id"), 64),
        "source_world_revision": max(0, int(envelope.get("source_world_revision") or 0)),
        "source_authority_epoch": max(0, int(envelope.get("source_authority_epoch") or 0)),
        "reason": reason,
        "message": _text(message, 300),
    }
    envelope_json = "{}"
    try:
        envelope_json = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError):
        envelope_json = "{}"
    with db() as connection:
        connection.execute(
            """
            INSERT INTO tracky_federation_quarantine(
              envelope_id,source_site_id,destination_site_id,source_world_revision,
              source_authority_epoch,reason,message,envelope_json
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                safe["envelope_id"], safe["source_site_id"], safe["destination_site_id"],
                safe["source_world_revision"], safe["source_authority_epoch"],
                reason, safe["message"], envelope_json,
            ),
        )
        if safe["source_site_id"] and connection.execute(
            "SELECT 1 FROM tracky_sites WHERE site_id=?",
            (safe["source_site_id"],),
        ).fetchone() is not None:
            connection.execute(
                """
                INSERT INTO tracky_federation_sync_peers(remote_site_id,status,quarantined_count)
                VALUES (?,'quarantined',1)
                ON CONFLICT(remote_site_id) DO UPDATE SET
                  status='quarantined',quarantined_count=quarantined_count+1,updated_at=CURRENT_TIMESTAMP
                """,
                (safe["source_site_id"],),
            )
    return safe

def receive_cursors() -> list[dict[str, Any]]:
    with db() as connection:
        rows = connection.execute(
            """
            SELECT remote_site_id,last_received_revision,last_received_fingerprint,last_received_authority_epoch
            FROM tracky_federation_sync_peers
            WHERE last_received_revision>0
            ORDER BY remote_site_id
            """
        ).fetchall()
    return [
        {
            "site_id": str(row["remote_site_id"]),
            "revision": int(row["last_received_revision"] or 0),
            "fingerprint": str(row["last_received_fingerprint"] or ""),
            "authority_epoch": int(row["last_received_authority_epoch"] or 0),
        }
        for row in rows
    ]

def cloud_sync_request() -> dict[str, Any]:
    site_id = local_site_id(auto_pin=True)
    return {
        "protocol": FEDERATION_SYNC_PROTOCOL,
        "schema_version": 1,
        "available": bool(site_id),
        "local_site_id": site_id or "",
        "received_cursors": receive_cursors(),
        "max_envelopes": 32,
        "authority_assignment": "local_only",
    }

def ingest_cloud_batch(input: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(input, dict):
        raise TrackyFederationSyncError("Federation relay batch must be an object.")
    if str(input.get("protocol") or "") != FEDERATION_SYNC_PROTOCOL:
        raise TrackyFederationSyncError("Federation relay protocol is unsupported.")
    local = local_site_id(auto_pin=True)
    if not local:
        return {"accepted": False, "reason": "local_site_unresolved", "applied": 0, "stale": 0, "idempotent": 0, "quarantined": 0}
    destination = _uuid(input.get("destination_site_id"), "destination_site_id")
    if destination != local:
        raise TrackyFederationSyncError("Federation relay batch is routed to a different local site.", 409)
    envelopes = list(input.get("envelopes") or [])
    if len(envelopes) > 64:
        raise TrackyFederationSyncError("Federation relay batch exceeds the HomeServer limit.")
    topology = _topology()
    applied = stale = idempotent = quarantined = ignored = 0
    for raw in envelopes:
        try:
            envelope = _normalize_envelope(raw)
        except TrackyFederationSyncError as exc:
            _quarantine(raw if isinstance(raw, dict) else {}, "invalid_fragment", str(exc))
            quarantined += 1
            continue
        if envelope["destination_site_id"] and envelope["destination_site_id"] != local:
            ignored += 1
            continue
        source = envelope["source_site_id"]
        if not _peer_allowed(topology, source, local):
            _quarantine(envelope, "source_not_federated", "Source site is not an approved federation peer.")
            quarantined += 1
            continue
        authority = _authority(topology, source)
        if authority is None or authority[1] < envelope["source_authority_epoch"]:
            _quarantine(envelope, "topology_ahead", "Local topology cannot yet verify the source authority epoch.")
            quarantined += 1
            continue
        if authority[0] != envelope["source_authority_device_id"] or authority[1] != envelope["source_authority_epoch"]:
            _quarantine(envelope, "authority_mismatch", "Envelope authority does not match current topology authority.")
            quarantined += 1
            continue

        with db() as connection:
            prior = connection.execute(
                """
                SELECT last_received_revision,last_received_fingerprint
                FROM tracky_federation_sync_peers WHERE remote_site_id=?
                """,
                (source,),
            ).fetchone()
        prior_revision = int(prior["last_received_revision"] or 0) if prior is not None else 0
        prior_fingerprint = str(prior["last_received_fingerprint"] or "") if prior is not None else ""
        revision = envelope["source_world_revision"]
        fingerprint = envelope["source_fingerprint"]
        if revision < prior_revision:
            stale += 1
            continue
        if revision == prior_revision and prior_revision > 0:
            if prior_fingerprint and prior_fingerprint != fingerprint:
                _quarantine(envelope, "revision_conflict", "Same site revision arrived with a different fingerprint.")
                quarantined += 1
            else:
                idempotent += 1
            continue

        try:
            tracky_federated_world.ingest_projection(
                {
                    "protocol": tracky_federated_world.FEDERATED_WORLD_PROTOCOL,
                    "schema_version": 1,
                    "sites": [envelope["fragment"]],
                    "identity_scope": "site_local",
                    "cross_site_identity_links": [],
                    "semantic_only": True,
                    "cloud_read_only": True,
                    "authority_assignment": "local_only",
                },
                source="federation_sync",
            )
        except tracky_federated_world.TrackyFederatedWorldError as exc:
            reason = "revision_conflict" if "revision conflicts" in str(exc).lower() else "authority_mismatch" if "authority" in str(exc).lower() else "invalid_fragment"
            _quarantine(envelope, reason, str(exc))
            quarantined += 1
            continue

        with db() as connection:
            connection.execute(
                """
                INSERT INTO tracky_federation_sync_peers(
                  remote_site_id,status,last_received_revision,last_received_fingerprint,
                  last_received_authority_epoch,last_received_at
                ) VALUES (?,'current',?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(remote_site_id) DO UPDATE SET
                  status='current',
                  last_received_revision=excluded.last_received_revision,
                  last_received_fingerprint=excluded.last_received_fingerprint,
                  last_received_authority_epoch=excluded.last_received_authority_epoch,
                  last_received_at=CURRENT_TIMESTAMP,
                  updated_at=CURRENT_TIMESTAMP
                """,
                (source, revision, fingerprint, envelope["source_authority_epoch"]),
            )
        applied += 1
    return {
        "accepted": True,
        "local_site_id": local,
        "applied": applied,
        "stale": stale,
        "idempotent": idempotent,
        "quarantined": quarantined,
        "ignored": ignored,
    }

def build_outbound_batch(destination_site_id: str, *, max_envelopes: int = 32) -> dict[str, Any]:
    destination_site_id = _uuid(destination_site_id, "destination_site_id")
    local = local_site_id(auto_pin=True)
    if not local:
        raise TrackyFederationSyncError("Local federation site is unresolved.", 409)
    topology = _topology()
    if not _peer_allowed(topology, local, destination_site_id):
        raise TrackyFederationSyncError("Destination site is not an approved federation peer.", 403)
    report = tracky_federated_world.current_report()
    envelopes: list[dict[str, Any]] = []
    for fragment in report.get("sites", []):
        if len(envelopes) >= max(1, min(int(max_envelopes), 64)):
            break
        source = str(fragment.get("site_id") or "")
        if source == destination_site_id or not _peer_allowed(topology, source, destination_site_id):
            continue
        with db() as connection:
            ack = connection.execute(
                "SELECT acknowledged_revision FROM tracky_federation_outbound_ack WHERE destination_site_id=? AND source_site_id=?",
                (destination_site_id, source),
            ).fetchone()
        acknowledged = int(ack["acknowledged_revision"] or 0) if ack is not None else 0
        revision = int(fragment.get("revision") or 0)
        if revision <= acknowledged:
            continue
        authority = _authority(topology, source)
        if authority is None:
            continue
        fingerprint = str(fragment.get("fingerprint") or "")
        envelopes.append({
            "protocol": FEDERATION_SYNC_PROTOCOL,
            "schema_version": 1,
            "envelope_id": f"fed:{source}:{authority[1]}:{revision}:{fingerprint[:24]}",
            "source_site_id": source,
            "destination_site_id": destination_site_id,
            "source_authority_device_id": authority[0],
            "source_authority_epoch": authority[1],
            "source_world_revision": revision,
            "source_fingerprint": fingerprint,
            "topology_revision": _topology_revision(topology),
            "emitted_at": _now_iso(),
            "fragment": fragment,
        })
    return {
        "protocol": FEDERATION_SYNC_PROTOCOL,
        "schema_version": 1,
        "destination_site_id": destination_site_id,
        "topology_revision": _topology_revision(topology),
        "emitted_at": _now_iso(),
        "envelopes": envelopes,
    }

def acknowledge_outbound(destination_site_id: str, acknowledgements: list[dict[str, Any]]) -> int:
    destination_site_id = _uuid(destination_site_id, "destination_site_id")
    changed = 0
    with db() as connection:
        for item in acknowledgements[:128]:
            if not isinstance(item, dict):
                continue
            source = _uuid(item.get("site_id"), "acknowledged site id")
            revision = max(0, int(item.get("revision") or 0))
            if revision < 1:
                continue
            fingerprint = _text(item.get("fingerprint"), 128)
            connection.execute(
                """
                INSERT INTO tracky_federation_outbound_ack(
                  destination_site_id,source_site_id,acknowledged_revision,acknowledged_fingerprint,acknowledged_at
                ) VALUES (?,?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(destination_site_id,source_site_id) DO UPDATE SET
                  acknowledged_revision=MAX(acknowledged_revision,excluded.acknowledged_revision),
                  acknowledged_fingerprint=CASE
                    WHEN excluded.acknowledged_revision>=acknowledged_revision THEN excluded.acknowledged_fingerprint
                    ELSE acknowledged_fingerprint END,
                  acknowledged_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                """,
                (destination_site_id, source, revision, fingerprint),
            )
            changed += 1
    return changed

def status() -> dict[str, Any]:
    local = local_site_id(auto_pin=False)
    with db() as connection:
        peers = [dict(row) for row in connection.execute("SELECT * FROM tracky_federation_sync_peers ORDER BY remote_site_id").fetchall()]
        quarantine_open = int(connection.execute("SELECT COUNT(*) FROM tracky_federation_quarantine WHERE status='open'").fetchone()[0])
        outbound = [dict(row) for row in connection.execute("SELECT * FROM tracky_federation_outbound_ack ORDER BY destination_site_id,source_site_id").fetchall()]
    return {
        "protocol": FEDERATION_SYNC_PROTOCOL,
        "schema_version": 1,
        "local_site_id": local or "",
        "local_site_resolved": bool(local),
        "peers": peers,
        "outbound_acknowledgements": outbound,
        "quarantine_open": quarantine_open,
        "authority_assignment": "local_only",
        "cloud_role": "relay_only",
    }

def public_capability() -> dict[str, Any]:
    return {
        "version": TRACKY_FEDERATION_SYNC_VERSION,
        "protocol": FEDERATION_SYNC_PROTOCOL,
        "transport": "existing_tracky_cloud_sync",
        "authority_assignment": "local_only",
        "cloud_role": "relay_only",
        "same_revision_conflicts": "quarantine",
        "topology_ahead": "hold",
        "cross_site_identity_linking": False,
    }
