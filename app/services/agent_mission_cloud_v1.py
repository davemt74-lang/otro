"""Cloud-facing projection for HomeServer-owned read-only missions.

Only paired VP3 may use these operations; no owner token is minted or echoed.
The HomeServer mission database remains authoritative.
"""
from __future__ import annotations

import uuid
from typing import Any

from ..database import db
from . import agent_mission_tool_contracts as tool_contracts
from . import agent_mission_runtime as runtime, agent_mission_control as control
from . import agent_routing, context_engine, app_scopes, agent_mission_cognition as cognition, agent_mission_execution as execution, agent_mission_browser as browser, agent_mission_live_browser as live, agent_mission_browser_actions as actions, agent_mission_browser_takeover as takeover, agent_mission_browser_plans as plans

CONTRACT = "vp3.agent-missions.cloud.v1"


def _bounded_string(value: Any, label: str, length: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > length:
        raise runtime.MissionError(f"{label} must contain 1 to {length} characters.")
    return text


def cloud_conversation(thread: Any, request_id: str) -> str:
    """Map a Cloud thread to the existing paired-app conversation namespace.

    Thread=0 represents a not-yet-created Cloud chat. Its mission request ID
    supplies a stable identity for retries without merging unrelated missions.
    """
    if isinstance(thread, bool):
        raise runtime.MissionError("Cloud thread ID is invalid.")
    try:
        thread_id = int(thread)
    except (ValueError, TypeError) as exc:
        raise runtime.MissionError("Cloud thread ID is invalid.") from exc
    if not 0 <= thread_id <= 2147483647:
        raise runtime.MissionError("Cloud thread ID is out of range.")
    key = _bounded_string(request_id, "Mission request ID", 128)
    namespace = f"vp3-cloud-thread:{thread_id}" if thread_id else f"vp3-cloud-request:{key}"
    return uuid.uuid5(uuid.NAMESPACE_URL, "app:vp3:" + namespace).hex


def _ensure_conversation(thread: Any, request_id: str, objective: str) -> str:
    cid = cloud_conversation(thread, request_id)
    parent = agent_routing.resolve_agent("app:vp3", owner=False)
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT OR IGNORE INTO conversations(id,agent_id,source_app_key,title) VALUES(?,?,?,?)",
            (cid, int(parent["id"]), "app:vp3", "VP3 Cloud: " + objective[:65]),
        )
        row = connection.execute(
            "SELECT agent_id,source_app_key,status FROM conversations WHERE id=?",
            (cid,),
        ).fetchone()
        if (not row or row["source_app_key"] != "app:vp3"
            or int(row["agent_id"] or 0) != int(parent["id"])
            or row["status"] != "active"):
            raise runtime.MissionError("Paired Cloud conversation is not available.", 409)
    return cid


def _cloud_export_allowed(raw: dict) -> bool:
    # A paired Cloud agent.chat grant does not override conversation privacy
    # or current app Cloud-export restrictions.
    try:
        settings = context_engine.get_settings(str(raw.get("conversation_id") or ""))
        scope = app_scopes.get_scope_for_source("app:vp3")
        return bool(settings.get("cloud_allowed", False) and scope.get("cloud_allowed", False))
    except Exception:
        return False


def _projection(raw: dict, *, detailed: bool = False) -> dict:
    allowed = _cloud_export_allowed(raw)
    tasks = []
    for task in raw.get("tasks", [])[:12]:
        item = {
            "id": str(task.get("id") or ""),
            "title": str(task.get("title") or "")[:160] if allowed else "Private HomeServer worker",
            "role": str(task.get("role") or "")[:80] if allowed else "private",
            "status": str(task.get("status") or "queued"),
            "attempt": int(task.get("attempt") or 0),
            "started_at": task.get("started_at"),
            "completed_at": task.get("completed_at"),
            "read_calls_used": task.get("read_calls_used", 0),
        }
        if detailed:
            # A validated structured draft must remain complete JSON. The
            # coordinated runtime bounds each output before it is stored.
            result_limit = 30000 if raw.get('tools_enabled') else 6000
            item["result"] = str(task.get("result") or "")[:result_limit] if allowed else ""
            item["error"] = str(task.get("error") or "")[:500] if allowed else ""
            item["model"] = str(task.get("model") or "")[:160] if allowed else ""
        tasks.append(item)
    result = {
        "id": str(raw.get("id") or ""),
        "objective": str(raw.get("objective") or "")[:2000] if allowed else "Private HomeServer mission",
        "status": str(raw.get("status") or ""),
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
        "completed_at": raw.get("completed_at"),
        "tasks": tasks,
        "read_only": True,
        "verified": False,
        "private": not allowed,
        "tools_enabled": bool(raw.get('tools_enabled')),
        "tools_configured": bool(raw.get('tools_configured')),
        "authority_current": bool(raw.get('authority_current', True)),
        "action_summaries": raw.get('action_summaries',[]) if allowed else [],
        "completion_report": raw.get('completion_report') if allowed else None,
        "chat_task": raw.get('chat_task') if allowed and raw.get('authority_current', True) else None,
        "contract": CONTRACT,
    }
    if detailed:
        result["result"] = str(raw.get("result") or "")[:8000] if allowed else ""
        result["events"] = [
            {"id": int(e.get("id") or 0), "task_id": e.get("task_id"),
             "kind": str(e.get("kind") or "")[:120],
             "created_at": e.get("created_at")}
            for e in raw.get("events", [])[-40:] if allowed
        ]
    return result


def _supervisor_projection(raw: dict, mission_snapshot: dict) -> dict:
    allowed = _cloud_export_allowed(mission_snapshot)
    return {
        "id": str(raw.get("id") or ""),
        "status": str(raw.get("status") or ""),
        "decision": str(raw.get("decision") or "") if allowed else "private",
        "reason": str(raw.get("reason") or "")[:1800] if allowed else "",
        "confidence": int(raw.get("confidence") or 0) if allowed else 0,
        "tasks": raw.get("tasks", [])[:4] if allowed else [],
        "created_at": raw.get("created_at"),
        "decided_at": raw.get("decided_at"),
        "private": not allowed,
        "requires_approval": raw.get("status") == "proposed",
    }


def execute(action: str, body: dict) -> dict:
    if action.startswith('schedule.'):
        from . import agent_mission_schedules as schedules
        source = 'app:vp3'
        if action == 'schedule.list': result = schedules.list_schedules(source)
        elif action == 'schedule.create':
            result = schedules.create(source, body.get('mission_id'), body.get('timing'), request_id=body.get('request_id'), expected_revision=body.get('expected_revision'), confirmed=body.get('confirmed'))
        elif action in ('schedule.pause','schedule.resume','schedule.cancel'):
            result = schedules.change(source, body.get('schedule_id'), action.split('.')[1], request_id=body.get('request_id'), expected_revision=body.get('expected_revision'), confirmed=body.get('confirmed'))
        else: raise runtime.MissionError('Unsupported schedule operation.',422)
        return {'ok':True,'contract':CONTRACT,'schedules':result if isinstance(result,list) else [result], 'scheduler_health':schedules.health()}
    if action == "list":
        count = body.get("limit", 6)
        if isinstance(count, bool) or not str(count).isdigit() or not 1 <= int(count) <= 8:
            raise runtime.MissionError("Mission list limit must be between 1 and 8.")
        return {"ok": True, "contract": CONTRACT, "items": [
            _projection(row) for row in runtime.list_missions("app:vp3", int(count))
        ]}
    if action in {"create", "task.prepare"}:
        objective = _bounded_string(body.get("objective"), "Mission objective", 4000)
        request_id = _bounded_string(body.get("request_id"), "Mission request ID", 128)
        # Important: claim model/mission authority through the source-scoped
        # runtime instead of owner routes or arbitrary Cloud conversation IDs.
        cid = _ensure_conversation(body.get("thread_id", 0), request_id, objective)
        if action == 'task.prepare':
            from . import agent_mission_chat_tasks as chat_tasks
            created = chat_tasks.prepare('app:vp3', conversation_id=cid, objective=objective, request_id=request_id)
        else:
            created = runtime.create_mission(
                "app:vp3", conversation_id=cid, objective=objective,
                client_request_id=request_id, owner=False,
            )
        return {"ok": True, "contract": CONTRACT, "mission": _projection(created, detailed=True)}
    mid = _bounded_string(body.get("mission_id"), "Mission ID", 80)
    # Source-scoped lookup prevents a paired Cloud call from reading owner
    # missions or a different app's mission ID.
    runtime.get_mission("app:vp3", mid)
    if action in {'actions.list','actions.review'}:
        from . import agent_mission_actions as changes
        current=runtime.get_mission('app:vp3',mid)
        if not _cloud_export_allowed(current):
            raise runtime.MissionError('Private specialist changes stay on HomeServer.',403)
        if action=='actions.list': items=changes.list_actions('app:vp3',mid)
        else: items=changes.review('app:vp3',mid,body.get('action_id'),expected_hash=body.get('expected_hash'),decision=body.get('decision'),request_id=body.get('request_id'),confirmed=body.get('confirmed'))
        return {'ok':True,'contract':CONTRACT,'actions':items}
    if action in {'tools.get','tools.configure','tools.start','tools.status'}:
        from . import agent_mission_orchestration as orchestration
        current=runtime.get_mission('app:vp3',mid)
        if not _cloud_export_allowed(current):
            raise runtime.MissionError('Private capability assignments stay on HomeServer.',403)
        if action=='tools.get': result=tool_contracts.get('app:vp3',mid)
        elif action=='tools.configure': result=tool_contracts.configure('app:vp3',mid,body.get('assignments'),request_id=body.get('request_id'),expected_revision=body.get('expected_revision'),confirmed=body.get('confirmed'))
        elif action=='tools.start':
            result=orchestration.start('app:vp3',mid,request_id=body.get('request_id'),expected_revision=body.get('expected_revision'),confirmed=body.get('confirmed'))
            return {'ok':True,'contract':CONTRACT,'mission':_projection(result,detailed=True)}
        else: return {'ok':True,'contract':CONTRACT,'orchestration':orchestration.status('app:vp3',mid)}
        return {'ok':True,'contract':CONTRACT,'tools':result}
    if action == "get":
        return {"ok": True, "contract": CONTRACT, "mission": _projection(runtime.get_mission("app:vp3", mid), detailed=True)}
    if action == "start":
        result = runtime.start_mission("app:vp3", mid)
        return {"ok": True, "contract": CONTRACT, "mission": _projection(result, detailed=True)}
    if action == "cancel":
        result = runtime.cancel_mission("app:vp3", mid)
        return {"ok": True, "contract": CONTRACT, "mission": _projection(result, detailed=True)}
    if action == "pause":
        result = control.pause("app:vp3", mid)
        return {"ok": True, "contract": CONTRACT, "mission": _projection(result, detailed=True)}
    if action == "resume":
        if body.get("allow_reexecution") is not True:
            raise runtime.MissionError("Explicit reexecution approval required.", 409)
        result = control.resume("app:vp3", mid, allow_reexecution=True)
        return {"ok": True, "contract": CONTRACT, "mission": _projection(result, detailed=True)}
    if action == "retry":
        tid = _bounded_string(body.get("task_id"), "Task ID", 80)
        result = control.retry("app:vp3", mid, tid)
        return {"ok": True, "contract": CONTRACT, "mission": _projection(result, detailed=True)}
    if action in {"evaluate", "decisions", "approve", "reject"}:
        current = runtime.get_mission("app:vp3", mid)
        if action == "evaluate":
            request_id = _bounded_string(body.get("request_id"), "Staffing request ID", 128)
            decision = cognition.propose("app:vp3", mid, request_id)
            return {"ok": True, "contract": CONTRACT,
                    "supervision": _supervisor_projection(decision, runtime.get_mission("app:vp3", mid))}
        if action == "decisions":
            recent = cognition.list_decisions("app:vp3", mid)
            return {"ok": True, "contract": CONTRACT,
                    "items": [_supervisor_projection(d, current) for d in recent["items"]]}
        decision_id = _bounded_string(body.get("decision_id"), "Decision ID", 80)
        decision = cognition.decide("app:vp3", mid, decision_id, approve=(action == "approve"))
        return {"ok": True, "contract": CONTRACT,
                "supervision": _supervisor_projection(decision, runtime.get_mission("app:vp3", mid))}
    if action in {"execution", "bind_provider"}:
        snapshot = runtime.get_mission("app:vp3", mid)
        if not _cloud_export_allowed(snapshot):
            raise runtime.MissionError("Configure private mission workers on HomeServer.", 403)
        if action == "execution":
            return {"ok": True, "contract": CONTRACT,
                    "execution": execution.list_profiles("app:vp3", mid)}
        tid = _bounded_string(body.get("task_id"), "Task ID", 80)
        key = _bounded_string(body.get("provider_key"), "Provider key", 20)
        updated = execution.configure("app:vp3", mid, tid, key)
        return {"ok": True, "contract": CONTRACT, "worker_execution": updated}
    if action in {"browser.grant", "browser.capture", "browser.get", "browser.revoke"}:
        snapshot = runtime.get_mission("app:vp3", mid)
        if not _cloud_export_allowed(snapshot):
            raise runtime.MissionError("Private browser workspaces must stay on HomeServer.", 403)
        tid = _bounded_string(body.get("task_id"), "Browser worker ID", 80)
        if action == "browser.grant":
            url = _bounded_string(body.get("url"), "Approved browser URL", 1400)
            state = browser.authorize("app:vp3", mid, tid, url)
        elif action == "browser.capture":
            target = body.get("url")
            state = browser.capture("app:vp3", mid, tid, str(target) if target else None)
        elif action == "browser.revoke":
            state = browser.revoke("app:vp3", mid, tid)
        else:
            state = browser.inspect("app:vp3", mid, tid, image=True)
        return {"ok": True, "contract": CONTRACT, "browser": state}
    if action in {"browser.live.start", "browser.live.get", "browser.live.refresh",
                  "browser.live.propose", "browser.live.approve", "browser.live.stop", "browser.live.plan"}:
        current = runtime.get_mission("app:vp3", mid)
        if not _cloud_export_allowed(current):
            raise runtime.MissionError("Private live browsers must stay on HomeServer.", 403)
        tid = _bounded_string(body.get("task_id"), "Live browser worker", 80)
        source = "app:vp3"
        if action == "browser.live.plan":
            plans.run(source,mid,tid,request_id=_bounded_string(body.get("request_id"),"Plan operation ID",36),confirmed=body.get("confirmed"))
            result=live.get(source,mid,tid)
        elif action == "browser.live.start":
            result = live.start(source, mid, tid)
        elif action == "browser.live.get":
            result = live.get(source, mid, tid)
        elif action == "browser.live.refresh":
            result = live.refresh(source, mid, tid)
        elif action == "browser.live.propose":
            result = live.propose(source, mid, tid)
        elif action == "browser.live.approve":
            proposal_id = _bounded_string(body.get("proposal_id"), "Approved navigation proposal", 80)
            result = live.approve_navigation(source, mid, tid, proposal_id)
        else:
            result = live.stop(source, mid, tid)
        return {"ok": True, "contract": CONTRACT, "live_browser": result}
    if action in {"browser.action.propose", "browser.action.approve"}:
        current = runtime.get_mission("app:vp3", mid)
        if not _cloud_export_allowed(current):
            raise runtime.MissionError("Private interactive browsers remain on HomeServer.", 403)
        tid = _bounded_string(body.get("task_id"), "Browser task ID", 80)
        if action == "browser.action.propose":
            result = actions.suggest("app:vp3", mid, tid)
        else:
            pid = _bounded_string(body.get("proposal_id"), "Browser action proposal ID", 80)
            if body.get("confirmed") is not True:
                raise runtime.MissionError("Browser action needs explicit confirmation.", 422)
            value = body.get("value")
            if type(value) not in (bool, int, str):
                raise runtime.MissionError("Browser input type is invalid.", 422)
            result = actions.approve("app:vp3", mid, tid, pid, value=value)
        return {"ok": True, "contract": CONTRACT, "live_browser": result}
    if action in {"browser.owner.takeover","browser.owner.release","browser.owner.control",
                  "browser.owner.search.review","browser.owner.search.submit"}:
        current = runtime.get_mission("app:vp3", mid)
        if not _cloud_export_allowed(current):
            raise runtime.MissionError("Private takeover controls stay on HomeServer.",403)
        source = "app:vp3"
        tid = _bounded_string(body.get("task_id"),"Browser task ID",80)
        if action == "browser.owner.takeover":
            result = takeover.acquire(source,mid,tid)
        elif action == "browser.owner.release":
            result = takeover.release(source,mid,tid,lease_id=_bounded_string(body.get("lease_id"),"Owner lease ID",36))
        elif action == "browser.owner.control":
            if body.get("confirmed") is not True:
                raise runtime.MissionError("Owner confirmation required.",422)
            index=body.get("index")
            if type(index) is not int or index<0 or index>119:
                raise runtime.MissionError("Control index is invalid.",422)
            fingerprint=_bounded_string(body.get("fingerprint"),"Control fingerprint",24)
            kind=_bounded_string(body.get("kind"),"Safe control kind",12)
            value=body.get("value")
            if type(value) not in (bool,int,str):
                raise runtime.MissionError("Control input type is invalid.",422)
            result=takeover.manual(source,mid,tid,index=index,fingerprint=fingerprint,kind=kind,value=value,request_id=_bounded_string(body.get("request_id"),"Operation ID",36),lease_id=_bounded_string(body.get("lease_id"),"Owner lease ID",36))
        elif action == "browser.owner.search.review":
            index=body.get("index")
            if type(index) is not int or index<0 or index>19:
                raise runtime.MissionError("Search form index is invalid.",422)
            fingerprint=_bounded_string(body.get("fingerprint"),"Search fingerprint",24)
            result=takeover.review_search(source,mid,tid,index=index,fingerprint=fingerprint,lease_id=_bounded_string(body.get("lease_id"),"Owner lease ID",36))
        else:
            if body.get("confirmed") is not True:
                raise runtime.MissionError("Explicit GET search submission approval required.",422)
            proposal_id=_bounded_string(body.get("proposal_id"),"Search approval ID",80)
            result=takeover.submit_search(source,mid,tid,proposal_id=proposal_id,lease_id=_bounded_string(body.get("lease_id"),"Owner lease ID",36))
        return {"ok":True,"contract":CONTRACT,"live_browser":result}
    if action == "events":
        snapshot = runtime.get_mission("app:vp3", mid)
        if not _cloud_export_allowed(snapshot):
            return {"ok": True, "contract": CONTRACT, "items": [], "has_more": False, "private": True, "next_cursor": 0}
        after, limit = body.get("after", 0), body.get("limit", 40)
        if isinstance(after, bool) or isinstance(limit, bool):
            raise runtime.MissionError("Event cursor is invalid.")
        try:
            after, limit = int(after), int(limit)
        except (TypeError, ValueError) as exc:
            raise runtime.MissionError("Event cursor is invalid.") from exc
        return {"ok": True, "contract": CONTRACT, **control.events("app:vp3", mid, after=after, limit=min(60, limit))}
    raise runtime.MissionError("Mission operation is not allowed.", 404)
