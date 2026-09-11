from __future__ import annotations

import json
import uuid
from typing import Any, Callable

from ..database import db
from . import approvals, tools

ACTIONS = {"vp3.booking.create", "vp3.booking.reschedule", "vp3.booking.cancel"}


def _normalize(action_key: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(arguments or {})
    definition = tools.TOOL_DEFINITIONS.get(action_key) or {}
    schema = definition.get("input_schema") or {}
    properties = set((schema.get("properties") or {}).keys())
    required = set(schema.get("required") or [])
    unknown = set(payload) - properties
    if unknown:
        raise approvals.ApprovalError(f"Unsupported {action_key} proposal argument: {sorted(unknown)[0]}")
    missing = [key for key in required if payload.get(key) in (None, "")]
    if missing:
        raise approvals.ApprovalError(f"{action_key} proposal requires {missing[0]}.")

    if action_key == "vp3.booking.create":
        kind = str(payload.get("kind") or "")
        target = int(payload.get("target_id") or 0)
        name = str(payload.get("guest_name") or "").strip()
        start = str(payload.get("start_at_utc") or "").strip()
        if kind not in {"personal", "team"} or target < 1 or not name or len(name) > 190 or len(start) < 16:
            raise approvals.ApprovalError("VP3 booking proposal is invalid.")
        payload.update(kind=kind, target_id=target, guest_name=name, start_at_utc=start)
    elif action_key == "vp3.booking.reschedule":
        if int(payload.get("booking_id") or 0) < 1 or len(str(payload.get("start_at_utc") or "")) < 16:
            raise approvals.ApprovalError("VP3 reschedule proposal is invalid.")
        payload["booking_id"] = int(payload["booking_id"])
    else:
        kind = str(payload.get("kind") or "")
        booking = int(payload.get("booking_id") or 0)
        target = int(payload.get("target_id") or 0)
        if kind not in {"personal", "team"} or booking < 1 or (kind == "team" and target < 1):
            raise approvals.ApprovalError("VP3 cancellation proposal is invalid.")
        payload.update(kind=kind, booking_id=booking)
        if kind == "team":
            payload["target_id"] = target
        else:
            payload.pop("target_id", None)

    key = str(payload.get("idempotency_key") or "").strip()
    if not key:
        key = "hs-action-" + uuid.uuid4().hex
    if len(key) > 160:
        raise approvals.ApprovalError("VP3 scheduling idempotency key is too long.")
    payload["idempotency_key"] = key
    return payload


def create_request(
    source_app_key: str,
    action_key: str,
    arguments: dict[str, Any] | None,
    *,
    owner: bool = False,
) -> dict[str, Any]:
    if action_key not in ACTIONS:
        raise approvals.ApprovalError("Unsupported VP3 scheduling action.")
    source = source_app_key.strip() or ("owner" if owner else "app:unknown")
    actor = "owner" if owner else "app"
    required = [] if owner else ["scheduling.write", "tools.execute"]
    try:
        normalized = _normalize(action_key, arguments)
    except (TypeError, ValueError, approvals.ApprovalError) as exc:
        message = str(exc) if isinstance(exc, approvals.ApprovalError) else "VP3 scheduling proposal contains invalid values."
        run_id = approvals._record_failed_proposal(
            source,
            actor,
            action_key,
            required,
            {"argument_keys": sorted((arguments or {}).keys())},
            message,
        )
        status = exc.status_code if isinstance(exc, approvals.ApprovalError) else 422
        raise approvals.ApprovalError(f"{message} Run {run_id} was recorded.", status) from exc
    meta = {
        "kind": str(normalized.get("kind") or ""),
        "target_id": int(normalized.get("target_id") or 0),
        "booking_id": int(normalized.get("booking_id") or 0),
        "guest_name_present": bool(normalized.get("guest_name")),
        "guest_email_present": bool(normalized.get("guest_email")),
    }
    return approvals._create_action_request(source, actor, action_key, normalized, meta, required)


def install() -> None:
    if getattr(approvals, "_vp3_scheduling_v059_installed", False):
        return
    original: Callable[[str], dict[str, Any]] = approvals.approve_request

    def approve_request(request_id: str) -> dict[str, Any]:
        request = approvals._request_for_owner(request_id)
        if request["action_key"] not in ACTIONS:
            return original(request_id)
        if request["status"] != "pending":
            raise approvals.ApprovalError(f"Action request is already {request['status']}.", 409)
        with db() as connection:
            reserved = connection.execute(
                "UPDATE action_requests SET status='executing', decided_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'",
                (request["id"],),
            )
            if reserved.rowcount != 1:
                raise approvals.ApprovalError("Action request is no longer pending.", 409)
        try:
            execution = tools.execute_tool(
                request["source_app_key"],
                request["action_key"],
                request["arguments"],
                set(),
                owner=True,
            )
        except tools.ToolError as exc:
            run_id = approvals._extract_run_id(str(exc))
            with db() as connection:
                connection.execute(
                    "UPDATE action_requests SET status='failed', execution_tool_run_id=?, error=?, executed_at=CURRENT_TIMESTAMP WHERE id=? AND status='executing'",
                    (run_id, str(exc)[:1000], request["id"]),
                )
                connection.execute(
                    "INSERT INTO activity_log(actor_type,actor_key,action,resource_type,resource_key,metadata_json) VALUES('owner','control-center','action.failed','action_request',?,?)",
                    (
                        request["id"],
                        json.dumps({"execution_tool_run_id": run_id}, separators=(",", ":")),
                    ),
                )
            raise approvals.ApprovalError(
                f"Approved scheduling action could not execute: {exc}", exc.status_code
            ) from exc
        with db() as connection:
            connection.execute(
                "UPDATE action_requests SET status='executed', execution_tool_run_id=?, error=NULL, executed_at=CURRENT_TIMESTAMP WHERE id=? AND status='executing'",
                (execution["run_id"], request["id"]),
            )
            connection.execute(
                "INSERT INTO activity_log(actor_type,actor_key,action,resource_type,resource_key,metadata_json) VALUES('owner','control-center','action.approved','action_request',?,?)",
                (
                    request["id"],
                    json.dumps({"execution_tool_run_id": execution["run_id"]}, separators=(",", ":")),
                ),
            )
        return approvals._request_for_owner(request["id"])

    approvals.approve_request = approve_request

    from . import approval_federation

    original_review = approval_federation.review_request_for_app

    def review_for_app(app_key: str, request_id: str, decision: str) -> dict[str, Any]:
        existing = approval_federation.get_request_for_app(app_key, request_id)
        if existing.get("action_key") in ACTIONS and str(decision).strip().lower() == "approve":
            raise approvals.ApprovalError(
                "VP3 booking mutations require local HomeServer owner approval.", 403
            )
        return original_review(app_key, request_id, decision)

    approval_federation.review_request_for_app = review_for_app
    approvals._vp3_scheduling_v059_installed = True
