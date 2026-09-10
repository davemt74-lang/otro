from __future__ import annotations

import json
import re
from typing import Any, Callable

from ..config import settings
from ..database import db
from . import approvals, tools


_REF_RE = re.compile(r"^hsf-[0-9]+-[0-9a-f]{16}$")
_FILE_ACTIONS = {"files.update", "files.delete"}
_MAX_UPDATE_CHARS = 50000


def _safe_file_meta(arguments: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(arguments or {})
    ref = str(payload.get("ref") or "")
    content = str(payload.get("content") or "")
    return {
        "ref_length": len(ref),
        "content_length": len(content),
        "content_bytes": len(content.encode("utf-8")),
        "argument_count": len(payload),
    }


def _validate_ref(value: Any, action_key: str) -> str:
    ref = str(value or "").strip().lower()
    if not _REF_RE.fullmatch(ref):
        raise approvals.ApprovalError(f"{action_key} proposal requires a valid HomeServer file reference.")
    return ref


def _validate_update(arguments: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(arguments or {})
    unknown = set(payload) - {"ref", "content"}
    if unknown:
        raise approvals.ApprovalError(f"Unsupported files.update proposal argument: {sorted(unknown)[0]}")
    ref = _validate_ref(payload.get("ref"), "files.update")
    if "content" not in payload or not isinstance(payload.get("content"), str):
        raise approvals.ApprovalError("files.update proposal requires text content.")
    content = str(payload["content"])
    if not content.strip():
        raise approvals.ApprovalError("files.update proposal requires non-empty indexed text.")
    if len(content) > _MAX_UPDATE_CHARS:
        raise approvals.ApprovalError(
            f"files.update proposal content exceeds {_MAX_UPDATE_CHARS:,} characters.", 413
        )
    size = len(content.encode("utf-8"))
    if size > settings.max_upload_bytes:
        raise approvals.ApprovalError(
            f"files.update proposal exceeds the {settings.max_upload_bytes // (1024 * 1024)} MB indexing limit.",
            413,
        )
    return {"ref": ref, "content": content}


def _validate_delete(arguments: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(arguments or {})
    unknown = set(payload) - {"ref"}
    if unknown:
        raise approvals.ApprovalError(f"Unsupported files.delete proposal argument: {sorted(unknown)[0]}")
    return {"ref": _validate_ref(payload.get("ref"), "files.delete")}


def _create_request(
    source_app_key: str,
    action_key: str,
    arguments: dict[str, Any] | None,
    *,
    owner: bool = False,
) -> dict[str, Any]:
    source = source_app_key.strip() or ("owner" if owner else "app:unknown")
    actor_type = "owner" if owner else "app"
    raw_meta = _safe_file_meta(arguments)
    required = [] if owner else ["files.write", "tools.execute"]
    try:
        normalized = _validate_update(arguments) if action_key == "files.update" else _validate_delete(arguments)
    except approvals.ApprovalError as exc:
        run_id = approvals._record_failed_proposal(source, actor_type, action_key, required, raw_meta, str(exc))
        raise approvals.ApprovalError(f"{exc} Run {run_id} was recorded.", exc.status_code) from exc
    meta = _safe_file_meta(normalized)
    return approvals._create_action_request(source, actor_type, action_key, normalized, meta, required)


def create_file_update_request(
    source_app_key: str,
    arguments: dict[str, Any] | None,
    *,
    owner: bool = False,
) -> dict[str, Any]:
    return _create_request(source_app_key, "files.update", arguments, owner=owner)


def create_file_delete_request(
    source_app_key: str,
    arguments: dict[str, Any] | None,
    *,
    owner: bool = False,
) -> dict[str, Any]:
    return _create_request(source_app_key, "files.delete", arguments, owner=owner)


def install() -> None:
    """Extend approvals while keeping governed file approval local-owner-only."""
    if getattr(approvals, "_local_file_actions_v039_installed", False):
        return

    original: Callable[[str], dict[str, Any]] = approvals.approve_request

    def extended_approve_request(request_id: str) -> dict[str, Any]:
        request = approvals._request_for_owner(request_id)
        if request["action_key"] not in _FILE_ACTIONS:
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
            execution_run_id = approvals._extract_run_id(str(exc))
            with db() as connection:
                connection.execute(
                    """
                    UPDATE action_requests
                    SET status='failed', execution_tool_run_id=?, error=?, executed_at=CURRENT_TIMESTAMP
                    WHERE id=? AND status='executing'
                    """,
                    (execution_run_id, str(exc)[:1000], request["id"]),
                )
                connection.execute(
                    """
                    INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
                    VALUES ('owner', 'control-center', 'action.failed', 'action_request', ?, ?)
                    """,
                    (
                        request["id"],
                        json.dumps({"execution_tool_run_id": execution_run_id}, separators=(",", ":")),
                    ),
                )
            raise approvals.ApprovalError(
                f"Approved action could not execute: {exc}", exc.status_code
            ) from exc

        with db() as connection:
            connection.execute(
                """
                UPDATE action_requests
                SET status='executed', execution_tool_run_id=?, error=NULL, executed_at=CURRENT_TIMESTAMP
                WHERE id=? AND status='executing'
                """,
                (execution["run_id"], request["id"]),
            )
            connection.execute(
                """
                INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
                VALUES ('owner', 'control-center', 'action.approved', 'action_request', ?, ?)
                """,
                (
                    request["id"],
                    json.dumps({"execution_tool_run_id": execution["run_id"]}, separators=(",", ":")),
                ),
            )
        return approvals._request_for_owner(request["id"])

    approvals.approve_request = extended_approve_request

    # Approval Federation intentionally supports delegated review for ordinary
    # memory/task actions. File mutations are different: a paired wrapper may
    # cancel/deny its own request, but only local owner control may approve one.
    from . import approval_federation

    original_federated_review = approval_federation.review_request_for_app

    def review_request_for_app(app_key: str, request_id: str, decision: str) -> dict[str, Any]:
        existing = approval_federation.get_request_for_app(app_key, request_id)
        normalized = str(decision or "").strip().lower()
        if existing.get("action_key") in _FILE_ACTIONS and normalized == "approve":
            raise approvals.ApprovalError(
                "Governed file mutations require approval from local HomeServer owner control.",
                403,
            )
        return original_federated_review(app_key, request_id, decision)

    approval_federation.review_request_for_app = review_request_for_app
    approvals._local_file_actions_v039_installed = True