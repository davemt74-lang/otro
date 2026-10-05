from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from ..database import atomic_write, db
from . import federated_data, local_automation, room_device_automation, tracky_federation_sync, tracky_site_topology

VERSION="2.81"
PROTOCOL="physical_federated_automation.v1"
SECTION=3
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

@atomic_write
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

@atomic_write
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

@atomic_write
def transition_run(run_id:str,state:str,*,reason:str="",actor:dict[str,Any]|None=None)->dict[str,Any]:
    run=get_run(run_id);next_state=_text(state,30).lower()
    if next_state not in RUN_STATES: raise FederatedAutomationError("Federated automation run state is invalid.")
    if run["state"]!=next_state:
        if run["state"] in TERMINAL_RUN: raise FederatedAutomationError("Terminal federated automation run is immutable.",409)
        if next_state not in RUN_TRANSITIONS.get(run["state"],set()): raise FederatedAutomationError("Federated automation run transition is invalid.",409)
        if next_state=="running": raise FederatedAutomationError("V2.81 Section 1 records automation ledgers but does not execute physical actions.",409)
    actor_n=_actor(actor);_validate_actor(actor_n);now=_now_ms();run["state"]=next_state;run["updated_at_ms"]=now;run["last_event"]={"state":next_state,"reason":_text(reason,240),"occurred_at_ms":now}
    run["recovery"]["resume_required"]=next_state=="recovering";run["recovery"]["last_checkpoint_ms"]=now
    _write_run(run);_event(run["automation_id"],run["run_id"],"run.state",next_state,actor_n,run["last_event"]);return get_run(run_id)

@atomic_write
def cancel_run(run_id:str,*,reason:str="",actor:dict[str,Any]|None=None)->dict[str,Any]:
    run=get_run(run_id)
    if run["state"] in TERMINAL_RUN:return run
    actor_n=_actor(actor);_validate_actor(actor_n);now=_now_ms();run["state"]="cancelled";run["updated_at_ms"]=now;run["last_event"]={"state":"cancelled","reason":_text(reason,240),"occurred_at_ms":now}
    with db() as c:
        for step in run["steps"]:
            if step["state"] not in TERMINAL_STEP:
                step["state"]="cancelled";step["updated_at_ms"]=now
                c.execute("UPDATE tracky_federated_automation_steps SET state='cancelled',step_json=?,updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND step_id=?",(_json(step),run_id,step["step_id"]))
        c.execute("UPDATE tracky_federated_automation_runs SET state='cancelled',run_json=?,updated_at=CURRENT_TIMESTAMP WHERE run_id=?",(_json(run),run_id))
    _event(run["automation_id"],run_id,"run.cancelled","cancelled",actor_n,{"reason":_text(reason,240)})
    return get_run(run_id)

@atomic_write
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


def _nested_event_value(event:dict[str,Any],path:str)->Any:
    value:Any=event
    for part in str(path or "").split("."):
        if not part:return None
        if not isinstance(value,dict):return None
        value=value.get(part)
    return value

def _trigger_condition_matches(event:dict[str,Any],condition:dict[str,Any])->bool:
    actual=_nested_event_value(event,_text(condition.get("field"),120))
    expected=condition.get("value")
    op=_text(condition.get("op") or "eq",16).lower()
    if op=="eq":return actual==expected
    if op=="neq":return actual!=expected
    if op=="in":return isinstance(expected,list) and actual in expected
    if op=="contains":
        if isinstance(actual,list):return expected in actual
        return str(expected or "") in str(actual or "")
    try:a=float(actual);b=float(expected)
    except (TypeError,ValueError):return False
    return {"gt":a>b,"gte":a>=b,"lt":a<b,"lte":a<=b}.get(op,False)

def _event_occurred_ms(event:dict[str,Any])->int:
    raw=str(event.get("occurred_at") or "").strip()
    try:return int(datetime.fromisoformat(raw.replace("Z","+00:00")).timestamp()*1000)
    except ValueError:return 0

@atomic_write
def process_physical_trigger_events(event_ids:list[str],*,now_ms:int|None=None)->list[dict[str,Any]]:
    now=int(now_ms or _now_ms());local=_local_site()
    reconciliation=federated_data.reconciliation_state("vp3_cloud")
    current=not bool(reconciliation.get("needs_reconciliation"))
    definitions=[x for x in list_definitions(500) if x.get("state")=="active" and (x.get("trigger") or {}).get("kind") in {"world_state","presence","device_state","event"}]
    results:list[dict[str,Any]]=[]
    for raw_event_id in event_ids[:100]:
        event_id=_id(raw_event_id,"trigger_event_id",160)
        with db() as c:
            row=c.execute("SELECT event_json FROM tracky_physical_events WHERE event_id=? LIMIT 1",(event_id,)).fetchone()
        if row is None:continue
        event=_decode(row["event_json"],{})
        event_key=_text(event.get("event_type"),120).lower()
        occurred=_event_occurred_ms(event)
        for definition in definitions:
            trigger=definition.get("trigger") or {}
            automation_id=definition["automation_id"];revision=int(definition["revision"])
            with db() as c:
                prior=c.execute("""SELECT decision,reason,run_id FROM tracky_federated_automation_trigger_receipts
                  WHERE automation_id=? AND automation_revision=? AND event_id=? LIMIT 1""",(automation_id,revision,event_id)).fetchone()
            if prior is not None:
                results.append({"automation_id":automation_id,"automation_revision":revision,"event_id":event_id,"decision":"duplicate","reason":"event_already_processed","run_id":prior["run_id"]})
                continue
            source=_site(trigger.get("source_site_id") or definition["origin_site_id"])
            decision="rejected";reason="event_key_mismatch";run_id=None
            configured=[_text(trigger.get("event_key"),120).lower()]
            config=trigger.get("config") if isinstance(trigger.get("config"),dict) else {}
            configured += [_text(x,120).lower() for x in (config.get("event_keys") or []) if _text(x,120)]
            key_match=not any(configured) or any(x==event_key or (x.endswith(".*") and event_key.startswith(x[:-1])) for x in configured if x)
            min_conf=max(0.0,min(1.0,float(config.get("min_confidence") or 0)))
            max_age=max(1000,min(86_400_000,int(config.get("max_age_ms") or 300_000)))
            age=max(0,now-occurred) if occurred else max_age+1
            conditions=config.get("conditions") if isinstance(config.get("conditions"),list) else []
            if source!=local:reason="wrong_source_site"
            elif not key_match:reason="event_key_mismatch"
            elif float(event.get("confidence") or 0)<min_conf:reason="confidence_below_threshold"
            elif bool(trigger.get("requires_fresh_world_state")) and not current:reason="reconciliation_not_current"
            elif bool(trigger.get("requires_fresh_world_state")) and age>max_age:reason="stale_world_state"
            elif not all(isinstance(x,dict) and _trigger_condition_matches(event,x) for x in conditions[:32]):reason="conditions_not_met"
            else:
                debounce=max(0,int(trigger.get("debounce_ms") or 0))
                with db() as c:
                    last=c.execute("""SELECT occurred_at_ms FROM tracky_federated_automation_trigger_receipts
                      WHERE automation_id=? AND decision='accepted' ORDER BY occurred_at_ms DESC,id DESC LIMIT 1""",(automation_id,)).fetchone()
                if debounce and last is not None and occurred-int(last["occurred_at_ms"] or 0)<debounce:
                    decision="debounced";reason="debounce_window"
                else:
                    idem="ptr-run:"+hashlib.sha256(f"{automation_id}|{revision}|{event_id}".encode("utf-8")).hexdigest()[:48]
                    run=create_run({"automation_id":automation_id,"idempotency_key":idem,"trigger_event_id":event_id},actor={"actor_type":"system","actor_id":"physical_trigger_runtime"})
                    if run["state"]=="running":raise FederatedAutomationError("Physical trigger runtime cannot execute actions.",409)
                    decision="accepted";reason="matched";run_id=run["run_id"]
            with db() as c:
                c.execute("""INSERT INTO tracky_federated_automation_trigger_receipts(
                  receipt_id,automation_id,automation_revision,event_id,event_key,source_site_id,occurred_at_ms,confidence,decision,reason,run_id,detail_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",(
                  "ptr:"+hashlib.sha256(f"{automation_id}|{revision}|{event_id}".encode("utf-8")).hexdigest()[:40],
                  automation_id,revision,event_id,event_key,local,occurred,float(event.get("confidence") or 0),decision,reason,run_id,
                  _json({"reconciliation_current":current,"age_ms":age,"max_age_ms":max_age,"min_confidence":min_conf})
                ))
                c.execute("INSERT INTO tracky_federated_automation_events(automation_id,run_id,event_kind,state,actor_json,detail_json) VALUES (?,?,?,?,?,?)",
                  (automation_id,run_id,"trigger."+decision,None,_json({"actor_type":"system","actor_id":"physical_trigger_runtime"}),_json({"event_id":event_id,"event_key":event_key,"reason":reason})))
            results.append({"automation_id":automation_id,"automation_revision":revision,"event_id":event_id,"decision":decision,"reason":reason,"run_id":run_id})
    return results

def trigger_receipts(limit:int=100)->list[dict[str,Any]]:
    with db() as c:
        rows=c.execute("""SELECT receipt_id,automation_id,automation_revision,event_id,event_key,source_site_id,occurred_at_ms,confidence,decision,reason,run_id,created_at
          FROM tracky_federated_automation_trigger_receipts ORDER BY id DESC LIMIT ?""",(max(1,min(500,int(limit))),)).fetchall()
    return [dict(x) for x in rows]


EXECUTION_PROTOCOL="physical_federated_execution.v1"

def _authority_for_site(site_id:str)->dict[str,Any]:
    site_id=_site(site_id)
    topology=tracky_site_topology.current_topology()
    site=next((x for x in topology.get("sites",[]) if _site(x.get("id"))==site_id),None)
    if not site or not site.get("authority_device_id") or int(site.get("authority_epoch") or 0)<1:
        raise FederatedAutomationError("Federated execution requires an active site authority.",409)
    device=tracky_site_topology.get_device(str(site["authority_device_id"]))
    if device.get("trust_state")!="trusted":
        raise FederatedAutomationError("Federated execution authority device is not trusted.",409)
    return {"site_id":site_id,"device_id":str(site["authority_device_id"]),"authority_epoch":int(site["authority_epoch"]),"device":device}

def _definition_step(definition:dict[str,Any],step_id:str)->dict[str,Any]:
    step=next((x for x in definition.get("steps",[]) if x.get("step_id")==step_id),None)
    if step is None: raise FederatedAutomationError("Federated automation step definition was not found.",404)
    return step

def _run_step(run:dict[str,Any],step_id:str)->dict[str,Any]:
    step=next((x for x in run.get("steps",[]) if x.get("step_id")==step_id),None)
    if step is None: raise FederatedAutomationError("Federated automation run step was not found.",404)
    return step

@atomic_write
def create_step_dispatch(run_id:str,step_id:str,*,approval_id:str="",actor:dict[str,Any]|None=None)->dict[str,Any]:
    run=get_run(run_id); definition=get_definition(run["automation_id"]); step_id=_id(step_id,"step_id",80)
    spec=_definition_step(definition,step_id); state=_run_step(run,step_id)
    if run["state"] in TERMINAL_RUN: raise FederatedAutomationError("Terminal federated automation run cannot dispatch.",409)
    if state["state"]!="ready": raise FederatedAutomationError("Federated automation step is not ready.",409)
    authority=_authority_for_site(spec["authority_site_id"])
    if state.get("dispatch_id"):
        with db() as c:
            prior=c.execute("SELECT dispatch_json FROM tracky_federated_automation_dispatches WHERE dispatch_id=?",(state["dispatch_id"],)).fetchone()
        if prior is not None:
            dispatch=_decode(prior["dispatch_json"],{})
            if int(dispatch.get("authority_epoch") or 0)!=authority["authority_epoch"]:
                raise FederatedAutomationError("Existing dispatch authority changed; a new attempt requires owner review.",409)
            actor_n=_actor(actor);_validate_actor(actor_n)
            return dispatch
    attempt=int(state.get("attempt") or 0)+1
    dispatch_id="fad-"+hashlib.sha256(f'{run_id}|{step_id}|{attempt}|{authority["site_id"]}|{authority["authority_epoch"]}'.encode("utf-8")).hexdigest()[:40]
    idem="exec:"+hashlib.sha256(f'{run_id}|{step_id}|{attempt}'.encode("utf-8")).hexdigest()[:48]
    dispatch={"protocol":EXECUTION_PROTOCOL,"section":3,"dispatch_id":dispatch_id,"idempotency_key":idem,"run_id":run_id,
      "automation_id":definition["automation_id"],"automation_revision":definition["revision"],"step_id":step_id,"attempt":attempt,
      "origin_site_id":definition["origin_site_id"],"authority_site_id":spec["authority_site_id"],"target_site_id":spec["target_site_id"],
      "authority_epoch":authority["authority_epoch"],"device_id":spec.get("device_id"),"action_type":spec["action_type"],"action_key":spec["action_key"],
      "arguments":spec.get("arguments") or {},"required_permissions":spec.get("required_permissions") or [],"approval_mode":spec.get("approval_mode") or "governed",
      "approval_id":_text(approval_id,160) or None,"issued_at_ms":_now_ms(),"deadline_at_ms":int(state.get("deadline_at_ms") or 0),
      "safety":{"cloud_execution_allowed":False,"agent_execution_allowed":False,"authoritative_homeserver_only":True}}
    actor_n=_actor(actor);_validate_actor(actor_n)
    with db() as c:
        prior=c.execute("SELECT dispatch_json FROM tracky_federated_automation_dispatches WHERE dispatch_id=?",(dispatch_id,)).fetchone()
        if prior is not None:return _decode(prior["dispatch_json"],{})
        c.execute("""INSERT INTO tracky_federated_automation_dispatches(
          dispatch_id,idempotency_key,run_id,step_id,authority_site_id,authority_epoch,state,dispatch_json
        ) VALUES (?,?,?,?,?,?,?,?)""",(dispatch_id,idem,run_id,step_id,spec["authority_site_id"],authority["authority_epoch"],"created",_json(dispatch)))
        state["dispatch_id"]=dispatch_id;state["attempt"]=attempt;state["updated_at_ms"]=_now_ms()
        c.execute("UPDATE tracky_federated_automation_steps SET attempt=?,dispatch_id=?,step_json=?,updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND step_id=?",
          (attempt,dispatch_id,_json(state),run_id,step_id))
    _event(run["automation_id"],run_id,"step.dispatched","ready",actor_n,{"step_id":step_id,"dispatch_id":dispatch_id,"authority_site_id":spec["authority_site_id"],"authority_epoch":authority["authority_epoch"]})
    return dispatch

@atomic_write
def _claim_execution_dispatch(dispatch:dict[str,Any])->dict[str,Any]|None:
    dispatch_id=str(dispatch["dispatch_id"])
    if dispatch.get("origin_site_id")==_local_site() and get_run(str(dispatch["run_id"]))["state"] in TERMINAL_RUN:
        raise FederatedAutomationError("Terminal federated automation cannot execute.",409)
    with db() as c:
        stored=c.execute("SELECT state,dispatch_json FROM tracky_federated_automation_dispatches WHERE dispatch_id=?",(dispatch_id,)).fetchone()
        if stored is not None and _hash(_decode(stored["dispatch_json"],{}))!=_hash(dispatch):
            raise FederatedAutomationError("Execution dispatch identity conflicts with its durable ledger.",409)
        prior=c.execute("SELECT receipt_json FROM tracky_federated_automation_execution_receipts WHERE dispatch_id=?",(dispatch_id,)).fetchone()
        if prior is not None:return _decode(prior["receipt_json"],{})
        if stored is None:
            c.execute("""INSERT INTO tracky_federated_automation_dispatches(dispatch_id,idempotency_key,run_id,step_id,authority_site_id,authority_epoch,state,dispatch_json)
              VALUES (?,?,?,?,?,?,'created',?)""",(dispatch_id,str(dispatch.get("idempotency_key") or ""),dispatch["run_id"],dispatch["step_id"],dispatch["authority_site_id"],int(dispatch["authority_epoch"]),_json(dispatch)))
        changed=c.execute("UPDATE tracky_federated_automation_dispatches SET state='running',updated_at=CURRENT_TIMESTAMP WHERE dispatch_id=? AND state='created'",(dispatch_id,))
        if changed.rowcount!=1:
            raise FederatedAutomationError("Execution is already claimed; an uncertain interrupted action requires owner review before a new attempt.",409)
    return None

@atomic_write
def _record_execution_receipt(dispatch:dict[str,Any],receipt:dict[str,Any],actor:dict[str,Any])->None:
    with db() as c:
        c.execute("""INSERT INTO tracky_federated_automation_execution_receipts(
          receipt_id,dispatch_id,idempotency_key,run_id,step_id,authority_site_id,authority_epoch,status,receipt_json
        ) VALUES (?,?,?,?,?,?,?,?,?)""",(receipt["receipt_id"],receipt["dispatch_id"],str(receipt["idempotency_key"] or ""),receipt["run_id"],receipt["step_id"],receipt["authority_site_id"],receipt["authority_epoch"],receipt["status"],_json(receipt)))
        c.execute("UPDATE tracky_federated_automation_dispatches SET state=?,updated_at=CURRENT_TIMESTAMP WHERE dispatch_id=? AND state='running'",(receipt["status"],receipt["dispatch_id"]))
    _event(str(dispatch["automation_id"]),str(dispatch["run_id"]),"step.executed",receipt["status"],actor,{"step_id":dispatch["step_id"],"dispatch_id":receipt["dispatch_id"],"authority_site_id":receipt["authority_site_id"]})

def execute_step_dispatch(dispatch:dict[str,Any],*,executor_device_id:str,permission_grants:list[str],actor:dict[str,Any]|None=None)->dict[str,Any]:
    if not isinstance(dispatch,dict) or dispatch.get("protocol")!=EXECUTION_PROTOCOL: raise FederatedAutomationError("Federated execution protocol is invalid.")
    dispatch_id=_id(dispatch.get("dispatch_id"),"dispatch_id",160);local=_local_site();authority_site=_site(dispatch.get("authority_site_id"))
    if local!=authority_site: raise FederatedAutomationError("Only the authoritative target HomeServer may execute this dispatch.",409)
    authority=_authority_for_site(authority_site)
    if _id(executor_device_id,"executor_device_id",100)!=authority["device_id"]: raise FederatedAutomationError("Executor device is not the current site authority.",409)
    if int(dispatch.get("authority_epoch") or 0)!=authority["authority_epoch"]: raise FederatedAutomationError("Federated execution authority epoch changed.",409)
    if bool(federated_data.reconciliation_state("vp3_cloud").get("needs_reconciliation")): raise FederatedAutomationError("Federated execution is blocked while reconciliation is required.",409)
    now=_now_ms()
    if int(dispatch.get("deadline_at_ms") or 0)>0 and now>int(dispatch["deadline_at_ms"]): raise FederatedAutomationError("Federated execution dispatch expired.",409)
    grants={_text(x,80) for x in permission_grants if _text(x,80)}
    missing=[x for x in dispatch.get("required_permissions",[]) if x not in grants]
    if missing: raise FederatedAutomationError("Federated execution permission denied.",403)
    if dispatch.get("action_type")=="physical_action" and dispatch.get("approval_mode")!="inherit" and not dispatch.get("approval_id"):
        raise FederatedAutomationError("Physical federated action requires approved execution evidence.",403)
    actor_n=_actor(actor);_validate_actor(actor_n)
    prior=_claim_execution_dispatch(dispatch)
    if prior is not None:return prior
    status="completed";result:dict[str,Any]={};error=""
    try:
        action_type=str(dispatch.get("action_type") or "")
        if action_type=="physical_action":
            args=dispatch.get("arguments") if isinstance(dispatch.get("arguments"),dict) else {}
            device_key=_text(args.get("device_key"),80)
            if not device_key: raise FederatedAutomationError("Physical federated action requires arguments.device_key.",409)
            executed=room_device_automation.execute_command(device_key,str(dispatch.get("action_key") or ""),args.get("command_arguments") if isinstance(args.get("command_arguments"),dict) else {},
              source_app_key="tracky-federated-automation",action_request_id=str(dispatch.get("approval_id") or ""))
            result={"executed":True,"action_id":executed.get("action_id"),"device_key":executed.get("device_key"),"state":executed.get("state")}
        elif action_type=="local_routine":
            executed=local_automation.run_routine(str(dispatch.get("action_key") or ""),source_kind=f"federated-automation:{dispatch['run_id']}",snapshot={"dispatch_id":dispatch_id,"run_id":dispatch["run_id"],"step_id":dispatch["step_id"]})
            result={"executed":True,"execution_id":executed.get("execution_id"),"status":executed.get("status")}
        else:
            raise FederatedAutomationError("This federated action type has no authoritative local executor in Section 3.",409)
    except Exception as exc:
        status="failed";error=f"{type(exc).__name__}: {str(exc)}"[:500]
    receipt={"protocol":EXECUTION_PROTOCOL,"section":3,"receipt_id":"fer-"+hashlib.sha256(f'{dispatch_id}|{status}|{error}'.encode("utf-8")).hexdigest()[:40],
      "dispatch_id":dispatch_id,"idempotency_key":dispatch.get("idempotency_key"),"run_id":dispatch["run_id"],"step_id":dispatch["step_id"],
      "attempt":int(dispatch.get("attempt") or 1),"authority_site_id":authority_site,"authority_epoch":authority["authority_epoch"],
      "executor_device_id":authority["device_id"],"status":status,"result":result,"error":error or None,"completed_at_ms":_now_ms()}
    _record_execution_receipt(dispatch,receipt,actor_n)
    return receipt

@atomic_write
def apply_execution_receipt(receipt:dict[str,Any],*,actor:dict[str,Any]|None=None)->dict[str,Any]:
    if not isinstance(receipt,dict) or receipt.get("protocol")!=EXECUTION_PROTOCOL: raise FederatedAutomationError("Federated execution receipt protocol is invalid.")
    run=get_run(str(receipt.get("run_id") or ""));definition=get_definition(run["automation_id"]);step_id=_id(receipt.get("step_id"),"step_id",80)
    state=_run_step(run,step_id);spec=_definition_step(definition,step_id)
    if _site(receipt.get("authority_site_id"))!=spec["authority_site_id"]: raise FederatedAutomationError("Execution receipt authority site mismatch.",409)
    authority=_authority_for_site(spec["authority_site_id"])
    if int(receipt.get("authority_epoch") or 0)!=authority["authority_epoch"]: raise FederatedAutomationError("Execution receipt authority epoch is stale.",409)
    if state.get("dispatch_id") and state["dispatch_id"]!=receipt.get("dispatch_id"): raise FederatedAutomationError("Execution receipt dispatch mismatch.",409)
    status=_text(receipt.get("status"),30).lower()
    if status not in {"completed","failed"}: raise FederatedAutomationError("Execution receipt state is invalid.")
    actor_n=_actor(actor);_validate_actor(actor_n)
    fingerprint=_hash(receipt)
    if state.get("execution_receipt_fingerprint")==fingerprint:
        return run
    if run["state"] in TERMINAL_RUN or state["state"] in TERMINAL_STEP:
        raise FederatedAutomationError("Terminal federated automation cannot accept a changed receipt.",409)
    if not state.get("dispatch_id") or state["dispatch_id"]!=receipt.get("dispatch_id") or int(receipt.get("attempt") or 0)!=int(state.get("attempt") or 0):
        raise FederatedAutomationError("Execution receipt has no matching dispatch attempt.",409)
    state["execution_receipt_fingerprint"]=fingerprint
    now=int(receipt.get("completed_at_ms") or _now_ms());state["state"]=status;state["last_error"]=_text(receipt.get("error"),500) if status=="failed" else None;state["updated_at_ms"]=now
    for candidate in run["steps"]:
        if candidate["state"]=="blocked" and all(next((x for x in run["steps"] if x["step_id"]==dep),{}).get("state")=="completed" for dep in candidate["depends_on"]):
            candidate["state"]="ready";candidate["updated_at_ms"]=now
    if all(x["state"]=="completed" for x in run["steps"]):run["state"]="completed"
    elif any(x["state"]=="failed" for x in run["steps"]):run["state"]="failed"
    elif any(x["state"]=="ready" for x in run["steps"]):run["state"]="running"
    else:run["state"]="waiting"
    run["updated_at_ms"]=now;run["safety"]["execution_enabled"]=True;run["safety"]["distributed_execution_only"]=True
    actor_n=_actor(actor);_validate_actor(actor_n)
    with db() as c:
        for step in run["steps"]:
            c.execute("UPDATE tracky_federated_automation_steps SET state=?,attempt=?,dispatch_id=?,step_json=?,updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND step_id=?",
              (step["state"],int(step.get("attempt") or 0),step.get("dispatch_id"),_json(step),run["run_id"],step["step_id"]))
        c.execute("UPDATE tracky_federated_automation_runs SET state=?,run_json=?,updated_at=CURRENT_TIMESTAMP WHERE run_id=?",(run["state"],_json(run),run["run_id"]))
    _event(run["automation_id"],run["run_id"],"execution.receipt",run["state"],actor_n,{"step_id":step_id,"dispatch_id":receipt.get("dispatch_id"),"status":status})
    return get_run(run["run_id"])

def execution_receipts(limit:int=100)->list[dict[str,Any]]:
    with db() as c:rows=c.execute("SELECT receipt_json FROM tracky_federated_automation_execution_receipts ORDER BY id DESC LIMIT ?",(max(1,min(500,int(limit))),)).fetchall()
    return [_decode(x["receipt_json"],{}) for x in rows]


def cloud_projection()->dict[str,Any]:
    expire_due_runs();defs=list_definitions(100);runs=list_runs(100)
    return {"protocol":PROTOCOL,"version":VERSION,"schema_version":1,"generated_at":_now_ms(),"local_site_id":_local_site(),
      "definitions":[{"automation_id":x["automation_id"],"revision":x["revision"],"name":x["name"],"state":x["state"],"origin_site_id":x["origin_site_id"],
                      "trigger_kind":x["trigger"]["kind"],"participating_site_ids":x["participating_site_ids"],"participating_device_ids":x["participating_device_ids"],
                      "step_count":len(x["steps"]),"default_deadline_ms":x["default_deadline_ms"]} for x in defs],
      "runs":[{"run_id":x["run_id"],"automation_id":x["automation_id"],"automation_revision":x["automation_revision"],"origin_site_id":x["origin_site_id"],
               "state":x["state"],"deadline_at_ms":x["deadline_at_ms"],"step_states":{s["step_id"]:s["state"] for s in x["steps"]}} for x in runs],
      "trigger_receipts":trigger_receipts(100),"execution_receipts":[{"receipt_id":x.get("receipt_id"),"dispatch_id":x.get("dispatch_id"),"run_id":x.get("run_id"),"step_id":x.get("step_id"),"authority_site_id":x.get("authority_site_id"),"authority_epoch":x.get("authority_epoch"),"status":x.get("status"),"completed_at_ms":x.get("completed_at_ms")} for x in execution_receipts(100)],"agent_context":agent_context(),"summary_only":True,"cloud_read_only":True,"remote_action_execution":False,"authority_mutation":False}

@atomic_write
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
    return {"protocol":PROTOCOL,"version":VERSION,"section":SECTION,"schema_version":56,
      "automation_states":sorted(AUTOMATION_STATES),"run_states":sorted(RUN_STATES),"step_states":sorted(STEP_STATES),
      "trigger_kinds":sorted(TRIGGER_KINDS),"action_types":sorted(ACTION_TYPES),"durable_action_ledger":True,"immutable_audit_events":True,
      "idempotent_definitions":True,"idempotent_runs":True,"dag_dependencies":True,"deadlines":True,"deadline_expiration":True,"cancellation":True,"recovery_state":True,
      "per_step_authority":True,"per_step_permissions":True,"execution_enabled":True,"distributed_action_execution":True,"authority_epoch_bound_dispatch":True,"immutable_execution_receipts":True,"cloud_execution_allowed":False,"agent_execution_allowed":False,
      "origin_homeserver_authoritative":True,"federation_v280_invariants_required":True,"physical_world_trigger_runtime":True,"trigger_receipts_immutable":True,"trigger_execution_enabled":False,"cloud_execution_allowed":False}
