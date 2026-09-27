from __future__ import annotations

import json
import uuid
from typing import Any

from ..database import db
from . import tracky_federation_sync, tracky_site_topology

TRACKY_FEDERATION_POLICY_VERSION = "2.78"
FEDERATION_POLICY_PROTOCOL = "physical_federation_policy.v1"
PERMISSION_SCOPES = {
    "semantic_world_read",
    "agent_context_read",
    "history_query",
    "identity_continuity_read",
    "identity_linking",
    "person_recognition",
    "voice_matching",
    "remote_observation",
}
CONSENT_SCOPES = {"person_recognition", "voice_matching", "identity_linking"}
CONSENT_STATES = {"pending", "granted", "denied", "revoked"}
SITE_MODES = {"private", "household", "team", "shared"}

class TrackyFederationPolicyError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = int(status_code)

def _uuid(value: Any, label: str) -> str:
    try:
        return str(uuid.UUID(str(value or "").strip()))
    except (TypeError, ValueError, AttributeError) as exc:
        raise TrackyFederationPolicyError(f"{label} must be a UUID.") from exc

def _text(value: Any, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[: max(1, int(limit))]

def _scope(value: Any) -> str:
    out = _text(value, 80).lower()
    if out not in PERMISSION_SCOPES:
        raise TrackyFederationPolicyError("Federation permission scope is unsupported.")
    return out

def _consent_scope(value: Any) -> str:
    out = _text(value, 80).lower()
    if out not in CONSENT_SCOPES:
        raise TrackyFederationPolicyError("Recognition consent scope is unsupported.")
    return out

def _local_site() -> str:
    local = tracky_federation_sync.local_site_id(auto_pin=False)
    if not local:
        raise TrackyFederationPolicyError("Local federation site is unresolved.", 409)
    return _uuid(local, "local site id")

def _authority(site_id: str) -> tuple[str, int]:
    topology = tracky_site_topology.current_topology()
    for site in topology.get("sites", []):
        if str(site.get("id") or "") != site_id or str(site.get("status") or "active") != "active":
            continue
        device = str(site.get("authority_device_id") or "")
        epoch = int(site.get("authority_epoch") or 0)
        if device and epoch > 0:
            return _uuid(device, "authority device id"), epoch
    raise TrackyFederationPolicyError("Site has no active authority.", 409)

def _next_revision(connection: Any) -> int:
    row = connection.execute("SELECT revision FROM tracky_federation_policy_state WHERE id=1").fetchone()
    return int(row["revision"] or 0) + 1 if row else 1

def _advance_state(connection: Any, *, revocation: bool = False) -> tuple[int, int]:
    revision = _next_revision(connection)
    row = connection.execute("SELECT revocation_epoch FROM tracky_federation_policy_state WHERE id=1").fetchone()
    epoch = int(row["revocation_epoch"] or 0) if row else 0
    if revocation:
        epoch += 1
    connection.execute(
        """
        UPDATE tracky_federation_policy_state
        SET revision=?,revocation_epoch=?,updated_at=CURRENT_TIMESTAMP
        WHERE id=1
        """,
        (revision, epoch),
    )
    return revision, epoch

def _history(connection: Any, site_id: str, event_type: str, revision: int, epoch: int, detail: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO tracky_federation_policy_history(
          governing_site_id,event_type,revision,revocation_epoch,detail_json
        ) VALUES (?,?,?,?,?)
        """,
        (site_id, event_type, revision, epoch, json.dumps(detail, separators=(",", ":"), sort_keys=True)),
    )

def set_site_policy(
    *,
    mode: str = "private",
    allow_federation: bool = False,
    allow_remote_observation: bool = False,
    default_identity_visibility: str = "none",
    allowed_peer_sites: list[str] | None = None,
) -> dict[str, Any]:
    local = _local_site()
    device, epoch = _authority(local)
    mode = mode if mode in SITE_MODES else "private"
    visibility = default_identity_visibility if default_identity_visibility in {"none", "anonymous", "consented"} else "none"
    peers = sorted({
        _uuid(site, "allowed peer site id")
        for site in (allowed_peer_sites or [])
        if str(site) != local
    })
    topology_sites = {str(item.get("id") or "") for item in tracky_site_topology.current_topology().get("sites", [])}
    if any(peer not in topology_sites for peer in peers):
        raise TrackyFederationPolicyError("Allowed federation peer is not present in topology.", 409)

    with db() as connection:
        prior = connection.execute(
            "SELECT revision FROM tracky_federation_site_policies WHERE site_id=?",
            (local,),
        ).fetchone()
        policy_revision = int(prior["revision"] or 0) + 1 if prior else 1
        global_revision, revocation_epoch = _advance_state(connection)
        connection.execute(
            """
            INSERT INTO tracky_federation_site_policies(
              site_id,revision,mode,allow_federation,allow_remote_observation,
              default_identity_visibility,allowed_peer_sites_json,origin_role,
              governing_authority_device_id,governing_authority_epoch,observed_updated_at_ms
            ) VALUES (?,?,?,?,?,?,?,?,?,?,0)
            ON CONFLICT(site_id) DO UPDATE SET
              revision=excluded.revision,mode=excluded.mode,allow_federation=excluded.allow_federation,
              allow_remote_observation=excluded.allow_remote_observation,
              default_identity_visibility=excluded.default_identity_visibility,
              allowed_peer_sites_json=excluded.allowed_peer_sites_json,
              origin_role='local_governed',
              governing_authority_device_id=excluded.governing_authority_device_id,
              governing_authority_epoch=excluded.governing_authority_epoch,
              updated_at=CURRENT_TIMESTAMP
            """,
            (
                local, policy_revision, mode, 1 if allow_federation else 0,
                1 if allow_remote_observation else 0, visibility,
                json.dumps(peers, separators=(",", ":")), "local_governed", device, epoch,
            ),
        )
        _history(connection, local, "site_policy_updated", global_revision, revocation_epoch, {
            "policy_revision": policy_revision,
            "mode": mode,
            "allow_federation": bool(allow_federation),
            "allow_remote_observation": bool(allow_remote_observation),
            "allowed_peer_sites": peers,
        })
    return site_policy(local)

def grant_permission(destination_site_id: str, scope: str, *, reason: str = "explicit_grant") -> dict[str, Any]:
    local = _local_site()
    destination = _uuid(destination_site_id, "destination site id")
    permission = _scope(scope)
    policy = site_policy(local)
    if not policy or not policy["allow_federation"]:
        raise TrackyFederationPolicyError("Source site does not permit federation.", 403)
    if destination not in policy["allowed_peer_sites"]:
        raise TrackyFederationPolicyError("Destination site is not an allowed federation peer.", 403)
    if permission == "remote_observation" and not policy["allow_remote_observation"]:
        raise TrackyFederationPolicyError("Source site does not permit remote observation.", 403)
    device, authority_epoch = _authority(local)

    with db() as connection:
        prior = connection.execute(
            """
            SELECT revision FROM tracky_federation_permissions
            WHERE source_site_id=? AND destination_site_id=? AND scope=?
            """,
            (local, destination, permission),
        ).fetchone()
        revision = int(prior["revision"] or 0) + 1 if prior else 1
        blocked = connection.execute(
            "SELECT revision FROM tracky_federation_policy_revocations WHERE revocation_key=?",
            (f"grant:{local}|{destination}|{permission}",),
        ).fetchone()
        if blocked and revision <= int(blocked["revision"] or 0):
            revision = int(blocked["revision"] or 0) + 1
        global_revision, revocation_epoch = _advance_state(connection)
        connection.execute(
            """
            INSERT INTO tracky_federation_permissions(
              source_site_id,destination_site_id,scope,status,revision,reason,origin_role,
              governing_authority_device_id,governing_authority_epoch,granted_at_ms,revoked_at_ms
            ) VALUES (?,?,?,?,?,?,?,?,?,?,NULL)
            ON CONFLICT(source_site_id,destination_site_id,scope) DO UPDATE SET
              status='granted',revision=excluded.revision,reason=excluded.reason,
              origin_role='local_governed',governing_authority_device_id=excluded.governing_authority_device_id,
              governing_authority_epoch=excluded.governing_authority_epoch,
              granted_at_ms=excluded.granted_at_ms,revoked_at_ms=NULL,updated_at=CURRENT_TIMESTAMP
            """,
            (local, destination, permission, "granted", revision, _text(reason, 200), "local_governed", device, authority_epoch, 0),
        )
        _history(connection, local, "permission_granted", global_revision, revocation_epoch, {
            "destination_site_id": destination, "scope": permission, "revision": revision
        })
    return permission_decision(local, destination, permission)

def revoke_permission(destination_site_id: str, scope: str, *, reason: str = "explicit_revocation") -> dict[str, Any]:
    local = _local_site()
    destination = _uuid(destination_site_id, "destination site id")
    permission = _scope(scope)
    device, authority_epoch = _authority(local)
    with db() as connection:
        prior = connection.execute(
            """
            SELECT revision FROM tracky_federation_permissions
            WHERE source_site_id=? AND destination_site_id=? AND scope=?
            """,
            (local, destination, permission),
        ).fetchone()
        revision = int(prior["revision"] or 0) + 1 if prior else 1
        global_revision, revocation_epoch = _advance_state(connection, revocation=True)
        reason_text = _text(reason, 200)
        connection.execute(
            """
            INSERT INTO tracky_federation_permissions(
              source_site_id,destination_site_id,scope,status,revision,reason,origin_role,
              governing_authority_device_id,governing_authority_epoch,granted_at_ms,revoked_at_ms
            ) VALUES (?,?,?,?,?,?,?,?,?,NULL,0)
            ON CONFLICT(source_site_id,destination_site_id,scope) DO UPDATE SET
              status='revoked',revision=excluded.revision,reason=excluded.reason,
              origin_role='local_governed',governing_authority_device_id=excluded.governing_authority_device_id,
              governing_authority_epoch=excluded.governing_authority_epoch,
              revoked_at_ms=excluded.revoked_at_ms,updated_at=CURRENT_TIMESTAMP
            """,
            (local, destination, permission, "revoked", revision, reason_text, "local_governed", device, authority_epoch),
        )
        key = f"grant:{local}|{destination}|{permission}"
        connection.execute(
            """
            INSERT INTO tracky_federation_policy_revocations(
              revocation_key,governing_site_id,revision,revocation_epoch,reason,origin_role,revoked_at_ms
            ) VALUES (?,?,?,?,?,'local_governed',0)
            ON CONFLICT(revocation_key) DO UPDATE SET
              revision=MAX(revision,excluded.revision),
              revocation_epoch=MAX(revocation_epoch,excluded.revocation_epoch),
              reason=excluded.reason,origin_role='local_governed',updated_at=CURRENT_TIMESTAMP
            """,
            (key, local, revision, revocation_epoch, reason_text),
        )
        _history(connection, local, "permission_revoked", global_revision, revocation_epoch, {
            "destination_site_id": destination, "scope": permission, "revision": revision
        })
    return permission_decision(local, destination, permission)

def set_recognition_consent(
    canonical_identity_id: str,
    scope: str,
    status: str,
    *,
    source: str = "user",
    reason: str = "",
) -> dict[str, Any]:
    local = _local_site()
    canonical = _uuid(canonical_identity_id, "canonical identity id")
    permission = _consent_scope(scope)
    status = _text(status, 40).lower()
    if status not in CONSENT_STATES:
        raise TrackyFederationPolicyError("Recognition consent state is unsupported.")
    device, authority_epoch = _authority(local)

    with db() as connection:
        prior = connection.execute(
            """
            SELECT revision FROM tracky_recognition_consents
            WHERE site_id=? AND canonical_identity_id=? AND scope=?
            """,
            (local, canonical, permission),
        ).fetchone()
        revision = int(prior["revision"] or 0) + 1 if prior else 1
        revoking = status in {"denied", "revoked"}
        global_revision, revocation_epoch = _advance_state(connection, revocation=revoking)
        reason_text = _text(reason, 200)
        connection.execute(
            """
            INSERT INTO tracky_recognition_consents(
              site_id,canonical_identity_id,scope,status,revision,source,reason,origin_role,
              governing_authority_device_id,governing_authority_epoch,decided_at_ms
            ) VALUES (?,?,?,?,?,?,?,?,?,?,0)
            ON CONFLICT(site_id,canonical_identity_id,scope) DO UPDATE SET
              status=excluded.status,revision=excluded.revision,source=excluded.source,
              reason=excluded.reason,origin_role='local_governed',
              governing_authority_device_id=excluded.governing_authority_device_id,
              governing_authority_epoch=excluded.governing_authority_epoch,
              decided_at_ms=excluded.decided_at_ms,updated_at=CURRENT_TIMESTAMP
            """,
            (local, canonical, permission, status, revision, _text(source, 80), reason_text, "local_governed", device, authority_epoch),
        )
        if revoking:
            key = f"consent:{local}|{canonical}|{permission}"
            connection.execute(
                """
                INSERT INTO tracky_federation_policy_revocations(
                  revocation_key,governing_site_id,revision,revocation_epoch,reason,origin_role,revoked_at_ms
                ) VALUES (?,?,?,?,?,'local_governed',0)
                ON CONFLICT(revocation_key) DO UPDATE SET
                  revision=MAX(revision,excluded.revision),
                  revocation_epoch=MAX(revocation_epoch,excluded.revocation_epoch),
                  reason=excluded.reason,origin_role='local_governed',updated_at=CURRENT_TIMESTAMP
                """,
                (key, local, revision, revocation_epoch, reason_text or status),
            )
        _history(connection, local, f"recognition_consent_{status}", global_revision, revocation_epoch, {
            "canonical_identity_id": canonical, "scope": permission, "revision": revision
        })
    return recognition_decision(local, canonical, permission)

def site_policy(site_id: str) -> dict[str, Any] | None:
    site = _uuid(site_id, "site id")
    with db() as connection:
        row = connection.execute(
            "SELECT * FROM tracky_federation_site_policies WHERE site_id=?",
            (site,),
        ).fetchone()
    if row is None:
        return None
    try:
        peers = json.loads(row["allowed_peer_sites_json"] or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        peers = []
    return {
        "site_id": site,
        "revision": int(row["revision"] or 0),
        "mode": str(row["mode"] or "private"),
        "allow_federation": bool(row["allow_federation"]),
        "allow_remote_observation": bool(row["allow_remote_observation"]),
        "default_identity_visibility": str(row["default_identity_visibility"] or "none"),
        "allowed_peer_sites": peers if isinstance(peers, list) else [],
        "origin_role": str(row["origin_role"] or ""),
        "governing_authority_device_id": str(row["governing_authority_device_id"] or ""),
        "governing_authority_epoch": int(row["governing_authority_epoch"] or 0),
    }

def permission_decision(
    source_site_id: str,
    destination_site_id: str,
    scope: str,
    *,
    canonical_identity_id: str | None = None,
) -> dict[str, Any]:
    source = _uuid(source_site_id, "source site id")
    destination = _uuid(destination_site_id, "destination site id")
    permission = _scope(scope)
    policy = site_policy(source)
    if policy is None:
        return {"allowed": False, "reason": "source_site_policy_missing"}
    if str(policy.get("origin_role") or "") == "cloud_mirror":
        try:
            current_device, current_epoch = _authority(source)
        except TrackyFederationPolicyError:
            return {"allowed": False, "reason": "source_policy_authority_unresolved"}
        if (
            str(policy.get("governing_authority_device_id") or "") != current_device
            or int(policy.get("governing_authority_epoch") or 0) != current_epoch
        ):
            return {"allowed": False, "reason": "source_policy_authority_stale"}
    if not policy["allow_federation"]:
        return {"allowed": False, "reason": "source_site_federation_disabled"}
    if destination not in policy["allowed_peer_sites"]:
        return {"allowed": False, "reason": "destination_not_allowed_peer"}
    if permission == "remote_observation" and not policy["allow_remote_observation"]:
        return {"allowed": False, "reason": "remote_observation_disabled"}

    with db() as connection:
        row = connection.execute(
            """
            SELECT status,revision,origin_role,governing_authority_device_id,governing_authority_epoch
            FROM tracky_federation_permissions
            WHERE source_site_id=? AND destination_site_id=? AND scope=?
            """,
            (source, destination, permission),
        ).fetchone()
        state = connection.execute(
            "SELECT revocation_epoch FROM tracky_federation_policy_state WHERE id=1"
        ).fetchone()
    if row is None:
        return {"allowed": False, "reason": "permission_not_granted"}
    with db() as connection:
        revoked = connection.execute(
            "SELECT revision FROM tracky_federation_policy_revocations WHERE revocation_key=? LIMIT 1",
            (f"grant:{source}|{destination}|{permission}",),
        ).fetchone()
    if (
        str(row["status"] or "") != "granted"
        or (revoked is not None and int(revoked["revision"] or 0) >= int(row["revision"] or 0))
    ):
        return {"allowed": False, "reason": "permission_revoked"}
    if str(row["origin_role"] or "") == "cloud_mirror":
        try:
            current_device, current_epoch = _authority(source)
        except TrackyFederationPolicyError:
            return {"allowed": False, "reason": "permission_authority_unresolved"}
        if (
            str(row["governing_authority_device_id"] or "") != current_device
            or int(row["governing_authority_epoch"] or 0) != current_epoch
        ):
            return {"allowed": False, "reason": "permission_authority_stale"}

    if canonical_identity_id and permission in CONSENT_SCOPES:
        consent = recognition_decision(source, canonical_identity_id, permission)
        if not consent["allowed"]:
            return consent

    return {
        "allowed": True,
        "reason": "explicit_site_grant",
        "grant_revision": int(row["revision"] or 0),
        "policy_revision": int(policy["revision"] or 0),
        "revocation_epoch": int(state["revocation_epoch"] or 0) if state else 0,
    }

def recognition_decision(site_id: str, canonical_identity_id: str, scope: str) -> dict[str, Any]:
    site = _uuid(site_id, "site id")
    canonical = _uuid(canonical_identity_id, "canonical identity id")
    permission = _consent_scope(scope)
    with db() as connection:
        row = connection.execute(
            """
            SELECT status,revision,origin_role,governing_authority_device_id,governing_authority_epoch
            FROM tracky_recognition_consents
            WHERE site_id=? AND canonical_identity_id=? AND scope=?
            """,
            (site, canonical, permission),
        ).fetchone()
        state = connection.execute(
            "SELECT revocation_epoch FROM tracky_federation_policy_state WHERE id=1"
        ).fetchone()
    if row is None:
        return {"allowed": False, "reason": "consent_required"}
    if str(row["origin_role"] or "") == "cloud_mirror":
        try:
            current_device, current_epoch = _authority(site)
        except TrackyFederationPolicyError:
            return {"allowed": False, "reason": "consent_authority_unresolved"}
        if (
            str(row["governing_authority_device_id"] or "") != current_device
            or int(row["governing_authority_epoch"] or 0) != current_epoch
        ):
            return {"allowed": False, "reason": "consent_authority_stale"}
    status = str(row["status"] or "")
    with db() as connection:
        revoked = connection.execute(
            "SELECT revision FROM tracky_federation_policy_revocations WHERE revocation_key=? LIMIT 1",
            (f"consent:{site}|{canonical}|{permission}",),
        ).fetchone()
    if status != "granted" or (revoked is not None and int(revoked["revision"] or 0) >= int(row["revision"] or 0)):
        return {"allowed": False, "reason": "consent_denied" if status == "denied" else "consent_revoked"}
    return {
        "allowed": True,
        "reason": "site_scoped_consent",
        "revision": int(row["revision"] or 0),
        "revocation_epoch": int(state["revocation_epoch"] or 0) if state else 0,
    }

def filter_world_fragment(source_site_id: str, destination_site_id: str, fragment: dict[str, Any]) -> dict[str, Any] | None:
    source = _uuid(source_site_id, "source site id")
    destination = _uuid(destination_site_id, "destination site id")
    decision = permission_decision(source, destination, "semantic_world_read")
    if not decision.get("allowed"):
        return None
    copied = json.loads(json.dumps(fragment))
    entities = copied.get("entities") if isinstance(copied.get("entities"), list) else []
    relations = copied.get("relations") if isinstance(copied.get("relations"), list) else []
    denied_local_ids = {
        str(entity.get("local_id") or "")
        for entity in entities
        if isinstance(entity, dict) and str(entity.get("type") or "") == "person"
    }
    denied_local_ids.discard("")
    copied["entities"] = [
        entity for entity in entities
        if isinstance(entity, dict) and str(entity.get("type") or "") != "person"
    ]
    copied["context"] = {}
    copied["relations"] = [
        relation for relation in relations
        if isinstance(relation, dict)
        and str(relation.get("subject_local_id") or "") not in denied_local_ids
        and str(relation.get("object_local_id") or "") not in denied_local_ids
    ]
    return copied

def ingest_cloud_mirror(bundle: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(bundle, dict):
        raise TrackyFederationPolicyError("Federation policy relay must be an object.")
    if str(bundle.get("protocol") or "") != "physical_federation_policy_relay.v1":
        raise TrackyFederationPolicyError("Federation policy relay protocol is unsupported.")
    local = _local_site()
    destination = _uuid(bundle.get("destination_site_id"), "policy relay destination site id")
    if destination != local:
        raise TrackyFederationPolicyError("Federation policy relay is routed to a different local site.", 409)
    projections = bundle.get("projections") if isinstance(bundle.get("projections"), list) else []
    if len(projections) > 128:
        raise TrackyFederationPolicyError("Federation policy relay exceeds the mirror limit.")

    changed = stale = idempotent = 0
    for projection in projections:
        if not isinstance(projection, dict):
            continue
        if str(projection.get("protocol") or "") != FEDERATION_POLICY_PROTOCOL:
            raise TrackyFederationPolicyError("Mirrored federation policy protocol is unsupported.")
        source = _uuid(projection.get("governing_site_id"), "mirrored governing site id")
        if source == local:
            continue
        device = _uuid(projection.get("governing_authority_device_id"), "mirrored authority device id")
        epoch = max(0, int(projection.get("governing_authority_epoch") or 0))
        expected_device, expected_epoch = _authority(source)
        if device != expected_device or epoch != expected_epoch:
            raise TrackyFederationPolicyError("Mirrored federation policy authority does not match current topology.", 409)

        sites = projection.get("sites") if isinstance(projection.get("sites"), list) else []
        grants = projection.get("grants") if isinstance(projection.get("grants"), list) else []
        consents = projection.get("consents") if isinstance(projection.get("consents"), list) else []
        revocations = projection.get("revocations") if isinstance(projection.get("revocations"), list) else []

        with db() as connection:
            for item in sites[:8]:
                if not isinstance(item, dict):
                    continue
                site_id = _uuid(item.get("site_id"), "mirrored site policy id")
                if site_id != source:
                    raise TrackyFederationPolicyError("Mirrored policy contains a non-governing site.", 409)
                revision = max(1, int(item.get("revision") or 1))
                mode = str(item.get("mode") or "private")
                if mode not in SITE_MODES:
                    mode = "private"
                visibility = str(item.get("default_identity_visibility") or "none")
                if visibility not in {"none", "anonymous", "consented"}:
                    visibility = "none"
                peers = sorted({
                    _uuid(peer, "mirrored allowed peer site id")
                    for peer in (item.get("allowed_peer_sites") if isinstance(item.get("allowed_peer_sites"), list) else [])
                    if str(peer) != source
                })
                prior = connection.execute(
                    "SELECT * FROM tracky_federation_site_policies WHERE site_id=?",
                    (source,),
                ).fetchone()
                if prior is not None and revision < int(prior["revision"] or 0):
                    stale += 1
                    continue
                encoded_peers = json.dumps(peers, separators=(",", ":"))
                if prior is not None and revision == int(prior["revision"] or 0):
                    same = (
                        str(prior["mode"] or "") == mode
                        and bool(prior["allow_federation"]) == bool(item.get("allow_federation"))
                        and bool(prior["allow_remote_observation"]) == bool(item.get("allow_remote_observation"))
                        and str(prior["default_identity_visibility"] or "") == visibility
                        and str(prior["allowed_peer_sites_json"] or "[]") == encoded_peers
                    )
                    if not same:
                        raise TrackyFederationPolicyError("Mirrored site policy revision conflicts with local mirror.", 409)
                    connection.execute(
                        """
                        UPDATE tracky_federation_site_policies
                        SET governing_authority_device_id=?,governing_authority_epoch=?,updated_at=CURRENT_TIMESTAMP
                        WHERE site_id=?
                        """,
                        (device, epoch, source),
                    )
                    idempotent += 1
                    continue
                connection.execute(
                    """
                    INSERT INTO tracky_federation_site_policies(
                      site_id,revision,mode,allow_federation,allow_remote_observation,
                      default_identity_visibility,allowed_peer_sites_json,origin_role,
                      governing_authority_device_id,governing_authority_epoch,observed_updated_at_ms
                    ) VALUES (?,?,?,?,?,?,?,'cloud_mirror',?,?,0)
                    ON CONFLICT(site_id) DO UPDATE SET
                      revision=excluded.revision,mode=excluded.mode,
                      allow_federation=excluded.allow_federation,
                      allow_remote_observation=excluded.allow_remote_observation,
                      default_identity_visibility=excluded.default_identity_visibility,
                      allowed_peer_sites_json=excluded.allowed_peer_sites_json,
                      origin_role='cloud_mirror',
                      governing_authority_device_id=excluded.governing_authority_device_id,
                      governing_authority_epoch=excluded.governing_authority_epoch,
                      updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        source, revision, mode, 1 if item.get("allow_federation") else 0,
                        1 if item.get("allow_remote_observation") else 0,
                        visibility, encoded_peers, device, epoch,
                    ),
                )
                changed += 1

            for item in grants[:1024]:
                if not isinstance(item, dict):
                    continue
                grant_source = _uuid(item.get("source_site_id"), "mirrored permission source site id")
                grant_destination = _uuid(item.get("destination_site_id"), "mirrored permission destination site id")
                if grant_source != source or grant_destination != local:
                    raise TrackyFederationPolicyError("Mirrored permission is outside the source-to-local boundary.", 409)
                permission = _scope(item.get("scope"))
                status = str(item.get("status") or "revoked")
                if status not in {"granted", "revoked"}:
                    status = "revoked"
                revision = max(1, int(item.get("revision") or 1))
                reason = _text(item.get("reason"), 200)
                prior = connection.execute(
                    """
                    SELECT * FROM tracky_federation_permissions
                    WHERE source_site_id=? AND destination_site_id=? AND scope=?
                    """,
                    (source, local, permission),
                ).fetchone()
                if prior is not None and revision < int(prior["revision"] or 0):
                    stale += 1
                    continue
                if prior is not None and revision == int(prior["revision"] or 0):
                    if str(prior["status"] or "") != status or str(prior["reason"] or "") != reason:
                        raise TrackyFederationPolicyError("Mirrored permission revision conflicts with local mirror.", 409)
                    connection.execute(
                        """
                        UPDATE tracky_federation_permissions
                        SET governing_authority_device_id=?,governing_authority_epoch=?,updated_at=CURRENT_TIMESTAMP
                        WHERE source_site_id=? AND destination_site_id=? AND scope=?
                        """,
                        (device, epoch, source, local, permission),
                    )
                    idempotent += 1
                    continue
                connection.execute(
                    """
                    INSERT INTO tracky_federation_permissions(
                      source_site_id,destination_site_id,scope,status,revision,reason,origin_role,
                      governing_authority_device_id,governing_authority_epoch,granted_at_ms,revoked_at_ms
                    ) VALUES (?,?,?,?,?,?,'cloud_mirror',?,?,NULL,NULL)
                    ON CONFLICT(source_site_id,destination_site_id,scope) DO UPDATE SET
                      status=excluded.status,revision=excluded.revision,reason=excluded.reason,
                      origin_role='cloud_mirror',
                      governing_authority_device_id=excluded.governing_authority_device_id,
                      governing_authority_epoch=excluded.governing_authority_epoch,
                      updated_at=CURRENT_TIMESTAMP
                    """,
                    (source, local, permission, status, revision, reason, device, epoch),
                )
                changed += 1

            for item in consents[:2048]:
                if not isinstance(item, dict):
                    continue
                consent_site = _uuid(item.get("site_id"), "mirrored consent site id")
                if consent_site != source:
                    raise TrackyFederationPolicyError("Mirrored recognition consent belongs to a different site.", 409)
                canonical = _uuid(item.get("canonical_identity_id"), "mirrored canonical identity id")
                permission = _consent_scope(item.get("scope"))
                status = str(item.get("status") or "pending")
                if status not in CONSENT_STATES:
                    status = "pending"
                revision = max(1, int(item.get("revision") or 1))
                consent_source = _text(item.get("source") or "user", 80)
                reason = _text(item.get("reason"), 200)
                prior = connection.execute(
                    """
                    SELECT * FROM tracky_recognition_consents
                    WHERE site_id=? AND canonical_identity_id=? AND scope=?
                    """,
                    (source, canonical, permission),
                ).fetchone()
                if prior is not None and revision < int(prior["revision"] or 0):
                    stale += 1
                    continue
                if prior is not None and revision == int(prior["revision"] or 0):
                    if (
                        str(prior["status"] or "") != status
                        or str(prior["source"] or "") != consent_source
                        or str(prior["reason"] or "") != reason
                    ):
                        raise TrackyFederationPolicyError("Mirrored consent revision conflicts with local mirror.", 409)
                    connection.execute(
                        """
                        UPDATE tracky_recognition_consents
                        SET governing_authority_device_id=?,governing_authority_epoch=?,updated_at=CURRENT_TIMESTAMP
                        WHERE site_id=? AND canonical_identity_id=? AND scope=?
                        """,
                        (device, epoch, source, canonical, permission),
                    )
                    idempotent += 1
                    continue
                connection.execute(
                    """
                    INSERT INTO tracky_recognition_consents(
                      site_id,canonical_identity_id,scope,status,revision,source,reason,origin_role,
                      governing_authority_device_id,governing_authority_epoch,decided_at_ms
                    ) VALUES (?,?,?,?,?,?,?,'cloud_mirror',?,?,0)
                    ON CONFLICT(site_id,canonical_identity_id,scope) DO UPDATE SET
                      status=excluded.status,revision=excluded.revision,source=excluded.source,
                      reason=excluded.reason,origin_role='cloud_mirror',
                      governing_authority_device_id=excluded.governing_authority_device_id,
                      governing_authority_epoch=excluded.governing_authority_epoch,
                      updated_at=CURRENT_TIMESTAMP
                    """,
                    (source, canonical, permission, status, revision, consent_source, reason, device, epoch),
                )
                changed += 1

            for item in revocations[:2048]:
                if not isinstance(item, dict):
                    continue
                governing = _uuid(item.get("governing_site_id") or source, "mirrored revocation governing site id")
                if governing != source:
                    raise TrackyFederationPolicyError("Mirrored revocation belongs to a different site.", 409)
                key = _text(item.get("revocation_key") or item.get("key"), 700)
                if not key:
                    continue
                revision = max(1, int(item.get("revision") or 1))
                revocation_epoch = max(1, int(item.get("revocation_epoch") or 1))
                reason = _text(item.get("reason"), 200)
                prior = connection.execute(
                    "SELECT * FROM tracky_federation_policy_revocations WHERE revocation_key=?",
                    (key,),
                ).fetchone()
                if prior is not None and (
                    revocation_epoch < int(prior["revocation_epoch"] or 0)
                    or (
                        revocation_epoch == int(prior["revocation_epoch"] or 0)
                        and revision < int(prior["revision"] or 0)
                    )
                ):
                    stale += 1
                    continue
                if prior is not None and revocation_epoch == int(prior["revocation_epoch"] or 0) and revision == int(prior["revision"] or 0):
                    if str(prior["reason"] or "") != reason:
                        raise TrackyFederationPolicyError("Mirrored revocation revision conflicts with local mirror.", 409)
                    idempotent += 1
                    continue
                connection.execute(
                    """
                    INSERT INTO tracky_federation_policy_revocations(
                      revocation_key,governing_site_id,revision,revocation_epoch,reason,origin_role,revoked_at_ms
                    ) VALUES (?,?,?,?,?,'cloud_mirror',0)
                    ON CONFLICT(revocation_key) DO UPDATE SET
                      governing_site_id=excluded.governing_site_id,revision=excluded.revision,
                      revocation_epoch=excluded.revocation_epoch,reason=excluded.reason,
                      origin_role='cloud_mirror',updated_at=CURRENT_TIMESTAMP
                    """,
                    (key, source, revision, revocation_epoch, reason),
                )
                changed += 1

    return {"accepted": True, "changed": changed, "stale": stale, "idempotent": idempotent}


def filter_world_report_for_local(report: dict[str, Any], site_id: str | None = None) -> dict[str, Any]:
    local = _local_site()
    sites = report.get("sites") if isinstance(report.get("sites"), list) else []
    visible = []
    for fragment in sites:
        if not isinstance(fragment, dict):
            continue
        source = str(fragment.get("site_id") or "")
        if site_id and source != site_id:
            continue
        if source == local:
            visible.append(fragment)
            continue
        if not source:
            continue
        decision = permission_decision(source, local, "semantic_world_read")
        if decision.get("allowed"):
            sanitized = json.loads(json.dumps(fragment))
            remote_entities = sanitized.get("entities") if isinstance(sanitized.get("entities"), list) else []
            denied_ids = {
                str(item.get("local_id") or "")
                for item in remote_entities
                if isinstance(item, dict) and str(item.get("type") or "") == "person"
            }
            denied_ids.discard("")
            sanitized["entities"] = [
                item for item in remote_entities
                if isinstance(item, dict) and str(item.get("type") or "") != "person"
            ]
            sanitized["relations"] = [
                item for item in (sanitized.get("relations") if isinstance(sanitized.get("relations"), list) else [])
                if isinstance(item, dict)
                and str(item.get("subject_local_id") or "") not in denied_ids
                and str(item.get("object_local_id") or "") not in denied_ids
            ]
            sanitized["context"] = {}
            visible.append(sanitized)
    entities = []
    relations = []
    for fragment in visible:
        for item in fragment.get("entities", []):
            if isinstance(item, dict):
                entities.append({**item, "site_id": fragment.get("site_id")})
        for item in fragment.get("relations", []):
            if isinstance(item, dict):
                relations.append({**item, "site_id": fragment.get("site_id")})
    return {
        **report,
        "available": bool(visible),
        "site_count": len(visible),
        "sites": visible,
        "entities": entities,
        "relations": relations,
        "policy_filtered": True,
        "destination_site_id": local,
    }


def filter_identity_report_for_local(report: dict[str, Any]) -> dict[str, Any]:
    local = _local_site()
    kept_links = []
    identity_ids: set[str] = set()
    for link in report.get("links", []):
        if not isinstance(link, dict):
            continue
        if str(link.get("origin_role") or "") != "cloud_mirror":
            kept_links.append(link)
            identity_ids.add(str(link.get("canonical_identity_id") or ""))
            continue
        source = str(link.get("governing_site_id") or "")
        if not source:
            continue
        read = permission_decision(source, local, "identity_continuity_read")
        if not read.get("allowed"):
            continue
        if str(link.get("entity_type") or "") == "person":
            consent = recognition_decision(source, str(link.get("canonical_identity_id") or ""), "identity_linking")
            if not consent.get("allowed"):
                continue
        kept_links.append(link)
        identity_ids.add(str(link.get("canonical_identity_id") or ""))
    identities = [
        item for item in report.get("identities", [])
        if isinstance(item, dict)
        and str(item.get("canonical_identity_id") or "") in identity_ids
    ]
    return {**report, "identities": identities, "links": kept_links, "policy_filtered": True}


def filter_mobile_report_for_local(report: dict[str, Any]) -> dict[str, Any]:
    local = _local_site()
    transitions = []
    for item in report.get("transitions", []):
        if not isinstance(item, dict):
            continue
        if str(item.get("origin_role") or "") != "cloud_mirror":
            transitions.append(item)
            continue
        source = str(item.get("source_site_id") or "")
        if source and permission_decision(source, local, "agent_context_read").get("allowed"):
            transitions.append(item)
    return {
        **report,
        "transitions": transitions,
        "active_count": len([item for item in transitions if str(item.get("state") or "") not in {"arrived", "canceled"}]),
        "policy_filtered": True,
    }


def resolve_identity_for_local(entity_ref: str) -> dict[str, Any] | None:
    ref = str(entity_ref or "").strip()
    if not ref:
        raise TrackyFederationPolicyError("Entity ref is required.")
    from . import tracky_identity_continuity
    report = filter_identity_report_for_local(tracky_identity_continuity.current_report())
    matches = [
        item for item in report.get("identities", [])
        if isinstance(item, dict)
        and item.get("status") == "active"
        and ref in (item.get("members") if isinstance(item.get("members"), list) else [])
    ]
    if len(matches) > 1:
        raise TrackyFederationPolicyError(
            "Entity ref resolves to multiple policy-visible canonical identities.", 409
        )
    return matches[0] if matches else None


def current_report() -> dict[str, Any]:
    with db() as connection:
        state = connection.execute(
            "SELECT revision,revocation_epoch FROM tracky_federation_policy_state WHERE id=1"
        ).fetchone()
        policies = [dict(row) for row in connection.execute(
            "SELECT * FROM tracky_federation_site_policies ORDER BY site_id"
        ).fetchall()]
        grants = [dict(row) for row in connection.execute(
            "SELECT * FROM tracky_federation_permissions ORDER BY source_site_id,destination_site_id,scope"
        ).fetchall()]
        consents = [dict(row) for row in connection.execute(
            "SELECT * FROM tracky_recognition_consents ORDER BY site_id,canonical_identity_id,scope"
        ).fetchall()]
        revocations = [dict(row) for row in connection.execute(
            "SELECT * FROM tracky_federation_policy_revocations ORDER BY revocation_epoch,revocation_key"
        ).fetchall()]
    for policy in policies:
        try:
            policy["allowed_peer_sites"] = json.loads(policy.pop("allowed_peer_sites_json") or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            policy["allowed_peer_sites"] = []
        policy["allow_federation"] = bool(policy["allow_federation"])
        policy["allow_remote_observation"] = bool(policy["allow_remote_observation"])
    return {
        "available": bool(policies or grants or consents or revocations),
        "protocol": FEDERATION_POLICY_PROTOCOL,
        "schema_version": 1,
        "revision": int(state["revision"] or 0) if state else 0,
        "revocation_epoch": int(state["revocation_epoch"] or 0) if state else 0,
        "sites": policies,
        "grants": grants,
        "consents": consents,
        "revocations": revocations,
        "semantic_only": True,
        "authority_assignment": "local_site_policy",
        "deny_by_default": True,
        "raw_perception": False,
    }

def cloud_projection(local_site_id: str | None = None) -> dict[str, Any]:
    local = _uuid(local_site_id or _local_site(), "local site id")
    report = current_report()
    site = [item for item in report["sites"] if str(item.get("site_id") or "") == local]
    grants = [item for item in report["grants"] if str(item.get("source_site_id") or "") == local and str(item.get("origin_role") or "") == "local_governed"]
    consents = [item for item in report["consents"] if str(item.get("site_id") or "") == local and str(item.get("origin_role") or "") == "local_governed"]
    revocations = [item for item in report["revocations"] if str(item.get("governing_site_id") or "") == local and str(item.get("origin_role") or "") == "local_governed"]
    device, epoch = _authority(local)
    return {
        "protocol": FEDERATION_POLICY_PROTOCOL,
        "schema_version": 1,
        "revision": report["revision"],
        "revocation_epoch": report["revocation_epoch"],
        "sites": site,
        "grants": grants,
        "consents": consents,
        "revocations": revocations,
        "governing_site_id": local,
        "governing_authority_device_id": device,
        "governing_authority_epoch": epoch,
        "semantic_only": True,
        "summary_only": True,
        "authority_assignment": "local_site_policy",
        "cloud_role": "mirror_relay_enforcer",
        "cloud_can_grant": False,
        "cloud_can_revoke": False,
        "cloud_can_change_consent": False,
        "raw_perception": False,
    }

def public_capability() -> dict[str, Any]:
    return {
        "version": TRACKY_FEDERATION_POLICY_VERSION,
        "protocol": FEDERATION_POLICY_PROTOCOL,
        "permission_scopes": sorted(PERMISSION_SCOPES),
        "consent_scopes": sorted(CONSENT_SCOPES),
        "deny_by_default": True,
        "authority_assignment": "local_site_policy",
        "cloud_role": "mirror_relay_enforcer",
        "cloud_can_grant": False,
        "cloud_can_revoke": False,
        "cloud_can_change_consent": False,
        "raw_perception": False,
    }
