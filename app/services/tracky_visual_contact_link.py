"""Tracky 1F/1: governed local self-participant to existing-contact association.

This binds a browser-REPORTED, owner-self participant to an existing local
HomeServer contact only on explicit owner approval. It does not verify facial
identity, create contacts, recognize bystanders, open cameras or synchronize
biometrics. Its HMAC receipt is local evidence, not a Cloud-verified credential.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
from datetime import datetime, timezone
from typing import Any

from ..database import db
from . import contacts, remote_identity, system_state

KEY = "tracky.visual.owner_contact_link.v1f1"
CONTRACT = "tracky.visual.owner-contact-association.v1f1"
SCOPE = "owner-self-existing-contact-association.v1"
CLOUD_SCOPE = "owner-self-cloud-status-only.v1"
_LOCK = threading.RLock()
_ACTIVE = "owner_attributed_unverified"


class VisualContactLinkError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _saved() -> dict[str, Any]:
    state = system_state._read_setting(KEY, {})
    return state if isinstance(state, dict) else {}


def _visual() -> dict[str, Any]:
    from . import onboarding_visual
    return onboarding_visual._read()


def _current(row: dict[str, Any], visual: dict[str, Any]) -> bool:
    return bool(
        row.get("state") == _ACTIVE
        and visual.get("phase") == "browser_reported"
        and visual.get("consent") is True
        and visual.get("scope") == "owner-self-local-recognition-v1"
        and row.get("client_reported_at")
        and hmac.compare_digest(str(row.get("client_reported_at")), str(visual.get("client_reported_at") or ""))
        and hmac.compare_digest(str(row.get("local_participant_id") or ""),
                                str(visual.get("local_participant_id") or ""))
    )


def _commit(row: dict[str, Any], action: str) -> None:
    # Atomic state transition and audit; a failed audit cannot leave an
    # unlogged approval/revocation. Log only opaque receipt IDs locally.
    encoded = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
    with db() as connection:
        connection.execute(
            "INSERT INTO system_settings(setting_key,value_json) VALUES(?,?) "
            "ON CONFLICT(setting_key) DO UPDATE SET "
            "value_json=excluded.value_json,updated_at=CURRENT_TIMESTAMP",
            (KEY, encoded),
        )
        connection.execute(
            "INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)"
            " VALUES ('owner','control-center',?,'tracky_visual_link',?,'{}')",
            (action, row["receipt_id"]),
        )


def _semantic_receipt(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "receipt_id": data["receipt_id"],
        "device_id": data["device_id"],
        # Neither the participant's browser ID nor local contact ID leaves
        # this module's local state; both references are keyed, opaque tags.
        "participant_ref": data["participant_ref"],
        "contact_ref": data["contact_ref"],
        "association_state": _ACTIVE,
        "evidence": "owner_attribution_of_browser_report_not_face_verification",
        "consent_scope": SCOPE,
        "approved_at": data["approved_at"],
        "face_recognition_verified": False,
        "native_hardware_certified": False,
        "cloud_biometrics": False,
        "cloud_delivery": "not_enabled",
    }

def _receipt_valid(row: dict[str, Any]) -> bool:
    if row.get("state") != _ACTIVE or not row.get("receipt_signature"):
        return False
    try:
        identity = remote_identity.load_or_create_remote_identity()
        if not hmac.compare_digest(str(row.get("device_id") or ""), identity["device_id"]):
            return False
        receipt = _semantic_receipt(row)
        canonical = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = hmac.new(identity["device_secret"].encode("utf-8"), canonical,
                          hashlib.sha256).hexdigest()
        return hmac.compare_digest(digest, str(row["receipt_signature"]))
    except (KeyError, OSError, TypeError, ValueError):
        return False


def _next_cloud_revision(row: dict[str, Any]) -> int:
    # Explicit local changes, not heartbeat polls, advance the Cloud order.
    # Legacy records without a revision have an effective baseline of 1 so
    # revoking an already-shared legacy state cannot reuse its first version.
    raw = row.get("cloud_revision", 0)
    if type(raw) is not int or raw < 0 or raw >= 2147483647:
        raise VisualContactLinkError(
            "Local sharing revision is invalid; use governed device recovery.", 409
        )
    return max(1, raw) + 1 if row else 1


def status(*, visual: dict[str, Any] | None = None) -> dict[str, Any]:
    row = _saved()
    v = _visual() if visual is None else visual
    current = _current(row, v)
    local = contacts.get_contact(int(row["contact_id"])) if current else None
    signed = _receipt_valid(row) if current and local else False
    valid = bool(current and local and signed)
    opted = bool(valid and row.get("cloud_share_opt_in") is True)
    revoke_signal = bool(row.get("cloud_projection_state") == "revoked"
                         or (row.get("cloud_share_opt_in") is True and not valid))
    accepted_state = str(row.get("cloud_last_accepted_state") or "")
    accepted_generation = str(row.get("cloud_last_accepted_generation") or "")
    current_generation = str(row.get("cloud_generation") or "")
    raw_revision = row.get("cloud_revision", 0)
    current_revision = raw_revision if type(raw_revision) is int and 0 <= raw_revision <= 2147483647 else 0
    accepted_revision = row.get("cloud_last_accepted_revision")
    acknowledged_current = bool(
        type(accepted_revision) is int
        and current_revision > 0 and accepted_revision == current_revision
        and accepted_generation and current_generation
        and hmac.compare_digest(accepted_generation, current_generation)
        and ((accepted_state == _ACTIVE and opted and not revoke_signal)
             or (accepted_state == "revoked" and not opted and not revoke_signal))
    )
    if not row:
        state, reason = "not_linked", "no_owner_association"
    elif row.get("state") == "revoked":
        state, reason = "revoked", str(row.get("revocation_reason") or "owner_revoked")
    elif not current:
        state, reason = "needs_review", "visual_report_or_consent_changed"
    elif not local:
        state, reason = "needs_review", "linked_contact_missing"
    elif not signed:
        state, reason = "needs_review", "device_signature_changed_or_receipt_invalid"
    else:
        state, reason = _ACTIVE, "owner_approved_local_association"
    return {
        "contract": CONTRACT, "state": state, "reason": reason,
        "active": valid, "receipt_id": str(row.get("receipt_id") or "") if valid else "",
        "contact": {"id": int(local["id"]), "display_name": local["display_name"]} if valid else None,
        "approved_at": str(row.get("approved_at") or "") if valid else "",
        "identity_verified": False, "owner_attribution_only": True,
        "contact_creation_automatic": False,
        "face_templates_retained_by_homeserver": False,
        # Opt-in reports only a semantic status through existing authenticated
        # Tracky site sync. Local signed receipt is NEVER exported.
        "cloud_sync_enabled": opted,
        "cloud_receipt_sync_enabled": False,
        "cloud_sharing_opted_in": opted,
        "cloud_revocation_pending": revoke_signal,
        "cloud_delivery_status": (
            "revocation_pending_sync" if revoke_signal
            else "authenticated_site_accepted_cloud_account_consent_separate"
                if opted and acknowledged_current
            else "pending_authenticated_sync_and_cloud_consent" if opted
            else "revocation_delivered" if acknowledged_current and accepted_state == "revoked"
            else "not_shared"
        ),
        "cloud_current_generation_acknowledged": acknowledged_current,
        "cloud_last_accepted_status": accepted_state if acknowledged_current else "",
        "cloud_last_accepted_at": str(row.get("cloud_last_accepted_at") or "") if acknowledged_current else "",
        "raw_media_in_receipt": False,
        "requires_explicit_owner_approval": True,
    }


def associate(*, consent: bool, scope: str, participant_id: str, contact_id: int) -> dict[str, Any]:
    if consent is not True or scope != SCOPE:
        raise VisualContactLinkError("Fresh approval for association with an existing contact is required.", 403)
    if type(contact_id) is not int or contact_id < 1:
        raise VisualContactLinkError("Select a valid existing local contact.", 422)
    with _LOCK:
        v = _visual()
        if v.get("phase") != "browser_reported" or not v.get("consent") or v.get("scope") != "owner-self-local-recognition-v1":
            raise VisualContactLinkError("Complete local self-enrollment and consent first.", 409)
        if not isinstance(participant_id, str) or not hmac.compare_digest(
            str(v.get("local_participant_id") or ""), participant_id
        ):
            raise VisualContactLinkError("The selected local participant does not match this HomeServer report.", 409)
        contact = contacts.get_contact(contact_id)
        if not contact:
            raise VisualContactLinkError("The selected local contact does not exist.", 404)
        existing = _saved()
        if existing.get("state") == _ACTIVE:
            raise VisualContactLinkError("Revoke the existing association before making another.", 409)
        secret = remote_identity.load_or_create_remote_identity()
        key = secret["device_secret"].encode("utf-8")
        def tag(value: str) -> str:
            return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()
        row = {
            "state": _ACTIVE,
            "receipt_id": secrets.token_hex(16),
            "local_participant_id": participant_id,
            "contact_id": contact_id,
            "client_reported_at": str(v.get("client_reported_at") or ""),
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "device_id": secret["device_id"],
            "participant_ref": tag("visual-participant:" + participant_id),
            "contact_ref": tag("visual-local-contact:" + str(contact_id)),
            # Keep a pending previously-shared revocation until the next
            # authenticated sync. New links ALWAYS require fresh Cloud opt-in.
            "cloud_share_opt_in": False,
            "cloud_revision": _next_cloud_revision(existing),
            "cloud_generation": secrets.token_hex(16),
            "cloud_projection_state": ("revoked"
                if existing.get("cloud_projection_state") == "revoked"
                or existing.get("cloud_share_opt_in") is True else ""),
        }
        receipt = _semantic_receipt(row)
        canonical = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode("utf-8")
        row["receipt_signature"] = hmac.new(key, canonical, hashlib.sha256).hexdigest()
        _commit(row, "tracky.visual.association.approved")
        return status(visual=v)


def revoke(*, consent: bool = False, reason: str = "owner_revoked") -> dict[str, Any]:
    if consent is not True and reason == "owner_revoked":
        raise VisualContactLinkError("Explicit owner consent is required to unlink.", 403)
    if reason not in {"owner_revoked", "visual_consent_revoked", "local_profile_deleted"}:
        raise VisualContactLinkError("Invalid association revocation reason.", 422)
    with _LOCK:
        row = _saved()
        if row.get("state") == _ACTIVE:
            row["state"] = "revoked"
            row["revocation_reason"] = reason
            row["revoked_at"] = datetime.now(timezone.utc).isoformat()
            # Explicitly revoke any previously shared semantic status. No
            # captured media, private local ID or contact value is sent.
            if row.get("cloud_share_opt_in") is True or row.get("cloud_projection_state") == "revoked":
                row["cloud_projection_state"] = "revoked"
            row["cloud_share_opt_in"] = False
            row["cloud_revision"] = _next_cloud_revision(row)
            row["cloud_generation"] = secrets.token_hex(16)
            row["cloud_last_accepted_state"] = ""
            row["cloud_last_accepted_generation"] = ""
            row["cloud_last_accepted_revision"] = 0
            # No active signed receipt may be reused after revocation.
            row["receipt_signature"] = ""
            for name in ("local_participant_id", "contact_id", "participant_ref",
                         "contact_ref", "device_id", "client_reported_at"):
                row.pop(name, None)
            _commit(row, "tracky.visual.association.revoked")
        return status()


def local_receipt() -> dict[str, Any]:
    """Read-only local evidence; never expose the signing secret or local IDs."""
    row = _saved()
    if not status()["active"] or not row.get("receipt_signature"):
        return {"available": False, "reason": "current_owner_association_required",
                "cloud_delivery": "not_enabled"}
    return {
        "available": True, "receipt": _semantic_receipt(row),
        "signature": row["receipt_signature"], "algorithm": "hmac-sha256-local-device",
        "cloud_delivery": "not_enabled", "independently_verified": False,
    }


def set_cloud_sharing(*, consent: bool, scope: str, enabled: bool) -> dict[str, Any]:
    """Explicit per-HomeServer consent. Cloud account consent is separate."""
    if consent is not True or scope != CLOUD_SCOPE or type(enabled) is not bool:
        raise VisualContactLinkError(
            "Explicit owner permission for non-biometric Cloud status is required.", 403
        )
    with _LOCK:
        row = _saved()
        if enabled:
            if not status()["active"]:
                raise VisualContactLinkError(
                    "Complete and review a current local owner association before sharing.", 409
                )
            if row.get("cloud_share_opt_in") is not True:
                row["cloud_share_opt_in"] = True
                row["cloud_revision"] = _next_cloud_revision(row)
                row["cloud_generation"] = secrets.token_hex(16)
                row["cloud_projection_state"] = _ACTIVE
                row["cloud_consented_at"] = datetime.now(timezone.utc).isoformat()
                row["cloud_last_accepted_state"] = ""
                row["cloud_last_accepted_generation"] = ""
                row["cloud_last_accepted_revision"] = 0
                _commit(row, "tracky.visual.cloud_sharing.enabled")
        elif row.get("cloud_share_opt_in") is True:
            row["cloud_share_opt_in"] = False
            row["cloud_revision"] = _next_cloud_revision(row)
            row["cloud_generation"] = secrets.token_hex(16)
            row["cloud_projection_state"] = "revoked"
            row["cloud_revoked_at"] = datetime.now(timezone.utc).isoformat()
            row["cloud_last_accepted_state"] = ""
            row["cloud_last_accepted_generation"] = ""
            row["cloud_last_accepted_revision"] = 0
            _commit(row, "tracky.visual.cloud_sharing.revoked")
        return status()


def cloud_snapshot() -> dict[str, Any]:
    """Local-only generation fencing for asynchronous Cloud ACKs.

    Only `state` is placed inside authenticated Tracky site health. The
    generation stays on HomeServer and prevents an old upload of the same
    state from acknowledging a NEW consent or a NEW participant association.
    No passive call starts synchronization.
    """
    with _LOCK:
        row = _saved()
        state = status()
        value = (_ACTIVE if state["cloud_sharing_opted_in"]
                 else "revoked" if state["cloud_revocation_pending"]
                 else "")
        raw = row.get("cloud_revision", 0)
        if type(raw) is not int or raw < 0 or raw > 2147483647:
            raise VisualContactLinkError("Local Cloud ordering state is invalid.", 409)
        return {
            "state": value, "generation": str(row.get("cloud_generation") or ""),
            "revision": max(1, raw) if value else 0,
        }


def cloud_projection() -> str | None:
    """Only an allowlisted semantic scalar may leave HomeServer."""
    return cloud_snapshot()["state"] or None


def mark_cloud_delivery(
    sent_state: str, *, generation: str = "", revision: int | None = None
) -> bool:
    """Reject late ACKs even when revoked/newly approved status has same label.

    A generation is mandatory for ACKs; the legacy one-argument call cannot
    acknowledge an uncorrelated response or clear a pending tombstone.
    """
    if sent_state not in {_ACTIVE, "revoked"}:
        raise ValueError("Unsupported semantic Cloud status")
    if not generation or type(revision) is not int or revision < 1:
        return False
    with _LOCK:
        row = _saved()
        actual_revision = row.get("cloud_revision", 0)
        if type(actual_revision) is not int or max(1, actual_revision) != revision:
            return False
        if not row or not hmac.compare_digest(str(row.get("cloud_generation") or ""), generation):
            return False
        if cloud_projection() != sent_state:
            return False
        row["cloud_revision"] = revision
        row["cloud_last_accepted_state"] = sent_state
        row["cloud_last_accepted_generation"] = generation
        row["cloud_last_accepted_revision"] = revision
        row["cloud_last_accepted_at"] = datetime.now(timezone.utc).isoformat()
        if sent_state == "revoked":
            row["cloud_share_opt_in"] = False
            row["cloud_projection_state"] = ""
        encoded = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        with db() as connection:
            connection.execute(
                "INSERT INTO system_settings(setting_key,value_json) VALUES (?,?) "
                "ON CONFLICT(setting_key) DO UPDATE SET "
                "value_json=excluded.value_json,updated_at=CURRENT_TIMESTAMP",
                (KEY, encoded),
            )
            connection.execute(
                "INSERT INTO activity_log(actor_type,actor_key,action,resource_type,resource_key,metadata_json) "
                "VALUES ('system','tracky-sync',?,'tracky_visual_link',?,'{}')",
                ("tracky.visual.cloud_status.delivered", str(row.get("receipt_id") or "")),
            )
        return True
