from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ..database import db
from . import homeserver_app_approvals

CONTRACT="vp3.homeserver.activity-center.v1"
_LEVEL_RANK={"info":0,"success":1,"warning":2,"error":3,"action_required":4}
_ALLOWED_LEVELS=set(_LEVEL_RANK)
_ALLOWED_PRIORITIES={"low","normal","high","urgent"}
_SECRET_KEYS=("password","secret","token","authorization","api_key","apikey","credential","cookie","filesystem_path","path")


class ActivityCenterError(RuntimeError):
    def __init__(self,message:str,status_code:int=400):
        super().__init__(message)
        self.status_code=status_code


def _now()->str:
    return datetime.now(timezone.utc).isoformat()


def _safe(value:Any,depth:int=0)->Any:
    if depth>5:
        return "[depth-limit]"
    if isinstance(value,dict):
        out={}
        for key,item in list(value.items())[:40]:
            name=str(key)
            lowered=name.lower().replace("-","_")
            if any(secret in lowered for secret in _SECRET_KEYS):
                continue
            out[name]=_safe(item,depth+1)
        return out
    if isinstance(value,list):
        return [_safe(item,depth+1) for item in value[:40]]
    if isinstance(value,str):
        return value[:500]
    if value is None or isinstance(value,(int,float,bool)):
        return value
    return str(value)[:500]


def _decode(raw:Any)->dict[str,Any]:
    try:
        value=json.loads(str(raw or "{}"))
    except json.JSONDecodeError:
        return {}
    return _safe(value) if isinstance(value,dict) else {}


def _severity(action:str,status:str="")->str:
    text=f"{action} {status}".lower()
    if any(word in text for word in ("failed","error","unavailable","denied")):
        return "error"
    if any(word in text for word in ("pending","requested","approval")):
        return "action_required"
    if any(word in text for word in ("completed","succeeded","executed","installed","updated")):
        return "success"
    if any(word in text for word in ("warning","stale","retry","reconcil")):
        return "warning"
    return "info"


def _category(source_kind:str,action:str)->str:
    if source_kind=="approval": return "approvals"
    if source_kind=="automation": return "automations"
    if source_kind=="agent": return "agent"
    if source_kind=="app": return "apps"
    if source_kind=="notification": return "notifications"
    if action.startswith("task."): return "tasks"
    return "system"


def _event(
    source_kind:str,source_id:str,created_at:str,action:str,
    *,
    actor_type:str="system",actor_key:str="",resource_type:str="",
    resource_key:str="",status:str="",source_key:str="",metadata:dict[str,Any]|None=None,
    notification_id:int|None=None,read_at:str|None=None,dismissed_at:str|None=None,
    archived_at:str|None=None,title:str="",body:str="",level:str|None=None,
    priority:str="normal",action_payload:dict[str,Any]|None=None,
    category_override:str="",
)->dict[str,Any]:
    severity=level if level in _ALLOWED_LEVELS else _severity(action,status)
    return {
        "event_id":f"{source_kind}:{source_id}",
        "source_kind":source_kind,
        "source_id":str(source_id),
        "source_key":source_key or actor_key,
        "category":category_override or _category(source_kind,action),
        "created_at":str(created_at),
        "actor_type":actor_type,
        "actor_key":actor_key,
        "action":action,
        "status":status,
        "resource_type":resource_type,
        "resource_key":resource_key,
        "title":title or action.replace("."," ").replace("_"," ").title(),
        "body":str(body or "")[:1000],
        "level":severity,
        "priority":priority if priority in _ALLOWED_PRIORITIES else "normal",
        "metadata":_safe(metadata or {}),
        "notification_id":notification_id,
        "read":read_at is not None,
        "dismissed":dismissed_at is not None,
        "archived":archived_at is not None,
        "needs_attention":severity in {"error","action_required"} and dismissed_at is None and archived_at is None,
        "action_payload":_safe(action_payload or {}),
    }


def list_activity(
    *,
    limit:int=200,
    category:str="",
    needs_attention:bool=False,
    unread_only:bool=False,
)->dict[str,Any]:
    bounded=max(1,min(int(limit),500))
    fetch=min(max(bounded*3,100),1200)
    items:list[dict[str,Any]]=[]
    with db() as connection:
        for row in connection.execute(
            """SELECT id,actor_type,actor_key,action,resource_type,resource_key,metadata_json,created_at
               FROM activity_log ORDER BY id DESC LIMIT ?""",(fetch,)
        ).fetchall():
            items.append(_event(
                "audit",str(row["id"]),str(row["created_at"]),str(row["action"]),
                actor_type=str(row["actor_type"]),actor_key=str(row["actor_key"] or ""),
                resource_type=str(row["resource_type"] or ""),resource_key=str(row["resource_key"] or ""),
                metadata=_decode(row["metadata_json"]),
            ))
        notification_rows=connection.execute(
            """SELECT n.*,t.status AS task_status
               FROM notifications n LEFT JOIN tasks t ON t.id=n.task_id
               ORDER BY n.id DESC LIMIT ?""",(fetch,)
        ).fetchall()
        notification_dedupe={str(row["dedupe_key"]) for row in notification_rows if row["dedupe_key"]}
        for row in notification_rows:
            action_payload=_decode(row["action_json"])
            items.append(_event(
                "notification",str(row["id"]),str(row["created_at"]),str(row["event_key"] or "notification"),
                actor_type="system",actor_key=str(row["source"] or "homeserver"),
                resource_type="notification",resource_key=str(row["id"]),
                status=str(row["task_status"] or ""),source_key=str(row["source_key"] or row["source"] or ""),
                metadata={"occurrence_count":int(row["occurrence_count"] or 1),"task_id":row["task_id"]},
                notification_id=int(row["id"]),read_at=row["read_at"],dismissed_at=row["dismissed_at"],
                archived_at=row["archived_at"],title=str(row["title"]),body=str(row["body"] or ""),
                level=str(row["level"] or "info"),priority=str(row["priority"] or "normal"),
                action_payload=action_payload,category_override=str(row["category"] or "notifications"),
            ))
        for row in connection.execute(
            """SELECT e.id,e.event_type,e.actor_type,e.actor_key,e.metadata_json,e.created_at,a.app_key
               FROM homeserver_app_events e JOIN homeserver_apps a ON a.app_id=e.app_id
               ORDER BY e.id DESC LIMIT ?""",(fetch,)
        ).fetchall():
            items.append(_event(
                "app",str(row["id"]),str(row["created_at"]),str(row["event_type"]),
                actor_type=str(row["actor_type"]),actor_key=str(row["actor_key"] or ""),
                resource_type="app",resource_key=str(row["app_key"]),source_key=str(row["app_key"]),
                metadata=_decode(row["metadata_json"]),
            ))
        for row in connection.execute(
            """SELECT id,action_key,source_app_key,actor_type,status,created_at,expires_at,error
               FROM action_requests ORDER BY created_at DESC LIMIT ?""",(fetch,)
        ).fetchall():
            if str(row["status"])=="pending" and f"approval:{row['id']}" in notification_dedupe:
                continue
            action_payload={}
            if str(row["status"])=="pending":
                action_payload={"type":"approval","request_id":str(row["id"])}
            items.append(_event(
                "approval",str(row["id"]),str(row["created_at"]),"action."+str(row["status"]),
                actor_type=str(row["actor_type"]),actor_key=str(row["source_app_key"]),
                resource_type="action_request",resource_key=str(row["id"]),status=str(row["status"]),
                source_key=str(row["source_app_key"]),
                metadata={"action_key":str(row["action_key"]),"expires_at":str(row["expires_at"]),"has_error":bool(row["error"])},
                title=f"{str(row['action_key'])} · {str(row['status'])}",
                action_payload=action_payload,
            ))
        for row in homeserver_app_approvals.list_rows(limit=fetch):
            request_id=str(row.get("id") or "")
            status=str(row.get("status") or "")
            if status=="pending" and f"app-approval:{request_id}" in notification_dedupe:
                continue
            action_payload={"type":"approval","request_id":request_id} if status=="pending" else {}
            items.append(_event(
                "approval",f"app-{request_id}",str(row.get("created_at") or ""),"action."+status,
                actor_type=str(row.get("actor_type") or "owner"),actor_key=str(row.get("source_app_key") or ""),
                resource_type="action_request",resource_key=request_id,status=status,
                source_key=str(row.get("source_app_key") or ""),
                metadata={
                    "action_key":str(row.get("action_key") or ""),
                    "expires_at":str(row.get("expires_at") or ""),
                    "has_error":bool(row.get("error")),
                    "homeserver_app":True,
                },
                title=f"{str(row.get('action_key') or 'apps.invoke')} · {status}",
                action_payload=action_payload,
            ))
        for row in connection.execute(
            """SELECT x.id,x.trigger_kind,x.status,x.action_count,x.error,x.created_at,
                      r.rule_key,ru.routine_key
               FROM automation_rule_executions x
               LEFT JOIN automation_rules r ON r.id=x.rule_id
               JOIN automation_routines ru ON ru.id=x.routine_id
               ORDER BY x.id DESC LIMIT ?""",(fetch,)
        ).fetchall():
            if str(row["status"])=="failed" and f"automation:{row['id']}" in notification_dedupe:
                continue
            items.append(_event(
                "automation",str(row["id"]),str(row["created_at"]),"automation."+str(row["status"]),
                actor_type="system",actor_key="local-automation",resource_type="routine",
                resource_key=str(row["routine_key"]),status=str(row["status"]),source_key="automation",
                metadata={"rule_key":row["rule_key"],"trigger_kind":row["trigger_kind"],"action_count":int(row["action_count"] or 0),"has_error":bool(row["error"])},
            ))
        for row in connection.execute(
            """SELECT r.id,r.run_key,r.job_id,r.status,r.compute_source,r.provider_key,r.model,
                      r.total_tokens,r.created_at,a.app_key
               FROM homeserver_app_ai_runs r JOIN homeserver_apps a ON a.app_id=r.app_id
               ORDER BY r.id DESC LIMIT ?""",(fetch,)
        ).fetchall():
            if str(row["status"])=="failed" and f"agent-run:{row['id']}" in notification_dedupe:
                continue
            items.append(_event(
                "agent",str(row["id"]),str(row["created_at"]),"app.agent."+str(row["status"]),
                actor_type="system",actor_key="app-agent-runtime",resource_type="app",resource_key=str(row["app_key"]),
                status=str(row["status"]),source_key=str(row["app_key"]),
                metadata={"run_key":row["run_key"],"job_id":row["job_id"],"compute_source":row["compute_source"],"provider_key":row["provider_key"],"model":row["model"],"total_tokens":int(row["total_tokens"] or 0)},
            ))
    seen=set()
    result=[]
    for item in sorted(items,key=lambda x:(x["created_at"],x["event_id"]),reverse=True):
        semantic=(item["source_kind"],item["source_id"])
        if semantic in seen: continue
        seen.add(semantic)
        if category and item["category"]!=category: continue
        if needs_attention and not item["needs_attention"]: continue
        if unread_only and (item["source_kind"]!="notification" or item["read"]): continue
        result.append(item)
        if len(result)>=bounded: break
    return {"contract":CONTRACT,"items":result,"count":len(result)}


def _preference_allows(source_key:str,level:str)->bool:
    if level=="action_required":
        return True
    key=str(source_key or "").strip()
    if not key:
        return True
    with db() as connection:
        row=connection.execute(
            "SELECT enabled,minimum_level FROM notification_preferences WHERE source_key=?",
            (key,),
        ).fetchone()
    if row is None:
        return True
    if not bool(row["enabled"]):
        return False
    minimum=str(row["minimum_level"] or "info")
    return _LEVEL_RANK.get(level,0)>=_LEVEL_RANK.get(minimum,0)


def emit_notification(
    *,
    source:str,title:str,body:str="",level:str="info",priority:str="normal",
    category:str="system",source_kind:str="system",source_key:str="",
    event_key:str="",dedupe_key:str="",action_payload:dict[str,Any]|None=None,
)->dict[str,Any]:
    if level not in _ALLOWED_LEVELS:
        raise ActivityCenterError("Unsupported notification level.")
    if priority not in _ALLOWED_PRIORITIES:
        raise ActivityCenterError("Unsupported notification priority.")
    preference_key=str(source_key or source or "").strip()
    if not _preference_allows(preference_key,level):
        return {"suppressed":True,"source_key":preference_key,"level":level}
    safe_title=" ".join(str(title or "").split())[:240]
    if not safe_title: raise ActivityCenterError("Notification title is required.")
    safe_body=str(body or "")[:5000]
    dedupe=str(dedupe_key or "").strip()[:240] or None
    action_json=json.dumps(_safe(action_payload or {}),separators=(",",":"),sort_keys=True)
    with db() as connection:
        if dedupe:
            existing=connection.execute("SELECT id FROM notifications WHERE dedupe_key=?",(dedupe,)).fetchone()
            if existing:
                connection.execute(
                    """UPDATE notifications SET title=?,body=?,level=?,priority=?,category=?,source_kind=?,source_key=?,
                       event_key=?,action_json=?,occurrence_count=occurrence_count+1,last_seen_at=CURRENT_TIMESTAMP
                       WHERE id=?""",
                    (safe_title,safe_body,level,priority,category,source_kind,source_key or None,event_key or None,action_json,int(existing["id"])),
                )
                row=connection.execute("SELECT * FROM notifications WHERE id=?",(int(existing["id"]),)).fetchone()
                return dict(row)
        cursor=connection.execute(
            """INSERT INTO notifications(
                 source,title,body,level,category,priority,source_kind,source_key,event_key,dedupe_key,
                 action_json,last_seen_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
            (source[:120],safe_title,safe_body,level,category[:60],priority,source_kind[:60],source_key[:160] or None,event_key[:160] or None,dedupe,action_json),
        )
        row=connection.execute("SELECT * FROM notifications WHERE id=?",(int(cursor.lastrowid),)).fetchone()
    return dict(row)


def prune_notifications(retention_days:int=90,max_rows:int=5000)->dict[str,int]:
    days=max(7,min(int(retention_days),3650))
    cap=max(500,min(int(max_rows),50000))
    deleted=0
    with db() as connection:
        cursor=connection.execute(
            """DELETE FROM notifications
               WHERE (dismissed_at IS NOT NULL OR archived_at IS NOT NULL)
                 AND created_at < datetime('now', ?)""",
            (f"-{days} days",),
        )
        deleted+=max(0,int(cursor.rowcount or 0))
        count=int(connection.execute("SELECT COUNT(*) FROM notifications").fetchone()[0])
        if count>cap:
            overflow=count-cap
            cursor=connection.execute(
                """DELETE FROM notifications WHERE id IN (
                     SELECT id FROM notifications
                     WHERE dismissed_at IS NOT NULL OR archived_at IS NOT NULL
                     ORDER BY id ASC LIMIT ?
                   )""",
                (overflow,),
            )
            deleted+=max(0,int(cursor.rowcount or 0))
    return {"deleted":deleted}


def sync_notifications(limit:int=250)->dict[str,int]:
    created=0
    pruned=prune_notifications()
    bounded=max(1,min(int(limit),1000))
    with db() as connection:
        approvals=connection.execute(
            """SELECT id,action_key,source_app_key,created_at FROM action_requests
               WHERE status='pending' ORDER BY created_at DESC LIMIT ?""",(bounded,)
        ).fetchall()
        failed_ai=connection.execute(
            """SELECT r.id,r.run_key,r.job_id,r.created_at,a.app_key
               FROM homeserver_app_ai_runs r JOIN homeserver_apps a ON a.app_id=r.app_id
               WHERE r.status='failed' ORDER BY r.id DESC LIMIT ?""",(bounded,)
        ).fetchall()
        failed_auto=connection.execute(
            """SELECT x.id,x.created_at,ru.routine_key FROM automation_rule_executions x
               JOIN automation_routines ru ON ru.id=x.routine_id
               WHERE x.status='failed' ORDER BY x.id DESC LIMIT ?""",(bounded,)
        ).fetchall()
    app_approvals=homeserver_app_approvals.list_rows(status="pending",limit=bounded)
    for row in approvals:
        before=_notification_by_dedupe(f"approval:{row['id']}")
        emitted=emit_notification(
            source="approvals",title=f"Approval required: {row['action_key']}",
            body=f"Requested by {row['source_app_key']}",level="action_required",priority="high",
            category="approvals",source_kind="approval",source_key=str(row["source_app_key"]),
            event_key="action.pending",dedupe_key=f"approval:{row['id']}",
            action_payload={"type":"approval","request_id":str(row["id"])},
        )
        created+=0 if before or emitted.get("suppressed") else 1
    for row in app_approvals:
        request_id=str(row.get("id") or "")
        before=_notification_by_dedupe(f"app-approval:{request_id}")
        emitted=emit_notification(
            source="approvals",title=f"Approval required: {str(row.get('action_key') or 'apps.invoke')}",
            body=f"Requested by {str(row.get('source_app_key') or 'owner')}",
            level="action_required",priority="high",category="approvals",source_kind="approval",
            source_key=str(row.get("source_app_key") or "owner"),
            event_key="action.pending",dedupe_key=f"app-approval:{request_id}",
            action_payload={"type":"approval","request_id":request_id},
        )
        created+=0 if before or emitted.get("suppressed") else 1
    for row in failed_ai:
        before=_notification_by_dedupe(f"agent-run:{row['id']}")
        emitted=emit_notification(
            source="agent-runtime",title=f"Agent job failed · {row['app_key']}",
            body=f"Run {row['run_key']}",level="error",priority="high",category="agent",
            source_kind="agent",source_key=str(row["app_key"]),event_key="app.agent.failed",
            dedupe_key=f"agent-run:{row['id']}",
            action_payload={"type":"open","target_view":"homeserver-apps"},
        )
        created+=0 if before or emitted.get("suppressed") else 1
    for row in failed_auto:
        before=_notification_by_dedupe(f"automation:{row['id']}")
        emitted=emit_notification(
            source="automation",title=f"Automation failed · {row['routine_key']}",
            level="error",priority="high",category="automations",source_kind="automation",
            source_key=str(row["routine_key"]),event_key="automation.failed",dedupe_key=f"automation:{row['id']}",
            action_payload={"type":"open","target_view":"automation"},
        )
        created+=0 if before or emitted.get("suppressed") else 1
    # Health transitions share this same maintenance notification ledger.
    # Local import avoids a module-level cycle: health_repair reads Activity Center.
    from . import health_maintenance
    health = health_maintenance.sync_health_notifications()
    return {"created": created + health["created"], "pruned": pruned["deleted"], "health": health}


def _notification_by_dedupe(key:str)->dict[str,Any]|None:
    with db() as connection:
        row=connection.execute("SELECT id FROM notifications WHERE dedupe_key=?",(key,)).fetchone()
    return dict(row) if row else None


def mark_notification(notification_id:int,*,read:bool|None=None,dismissed:bool|None=None,archived:bool|None=None)->dict[str,Any]:
    sets=[]; values=[]
    if read is not None:
        sets.append("read_at=?"); values.append(_now() if read else None)
    if dismissed is not None:
        sets.append("dismissed_at=?"); values.append(_now() if dismissed else None)
    if archived is not None:
        sets.append("archived_at=?"); values.append(_now() if archived else None)
    if not sets: raise ActivityCenterError("No notification change was supplied.")
    values.append(int(notification_id))
    with db() as connection:
        cursor=connection.execute(f"UPDATE notifications SET {', '.join(sets)} WHERE id=?",values)
        if cursor.rowcount!=1: raise ActivityCenterError("Notification not found.",404)
        row=connection.execute("SELECT * FROM notifications WHERE id=?",(int(notification_id),)).fetchone()
    return dict(row)


def preferences()->dict[str,Any]:
    with db() as connection:
        rows=connection.execute("SELECT * FROM notification_preferences ORDER BY source_key").fetchall()
    return {"contract":CONTRACT,"items":[{**dict(r),"enabled":bool(r["enabled"]),"sound_enabled":bool(r["sound_enabled"]),"toast_enabled":bool(r["toast_enabled"])} for r in rows]}


def update_preference(source_key:str,values:dict[str,Any])->dict[str,Any]:
    key=str(source_key or "").strip()[:160]
    if not key: raise ActivityCenterError("source_key is required.")
    allowed={"enabled","minimum_level","sound_enabled","toast_enabled"}
    if set(values)-allowed: raise ActivityCenterError("Unsupported notification preference field.")
    current={"enabled":True,"minimum_level":"info","sound_enabled":False,"toast_enabled":True}
    with db() as connection:
        row=connection.execute("SELECT * FROM notification_preferences WHERE source_key=?",(key,)).fetchone()
        if row:
            current.update(dict(row))
        level=str(values.get("minimum_level",current["minimum_level"]))
        if level not in _ALLOWED_LEVELS: raise ActivityCenterError("Unsupported minimum notification level.")
        payload={
            "enabled":bool(values.get("enabled",current["enabled"])),
            "minimum_level":level,
            "sound_enabled":bool(values.get("sound_enabled",current["sound_enabled"])),
            "toast_enabled":bool(values.get("toast_enabled",current["toast_enabled"])),
        }
        connection.execute(
            """INSERT INTO notification_preferences(source_key,enabled,minimum_level,sound_enabled,toast_enabled,updated_at)
               VALUES (?,?,?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(source_key) DO UPDATE SET enabled=excluded.enabled,minimum_level=excluded.minimum_level,
                 sound_enabled=excluded.sound_enabled,toast_enabled=excluded.toast_enabled,updated_at=CURRENT_TIMESTAMP""",
            (key,1 if payload["enabled"] else 0,level,1 if payload["sound_enabled"] else 0,1 if payload["toast_enabled"] else 0),
        )
    return {"source_key":key,**payload}


def summary()->dict[str,Any]:
    sync_notifications()
    with db() as connection:
        unread=int(connection.execute("SELECT COUNT(*) FROM notifications WHERE read_at IS NULL AND dismissed_at IS NULL AND archived_at IS NULL").fetchone()[0])
        attention=int(connection.execute("""SELECT COUNT(*) FROM notifications WHERE level IN ('error','action_required') AND dismissed_at IS NULL AND archived_at IS NULL""").fetchone()[0])
        pending_db=int(connection.execute("SELECT COUNT(*) FROM action_requests WHERE status='pending'").fetchone()[0])
        failed_auto=int(connection.execute(
            """SELECT COUNT(*) FROM notifications
               WHERE source_kind='automation' AND level='error'
                 AND dismissed_at IS NULL AND archived_at IS NULL"""
        ).fetchone()[0])
        failed_agent=int(connection.execute(
            """SELECT COUNT(*) FROM notifications
               WHERE source_kind='agent' AND level='error'
                 AND dismissed_at IS NULL AND archived_at IS NULL"""
        ).fetchone()[0])
    return {
        "contract":CONTRACT,"unread":unread,"needs_attention":attention,"pending_approvals":pending_db+len(homeserver_app_approvals.list_rows(status="pending",limit=500)),
        "failed_automations":failed_auto,"failed_agent_runs":failed_agent,
    }


def brain_context(limit:int=20)->dict[str,Any]:
    state=summary()
    activity=list_activity(limit=max(1,min(int(limit),50)),needs_attention=True)["items"]
    return {
        "contract":"vp3.homeserver.activity-center.brain-context.v1",
        "summary":state,
        "attention":[{
            "event_id":row["event_id"],"category":row["category"],"created_at":row["created_at"],
            "title":row["title"],"level":row["level"],"source_kind":row["source_kind"],
            "source_key":row["source_key"],"action":row["action"],
        } for row in activity],
        "governance":{
            "raw_secrets_exposed":False,"raw_arguments_exposed":False,"filesystem_paths_exposed":False,
            "actionable_notifications_use_existing_governance":True,
        },
    }


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,"unified_activity_feed":True,"notifications":True,"dedupe":True,
        "bounded_retention":True,"default_retention_days":90,"notification_cap":5000,
        "read_dismiss_archive":True,"preferences":True,"actionable_approvals":True,
        "agent_brain_context":True,"bounded_projection":True,"raw_secrets_exposed":False,
        "homeserver_execution_authority":True,
    }
