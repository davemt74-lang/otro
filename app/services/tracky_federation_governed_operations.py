from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from ..database import db
from . import fleet_management, hardware_adapters, tracky_federation_access_operations, tracky_federation_agent_health, tracky_federation_fleet_health, tracky_federation_reconciliation, tracky_site_topology

VERSION="2.80"
PROTOCOL="physical_federation_governed_operations.v1"
OPERATIONS=("reconnect","reconcile","restart_runtime","request_update","revoke_site","revoke_device","transfer_authority")
STATES=("proposed","awaiting_approval","approved","queued","running","reconciling","completed","failed","rejected","cancelled","expired")
TERMINAL={"completed","failed","rejected","cancelled","expired"}
TRANSITIONS={
 "proposed":{"awaiting_approval","approved","rejected","cancelled","expired"},
 "awaiting_approval":{"approved","rejected","cancelled","expired"},
 "approved":{"queued","rejected","cancelled","expired"},
 "queued":{"running","failed","cancelled","expired"},
 "running":{"reconciling","completed","failed","cancelled","expired"},
 "reconciling":{"completed","failed","cancelled","expired"},
 "completed":set(),"failed":set(),"rejected":set(),"cancelled":set(),"expired":set(),
}
HIGH_RISK={"restart_runtime","request_update","revoke_site","revoke_device","transfer_authority"}

class FederationOperationError(RuntimeError):
    def __init__(self,message:str,status_code:int=400):
        super().__init__(message); self.status_code=status_code

def _now_ms()->int: return int(datetime.now(timezone.utc).timestamp()*1000)
def _text(v:Any,n:int=240)->str: return " ".join(str(v or "").split())[:max(1,n)]
def _site(v:Any)->str: return _text(v,64).lower()
def _json(v:Any)->str: return json.dumps(v,separators=(",",":"),sort_keys=True)
def _loads(v:Any,default:Any)->Any:
    try: return json.loads(str(v or ""))
    except (ValueError,TypeError,json.JSONDecodeError): return default

def _row(row:Any)->dict[str,Any]:
    return {
      "request_id":str(row["request_id"]),"idempotency_key":str(row["idempotency_key"]),"operation_type":str(row["operation_type"]),
      "target_site_id":str(row["target_site_id"]),"device_id":str(row["device_id"] or ""),"new_authority_device_id":str(row["new_authority_device_id"] or ""),
      "state":str(row["state"]),"requires_approval":bool(row["requires_approval"]),"requires_reconciliation":bool(row["requires_reconciliation"]),"expires_at_ms":int(row["expires_at_ms"] or 0),
      "actor":_loads(row["actor_json"],{}),"reason_codes":_loads(row["reason_codes_json"],[]),"parameters":_loads(row["parameters_json"],{}),
      "authority_epoch_before":int(row["authority_epoch_before"] or 0),"authority_epoch_after":int(row["authority_epoch_after"] or 0),
      "result":_loads(row["result_json"],{}),"last_error":str(row["last_error"] or ""),"created_at":str(row["created_at"] or ""),"updated_at":str(row["updated_at"] or "")
    }

def _get(request_id:str)->dict[str,Any]:
    with db() as c:
        r=c.execute("SELECT * FROM tracky_federation_operation_ledger WHERE request_id=? LIMIT 1",(request_id,)).fetchone()
    if r is None: raise FederationOperationError("Federation operation was not found.",404)
    return _row(r)

def _set(request_id:str,state:str,*,result:dict[str,Any]|None=None,error:str="",authority_epoch_after:int|None=None)->dict[str,Any]:
    if state not in STATES: raise FederationOperationError("Federation operation state is invalid.")
    with db() as c:
        current=c.execute("SELECT state FROM tracky_federation_operation_ledger WHERE request_id=?",(request_id,)).fetchone()
        if current is None: raise FederationOperationError("Federation operation was not found.",404)
        current_state=str(current["state"])
        if current_state in TERMINAL and current_state!=state: raise FederationOperationError("Terminal federation operation is immutable.",409)
        if current_state!=state and state not in TRANSITIONS.get(current_state,set()): raise FederationOperationError("Federation operation transition is not allowed.",409)
        c.execute("""UPDATE tracky_federation_operation_ledger SET state=?,result_json=?,last_error=?,
          authority_epoch_after=COALESCE(?,authority_epoch_after),updated_at=CURRENT_TIMESTAMP WHERE request_id=?""",
          (state,_json(result or {}),_text(error,500),authority_epoch_after,request_id))
        c.execute("INSERT INTO tracky_federation_operation_events(request_id,state,detail_json) VALUES (?,?,?)",(request_id,state,_json(result or {"error":_text(error,500)})))
    return _get(request_id)

def propose(payload:dict[str,Any],*,actor:dict[str,Any]|None=None)->dict[str,Any]:
    op=_text(payload.get("operation_type"),40).lower()
    if op not in OPERATIONS: raise FederationOperationError("Unsupported federation operation.")
    target=_site(payload.get("target_site_id"))
    if not target: raise FederationOperationError("Target site is required.")
    actor=dict(actor or payload.get("actor") or {})
    actor_type=_text(actor.get("actor_type") or actor.get("type") or "user",30).lower()
    request_id=_text(payload.get("request_id"),128) or "fop-"+uuid.uuid4().hex
    requested_at_ms=_now_ms()
    expires_at_ms=max(requested_at_ms+60_000,int(payload.get("expires_at_ms") or requested_at_ms+900_000))
    idem=_text(payload.get("idempotency_key"),160) or request_id
    access=tracky_federation_access_operations.current_report()
    health=tracky_federation_agent_health.current_report()
    fleet=tracky_federation_fleet_health.current_report()
    local=_site(health.get("local_site_id") or fleet.get("local_site_id") or access.get("local_site_id"))
    peer=next((x for x in access.get("peers",[]) if _site(x.get("site_id"))==target),{})
    health_site=next((x for x in health.get("sites",[]) if _site(x.get("site_id"))==target),{})
    fleet_site=next((x for x in fleet.get("sites",[]) if _site(x.get("site_id"))==target),{})
    reasons=[]
    if target!=local and not peer.get("policy_peer_allowed"): reasons.append("site_not_permitted")
    if actor_type=="agent": reasons.append("agent_may_propose_only")
    elif actor_type=="cloud_user": reasons.append("cloud_request_requires_local_approval")
    elif actor_type not in {"user","owner","admin","system"}: reasons.append("actor_not_authorized")
    device_id=_text(payload.get("device_id"),80)
    if op in {"restart_runtime","request_update","revoke_device"} and not device_id: reasons.append("device_required")
    if op=="revoke_site" and target==local: reasons.append("revoke_site_must_target_peer")
    if op in {"restart_runtime","request_update","revoke_device","transfer_authority"} and target!=local: reasons.append("operation_requires_origin_local")
    if op=="transfer_authority":
        if target!=local: reasons.append("authority_transfer_must_be_origin_local")
        if not payload.get("new_authority_device_id"): reasons.append("new_authority_device_required")
        if not payload.get("confirmation_token"): reasons.append("explicit_confirmation_required")
        if not (health_site.get("state")=="current" and health_site.get("fresh") and health_site.get("recovery_complete")): reasons.append("authority_transfer_requires_current_source")
    hard=[x for x in reasons if x not in {"agent_may_propose_only","cloud_request_requires_local_approval"}]
    requires_approval=op in HIGH_RISK or actor_type in {"agent","cloud_user"} or bool(payload.get("require_approval"))
    state="rejected" if hard else ("awaiting_approval" if requires_approval else "approved")
    requires_reconciliation=op in {"reconnect","reconcile","restart_runtime","request_update","transfer_authority"}
    topology=tracky_site_topology.current_topology()
    site=next((x for x in topology.get("sites",[]) if _site(x.get("id") or x.get("site_id"))==target),{})
    before=int(site.get("authority_epoch") or (site.get("authority") or {}).get("epoch") or 0)
    params=payload.get("parameters") if isinstance(payload.get("parameters"),dict) else {}
    with db() as c:
        existing=c.execute("SELECT * FROM tracky_federation_operation_ledger WHERE idempotency_key=? LIMIT 1",(idem,)).fetchone()
        if existing is not None:
            row=_row(existing)
            if row["operation_type"]!=op or row["target_site_id"]!=target or row["device_id"]!=device_id: raise FederationOperationError("Federation operation idempotency conflict.",409)
            return row
        c.execute("""INSERT INTO tracky_federation_operation_ledger(
          request_id,idempotency_key,operation_type,target_site_id,device_id,new_authority_device_id,state,requires_approval,requires_reconciliation,
          actor_json,reason_codes_json,parameters_json,authority_epoch_before
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",(request_id,idem,op,target,device_id,_text(payload.get("new_authority_device_id"),80),state,1 if requires_approval else 0,1 if requires_reconciliation else 0,_json(actor),_json(reasons),_json(params),before))
        c.execute("INSERT INTO tracky_federation_operation_events(request_id,state,detail_json) VALUES (?,?,?)",(request_id,state,_json({"reason_codes":reasons})))
    return _get(request_id)

def decide(request_id:str,approved:bool,*,actor:dict[str,Any]|None=None)->dict[str,Any]:
    row=_get(request_id)
    if row["state"]!="awaiting_approval": raise FederationOperationError("Operation is not awaiting approval.",409)
    return _set(request_id,"approved" if approved else "rejected",result={"approved":bool(approved),"actor":actor or {}})

def execute(request_id:str)->dict[str,Any]:
    row=_get(request_id)
    if row["state"] not in {"approved","queued"}: raise FederationOperationError("Operation is not executable in its current state.",409)
    row=_set(request_id,"running",result={"execution_started":True})
    op=row["operation_type"]; target=row["target_site_id"]; p=row["parameters"]
    try:
        result:dict[str,Any]={}
        if op in {"reconnect","reconcile"}:
            result=tracky_federation_reconciliation.schedule_retry(target,reason="operator_"+op)
        elif op=="restart_runtime":
            hardware_adapters.stop(); hardware_adapters.start(); result={"runtime":"hardware_adapters","restart_requested":True}
        elif op=="request_update":
            fleet_settings=fleet_management.get_settings()
            requester=_text(p.get("requester_app_key") or fleet_settings.get("controller_app_key"),100)
            result=fleet_management.request_update(requester,_text(p.get("request_key") or row["request_id"],120),_text(p.get("package_sha256"),64),_text(p.get("release_version"),40),int(p["rollout_id"]) if p.get("rollout_id") is not None else None)
        elif op=="revoke_device":
            result=fleet_management.remove_inventory_device(row["device_id"])
            return _set(request_id,"completed",result=result)
        elif op=="transfer_authority":
            result=tracky_site_topology.claim_site_authority(site_id=target,device_id=row["new_authority_device_id"],replace=True,reason="governed_federation_operation")
            topology=tracky_site_topology.current_topology()
            site=next((x for x in topology.get("sites",[]) if _site(x.get("id") or x.get("site_id"))==target),{})
            after=int(site.get("authority_epoch") or (site.get("authority") or {}).get("epoch") or 0)
            if after<=row["authority_epoch_before"]: raise FederationOperationError("Authority epoch did not advance.",409)
            tracky_federation_reconciliation.schedule_retry(target,reason="authority_transfer")
            return _set(request_id,"reconciling",result=result,authority_epoch_after=after)
        if row["requires_reconciliation"]:
            return _set(request_id,"reconciling",result=result)
        return _set(request_id,"completed",result=result)
    except Exception as exc:
        _set(request_id,"failed",error=str(exc))
        raise

def refresh_reconciliation(request_id:str)->dict[str,Any]:
    row=_get(request_id)
    if row["state"]!="reconciling": return row
    health=tracky_federation_agent_health.current_report()
    site=next((x for x in health.get("sites",[]) if _site(x.get("site_id"))==row["target_site_id"]),{})
    if site.get("state")=="current" and site.get("fresh") and site.get("recovery_complete"):
        if row["operation_type"]=="transfer_authority" and row["authority_epoch_after"]<=row["authority_epoch_before"]:
            return _set(request_id,"failed",error="Authority epoch did not advance.")
        return _set(request_id,"completed",result={**row.get("result",{}),"authoritative_reconciliation":{"state":"current","fresh":True,"recovery_complete":True}})
    return row

def cancel(request_id:str)->dict[str,Any]:
    row=_get(request_id)
    if row["state"] in TERMINAL: return row
    return _set(request_id,"cancelled",result={"cancelled":True})

def report(limit:int=100)->dict[str,Any]:
    with db() as c:
        rows=c.execute("SELECT * FROM tracky_federation_operation_ledger ORDER BY id DESC LIMIT ?",(max(1,min(500,int(limit))),)).fetchall()
    items=[_row(r) for r in rows]
    active=[x for x in items if x["state"] not in TERMINAL]
    return {"protocol":PROTOCOL,"version":VERSION,"schema_version":1,"operations":items,"active":active,
      "counts":{"total":len(items),"active":len(active),"awaiting_approval":sum(1 for x in active if x["state"]=="awaiting_approval"),"reconciling":sum(1 for x in active if x["state"]=="reconciling")},
      "agent_context":{"active":[{"request_id":x["request_id"],"operation_type":x["operation_type"],"target_site_id":x["target_site_id"],"state":x["state"],"reason_codes":x["reason_codes"]} for x in active[:32]],"agent_may_propose":True,"agent_may_execute":False,"cloud_may_execute":False,"recovery_rule":"authoritative_reconciliation_required"},
      "safety":{"section7_health_is_authoritative":True,"cloud_execution_allowed":False,"agent_execution_allowed":False,"authority_transfer_automatic":False,"reconnect_marks_recovered":False}}

def ingest_cloud_requests(projection:dict[str,Any])->list[dict[str,Any]]:
    if not isinstance(projection,dict) or projection.get("cloud_role")!="request_relay_only": return []
    if projection.get("remote_command_execution") or projection.get("authority_mutation"): raise FederationOperationError("Cloud request relay attempted forbidden execution authority.",403)
    out=[]
    for item in list(projection.get("requests") or [])[:50]:
        if not isinstance(item,dict): continue
        payload={
          "request_id":_text(item.get("request_id"),128),"idempotency_key":_text(item.get("idempotency_key"),160),
          "operation_type":_text(item.get("operation_type"),40),"target_site_id":_site(item.get("target_site_id")),
          "device_id":_text(item.get("device_id"),80),"new_authority_device_id":_text(item.get("new_authority_device_id"),80),
          "confirmation_token":"cloud_user_explicit_request" if item.get("explicit_confirmation") else "",
          "require_approval":True,"parameters":item.get("parameters") if isinstance(item.get("parameters"),dict) else {}
        }
        out.append(propose(payload,actor={"actor_type":"cloud_user","actor_id":"vp3_cloud_account"}))
    return out

def cloud_projection()->dict[str,Any]:
    r=report()
    safe=[]
    for x in r.get("operations",[]):
        safe.append({k:x.get(k) for k in ("request_id","idempotency_key","operation_type","target_site_id","device_id","new_authority_device_id","state","requires_approval","requires_reconciliation","authority_epoch_before","authority_epoch_after","last_error","created_at","updated_at")})
    return {"protocol":PROTOCOL,"version":VERSION,"schema_version":1,"generated_at":_now_ms(),"local_site_id":tracky_federation_agent_health.current_report().get("local_site_id") or "",
      "operations":safe,"counts":r.get("counts",{}),"summary_only":True,"cloud_read_only":True,"remote_command_execution":False,"authority_mutation":False,
      "safety":r.get("safety",{})}

def public_capability()->dict[str,Any]:
    return {"version":VERSION,"protocol":PROTOCOL,"operations":list(OPERATIONS),"states":list(STATES),"idempotent_requests":True,
      "durable_audit":True,"agent_proposal_only":True,"cloud_execution_allowed":False,"section7_health_is_authoritative":True,
      "completion_requires_authoritative_reconciliation":True,"authority_transfer_requires_epoch_advance":True}
