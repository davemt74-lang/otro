from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from ..database import db
from . import (
    fleet_management,
    hardware_adapters,
    tracky_federation_access_operations,
    tracky_federation_agent_health,
    tracky_federation_fleet_health,
    tracky_federation_reconciliation,
    tracky_site_topology,
)

VERSION = "2.80"
PROTOCOL = "physical_federation_governed_operations.v1"
OPERATIONS = (
    "reconnect",
    "reconcile",
    "restart_runtime",
    "request_update",
    "revoke_site",
    "revoke_device",
    "transfer_authority",
)
STATES = (
    "proposed",
    "awaiting_approval",
    "approved",
    "queued",
    "running",
    "reconciling",
    "completed",
    "failed",
    "rejected",
    "cancelled",
    "expired",
)
TERMINAL = {"completed", "failed", "rejected", "cancelled", "expired"}
HIGH_RISK = {"restart_runtime", "request_update", "revoke_site", "revoke_device", "transfer_authority"}
TRANSITIONS = {
    "proposed": {"awaiting_approval", "approved", "rejected", "cancelled", "expired"},
    "awaiting_approval": {"approved", "rejected", "cancelled", "expired"},
    "approved": {"queued", "rejected", "cancelled", "expired"},
    "queued": {"running", "failed", "cancelled", "expired"},
    "running": {"reconciling", "completed", "failed", "cancelled", "expired"},
    "reconciling": {"completed", "failed", "cancelled", "expired"},
    "completed": set(),
    "failed": set(),
    "rejected": set(),
    "cancelled": set(),
    "expired": set(),
}


class FederationOperationError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = int(status_code)


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _text(value: Any, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[: max(1, int(limit))]


def _site(value: Any) -> str:
    return _text(value, 64).lower()


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except (ValueError, TypeError, json.JSONDecodeError):
        return default


def _row(row: Any) -> dict[str, Any]:
    return {
        "request_id": str(row["request_id"]),
        "idempotency_key": str(row["idempotency_key"]),
        "operation_type": str(row["operation_type"]),
        "target_site_id": str(row["target_site_id"]),
        "device_id": str(row["device_id"] or ""),
        "new_authority_device_id": str(row["new_authority_device_id"] or ""),
        "state": str(row["state"]),
        "requires_approval": bool(row["requires_approval"]),
        "requires_reconciliation": bool(row["requires_reconciliation"]),
        "expires_at_ms": int(row["expires_at_ms"] or 0),
        "actor": _loads(row["actor_json"], {}),
        "reason_codes": _loads(row["reason_codes_json"], []),
        "parameters": _loads(row["parameters_json"], {}),
        "authority_epoch_before": int(row["authority_epoch_before"] or 0),
        "authority_epoch_after": int(row["authority_epoch_after"] or 0),
        "result": _loads(row["result_json"], {}),
        "last_error": str(row["last_error"] or ""),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
    }


def _get(request_id: str) -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            "SELECT * FROM tracky_federation_operation_ledger WHERE request_id=? LIMIT 1",
            (request_id,),
        ).fetchone()
    if row is None:
        raise FederationOperationError("Federation operation was not found.", 404)
    return _row(row)


def _set(
    request_id: str,
    state: str,
    *,
    result: dict[str, Any] | None = None,
    error: str = "",
    authority_epoch_after: int | None = None,
) -> dict[str, Any]:
    if state not in STATES:
        raise FederationOperationError("Federation operation state is invalid.")
    with db() as connection:
        current = connection.execute(
            "SELECT state FROM tracky_federation_operation_ledger WHERE request_id=?",
            (request_id,),
        ).fetchone()
        if current is None:
            raise FederationOperationError("Federation operation was not found.", 404)
        current_state = str(current["state"])
        if current_state in TERMINAL and current_state != state:
            raise FederationOperationError("Terminal federation operation is immutable.", 409)
        if current_state != state and state not in TRANSITIONS.get(current_state, set()):
            raise FederationOperationError("Federation operation transition is not allowed.", 409)
        connection.execute(
            """UPDATE tracky_federation_operation_ledger
               SET state=?,result_json=?,last_error=?,
                   authority_epoch_after=COALESCE(?,authority_epoch_after),
                   updated_at=CURRENT_TIMESTAMP
               WHERE request_id=?""",
            (state, _json(result or {}), _text(error, 500), authority_epoch_after, request_id),
        )
        if current_state != state:
            connection.execute(
                "INSERT INTO tracky_federation_operation_events(request_id,state,detail_json) VALUES (?,?,?)",
                (request_id, state, _json(result or {"error": _text(error, 500)})),
            )
    return _get(request_id)


def _expire_if_due(row: dict[str, Any]) -> dict[str, Any]:
    expires_at_ms = int(row.get("expires_at_ms") or 0)
    if row["state"] not in TERMINAL and expires_at_ms > 0 and _now_ms() >= expires_at_ms:
        return _set(row["request_id"], "expired", result={"expired": True})
    return row


def _expire_due_operations() -> None:
    with db() as connection:
        rows = connection.execute(
            """SELECT request_id FROM tracky_federation_operation_ledger
               WHERE state NOT IN ('completed','failed','rejected','cancelled','expired')
                 AND expires_at_ms>0 AND expires_at_ms<=?""",
            (_now_ms(),),
        ).fetchall()
    for row in rows:
        try:
            _set(str(row["request_id"]), "expired", result={"expired": True})
        except FederationOperationError:
            pass


def propose(payload: dict[str, Any], *, actor: dict[str, Any] | None = None) -> dict[str, Any]:
    op = _text(payload.get("operation_type"), 40).lower()
    if op not in OPERATIONS:
        raise FederationOperationError("Unsupported federation operation.")
    target = _site(payload.get("target_site_id"))
    if not target:
        raise FederationOperationError("Target site is required.")

    actor = dict(actor or payload.get("actor") or {})
    actor_type = _text(actor.get("actor_type") or actor.get("type") or "user", 30).lower()
    request_id = _text(payload.get("request_id"), 128) or "fop-" + uuid.uuid4().hex
    idempotency_key = _text(payload.get("idempotency_key"), 160) or request_id
    requested_at_ms = _now_ms()
    requested_expiry = int(payload.get("expires_at_ms") or 0)
    expires_at_ms = requested_expiry if requested_expiry > requested_at_ms else requested_at_ms + 900_000
    expires_at_ms = max(requested_at_ms + 60_000, min(expires_at_ms, requested_at_ms + 86_400_000))

    access = tracky_federation_access_operations.current_report()
    health = tracky_federation_agent_health.current_report()
    fleet = tracky_federation_fleet_health.current_report()
    local = _site(health.get("local_site_id") or fleet.get("local_site_id") or access.get("local_site_id"))
    peer = next((item for item in access.get("peers", []) if _site(item.get("site_id")) == target), {})
    health_site = next((item for item in health.get("sites", []) if _site(item.get("site_id")) == target), {})
    reasons: list[str] = []

    if target != local and not peer.get("policy_peer_allowed"):
        reasons.append("site_not_permitted")
    if actor_type == "agent":
        reasons.append("agent_may_propose_only")
    elif actor_type == "cloud_user":
        reasons.append("cloud_request_requires_local_approval")
    elif actor_type not in {"user", "owner", "admin", "system"}:
        reasons.append("actor_not_authorized")

    device_id = _text(payload.get("device_id"), 80)
    parameters = payload.get("parameters") if isinstance(payload.get("parameters"), dict) else {}
    if op in {"restart_runtime", "request_update", "revoke_device"} and not device_id:
        reasons.append("device_required")
    if op == "request_update" and (not _text(parameters.get("package_sha256"), 64) or not _text(parameters.get("release_version"), 40)):
        reasons.append("update_package_required")
    if op == "revoke_site" and target == local:
        reasons.append("revoke_site_must_target_peer")
    if op in {"restart_runtime", "request_update", "revoke_device", "transfer_authority"} and target != local:
        reasons.append("operation_requires_origin_local")
    if op == "transfer_authority":
        if not payload.get("new_authority_device_id"):
            reasons.append("new_authority_device_required")
        if not payload.get("confirmation_token"):
            reasons.append("explicit_confirmation_required")
        if not (
            health_site.get("state") == "current"
            and health_site.get("fresh")
            and health_site.get("recovery_complete")
        ):
            reasons.append("authority_transfer_requires_current_source")

    hard_blocks = [
        reason
        for reason in reasons
        if reason not in {"agent_may_propose_only", "cloud_request_requires_local_approval"}
    ]
    requires_approval = (
        op in HIGH_RISK
        or actor_type in {"agent", "cloud_user"}
        or bool(payload.get("require_approval"))
    )
    state = "rejected" if hard_blocks else ("awaiting_approval" if requires_approval else "approved")
    requires_reconciliation = op in {
        "reconnect",
        "reconcile",
        "restart_runtime",
        "request_update",
        "transfer_authority",
    }

    topology = tracky_site_topology.current_topology()
    topology_site = next(
        (item for item in topology.get("sites", []) if _site(item.get("id") or item.get("site_id")) == target),
        {},
    )
    authority_epoch_before = int(
        topology_site.get("authority_epoch")
        or (topology_site.get("authority") or {}).get("epoch")
        or 0
    )

    with db() as connection:
        existing = connection.execute(
            "SELECT * FROM tracky_federation_operation_ledger WHERE idempotency_key=? LIMIT 1",
            (idempotency_key,),
        ).fetchone()
        if existing is not None:
            row = _row(existing)
            same = (
                row["operation_type"] == op
                and row["target_site_id"] == target
                and row["device_id"] == device_id
                and row["new_authority_device_id"] == _text(payload.get("new_authority_device_id"), 80)
            )
            if not same:
                raise FederationOperationError("Federation operation idempotency conflict.", 409)
            return _expire_if_due(row)
        connection.execute(
            """INSERT INTO tracky_federation_operation_ledger(
                 request_id,idempotency_key,operation_type,target_site_id,device_id,new_authority_device_id,
                 state,requires_approval,requires_reconciliation,expires_at_ms,actor_json,reason_codes_json,
                 parameters_json,authority_epoch_before
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                request_id,
                idempotency_key,
                op,
                target,
                device_id,
                _text(payload.get("new_authority_device_id"), 80),
                state,
                1 if requires_approval else 0,
                1 if requires_reconciliation else 0,
                expires_at_ms,
                _json(actor),
                _json(reasons),
                _json(parameters),
                authority_epoch_before,
            ),
        )
        connection.execute(
            "INSERT INTO tracky_federation_operation_events(request_id,state,detail_json) VALUES (?,?,?)",
            (request_id, state, _json({"reason_codes": reasons, "expires_at_ms": expires_at_ms})),
        )
    return _get(request_id)


def decide(request_id: str, approved: bool, *, actor: dict[str, Any] | None = None) -> dict[str, Any]:
    row = _expire_if_due(_get(request_id))
    if row["state"] == "expired":
        return row
    if row["state"] != "awaiting_approval":
        raise FederationOperationError("Operation is not awaiting approval.", 409)
    return _set(
        request_id,
        "approved" if approved else "rejected",
        result={"approved": bool(approved), "actor": actor or {}},
    )


def _revoke_site_access(target: str) -> dict[str, Any]:
    access = tracky_federation_access_operations.current_report()
    policy = access.get("local_policy") or {}
    revoked_scopes: list[str] = []
    for grant in access.get("grants", []):
        if not isinstance(grant, dict):
            continue
        if _site(grant.get("destination_site_id")) != target:
            continue
        if not grant.get("effective_allowed") and grant.get("status") != "granted":
            continue
        scope = _text(grant.get("scope"), 80).lower()
        if not scope:
            continue
        tracky_federation_access_operations.set_permission(
            {
                "action": "revoke",
                "destination_site_id": target,
                "scope": scope,
                "reason": "governed_site_revocation",
            }
        )
        revoked_scopes.append(scope)
    peers = [
        _site(peer)
        for peer in (policy.get("allowed_peer_sites") or [])
        if _site(peer) and _site(peer) != target
    ]
    updated = tracky_federation_access_operations.update_site_policy(
        {
            "mode": policy.get("mode") or "private",
            "allow_federation": bool(policy.get("allow_federation")),
            "allow_remote_observation": bool(policy.get("allow_remote_observation")),
            "default_identity_visibility": policy.get("default_identity_visibility") or "none",
            "allowed_peer_sites": peers,
        }
    )
    return {
        "site_revoked": target,
        "revoked_scopes": sorted(set(revoked_scopes)),
        "policy_revision": int((updated.get("site_policy") or {}).get("revision") or 0),
        "revocation_wins": True,
    }


def _revoke_device(device_id: str, target_site: str) -> dict[str, Any]:
    device = tracky_site_topology.get_device(device_id)
    if _site(device.get("site_id")) != target_site:
        raise FederationOperationError("Device does not belong to the target site.", 409)
    revoked = tracky_site_topology.register_device(
        device_id=device["id"],
        label=device.get("label") or "",
        site_id=device.get("site_id"),
        hardware_profile=device.get("hardware_profile") or "custom",
        hardware_profile_label=device.get("hardware_profile_label") or "",
        mobility=device.get("mobility") or "unknown",
        trust_state="revoked",
        roles=list(device.get("roles") or []),
        capabilities=dict(device.get("capabilities") or {}),
        aliases=list(device.get("aliases") or []),
        metadata=dict(device.get("metadata") or {}),
    )
    inventory_removed = False
    try:
        inventory_removed = bool(fleet_management.remove_inventory_device(device_id).get("removed"))
    except fleet_management.FleetError as exc:
        if int(getattr(exc, "status_code", 500)) != 404:
            raise
    return {
        "device_id": device_id,
        "trust_state": revoked.get("trust_state"),
        "fleet_inventory_removed": inventory_removed,
        "authority_must_be_transferred_first": True,
    }


def execute(request_id: str) -> dict[str, Any]:
    row = _expire_if_due(_get(request_id))
    if row["state"] == "expired":
        return row
    if row["state"] not in {"approved", "queued"}:
        raise FederationOperationError("Operation is not executable in its current state.", 409)
    if row["state"] == "approved":
        row = _set(request_id, "queued", result={"queued": True})
    row = _set(request_id, "running", result={"execution_started": True})
    op = row["operation_type"]
    target = row["target_site_id"]
    parameters = row["parameters"]

    try:
        result: dict[str, Any]
        if op in {"reconnect", "reconcile"}:
            result = tracky_federation_reconciliation.schedule_retry(target, reason="operator_" + op)
        elif op == "restart_runtime":
            hardware_adapters.stop()
            hardware_adapters.start()
            result = {"runtime": "hardware_adapters", "restart_requested": True}
        elif op == "request_update":
            fleet_settings = fleet_management.get_settings()
            requester = _text(
                parameters.get("requester_app_key") or fleet_settings.get("controller_app_key"),
                100,
            )
            result = fleet_management.request_update(
                requester,
                _text(parameters.get("request_key") or row["request_id"], 120),
                _text(parameters.get("package_sha256"), 64),
                _text(parameters.get("release_version"), 40),
                int(parameters["rollout_id"]) if parameters.get("rollout_id") is not None else None,
            )
        elif op == "revoke_site":
            return _set(request_id, "completed", result=_revoke_site_access(target))
        elif op == "revoke_device":
            return _set(request_id, "completed", result=_revoke_device(row["device_id"], target))
        elif op == "transfer_authority":
            result = tracky_site_topology.claim_site_authority(
                site_id=target,
                device_id=row["new_authority_device_id"],
                replace=True,
                reason="governed_federation_operation",
            )
            authority_epoch_after = int(result.get("authority_epoch") or 0)
            if authority_epoch_after <= row["authority_epoch_before"]:
                raise FederationOperationError("Authority epoch did not advance.", 409)
            tracky_federation_reconciliation.schedule_retry(target, reason="authority_transfer")
            return _set(
                request_id,
                "reconciling",
                result=result,
                authority_epoch_after=authority_epoch_after,
            )
        else:
            raise FederationOperationError("Unsupported federation operation.")

        if row["requires_reconciliation"]:
            return _set(request_id, "reconciling", result=result)
        return _set(request_id, "completed", result=result)
    except Exception as exc:
        try:
            _set(request_id, "failed", error=str(exc))
        except FederationOperationError:
            pass
        raise


def refresh_reconciliation(request_id: str) -> dict[str, Any]:
    row = _expire_if_due(_get(request_id))
    if row["state"] != "reconciling":
        return row
    health = tracky_federation_agent_health.current_report()
    site = next(
        (item for item in health.get("sites", []) if _site(item.get("site_id")) == row["target_site_id"]),
        {},
    )
    if site.get("state") == "current" and site.get("fresh") and site.get("recovery_complete"):
        if (
            row["operation_type"] == "transfer_authority"
            and row["authority_epoch_after"] <= row["authority_epoch_before"]
        ):
            return _set(request_id, "failed", error="Authority epoch did not advance.")
        return _set(
            request_id,
            "completed",
            result={
                **row.get("result", {}),
                "authoritative_reconciliation": {
                    "state": "current",
                    "fresh": True,
                    "recovery_complete": True,
                },
            },
        )
    return row


def cancel(request_id: str) -> dict[str, Any]:
    row = _expire_if_due(_get(request_id))
    if row["state"] in TERMINAL:
        return row
    return _set(request_id, "cancelled", result={"cancelled": True})


def report(limit: int = 100) -> dict[str, Any]:
    _expire_due_operations()
    with db() as connection:
        rows = connection.execute(
            "SELECT * FROM tracky_federation_operation_ledger ORDER BY id DESC LIMIT ?",
            (max(1, min(500, int(limit))),),
        ).fetchall()
    items = [_row(row) for row in rows]
    active = [item for item in items if item["state"] not in TERMINAL]
    return {
        "protocol": PROTOCOL,
        "version": VERSION,
        "schema_version": 1,
        "generated_at": _now_ms(),
        "operations": items,
        "active": active,
        "counts": {
            "total": len(items),
            "active": len(active),
            "awaiting_approval": sum(1 for item in active if item["state"] == "awaiting_approval"),
            "reconciling": sum(1 for item in active if item["state"] == "reconciling"),
        },
        "agent_context": {
            "active": [
                {
                    "request_id": item["request_id"],
                    "operation_type": item["operation_type"],
                    "target_site_id": item["target_site_id"],
                    "state": item["state"],
                    "reason_codes": item["reason_codes"],
                }
                for item in active[:32]
            ],
            "agent_may_propose": True,
            "agent_may_execute": False,
            "cloud_may_execute": False,
            "recovery_rule": "authoritative_reconciliation_required",
        },
        "safety": {
            "section7_health_is_authoritative": True,
            "cloud_execution_allowed": False,
            "agent_execution_allowed": False,
            "authority_transfer_automatic": False,
            "reconnect_marks_recovered": False,
            "revocation_wins": True,
        },
    }


def ingest_cloud_requests(projection: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(projection, dict) or projection.get("cloud_role") != "request_relay_only":
        return []
    if projection.get("remote_command_execution") or projection.get("authority_mutation"):
        raise FederationOperationError(
            "Cloud request relay attempted forbidden execution authority.",
            403,
        )
    local_site = _site(
        tracky_federation_agent_health.current_report().get("local_site_id")
        or tracky_federation_fleet_health.current_report().get("local_site_id")
    )
    out = []
    for item in list(projection.get("requests") or [])[:50]:
        if not isinstance(item, dict):
            continue
        origin_site = _site(item.get("origin_site_id"))
        if not local_site or origin_site != local_site:
            raise FederationOperationError("Cloud operation request was routed to the wrong origin HomeServer.", 409)
        payload = {
            "request_id": _text(item.get("request_id"), 128),
            "idempotency_key": _text(item.get("idempotency_key"), 160),
            "operation_type": _text(item.get("operation_type"), 40),
            "target_site_id": _site(item.get("target_site_id")),
            "device_id": _text(item.get("device_id"), 80),
            "new_authority_device_id": _text(item.get("new_authority_device_id"), 80),
            "confirmation_token": "cloud_user_explicit_request" if item.get("explicit_confirmation") else "",
            "require_approval": True,
            "parameters": item.get("parameters") if isinstance(item.get("parameters"), dict) else {},
        }
        out.append(
            propose(
                payload,
                actor={"actor_type": "cloud_user", "actor_id": "vp3_cloud_account"},
            )
        )
    return out


def cloud_projection() -> dict[str, Any]:
    report_data = report()
    safe = []
    for item in report_data.get("operations", []):
        safe.append(
            {
                key: item.get(key)
                for key in (
                    "request_id",
                    "idempotency_key",
                    "operation_type",
                    "target_site_id",
                    "device_id",
                    "new_authority_device_id",
                    "state",
                    "requires_approval",
                    "requires_reconciliation",
                    "expires_at_ms",
                    "authority_epoch_before",
                    "authority_epoch_after",
                    "last_error",
                    "created_at",
                    "updated_at",
                )
            }
        )
    return {
        "protocol": PROTOCOL,
        "version": VERSION,
        "schema_version": 1,
        "generated_at": _now_ms(),
        "local_site_id": tracky_federation_agent_health.current_report().get("local_site_id") or "",
        "operations": safe,
        "counts": report_data.get("counts", {}),
        "summary_only": True,
        "cloud_read_only": True,
        "remote_command_execution": False,
        "authority_mutation": False,
        "safety": report_data.get("safety", {}),
    }


def public_capability() -> dict[str, Any]:
    return {
        "version": VERSION,
        "protocol": PROTOCOL,
        "operations": list(OPERATIONS),
        "states": list(STATES),
        "idempotent_requests": True,
        "monotonic_state_machine": True,
        "queued_before_running": True,
        "operation_expiration": True,
        "durable_audit": True,
        "update_uses_staged_rollout": True,
        "revocation_wins": True,
        "agent_proposal_only": True,
        "cloud_execution_allowed": False,
        "section7_health_is_authoritative": True,
        "completion_requires_authoritative_reconciliation": True,
        "authority_transfer_requires_epoch_advance": True,
    }
