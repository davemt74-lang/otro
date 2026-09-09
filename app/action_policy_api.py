from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel

from .services import action_policy
from .services.pairing import authenticate

router = APIRouter()


class ActionPolicyUpdate(BaseModel):
    policy_mode: str = "inherit"


def _current_app(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    identity = authenticate(authorization.removeprefix("Bearer ").strip())
    if identity is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked token")
    return identity


@router.get("/api/v1/action-policy")
def client_action_policy(
    audit_limit: int = Query(default=50, ge=1, le=200),
    identity: dict = Depends(_current_app),
) -> dict:
    app_id = int(identity["id"])
    app_key = str(identity["app_key"])
    return {
        "version": "v0.35",
        "app": {"id": app_id, "app_key": app_key, "name": identity.get("name")},
        "policies": action_policy.list_policy_for_app(app_id, app_key),
        "audit": action_policy.list_audit(audit_limit, app_id),
    }


@router.get("/api/v1/control/action-policies")
def control_action_policies(audit_limit: int = Query(default=100, ge=1, le=500)) -> dict:
    result = action_policy.list_owner_policies()
    result["audit"] = action_policy.list_audit(audit_limit)
    return result


@router.put("/api/v1/control/action-policies/{app_id}/{tool_key}")
def control_action_policy_update(app_id: int, tool_key: str, payload: ActionPolicyUpdate) -> dict:
    try:
        policy = action_policy.set_policy(app_id, tool_key, payload.policy_mode)
    except action_policy.ActionPolicyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {"updated": True, "policy": policy}
