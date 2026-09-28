from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from ..database import db
from . import tracky_federation_sync, tracky_site_topology

VERSION="2.81"
PROTOCOL="physical_federated_automation.v1"
SECTION=1
AUTOMATION_STATES={"draft","active","paused","retired"}
RUN_STATES={"planned","waiting","ready","running","blocked","recovering","completed","failed","cancelled","expired"}
STEP_STATES={"pending","blocked","ready","running","completed","failed","cancelled","expired"}
TRIGGER_KINDS={"manual","schedule","world_state","device_state","presence","event"}
ACTION_TYPES={"physical_action","local_routine","agent_workflow","notification","data_operation"}
TERMINAL_RUN={"completed","failed","cancelled","expired"}
TERMINAL_STEP={"completed","failed","cancelled","expired"}
RUN_TRANSITIONS={
 "planned":{"waiting","ready","blocked","cancelled","expired","failed"},
 "waiting":{"ready","blocked","recovering","cancelled","expired","failed"},
 "ready":{"running","blocked","recovering","cancelled","expired","failed"},
 "running":{"waiting","blocked","recovering","completed","cancelled","expired","failed"},
 "blocked":{"waiting","ready","recovering","cancelled","expired","failed"},
 "recovering":{"waiting","ready","blocked","cancelled","expired","failed"},
 "completed":set(),"failed":set(),"cancelled":set(),"expired":set(),
}

class FederatedAutomationError(RuntimeError):
    def __init__(self,message:str,status_code:int=422):
        super().__init__(message); self.status_code=int(status_code)

def _now_ms()->int: return int(datetime.now(timezone.utc).timestamp()*1000)
def _text(v:Any,n:int=240)->str: return " ".join(str(v or "").split())[:max(1,n)]
def _site(v:Any)->str:
    s=_text(v,64).lower()
    if not __import__("re").match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",s):
        raise FederatedAutomationError("Federated automation site id must be a UUID.")
    return s
def _id(v:Any,label:str,n:int=160)->str:
    s=_text(v,n)
    if not s: raise FederatedAutomationError(f"{label} is required.")
    if not __import__("re").match(r"^[A-Za-z0-9._:-]+$",s): raise FederatedAutomationError(f"{label} contains unsupported characters.")
    return s
def _json(v:Any)->str: return json.dumps(v,separators=(",",":"),sort_keys=True,ensure_ascii=False)
def _decode(v:Any,default:Any)->Any:
    try:return json.loads(str(v or ""))
    except (ValueError,TypeError,json.JSONDecodeError):return default
def _hash(v:Any)->str:return hashlib.sha256(_json(v).encode("utf-8")).hexdigest()
def _actor(v:Any)->dict[str,Any]:
    a=v if isinstance(v,dict) else {}
    return {"actor_id":_text(a.get("actor_id") or a.get("id"),100),"actor_type":_text(a.get("actor_type") or a.get("type") or "user",30).lower(),"display_name":_text(a.get("display_name") or a.get("label"),120)}
def _local_site()->str:
    local=tracky_federation_sync.local_site_id(auto_pin=False)
    if not local: raise FederatedAutomationError("Federated automation requires a resolved local federation site.",409)
    return _site(local)
def _known_sites()->set[str]:
    return {_site(x.get("id") or x.get("site_id")) for x in tracky_site_topology.current_topology().get("sites",[]) if (x.get("id") or x.get("site_id"))}
def _validate_actor(actor:dict[str,Any])->None:
    if actor["actor_type"]=="agent": raise FederatedAutomationError("Agent may propose federated automation but cannot create authoritative definitions.",403)
    if actor["actor_type"] not in {"user","owner","admin","system"}: raise FederatedAutomationError("Actor is not authorized for federated automation.",403)

def _normalize_trigger(raw:Any,origin:str)->dict[str,Any]:
    item=raw if isinstance(raw,dict) else {}
    kind=_text(item.get("kind") or "manual",40).lower()
    if kind not in TRIGGER_KINDS: raise FederatedAutomationError("Federated automation trigger kind is invalid.")
    source=_site(item.get("source_site_id") or origin)
    return {"kind":kind,"source_site_id":source,"event_key":_text(item.get("event_key"),120) or None,
      "config":item.get("config") if isinstance(item.get("config"),dict) else {},
      "debounce_ms":min(86_400_000,max(0,int(item.get("debounce_ms") or 0))),
      "requires_fresh_world_state":kind in {"world_state","device_state","presence"}}

def _normalize_steps(raw:Any,origin:str)->list[dict[str,Any]]:
    if not isinstance(raw,list) or not 1<=len(raw)<=64: raise FederatedAutomationError("Federated automation requires between 1 and 64 steps.")
    known=_known_sites(); out=[]; ids=set()
    for pos,item in enumerate(raw,1):
        if not isinstance(item,dict): raise FederatedAutomationError("Each federated automation step must be an object.")
        step_id=_id(item.get("step_id") or f"step-{pos}","step_id",80)
        if step_id in ids: raise FederatedAutomationError("Federated automation step ids must be unique.")
        ids.add(step_id)
        action_type=_text(item.get("action_type"),40).lower()
        if action_type not in ACTION_TYPES: raise FederatedAutomationError("Federated automation action_type is invalid.")
        authority=_site(item.get("authority_site_id") or item.get("site_id") or origin)
        target=_site(item.get("target_site_id") or authority)
        if authority not in known or target not in known: raise FederatedAutomationError("Federated automation step references an unknown federation site.",409)
        permissions=sorted({_text(x,80) for x in (item.get("required_permissions") or []) if _text(x,80)})
        if action_type=="physical_action" and not permissions: raise FederatedAutomationError("Physical automation steps require at least one permission scope.")
        approval=_text(item.get("approval_mode") or "governed",30).lower()
        if approval not in {"governed","always","inherit"}: raise FederatedAutomationError("Federated automation approval mode is invalid.")
        device=_text(item.get("device_id"),100) or None
        if device:
            try:
                topo_device=tracky_site_topology.get_device(device)
            except Exception as exc:
                raise FederatedAutomationError("Federated automation references an unknown device.",409) from exc
            if _site(topo_device.get("site_id"))!=target: raise FederatedAutomationError("Federated automation device does not belong to the target site.",409)
        out.append({"step_id":step_id,"label":_text(item.get("label"),120) or step_id,"action_type":action_type,
          "authority_site_id":authority,"target_site_id":target,"device_id":device,
          "action_key":_id(item.get("action_key") or item.get("command") or action_type,"action_key",120),
          "arguments":item.get("arguments") if isinstance(item.get("arguments"),dict) else {},
          "depends_on":sorted({_id(x,"dependency",80) for x in (item.get("depends_on") or [])}),
          "required_permissions":permissions,"approval_mode":approval,
          "timeout_ms":min(3_600_000,max(1000,int(item.get("timeout_ms") or 60_000))),
          "deadline_offset_ms":min(604_800_000,max(0,int(item.get("deadline_offset_ms") or 0))),
          "retry_policy":{"max_attempts":min(10,max(1,int((item.get("retry_policy") or {}).get("max_attempts") or 1))),"backoff_ms":min(3_600_000,max(0,int((item.get("retry_policy") or {}).get("backoff_ms") or 0)))},
          "reversible":bool(item.get("reversible")),"compensating_action":item.get("compensating_action") if isinstance(item.get("compensating_action"),dict) else None,
          "execution_contract":"authoritative_homeserver_only"})
    by_id={x["step_id"]:x for x in out}
    for step in out:
        for dep in step["depends_on"]:
            if dep==step["step_id"]: raise FederatedAutomationError("Federated automation step cannot depend on itself.")
            if dep not in by_id: raise FederatedAutomationError("Federated automation dependency does not exist.")
    visiting=set(); visited=set()
    def visit(step_id:str)->None:
        if step_id in visiting: raise FederatedAutomationError("Federated automation dependency graph contains a cycle.")
        if step_id in visited:return
        visiting.add(step_id)
        for dep in by_id[step_id]["depends_on"]:visit(dep)
        visiting.remove(step_id);visited.add(step_id)
    for step_id in by_id:visit(step_id)
    return out

def _definition_row(row:Any)->dict[str,Any]:
    return _decode(row["definition_json"],{}) if row else {}

def create_definition(payload:dict[str,Any],*,actor:dict[str,Any]|None=None)->dict[str,Any]:
    actor_n=_actor(actor or payload.get("actor"));_validate_actor(actor_n)
    origin=_site(payload.get("origin_site_id")); local=_local_site()
    if origin!=local: raise FederatedAutomationError("Only the origin HomeServer may author an authoritative federated automation definition.",409)
    explicit_idempotency=_text(payload.get("idempotency_key"),160)
    explicit_automation=_text(payload.get("automation_id"),128)
    if not explicit_automation and not explicit_idempotency:
        raise FederatedAutomationError("Federated automation requires automation_id or idempotency_key for replay-safe creation.")
    if explicit_automation:
        automation_id=_id(explicit_automation,"automation_id",128)
    else:
        automation_id="fa-"+hashlib.sha256(explicit_idempotency.encode("utf-8")).hexdigest()[:32]
    idempotency=_id(explicit_idempotency or automation_id+":"+str(int(payload.get("revision") or 1)),"idempotency_key",160)
    state=_text(payload.get("state") or "draft",30).lower()
    if state not in AUTOMATION_STATES: raise FederatedAutomationError("Federated automation state is invalid.")
    request_fingerprint=_hash({k:payload.get(k) for k in ("automation_id","revision","idempotency_key","name","description","origin_site_id","state","trigger","steps","participating_site_ids","participating_device_ids","approval_policy","default_deadline_ms")})
    with db() as c:
        replay=c.execute("SELECT definition_json FROM tracky_federated_automation_definitions WHERE idempotency_key=? LIMIT 1",(idempotency,)).fetchone()
    if replay is not None:
        prior=_definition_row(replay)
        if prior.get("request_fingerprint")!=request_fingerprint: raise FederatedAutomationError("Federated automation definition idempotency conflict.",409)
        return prior
    trigger=_normalize_trigger(payload.get("trigger"),origin); steps=_normalize_steps(payload.get("steps"),origin)
    known_sites=_known_sites()
    if trigger["source_site_id"] not in known_sites: raise FederatedAutomationError("Federated automation trigger references an unknown federation site.",409)
    participating_sites={origin,trigger["source_site_id"],*[x["authority_site_id"] for x in steps],*[x["target_site_id"] for x in steps]}
    for raw_site in payload.get("participating_site_ids") or []:
        extra_site=_site(raw_site)
        if extra_site not in known_sites: raise FederatedAutomationError("Federated automation participant references an unknown federation site.",409)
        participating_sites.add(extra_site)
    participating_devices={x["device_id"] for x in steps if x["device_id"]}
    for raw_device in payload.get("participating_device_ids") or []:
        device_id=_text(raw_device,100)
        if not device_id: continue
        try: tracky_site_topology.get_device(device_id)
        except Exception as exc: raise FederatedAutomationError("Federated automation participant references an unknown device.",409) from exc
        participating_devices.add(device_id)
    participating_sites=sorted(participating_sites);participating_devices=sorted(participating_devices)
    approval_policy=_text(payload.get("approval_policy") or "governed",30).lower()
    if approval_policy not in {"governed","always"}: raise FederatedAutomationError("Federated automation approval policy is invalid.")
    default_deadline=min(2_592_000_000,max(0,int(payload.get("default_deadline_ms") or 0)))
    with db() as c:
        head=c.execute("SELECT * FROM tracky_federated_automation_heads WHERE automation_id=? LIMIT 1",(automation_id,)).fetchone()
        revision=int(payload.get("revision") or (int(head["current_revision"])+1 if head else 1))
        if head is not None:
            if str(head["origin_site_id"])!=origin: raise FederatedAutomationError("Federated automation origin site is immutable.",409)
            if revision!=int(head["current_revision"])+1: raise FederatedAutomationError("Federated automation revision must advance exactly once.",409)
        elif revision!=1: raise FederatedAutomationError("First federated automation revision must be 1.",409)
        definition={"protocol":PROTOCOL,"version":VERSION,"schema_version":1,"automation_id":automation_id,"revision":revision,
          "name":_text(payload.get("name"),160) or automation_id,"description":_text(payload.get("description"),1000),"origin_site_id":origin,
          "state":state,"trigger":trigger,"steps":steps,"participating_site_ids":participating_sites,"participating_device_ids":participating_devices,
          "approval_policy":approval_policy,"default_deadline_ms":default_deadline,
          "idempotency_key":idempotency,"request_fingerprint":request_fingerprint,"actor":actor_n,"created_at_ms":_now_ms(),"updated_at_ms":_now_ms(),
          "safety":{"execution_enabled":False,"cloud_execution_allowed":False,"agent_execution_allowed":False,"origin_homeserver_authoritative":True,
                    "step_authority_site_required":True,"permissions_required":True,"federation_v280_invariants_required":True}}
        semantic=_hash({k:v for k,v in definition.items() if k not in {"created_at_ms","updated_at_ms"}})
        c.execute("""INSERT INTO tracky_federated_automation_definitions(automation_id,revision,idempotency_key,origin_site_id,state,name,semantic_hash,definition_json,actor_json)
          VALUES(?,?,?,?,?,?,?,?,?)""",(automation_id,revision,idempotency,origin,state,definition["name"],semantic,_json(definition),_json(actor_n)))
        c.execute("""INSERT INTO tracky_federated_automation_heads(automation_id,origin_site_id,current_revision,state,name)
          VALUES(?,?,?,?,?) ON CONFLICT(automation_id) DO UPDATE SET current_revision=excluded.current_revision,state=excluded.state,name=excluded.name,updated_at=CURRENT_TIMESTAMP""",
          (automation_id,origin,revision,state,definition["name"]))
        c.execute("INSERT INTO tracky_federated_automation_events(automation_id,event_kind,state,actor_json,detail_json) VALUES (?,?,?,?,?)",
          (automation_id,"definition.created",state,_json(actor_n),_json({"revision":revision,"semantic_hash":semantic})))
    return definition

def get_definition(automation_id:str,revision:int|None=None)->dict[str,Any]:
    key=_id(automation_id,"automation_id",128)
    with db() as c:
        if revision is None:
            head=c.execute("SELECT current_revision FROM tracky_federated_automation_heads WHERE automation_id=?",(key,)).fetchone()
            if head is None: raise FederatedAutomationError("Federated automation was not found.",404)
            revision=int(head["current_revision"])
        row=c.execute("SELECT definition_json FROM tracky_federated_automation_definitions WHERE automation_id=? AND revision=?",(key,int(revision))).fetchone()
    if row is None: raise FederatedAutomationError("Federated automation revision was not found.",404)
    return _definition_row(row)

def list_definitions(limit:int=100)->list[dict[str,Any]]:
    with db() as c:
        rows=c.execute("""SELECT d.definition_json FROM tracky_federated_automation_heads h
          JOIN tracky_federated_automation_definitions d ON d.automation_id=h.automation_id AND d.revision=h.current_revision
          ORDER BY h.updated_at DESC LIMIT ?""",(max(1,min(500,int(limit))),)).fetchall()
    return [_definition_row(x) for x in rows]

def _run_row(row:Any)->dict[str,Any]:
    return _decode(row["run_json"],{}) if row else {}

def _load_steps(run_id:str)->list[dict[str,Any]]:
    with db() as c: rows=c.execute("SELECT step_json FROM tracky_federated_automation_steps WHERE run_id=? ORDER BY id",(run_id,)).fetchall()
    return [_decode(x["step_json"],{}) for x in rows]

def _write_run(run:dict[str,Any])->None:
    with db() as c:
        c.execute("UPDATE tracky_federated_automation_runs SET state=?,run_json=?,last_error=?,updated_at=CURRENT_TIMESTAMP WHERE run_id=?",
          (run["state"],_json(run),_text(run.get("last_error"),500),run["run_id"]))

def create_run(payload:dict[str,Any],*,actor:dict[str,Any]|None=None)->dict[str,Any]:
    actor_n=_actor(actor or payload.get("actor"));_validate_actor(actor_n)
    automation_id=_id(payload.get("automation_id"),"automation_id",128);definition=get_definition(automation_id)
    if definition["state"]!="active": raise FederatedAutomationError("Only active federated automations may create runs.",409)
    if definition["origin_site_id"]!=_local_site(): raise FederatedAutomationError("Only the origin HomeServer may create a federated automation run.",409)
    explicit_run=_text(payload.get("run_id"),160);explicit_idem=_text(payload.get("idempotency_key"),160)
    if not explicit_run and not explicit_idem: raise FederatedAutomationError("Federated automation run requires run_id or idempotency_key for replay-safe creation.")
    run_id=_id(explicit_run or ("far-"+hashlib.sha256(explicit_idem.encode("utf-8")).hexdigest()[:32]),"run_id",160);idem=_id(explicit_idem or run_id,"idempotency_key",160)
    run_request_fingerprint=_hash({k:payload.get(k) for k in ("automation_id","idempotency_key","trigger_event_id","deadline_at_ms")})
    with db() as c:
        existing=c.execute("SELECT run_json FROM tracky_federated_automation_runs WHERE idempotency_key=? LIMIT 1",(idem,)).fetchone()
        if existing is not None:
            run=_run_row(existing)
            if run.get("request_fingerprint")!=run_request_fingerprint: raise FederatedAutomationError("Federated automation run idempotency conflict.",409)
            return run
    now=_now_ms();deadline=int(payload.get("deadline_at_ms") or (now+int(definition.get("default_deadline_ms") or 0) if definition.get("default_deadline_ms") else 0))
    steps=[]
    for step in definition["steps"]:
        state="blocked" if step["depends_on"] else "ready"
        step_row={"step_id":step["step_id"],"state":state,"attempt":0,"authority_site_id":step["authority_site_id"],"target_site_id":step["target_site_id"],
          "device_id":step["device_id"],"required_permissions":step["required_permissions"],"depends_on":step["depends_on"],
          "deadline_at_ms":now+int(step["deadline_offset_ms"]) if step["deadline_offset_ms"] else deadline,"last_error":None,"dispatch_id":None,"updated_at_ms":now}
        steps.append(step_row)
    run={"protocol":PROTOCOL,"version":VERSION,"schema_version":1,"run_id":run_id,"idempotency_key":idem,"automation_id":automation_id,
      "automation_revision":definition["revision"],"origin_site_id":definition["origin_site_id"],"trigger_event_id":_text(payload.get("trigger_event_id"),160) or None,
      "state":"waiting" if any(x["state"]=="blocked" for x in steps) else "ready","deadline_at_ms":deadline,"steps":steps,"request_fingerprint":run_request_fingerprint,"created_at_ms":now,"updated_at_ms":now,
      "last_event":None,"last_error":"","recovery":{"durable":True,"resume_required":False,"last_checkpoint_ms":now},
      "safety":{"execution_enabled":False,"cloud_execution_allowed":False,"agent_execution_allowed":False,"authoritative_homeserver_required":True}}
    with db() as c:
        c.execute("""INSERT INTO tracky_federated_automation_runs(run_id,idempotency_key,automation_id,automation_revision,origin_site_id,trigger_event_id,state,deadline_at_ms,run_json)
          VALUES(?,?,?,?,?,?,?,?,?)""",(run_id,idem,automation_id,definition["revision"],definition["origin_site_id"],run["trigger_event_id"],run["state"],deadline,_json(run)))
        for step in steps:
            c.execute("""INSERT INTO tracky_federated_automation_steps(run_id,step_id,authority_site_id,target_site_id,device_id,state,attempt,deadline_at_ms,step_json)
              VALUES(?,?,?,?,?,?,?,?,?)""",(run_id,step["step_id"],step["authority_site_id"],step["target_site_id"],step["device_id"],step["state"],0,step["deadline_at_ms"],_json(step)))
        c.execute("INSERT INTO tracky_federated_automation_events(automation_id,run_id,event_kind,state,actor_json,detail_json) VALUES (?,?,?,?,?,?)",
          (automation_id,run_id,"run.created",run["state"],_json(actor_n),_json({"automation_revision":definition["revision"],"trigger_event_id":run["trigger_event_id"]})))
    return run

def get_run(run_id:str)->dict[str,Any]:
    key=_id(run_id,"run_id",160)
    with db() as c:row=c.execute("SELECT run_json FROM tracky_federated_automation_runs WHERE run_id=?",(key,)).fetchone()
    if row is None: raise FederatedAutomationError("Federated automation run was not found.",404)
    run=_run_row(row);run["steps"]=_load_steps(key);return run

def list_runs(limit:int=100,active_only:bool=False)->list[dict[str,Any]]:
    sql="SELECT run_id FROM tracky_federated_automation_runs"
    args=[]
    if active_only:
        sql+=" WHERE state NOT IN ('completed','failed','cancelled','expired')"
    sql+=" ORDER BY id DESC LIMIT ?";args.append(max(1,min(500,int(limit))))
    with db() as c:rows=c.execute(sql,args).fetchall()
    return [get_run(str(x["run_id"])) for x in rows]

def _event(automation_id:str,run_id:str|None,event_kind:str,state:str|None,actor:dict[str,Any],detail:dict[str,Any])->None:
    with db() as c:c.execute("INSERT INTO tracky_federated_automation_events(automation_id,run_id,event_kind,state,actor_json,detail_json) VALUES (?,?,?,?,?,?)",
      (automation_id,run_id,event_kind,state,_json(actor),_json(detail)))

def transition_run(run_id:str,state:str,*,reason:str="",actor:dict[str,Any]|None=None)->dict[str,Any]:
    run=get_run(run_id);next_state=_text(state,30).lower()
    if next_state not in RUN_STATES: raise FederatedAutomationError("Federated automation run state is invalid.")
    if run["state"]!=next_state:
        if run["state"] in TERMINAL_RUN: raise FederatedAutomationError("Terminal federated automation run is immutable.",409)
        if next_state not in RUN_TRANSITIONS.get(run["state"],set()): raise FederatedAutomationError("Federated automation run transition is invalid.",409)
        if next_state=="running": raise FederatedAutomationError("V2.81 Section 1 records automation ledgers but does not execute physical actions.",409)
    actor_n=_actor(actor);now=_now_ms();run["state"]=next_state;run["updated_at_ms"]=now;run["last_event"]={"state":next_state,"reason":_text(reason,240),"occurred_at_ms":now}
    run["recovery"]["resume_required"]=next_state=="recovering";run["recovery"]["last_checkpoint_ms"]=now
    _write_run(run);_event(run["automation_id"],run["run_id"],"run.state",next_state,actor_n,run["last_event"]);return get_run(run_id)

def cancel_run(run_id:str,*,reason:str="",actor:dict[str,Any]|None=None)->dict[str,Any]:
    run=get_run(run_id)
    if run["state"] in TERMINAL_RUN:return run
    actor_n=_actor(actor);now=_now_ms();run["state"]="cancelled";run["updated_at_ms"]=now;run["last_event"]={"state":"cancelled","reason":_text(reason,240),"occurred_at_ms":now}
    with db() as c:
        for step in run["steps"]:
            if step["state"] not in TERMINAL_STEP:
                step["state"]="cancelled";step["updated_at_ms"]=now
                c.execute("UPDATE tracky_federated_automation_steps SET state='cancelled',step_json=?,updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND step_id=?",(_json(step),run_id,step["step_id"]))
        c.execute("UPDATE tracky_federated_automation_runs SET state='cancelled',run_json=?,updated_at=CURRENT_TIMESTAMP WHERE run_id=?",(_json(run),run_id))
    _event(run["automation_id"],run_id,"run.cancelled","cancelled",actor_n,{"reason":_text(reason,240)})
    return get_run(run_id)

def recover_incomplete_runs()->dict[str,Any]:
    recovered=[]
    with db() as c:
        rows=c.execute("SELECT run_id,state FROM tracky_federated_automation_runs WHERE state='running'").fetchall()
    for row in rows:
        recovered.append(transition_run(str(row["run_id"]),"recovering",reason="process_recovery",actor={"actor_type":"system","actor_id":"homeserver_recovery"}))
    return {"recovered":len(recovered),"run_ids":[x["run_id"] for x in recovered]}

def agent_context()->dict[str,Any]:
    expire_due_runs();defs=list_definitions(32);runs=list_runs(32,active_only=True)
    return {"protocol":PROTOCOL,"version":VERSION,
      "definitions":[{"automation_id":x["automation_id"],"revision":x["revision"],"name":x["name"],"state":x["state"],"origin_site_id":x["origin_site_id"],"participating_site_ids":x["participating_site_ids"]} for x in defs],
      "active_runs":[{"run_id":x["run_id"],"automation_id":x["automation_id"],"state":x["state"],"origin_site_id":x["origin_site_id"],"deadline_at_ms":x["deadline_at_ms"]} for x in runs],
      "agent_may_propose":True,"agent_may_activate":False,"agent_may_execute":False,"cloud_may_execute":False,"execution_phase":"future_v281_distributed_execution"}

def cloud_projection()->dict[str,Any]:
    expire_due_runs();defs=list_definitions(100);runs=list_runs(100)
    return {"protocol":PROTOCOL,"version":VERSION,"schema_version":1,"generated_at":_now_ms(),"local_site_id":_local_site(),
      "definitions":[{"automation_id":x["automation_id"],"revision":x["revision"],"name":x["name"],"state":x["state"],"origin_site_id":x["origin_site_id"],
                      "trigger_kind":x["trigger"]["kind"],"participating_site_ids":x["participating_site_ids"],"participating_device_ids":x["participating_device_ids"],
                      "step_count":len(x["steps"]),"default_deadline_ms":x["default_deadline_ms"]} for x in defs],
      "runs":[{"run_id":x["run_id"],"automation_id":x["automation_id"],"automation_revision":x["automation_revision"],"origin_site_id":x["origin_site_id"],
               "state":x["state"],"deadline_at_ms":x["deadline_at_ms"],"step_states":{s["step_id"]:s["state"] for s in x["steps"]}} for x in runs],
      "agent_context":agent_context(),"summary_only":True,"cloud_read_only":True,"remote_action_execution":False,"authority_mutation":False}

def expire_due_runs(now_ms:int|None=None)->dict[str,Any]:
    cutoff=int(now_ms or _now_ms());expired=[]
    with db() as c:
        rows=c.execute("""SELECT run_id FROM tracky_federated_automation_runs
          WHERE deadline_at_ms>0 AND deadline_at_ms<=? AND state NOT IN ('completed','failed','cancelled','expired')""",(cutoff,)).fetchall()
    for row in rows:
        try: expired.append(transition_run(str(row["run_id"]),"expired",reason="deadline_expired",actor={"actor_type":"system","actor_id":"homeserver_deadline"}))
        except FederatedAutomationError: pass
    return {"expired":len(expired),"run_ids":[x["run_id"] for x in expired]}

def report()->dict[str,Any]:
    expire_due_runs();defs=list_definitions(100);runs=list_runs(100)
    with db() as c:
        event_count=int(c.execute("SELECT COUNT(*) FROM tracky_federated_automation_events").fetchone()[0])
    return {"protocol":PROTOCOL,"version":VERSION,"section":SECTION,"definitions":defs,"runs":runs,"event_count":event_count,
      "counts":{"definitions":len(defs),"active_runs":sum(1 for x in runs if x["state"] not in TERMINAL_RUN),"runs":len(runs)},
      "agent_context":agent_context(),"safety":{"execution_enabled":False,"cloud_execution_allowed":False,"agent_execution_allowed":False,"origin_homeserver_authoritative":True}}

def public_capability()->dict[str,Any]:
    return {"protocol":PROTOCOL,"version":VERSION,"section":SECTION,"schema_version":54,
      "automation_states":sorted(AUTOMATION_STATES),"run_states":sorted(RUN_STATES),"step_states":sorted(STEP_STATES),
      "trigger_kinds":sorted(TRIGGER_KINDS),"action_types":sorted(ACTION_TYPES),"durable_action_ledger":True,"immutable_audit_events":True,
      "idempotent_definitions":True,"idempotent_runs":True,"dag_dependencies":True,"deadlines":True,"deadline_expiration":True,"cancellation":True,"recovery_state":True,
      "per_step_authority":True,"per_step_permissions":True,"execution_enabled":False,"cloud_execution_allowed":False,"agent_execution_allowed":False,
      "origin_homeserver_authoritative":True,"federation_v280_invariants_required":True}
