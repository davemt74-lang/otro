from __future__ import annotations

import json
from typing import Any

from ..database import db
from . import approvals

_VALID_STATUSES = {"pending", "executing", "executed", "denied", "failed", "expired"}
_VALID_DECISIONS = {"approve", "deny"}


def _source(app_key: str) -> str:
    key = str(app_key or "").strip()
    if not key:
        raise approvals.ApprovalError("Paired application identity is required.", 401)
    return f"app:{key}"


def list_requests_for_app(app_key: str, status: str | None = "pending", limit: int = 100) -> list[dict[str, Any]]:
    source = _source(app_key)
    if status is not None and status not in _VALID_STATUSES:
        raise approvals.ApprovalError("Invalid action request status.")
    safe_limit = max(1, min(200, int(limit)))
    approvals._expire_pending()
    params: list[Any] = [source]
    where = "source_app_key=?"
    if status:
        where += " AND status=?"
        params.append(status)
    params.append(safe_limit)
    with db() as connection:
        rows = connection.execute(
            f"SELECT id FROM action_requests WHERE {where} ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = approvals.get_request_for_source(str(row["id"]), source)
        if item is not None:
            items.append(item)
    return items


def get_request_for_app(app_key: str, request_id: str) -> dict[str, Any]:
    source = _source(app_key)
    item = approvals.get_request_for_source(request_id, source)
    if item is None:
        raise approvals.ApprovalError("Action request not found.", 404)
    return item


def review_request_for_app(app_key: str, request_id: str, decision: str) -> dict[str, Any]:
    source = _source(app_key)
    normalized = str(decision or "").strip().lower()
    if normalized not in _VALID_DECISIONS:
        raise approvals.ApprovalError("Decision must be approve or deny.")

    # Source identity is immutable for an action request. This check prevents a
    # paired wrapper from reviewing another wrapper's request while preserving
    # HomeServer as the sole execution authority.
    existing = approvals.get_request_for_source(request_id, source)
    if existing is None:
        raise approvals.ApprovalError("Action request not found.", 404)

    if (
        normalized == "approve"
        and str(existing.get("action_key") or "") in approvals.LOCAL_OWNER_ONLY_ACTIONS
    ):
        raise approvals.ApprovalError(
            "This physical action requires local HomeServer owner approval.",
            403,
        )

    from . import tool_authority
    with db() as connection:
        app = connection.execute("SELECT id FROM paired_apps WHERE app_key=? AND status='active'", (app_key,)).fetchone()
        permitted = app is not None and connection.execute("SELECT 1 FROM app_permissions WHERE paired_app_id=? AND permission='approvals.review' AND allowed=1", (app["id"],)).fetchone()
    if not permitted:
        raise approvals.ApprovalError("Federated approval permission is no longer available.", 403)
    token = tool_authority.federated_reviewer.set(source)
    try:
        if normalized == "approve":
            approvals.approve_request(request_id)
        else:
            approvals.deny_request(request_id)
    finally:
        tool_authority.federated_reviewer.reset(token)

    with db() as connection:
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES ('app', ?, 'action.federated_review', 'action_request', ?, ?)
            """,
            (
                source,
                request_id,
                json.dumps({"decision": normalized}, separators=(",", ":")),
            ),
        )

    return get_request_for_app(app_key, request_id)
