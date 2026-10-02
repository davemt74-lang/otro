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


def status(*, visual: dict[str, Any] | None = None) -> dict[str, Any]:
    row = _saved()
    v = _visual() if visual is None else visual
    current = _current(row, v)
    local = contacts.get_contact(int(row["contact_id"])) if current else None
    valid = bool(current and local)
    if not row:
        state, reason = "not_linked", "no_owner_association"
    elif row.get("state") == "revoked":
        state, reason = "revoked", str(row.get("revocation_reason") or "owner_revoked")
    elif not current:
        state, reason = "needs_review", "visual_report_or_consent_changed"
    elif not local:
        state, reason = "needs_review", "linked_contact_missing"
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
        "cloud_sync_enabled": False, "raw_media_in_receipt": False,
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
            # No active signed receipt may be reused after revocation.
            row["receipt_signature"] = ""
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
