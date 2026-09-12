from __future__ import annotations

import json
import uuid
from typing import Any, Callable

from ..database import db
from . import approvals, tools, vp3_commerce_agent_connector

ACTIONS = {"vp3.commerce.fulfillment.update"}


def _connector_ready() -> bool:
    try:return bool(vp3_commerce_agent_connector.status().get("configured"))
    except Exception:return False


def _normalize(action_key:str,arguments:dict[str,Any]|None)->dict[str,Any]:
    payload=dict(arguments or {});payload.pop("idempotency_key",None)
    unknown=set(payload)-{"order_id","status"}
    if unknown:raise approvals.ApprovalError(f"Unsupported fulfillment proposal argument: {sorted(unknown)[0]}")
    order_id=str(payload.get("order_id") or "").strip();status=str(payload.get("status") or "").strip().lower()
    if not order_id or len(order_id)>160 or not all(ch.isalnum() or ch in "._:-" for ch in order_id):raise approvals.ApprovalError("VP3 fulfillment proposal has an invalid order_id.")
    if status not in {"processing","fulfilled"}:raise approvals.ApprovalError("VP3 fulfillment proposal status must be processing or fulfilled.")
    return {"order_id":order_id,"status":status,"idempotency_key":"hs-commerce-action-"+uuid.uuid4().hex}


def create_request(source_app_key:str,action_key:str,arguments:dict[str,Any]|None,*,owner:bool=False)->dict[str,Any]:
    if action_key not in ACTIONS:raise approvals.ApprovalError("Unsupported VP3 Agent Commerce action.")
    if not _connector_ready():raise approvals.ApprovalError("VP3 Agent Commerce connector is not configured.",409)
    source=source_app_key.strip() or ("owner" if owner else "app:unknown");actor="owner" if owner else "app";required=[] if owner else ["commerce.fulfill","tools.execute"]
    try:normalized=_normalize(action_key,arguments)
    except approvals.ApprovalError as exc:
        run_id=approvals._record_failed_proposal(source,actor,action_key,required,{"argument_keys":sorted((arguments or {}).keys())},str(exc));raise approvals.ApprovalError(f"{exc} Run {run_id} was recorded.",exc.status_code) from exc
    meta={"order_id":normalized["order_id"][:32],"status":normalized["status"]}
    return approvals._create_action_request(source,actor,action_key,normalized,meta,required)


def install()->None:
    if getattr(approvals,"_vp3_commerce_agent_v061_installed",False):return
    original:Callable[[str],dict[str,Any]]=approvals.approve_request
    def approve_request(request_id:str)->dict[str,Any]:
        request=approvals._request_for_owner(request_id)
        if request["action_key"] not in ACTIONS:return original(request_id)
        if request["status"]!="pending":raise approvals.ApprovalError(f"Action request is already {request['status']}.",409)
        with db() as connection:
            reserved=connection.execute("UPDATE action_requests SET status='executing', decided_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'",(request["id"],))
            if reserved.rowcount!=1:raise approvals.ApprovalError("Action request is no longer pending.",409)
        try:execution=tools.execute_tool(request["source_app_key"],request["action_key"],request["arguments"],set(),owner=True)
        except tools.ToolError as exc:
            run_id=approvals._extract_run_id(str(exc))
            with db() as connection:
                connection.execute("UPDATE action_requests SET status='failed', execution_tool_run_id=?, error=?, executed_at=CURRENT_TIMESTAMP WHERE id=? AND status='executing'",(run_id,str(exc)[:1000],request["id"]))
                connection.execute("INSERT INTO activity_log(actor_type,actor_key,action,resource_type,resource_key,metadata_json) VALUES('owner','control-center','action.failed','action_request',?,?)",(request["id"],json.dumps({"execution_tool_run_id":run_id},separators=(",",":"))))
            raise approvals.ApprovalError(f"Approved VP3 fulfillment action could not execute: {exc}",exc.status_code) from exc
        with db() as connection:
            connection.execute("UPDATE action_requests SET status='executed', execution_tool_run_id=?, error=NULL, executed_at=CURRENT_TIMESTAMP WHERE id=? AND status='executing'",(execution["run_id"],request["id"]))
            connection.execute("INSERT INTO activity_log(actor_type,actor_key,action,resource_type,resource_key,metadata_json) VALUES('owner','control-center','action.approved','action_request',?,?)",(request["id"],json.dumps({"execution_tool_run_id":execution["run_id"]},separators=(",",":"))))
        return approvals._request_for_owner(request["id"])
    approvals.approve_request=approve_request
    from . import approval_federation
    original_review=approval_federation.review_request_for_app
    def review_for_app(app_key:str,request_id:str,decision:str)->dict[str,Any]:
        existing=approval_federation.get_request_for_app(app_key,request_id)
        if existing.get("action_key") in ACTIONS and str(decision).strip().lower()=="approve":raise approvals.ApprovalError("VP3 Commerce fulfillment requires local HomeServer owner approval.",403)
        return original_review(app_key,request_id,decision)
    approval_federation.review_request_for_app=review_for_app;approvals._vp3_commerce_agent_v061_installed=True
