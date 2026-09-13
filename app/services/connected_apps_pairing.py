from __future__ import annotations

from ..database import db
from .pairing import approve_pairing_request


class ConnectedAppsPairingError(RuntimeError):
    pass


def approve_pending_pairing(pairing_id: int) -> dict:
    if pairing_id < 1:
        raise ConnectedAppsPairingError("Pairing request was not found")
    with db() as connection:
        row = connection.execute(
            "SELECT id, request_id, app_key, status FROM pairing_requests WHERE id=? LIMIT 1",
            (pairing_id,),
        ).fetchone()
    if row is None:
        raise ConnectedAppsPairingError("Pairing request was not found")
    if str(row["status"]) != "pending":
        raise ConnectedAppsPairingError("Pairing request is no longer pending")
    request_id = str(row["request_id"] or "").strip()
    if not request_id:
        raise ConnectedAppsPairingError("Pairing request cannot be approved directly")
    result = approve_pairing_request(request_id)
    if result is None:
        raise ConnectedAppsPairingError("Pairing request is no longer pending")
    return {
        "approved": True,
        "app_key": str(result.get("app_key") or row["app_key"]),
        "permissions": result.get("permissions") if isinstance(result.get("permissions"), list) else [],
        "delivery": str(result.get("delivery") or "claim_token"),
    }
