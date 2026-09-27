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
    if not policy["allow_federation"]:
        return {"allowed": False, "reason": "source_site_federation_disabled"}
    if destination not in policy["allowed_peer_sites"]:
        return {"allowed": False, "reason": "destination_not_allowed_peer"}
    if permission == "remote_observation" and not policy["allow_remote_observation"]:
        return {"allowed": False, "reason": "remote_observation_disabled"}

    with db() as connection:
        row = connection.execute(
            """
            SELECT status,revision FROM tracky_federation_permissions
            WHERE source_site_id=? AND destination_site_id=? AND scope=?
            """,
            (source, destination, permission),
        ).fetchone()
        state = connection.execute(
            "SELECT revocation_epoch FROM tracky_federation_policy_state WHERE id=1"
        ).fetchone()
    if row is None:
        return {"allowed": False, "reason": "permission_not_granted"}
    if str(row["status"] or "") != "granted":
        return {"allowed": False, "reason": "permission_revoked"}

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
            SELECT status,revision FROM tracky_recognition_consents
            WHERE site_id=? AND canonical_identity_id=? AND scope=?
            """,
            (site, canonical, permission),
        ).fetchone()
        state = connection.execute(
            "SELECT revocation_epoch FROM tracky_federation_policy_state WHERE id=1"
        ).fetchone()
    if row is None:
        return {"allowed": False, "reason": "consent_required"}
    status = str(row["status"] or "")
    if status != "granted":
        return {"allowed": False, "reason": "consent_revoked" if status == "revoked" else "consent_denied" if status == "denied" else "consent_required"}
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
