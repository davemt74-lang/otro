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
        "cloud_sync_enabled": False,
        "cloud_sharing_opted_in": opted,
        "cloud_revocation_pending": revoke_signal,
        "cloud_delivery_status": ("pending_authenticated_sync_and_cloud_consent"
                                  if opted or revoke_signal else "not_shared"),
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
                row["cloud_projection_state"] = _ACTIVE
                row["cloud_consented_at"] = datetime.now(timezone.utc).isoformat()
                _commit(row, "tracky.visual.cloud_sharing.enabled")
        elif row.get("cloud_share_opt_in") is True:
            row["cloud_share_opt_in"] = False
            row["cloud_projection_state"] = "revoked"
            row["cloud_revoked_at"] = datetime.now(timezone.utc).isoformat()
            _commit(row, "tracky.visual.cloud_sharing.revoked")
        return status()


def cloud_projection() -> str | None:
    """Fail-closed semantic scalar for existing authenticated Tracky sync.

    Never exports local receipt, signature, participant ref, contact ref,
    local contact details, model outputs or visual biometrics.
    """
    state = status()
    if state["cloud_sharing_opted_in"]:
        return _ACTIVE
    if state["cloud_revocation_pending"]:
        return "revoked"
    return None


def mark_cloud_delivery(sent_state: str) -> bool:
    """Record authenticated Cloud site acceptance without promoting identity."""
    if sent_state not in {_ACTIVE, "revoked"}:
        raise ValueError("Unsupported semantic Cloud status")
    with _LOCK:
        # Concurrent owner revocation/change wins over an in-flight sync ACK.
        if cloud_projection() != sent_state:
            return False
        row = _saved()
        if not row:
            return False
        row["cloud_last_accepted_state"] = sent_state
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
