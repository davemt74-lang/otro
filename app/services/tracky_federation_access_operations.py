from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ..database import db
from . import tracky_federation_policy, tracky_site_topology, tracky_sync_visibility

TRACKY_ACCESS_OPERATIONS_VERSION = "2.80"
FEDERATION_ACCESS_OPERATIONS_PROTOCOL = "physical_federation_access_operations.v1"
ACCESS_CATEGORIES = {
    "observation": ["remote_observation"],
    "retention": ["history_query"],
    "identification": ["person_recognition", "voice_matching", "identity_linking"],
    "cloud_sync": ["semantic_world_read"],
    "agent_use": ["agent_context_read"],
    "federation_sharing": ["semantic_world_read", "identity_continuity_read"],
}


def _text(value: Any, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[: max(1, int(limit))]


def _number(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _revocation_map(policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in policy.get("revocations", []):
        if not isinstance(row, dict):
            continue
        key = _text(row.get("revocation_key") or row.get("key"), 320)
        if not key:
            continue
        candidate = {
            "key": key,
            "revision": _number(row.get("revision")),
            "revocation_epoch": _number(row.get("revocation_epoch")),
            "reason": _text(row.get("reason"), 200),
            "at": _number(row.get("revoked_at_ms") or row.get("at")),
        }
        prior = out.get(key)
        if (
            prior is None
            or candidate["revocation_epoch"] > prior["revocation_epoch"]
            or (
                candidate["revocation_epoch"] == prior["revocation_epoch"]
                and candidate["revision"] > prior["revision"]
            )
        ):
            out[key] = candidate
    return out


def _effective_grant(row: dict[str, Any], revocations: dict[str, dict[str, Any]]) -> dict[str, Any]:
    source = _text(row.get("source_site_id"), 64).lower()
    destination = _text(row.get("destination_site_id"), 64).lower()
    scope = _text(row.get("scope"), 80).lower()
    tombstone = revocations.get(f"grant:{source}|{destination}|{scope}")
    revision = _number(row.get("revision"))
    raw_status = _text(row.get("status") or "revoked", 30).lower()
    suppressed = bool(tombstone and tombstone["revision"] >= revision)
    status = "revoked" if suppressed else ("granted" if raw_status == "granted" else "revoked")
    return {
        "source_site_id": source,
        "destination_site_id": destination,
        "scope": scope,
        "status": status,
        "revision": revision,
        "reason": _text(row.get("reason"), 200),
        "origin_role": _text(row.get("origin_role") or "unknown", 40),
        "governing_authority_device_id": _text(row.get("governing_authority_device_id"), 64).lower(),
        "governing_authority_epoch": _number(row.get("governing_authority_epoch")),
        "granted_at_ms": _number(row.get("granted_at_ms")),
        "revoked_at_ms": _number(row.get("revoked_at_ms")),
        "revocation_epoch": _number((tombstone or {}).get("revocation_epoch")),
        "revocation_reason": _text((tombstone or {}).get("reason"), 200),
        "effective_allowed": status == "granted",
        "stale_grant_suppressed": suppressed and raw_status == "granted",
    }


def _effective_consent(
    row: dict[str, Any],
    revocations: dict[str, dict[str, Any]],
    now_ms: int,
) -> dict[str, Any]:
    site = _text(row.get("site_id"), 64).lower()
    identity = _text(row.get("canonical_identity_id"), 64).lower()
    scope = _text(row.get("scope"), 80).lower()
    tombstone = revocations.get(f"consent:{site}|{identity}|{scope}")
    revision = _number(row.get("revision"))
    raw_status = _text(row.get("status") or "pending", 30).lower()
    expires_at_ms = _number(row.get("expires_at_ms"))
    expired = expires_at_ms > 0 and now_ms >= expires_at_ms
    suppressed = bool(tombstone and tombstone["revision"] >= revision)
    status = raw_status
    if suppressed:
        status = "revoked"
    elif expired and raw_status == "granted":
        status = "expired"
    elif status not in {"pending", "granted", "denied", "revoked"}:
        status = "pending"
    return {
        "site_id": site,
        "canonical_identity_id": identity,
        "scope": scope,
        "status": status,
        "revision": revision,
        "source": _text(row.get("source") or "user", 80),
        "reason": _text(row.get("reason"), 200),
        "decided_at_ms": _number(row.get("decided_at_ms")),
        "expires_at_ms": expires_at_ms,
        "governing_authority_device_id": _text(row.get("governing_authority_device_id"), 64).lower(),
        "governing_authority_epoch": _number(row.get("governing_authority_epoch")),
        "revocation_epoch": _number((tombstone or {}).get("revocation_epoch")),
        "revocation_reason": _text((tombstone or {}).get("reason"), 200),
        "effective_allowed": status == "granted",
        "stale_grant_suppressed": suppressed and raw_status == "granted",
    }


def _category_state(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "denied"
    allowed = sum(1 for row in rows if row.get("effective_allowed"))
    if allowed == len(rows):
        return "allowed"
    if allowed == 0:
        return "revoked" if any(row.get("status") == "revoked" for row in rows) else "denied"
    return "limited"


def _history(limit: int = 200) -> list[dict[str, Any]]:
    with db() as connection:
        rows = connection.execute(
            """
            SELECT id,governing_site_id,event_type,revision,revocation_epoch,detail_json,created_at
            FROM tracky_federation_policy_history
            ORDER BY id DESC LIMIT ?
            """,
            (max(1, min(1000, int(limit))),),
        ).fetchall()
    out = []
    for row in rows:
        try:
            detail = json.loads(row["detail_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            detail = {}
        out.append({
            "event_id": f"policy-history:{int(row['id'])}",
            "governing_site_id": str(row["governing_site_id"] or ""),
            "event_type": str(row["event_type"] or ""),
            "revision": int(row["revision"] or 0),
            "revocation_epoch": int(row["revocation_epoch"] or 0),
            "detail": detail if isinstance(detail, dict) else {},
            "occurred_at": str(row["created_at"] or ""),
            "immutable": True,
        })
    return out


def _topology_sites() -> list[dict[str, Any]]:
    topology = tracky_site_topology.current_topology()
    return [row for row in topology.get("sites", []) if isinstance(row, dict)]


def build_report(
    policy: dict[str, Any],
    topology: dict[str, Any],
    sync_visibility: dict[str, Any],
    *,
    history: list[dict[str, Any]] | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    now_ms = int(now_ms if now_ms is not None else datetime.now(timezone.utc).timestamp() * 1000)
    local_site = _text(sync_visibility.get("local_site_id"), 64).lower()
    if not local_site:
        for row in policy.get("sites", []):
            if isinstance(row, dict) and row.get("origin_role") == "local_governed":
                local_site = _text(row.get("site_id"), 64).lower()
                break

    site_map: dict[str, dict[str, Any]] = {}
    for row in topology.get("sites", []):
        if not isinstance(row, dict):
            continue
        site_id = _text(row.get("id") or row.get("site_id"), 64).lower()
        if not site_id:
            continue
        site_map[site_id] = {
            "id": site_id,
            "label": _text(row.get("label") or site_id, 160),
            "authority_device_id": _text(row.get("authority_device_id") or (row.get("authority") or {}).get("device_id"), 64).lower(),
            "authority_epoch": _number(row.get("authority_epoch") or (row.get("authority") or {}).get("epoch")),
        }
    for row in policy.get("sites", []):
        if not isinstance(row, dict):
            continue
        site_id = _text(row.get("site_id"), 64).lower()
        if site_id and site_id not in site_map:
            site_map[site_id] = {"id": site_id, "label": site_id, "authority_device_id": "", "authority_epoch": 0}

    revocations = _revocation_map(policy)
    grants = [
        _effective_grant(row, revocations)
        for row in policy.get("grants", [])
        if isinstance(row, dict)
    ]
    consents = [
        _effective_consent(row, revocations, now_ms)
        for row in policy.get("consents", [])
        if isinstance(row, dict)
    ]
    local_policy = next(
        (
            row for row in policy.get("sites", [])
            if isinstance(row, dict) and _text(row.get("site_id"), 64).lower() == local_site
        ),
        None,
    )

    sync_map = {
        _text(row.get("site_id"), 64).lower(): row
        for row in sync_visibility.get("sites", [])
        if isinstance(row, dict)
    }
    peers = []
    for site in sorted(site_map.values(), key=lambda row: row["label"]):
        if site["id"] == local_site:
            continue
        scope_rows = [
            row for row in grants
            if row["source_site_id"] == local_site and row["destination_site_id"] == site["id"]
        ]
        categories = {}
        for category, scopes in ACCESS_CATEGORIES.items():
            rows = []
            for scope in scopes:
                match = next((row for row in scope_rows if row["scope"] == scope), None)
                rows.append(match or {
                    "source_site_id": local_site,
                    "destination_site_id": site["id"],
                    "scope": scope,
                    "status": "not_granted",
                    "revision": 0,
                    "effective_allowed": False,
                    "stale_grant_suppressed": False,
                    "revocation_epoch": 0,
                })
            categories[category] = {"state": _category_state(rows), "scopes": rows}
        sync = sync_map.get(site["id"]) or {}
        peers.append({
            "site_id": site["id"],
            "label": site["label"],
            "policy_peer_allowed": site["id"] in (local_policy or {}).get("allowed_peer_sites", []),
            "site_policy_mode": _text((local_policy or {}).get("mode") or "private", 30),
            "federation_enabled": bool((local_policy or {}).get("allow_federation")),
            "remote_observation_enabled": bool((local_policy or {}).get("allow_remote_observation")),
            "categories": categories,
            "sync": {
                "status": _text(sync.get("status") or "unknown", 24),
                "fresh": bool(sync.get("fresh")),
                "stale_age_ms": _number(sync.get("stale_age_ms")),
                "reconciliation_required": bool(sync.get("reconciliation_required", True)),
            },
            "revocation_protection": {
                "stale_remote_grant_can_restore_access": False,
                "revocation_wins": True,
                "remote_policy_freshness_required": not bool(sync.get("fresh")),
            },
        })

    identity_map: dict[str, dict[str, Any]] = {}
    for row in consents:
        if row["site_id"] != local_site:
            continue
        item = identity_map.setdefault(
            row["canonical_identity_id"],
            {"canonical_identity_id": row["canonical_identity_id"], "consents": []},
        )
        item["consents"].append(row)
    identities = []
    for item in identity_map.values():
        states = [row["status"] for row in item["consents"]]
        state = "pending"
        if "revoked" in states:
            state = "revoked"
        elif "expired" in states:
            state = "expired"
        elif states and all(value == "granted" for value in states):
            state = "allowed"
        elif "granted" in states:
            state = "limited"
        elif "denied" in states:
            state = "denied"
        identities.append({**item, "state": state})
    identities.sort(key=lambda row: row["canonical_identity_id"])

    suppressed = [row for row in [*grants, *consents] if row.get("stale_grant_suppressed")]
    return {
        "protocol": FEDERATION_ACCESS_OPERATIONS_PROTOCOL,
        "version": TRACKY_ACCESS_OPERATIONS_VERSION,
        "schema_version": 1,
        "generated_at": now_ms,
        "local_site_id": local_site,
        "policy_revision": _number(policy.get("revision")),
        "revocation_epoch": _number(policy.get("revocation_epoch")),
        "local_policy": local_policy,
        "peers": peers,
        "grants": grants,
        "consents": consents,
        "identities": identities,
        "revocations": sorted(revocations.values(), key=lambda row: -row["revocation_epoch"]),
        "history": history or [],
        "counts": {
            "peers": len(peers),
            "grants_allowed": sum(1 for row in grants if row["effective_allowed"]),
            "grants_revoked": sum(1 for row in grants if row["status"] == "revoked"),
            "consents_allowed": sum(1 for row in consents if row["status"] == "granted"),
            "consents_denied": sum(1 for row in consents if row["status"] == "denied"),
            "consents_revoked": sum(1 for row in consents if row["status"] == "revoked"),
            "consents_expired": sum(1 for row in consents if row["status"] == "expired"),
            "stale_grants_suppressed": len(suppressed),
        },
        "agent_context": {
            "local_site_id": local_site,
            "policy_revision": _number(policy.get("revision")),
            "revocation_epoch": _number(policy.get("revocation_epoch")),
            "active_revocations": sum(1 for row in grants if row["status"] == "revoked")
                + sum(1 for row in consents if row["status"] in {"revoked", "denied", "expired"}),
            "stale_grants_suppressed": len(suppressed),
            "summary": (
                f"{len(suppressed)} stale grant{'s' if len(suppressed) != 1 else ''} suppressed by newer revocation."
                if suppressed else "No stale grants are overriding current revocations."
            ),
            "revocation_wins": True,
        },
        "operations": {
            "site_policy_local_only": True,
            "grants_local_source_only": True,
            "consent_local_site_only": True,
            "cloud_read_only": True,
            "paired_apps_read_only": True,
        },
        "boundaries": [
            "deny-by-default",
            "revocation-always-wins",
            "stale-grant-never-resurrects-access",
            "site-local-policy-authority",
            "consent-required-for-recognition",
            "cloud-mirror-only",
            "cross-site-identity-merge-disabled",
            "raw-perception-never-federated",
        ],
    }


def current_report() -> dict[str, Any]:
    topology = tracky_site_topology.current_topology()
    policy = tracky_federation_policy.current_report()
    sync = tracky_sync_visibility.current_report()
    return build_report(policy, topology, sync, history=_history(250))


def update_site_policy(payload: dict[str, Any]) -> dict[str, Any]:
    result = tracky_federation_policy.set_site_policy(
        mode=_text(payload.get("mode") or "private", 30),
        allow_federation=bool(payload.get("allow_federation")),
        allow_remote_observation=bool(payload.get("allow_remote_observation")),
        default_identity_visibility=_text(payload.get("default_identity_visibility") or "none", 30),
        allowed_peer_sites=[
            _text(value, 64).lower()
            for value in (payload.get("allowed_peer_sites") if isinstance(payload.get("allowed_peer_sites"), list) else [])
        ],
    )
    return {"updated": True, "site_policy": result, "access": current_report()}


def set_permission(payload: dict[str, Any]) -> dict[str, Any]:
    action = _text(payload.get("action") or "", 20).lower()
    destination = _text(payload.get("destination_site_id"), 64).lower()
    scope = _text(payload.get("scope"), 80).lower()
    reason = _text(payload.get("reason") or ("operator_" + action), 200)
    if action == "grant":
        decision = tracky_federation_policy.grant_permission(destination, scope, reason=reason)
    elif action == "revoke":
        decision = tracky_federation_policy.revoke_permission(destination, scope, reason=reason)
    else:
        raise tracky_federation_policy.TrackyFederationPolicyError("Permission action must be grant or revoke.")
    return {"updated": True, "decision": decision, "access": current_report()}


def set_consent(payload: dict[str, Any]) -> dict[str, Any]:
    decision = tracky_federation_policy.set_recognition_consent(
        _text(payload.get("canonical_identity_id"), 64).lower(),
        _text(payload.get("scope"), 80).lower(),
        _text(payload.get("status"), 30).lower(),
        source="operator",
        reason=_text(payload.get("reason"), 200),
    )
    return {"updated": True, "decision": decision, "access": current_report()}


def public_capability() -> dict[str, Any]:
    return {
        "version": TRACKY_ACCESS_OPERATIONS_VERSION,
        "protocol": FEDERATION_ACCESS_OPERATIONS_PROTOCOL,
        "categories": sorted(ACCESS_CATEGORIES),
        "permission_states": ["allowed", "denied", "limited", "revoked"],
        "consent_states": ["allowed", "denied", "limited", "expired", "revoked", "pending"],
        "local_site_policy_operations": True,
        "local_grant_revoke_operations": True,
        "local_consent_operations": True,
        "revocation_wins": True,
        "stale_remote_grant_can_restore_access": False,
        "cloud_read_only": True,
        "paired_apps_read_only": True,
        "authority_mutation": False,
        "cross_site_identity_merge": False,
    }
