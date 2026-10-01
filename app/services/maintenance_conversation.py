"""Issue-bound Agent Chat maintenance: read-only diagnosis and governed proposals.

Never executes a repair. The canonical Apps approval system remains authoritative.
"""
from __future__ import annotations
import re
import json
from datetime import datetime, timezone
from threading import RLock
from typing import Any
from . import health_repair

_KEY = re.compile(r"^[a-z0-9][a-z0-9:._-]{0,159}$")
_ALLOWED = frozenset({"apps.recover"})
_PROPOSAL_LOCK=RLock()
CONTRACT = "vp3.homeserver.maintenance-conversation.v1"

class MaintenanceError(ValueError):
    def __init__(self, message: str, status_code: int=422):
        super().__init__(message)
        self.status_code=status_code

def _validate(arguments: dict[str, Any]) -> str:
    if not isinstance(arguments, dict) or set(arguments)!={"issue_key"}:
        raise MaintenanceError("Supply only issue_key.")
    key=arguments["issue_key"]
    if not isinstance(key,str) or not _KEY.fullmatch(key):
        raise MaintenanceError("Invalid health issue key.")
    return key

def issue_detail(arguments: dict[str, Any]) -> dict[str, Any]:
    key=_validate(arguments)
    state=health_repair.status()
    issue=next((item for item in state["issues"] if item["key"]==key),None)
    if issue is None:
        raise MaintenanceError("Health issue is not currently active.",404)
    repair=issue.get("repair") or {}
    action=repair.get("action_key")
    # Do not expose arbitrary app labels/details as instructions to the model.
    return {
        "contract":CONTRACT,
        "issue_key":key,
        "severity":issue["severity"],
        "source":issue["source"],
        "snapshot_complete":bool(state.get("snapshot_complete",True)),
        "unavailable_check_count":int(state.get("unavailable_check_count",0)),
        "repair_class":repair.get("class") or "diagnose_only",
        "canonical_action":action if action in _ALLOWED else None,
        "owner_approval_required":bool(repair.get("owner_approval_required")),
        "agent_can_propose":bool(
            action in _ALLOWED and repair.get("agent_can_execute")
            and repair.get("owner_approval_required")
            and state.get("snapshot_complete",True)
        ),
        "automatic_execution":False,
    }

def propose_repair(arguments: dict[str,Any], *, source_app_key: str, owner: bool) -> dict[str,Any]:
    if not owner:
        raise MaintenanceError("Only the HomeServer owner Agent can propose repairs.",403)
    key=_validate(arguments)
    # Re-probe immediately, rather than trusting a stale notification or model claim.
    state=health_repair.status()
    if not state.get("snapshot_complete",True):
        raise MaintenanceError("Health snapshot is incomplete; retry diagnostics first.",409)
    issue=next((item for item in state["issues"] if item["key"]==key),None)
    if issue is None:
        raise MaintenanceError("Health issue has resolved or changed; diagnose again.",409)
    repair=issue.get("repair") or {}
    action=repair.get("action_key")
    args=repair.get("arguments")
    if (
        action not in _ALLOWED or not repair.get("agent_can_execute")
        or not repair.get("owner_approval_required")
        or not isinstance(args,dict)
        or set(args)!={"app_key"} or not isinstance(args.get("app_key"),str)
    ):
        raise MaintenanceError("No approved canonical recovery is available for this issue.",409)
    # Reuse the durable canonical Apps approval ledger; suppress accidental
    # repeated requests when the Agent sees the same notification twice.
    from . import approvals, homeserver_app_approvals
    with _PROPOSAL_LOCK:
        for pending in homeserver_app_approvals.list_rows(status="pending",limit=500):
            if pending.get("action_key")!=action or pending.get("source_app_key")!=source_app_key:
                continue
            try:
                expires=datetime.fromisoformat(str(pending.get("expires_at") or ""))
                arguments_json=json.loads(str(pending.get("arguments_json") or "{}"))
                if (expires.tzinfo is not None and expires > datetime.now(timezone.utc)
                        and arguments_json==args):
                    result={
                        "tool":f"{action}.request",
                        "run_id":pending.get("request_tool_run_id"),
                        "status":"completed",
                        "result":{
                            "request_id":str(pending["id"]),
                            "status":"pending",
                            "action":action,
                            "owner_approval_required":True,
                            "expires_at":pending.get("expires_at"),
                            "duplicate_suppressed":True,
                        },
                    }
                    break
            except (TypeError,ValueError,KeyError):
                continue
        else:
            result=approvals.create_app_action_request(source_app_key,action,args,owner=True)
    return {
        **result,
        "maintenance":{
            "contract":CONTRACT,
            "issue_key":key,
            "action":action,
            "status":"pending_owner_approval",
            "automatic_execution":False,
        },
    }
