from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from .services import approval_federation, approvals
from .services.pairing import authenticate

router = APIRouter()


def _current_app(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    identity = authenticate(authorization.removeprefix("Bearer ").strip())
    if identity is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked token")
    return identity


def _approval_or_http(callable_, *args, **kwargs):
    try:
        return callable_(*args, **kwargs)
    except approvals.ApprovalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _require_review(identity: dict) -> None:
    if "approvals.review" not in set(identity.get("permissions") or []):
        raise HTTPException(status_code=403, detail="Missing approvals.review permission")


@router.get("/api/v1/action-requests/{request_id}")
def client_action_request(request_id: str, identity: dict = Depends(_current_app)) -> dict:
    item = approvals.get_request_for_source(request_id, f"app:{identity['app_key']}")
    if item is None:
        raise HTTPException(status_code=404, detail="Action request not found")
    return {"request": item}


@router.get("/api/v1/action-requests")
def client_action_requests(
    status: str | None = Query(default="pending"),
    limit: int = Query(default=100, ge=1, le=200),
    identity: dict = Depends(_current_app),
) -> dict:
    _require_review(identity)
    items = _approval_or_http(approval_federation.list_requests_for_app, identity["app_key"], status, limit)
    return {"items": items}


@router.post("/api/v1/action-requests/{request_id}/approve")
def client_action_request_approve(request_id: str, identity: dict = Depends(_current_app)) -> dict:
    _require_review(identity)
    item = _approval_or_http(approval_federation.review_request_for_app, identity["app_key"], request_id, "approve")
    return {"request": item}


@router.post("/api/v1/action-requests/{request_id}/deny")
def client_action_request_deny(request_id: str, identity: dict = Depends(_current_app)) -> dict:
    _require_review(identity)
    item = _approval_or_http(approval_federation.review_request_for_app, identity["app_key"], request_id, "deny")
    return {"request": item}


@router.get("/api/v1/control/action-requests")
def control_action_requests(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    items = _approval_or_http(approvals.list_requests, status, limit)
    return {"items": items}


@router.post("/api/v1/control/action-requests/{request_id}/approve")
def control_action_request_approve(request_id: str) -> dict:
    item = _approval_or_http(approvals.approve_request, request_id)
    return {"request": item}


@router.post("/api/v1/control/action-requests/{request_id}/deny")
def control_action_request_deny(request_id: str) -> dict:
    item = _approval_or_http(approvals.deny_request, request_id)
    return {"request": item}
