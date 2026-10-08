"""A5B owner-approved browser grants. No model-issued browser authority."""
from __future__ import annotations
import uuid
from ..database import db
from . import agent_mission_runtime as mission, context_engine, app_scopes
from . import agent_browser_policy as policy, agent_browser_capture

MAX_VISITS=5

def _context(source: str, mid: str, tid: str) -> dict:
    snapshot=mission.get_mission(source,mid)
    task=next((t for t in snapshot["tasks"] if t["id"]==tid),None)
    if task is None:
        raise mission.MissionError("Task is not in this mission.",404)
    settings=context_engine.get_settings(str(snapshot["conversation_id"]))
    if not settings.get("cloud_allowed",False):
        raise mission.MissionError("Local-only missions cannot browse externally.",403)
    if source!="owner" and not app_scopes.get_scope_for_source(source).get("cloud_allowed",False):
        raise mission.MissionError("App browser permission is not granted.",403)
    mission._route(source,str(snapshot["conversation_id"]))
    return {"mission":snapshot,"task":task}

def _projection(row,*,image=False) -> dict:
    out={k:row[k] for k in (
        "task_id","mission_id","status","approved_origin","current_url",
        "page_title","visit_count","expires_at","updated_at","last_error")}
    out["read_only"]=True
    out["mode"]="isolated_browser"
    out["max_visits"]=MAX_VISITS
    out["text_snapshot"]=str(row["text_snapshot"] or "")[:9000]
    if image:
        out["image_base64"]=str(row["image_base64"] or "")[:200000]
    return out

def inspect(source: str,mid: str,tid: str,*,image: bool=False):
    _context(source,mid,tid)
    with db() as conn:
        row=conn.execute("SELECT * FROM agent_mission_browser_v1 "
                         "WHERE task_id=? AND mission_id=? AND source_app_key=?",
                         (tid,mid,source)).fetchone()
    return _projection(row,image=image) if row else None

def authorize(source: str,mid: str,tid: str,url: str):
    snapshot=_context(source,mid,tid)
    valid,hostname,origin=policy.parse_url(url)
    pinned=policy.pinned_public_ipv4(hostname)
    if snapshot["task"]["status"]!="queued" or snapshot["mission"]["status"] not in ("planned","running"):
        raise mission.MissionError("Only queued workers may receive a browser grant.",409)
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row=conn.execute("SELECT t.status,m.status mission_status FROM agent_mission_tasks_v1 t "
                         "JOIN agent_missions_v1 m ON m.id=t.mission_id "
                         "WHERE t.id=? AND m.id=? AND m.source_app_key=?",
                         (tid,mid,source)).fetchone()
        if not row or row["status"]!="queued" or row["mission_status"] not in ("planned","running"):
            raise mission.MissionError("Task state changed while approving browser.",409)
        exists=conn.execute("SELECT 1 FROM agent_mission_browser_v1 WHERE task_id=?", (tid,)).fetchone()
        if exists:
            raise mission.MissionError("A browser grant already exists for this worker.",409)
        conn.execute("INSERT INTO agent_mission_browser_v1 "
                     "(task_id,mission_id,source_app_key,pinned_ip,approved_origin,current_url,expires_at) "
                     "VALUES(?,?,?,?,?,?,datetime('now','+15 minutes'))",
                     (tid,mid,source,pinned,origin,valid))
        mission._event(conn,mid,"browser.approved",tid,{"origin":origin,"read_only":True})
    return inspect(source,mid,tid)

def revoke(source: str,mid: str,tid: str):
    _context(source,mid,tid)
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        updated=conn.execute("UPDATE agent_mission_browser_v1 SET status='closed',capture_token=NULL,"
                             "text_snapshot='',image_base64='',updated_at=CURRENT_TIMESTAMP "
                             "WHERE task_id=? AND mission_id=? AND source_app_key=? AND status!='closed'",
                             (tid,mid,source))
        if not updated.rowcount:
            raise mission.MissionError("Active worker browser was not found.",409)
        mission._event(conn,mid,"browser.revoked",tid)
    return {"task_id":tid,"status":"closed"}

def recover_interrupted():
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows=conn.execute("SELECT task_id,mission_id FROM agent_mission_browser_v1 "
                          "WHERE status='capturing'").fetchall()
        for row in rows:
            conn.execute("UPDATE agent_mission_browser_v1 SET status='closed',capture_token=NULL,"
                         "text_snapshot='',image_base64='',last_error='Interrupted capture; grant closed.' "
                         "WHERE task_id=?", (row["task_id"],))
            mission._event(conn,row["mission_id"],"browser.recovery_closed",row["task_id"])
    return len(rows)
