"""A5B4: exclusive human takeover and one-time safe search submission.

The agent cannot act while an owner controls a session. The owner may use
existing non-submitting controls or review a single-purpose GET search form.
No passwords, POST, payments, messaging or arbitrary click execution.
"""
from __future__ import annotations
import json
import uuid
from ..database import db
from . import agent_mission_live_browser as live, agent_mission_runtime as mission
from . import agent_browser_live_actor as actor

MAX_OWNER_ACTIONS=8

def _session(source,mid,tid):
    ctx,grant=live._required(source,mid,tid,executable=True)
    if ctx["task"]["status"]!="queued":
        raise mission.MissionError("Worker is no longer queued.",409)
    with db() as conn:
        row=conn.execute("SELECT * FROM agent_mission_live_browser_v2 "
                         "WHERE mission_id=? AND task_id=? AND source_app_key=?",
                         (mid,tid,source)).fetchone()
    if not row or row["status"]!="live" or not actor.active(tid):
        raise mission.MissionError("A live browser is required.",409)
    return ctx,grant,row

def _lease(source,mid,tid):
    _,_,session=_session(source,mid,tid)
    with db() as conn:
        row=conn.execute("SELECT * FROM agent_mission_browser_takeover_v4 "
                         "WHERE task_id=? AND mission_id=? AND source_app_key=? AND "
                         "session_token=?",(tid,mid,source,session["session_token"])).fetchone()
        if not row or row["mode"]!="owner" or conn.execute(
            "SELECT 1 WHERE datetime('now')>=datetime(?)",(row["expires_at"],)
        ).fetchone():
            raise mission.MissionError("Owner control lease is absent or expired.",409)
    return session,row

def status(source,mid,tid):
    mission.get_mission(source,mid)
    with db() as conn:
        row=conn.execute("SELECT * FROM agent_mission_browser_takeover_v4 "
                         "WHERE task_id=? AND mission_id=? AND source_app_key=?",
                         (tid,mid,source)).fetchone()
        if not row:
            return {"mode":"agent","actions_used":0,"max_actions":MAX_OWNER_ACTIONS,"forms":[],"pending_form":{}}
        current=conn.execute("SELECT session_token FROM agent_mission_live_browser_v2 "
                             "WHERE task_id=?",(tid,)).fetchone()
        good=bool(current and current["session_token"]==row["session_token"] and
                  row["mode"]=="owner" and row["expires_at"] and not conn.execute(
                      "SELECT 1 WHERE datetime('now')>=datetime(?)",(row["expires_at"],)
                  ).fetchone())
        return {"mode":"owner" if good else "agent","revision":int(row["revision"]),
                "expires_at":row["expires_at"] if good else None,
                "actions_used":int(row["actions_used"]),"max_actions":MAX_OWNER_ACTIONS,
                "forms":json.loads(row["forms_json"] or "[]") if good else [],
                "pending_form":json.loads(row["pending_form_json"] or "{}") if good else {}}

def guard_agent(source,mid,tid):
    """Agent proposal and approval APIs must all fail during takeover."""
    info=status(source,mid,tid)
    if info["mode"]=="owner":
        raise mission.MissionError("Agent controls are suspended during owner takeover.",409)

def acquire(source,mid,tid):
    _,_,session=_session(source,mid,tid)
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        prev=conn.execute("SELECT * FROM agent_mission_browser_takeover_v4 "
                          "WHERE task_id=?",(tid,)).fetchone()
        if prev and prev["mode"]=="owner" and prev["session_token"]==session["session_token"] and not conn.execute(
            "SELECT 1 WHERE datetime('now')>=datetime(?)",(prev["expires_at"],)
        ).fetchone():
            raise mission.MissionError("Owner already controls this browser.",409)
        conn.execute("INSERT INTO agent_mission_browser_takeover_v4 "
                     "(task_id,mission_id,source_app_key,session_token,mode,lease_id,expires_at) "
                     "VALUES(?,?,?,?, 'owner',?,datetime('now','+5 minutes')) "
                     "ON CONFLICT(task_id) DO UPDATE SET mode='owner',"
                     "session_token=excluded.session_token,lease_id=excluded.lease_id,"
                     "expires_at=excluded.expires_at,revision=revision+1,"
                     "actions_used=0,pending_form_json='{}',updated_at=CURRENT_TIMESTAMP",
                     (tid,mid,source,session["session_token"],str(uuid.uuid4())))
        # Supersede any AI recommendations generated before takeover.
        conn.execute("UPDATE agent_mission_live_browser_v2 SET pending_proposal_json='{}' "
                     "WHERE task_id=? AND session_token=?",(tid,session["session_token"]))
        conn.execute("UPDATE agent_mission_browser_controls_v3 SET pending_action_json='{}' "
                     "WHERE task_id=?",(tid,))
        mission._event(conn,mid,"browser.owner_took_control",tid)
    return live.get(source,mid,tid)

def release(source,mid,tid):
    mission.get_mission(source,mid)
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        update=conn.execute("UPDATE agent_mission_browser_takeover_v4 SET mode='agent',"
                            "lease_id='',expires_at=NULL,pending_form_json='{}',"
                            "revision=revision+1,updated_at=CURRENT_TIMESTAMP "
                            "WHERE task_id=? AND mission_id=? AND source_app_key=? AND mode='owner'",
                            (tid,mid,source))
        if update.rowcount!=1:
            raise mission.MissionError("No active owner takeover found.",409)
        mission._event(conn,mid,"browser.owner_released_control",tid)
    return live.get(source,mid,tid)

def _budget(row):
    if int(row["actions_used"])>=MAX_OWNER_ACTIONS:
        raise mission.MissionError("Owner action budget exhausted.",409)

def manual(source,mid,tid,*,index,fingerprint,kind,value):
    session,row=_lease(source,mid,tid)
    _budget(row)
    if kind not in ("fill","check","select","toggle"):
        raise mission.MissionError("Unsupported manual control.",403)
    # The existing DOM policy validates current index/fingerprint and input type.
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        claimed=conn.execute("UPDATE agent_mission_browser_takeover_v4 SET actions_used=actions_used+1,"
                             "revision=revision+1,pending_form_json='{}' "
                             "WHERE task_id=? AND session_token=? AND mode='owner' "
                             "AND revision=? AND actions_used<? AND datetime('now')<datetime(expires_at)",
                             (tid,session["session_token"],row["revision"],MAX_OWNER_ACTIONS))
        if claimed.rowcount!=1:
            raise mission.MissionError("Owner takeover changed before action.",409)
        conn.execute("UPDATE agent_mission_live_browser_v2 SET status='navigating',"
                     "pending_proposal_json='{}' WHERE task_id=? AND status='live'",(tid,))
    try:
        result=actor.execute(tid,"interact",{"index":index,"fingerprint":fingerprint,
                                            "kind":kind,"value":value})
        _lease_after_action(source,mid,tid,session)
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            live._store_snapshot(conn,tid,mid,session["session_token"],result,
                                 status="navigating",navigation=False)
            conn.execute("UPDATE agent_mission_live_browser_v2 SET status='live',"
                         "revision=revision+1,pending_proposal_json='{}' WHERE task_id=? "
                         "AND session_token=?",(tid,session["session_token"]))
            mission._event(conn,mid,"browser.owner_controlled",tid,{"kind":kind})
    except Exception:
        actor.close_session(tid)
        with db() as conn:
            conn.execute("UPDATE agent_mission_live_browser_v2 SET status='stopped' "
                         "WHERE task_id=? AND session_token=? AND status='navigating'",
                         (tid,session["session_token"]))
        raise
    return live.get(source,mid,tid)

def _lease_after_action(source,mid,tid,session):
    context,_ = live._required(source,mid,tid,executable=True)
    if context["task"]["status"]!="queued":
        raise mission.MissionError("Worker started while owner was controlling browser.",409)
    with db() as conn:
        owner=conn.execute("SELECT lease_id FROM agent_mission_browser_takeover_v4 "
                           "WHERE task_id=? AND mission_id=? AND source_app_key=? "
                           "AND session_token=? AND mode='owner' "
                           "AND datetime('now')<datetime(expires_at)",
                           (tid,mid,source,session["session_token"])).fetchone()
    if not owner:
        raise mission.MissionError("Owner relinquished control during browser action.",409)

def review_search(source,mid,tid,*,index,fingerprint):
    session,row=_lease(source,mid,tid)
    _budget(row)
    forms=json.loads(row["forms_json"] or "[]")
    candidate=next((f for f in forms if f["index"]==index and f["fingerprint"]==fingerprint),None)
    if candidate is None:
        raise mission.MissionError("This search form is no longer in the current snapshot.",409)
    proposal={"id":str(uuid.uuid4()),"index":index,"fingerprint":fingerprint,
              "action":candidate["action"],"method":"GET",
              "revision":int(row["revision"])}
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        upd=conn.execute("UPDATE agent_mission_browser_takeover_v4 SET pending_form_json=? "
                         "WHERE task_id=? AND session_token=? AND mode='owner' AND revision=? "
                         "AND datetime('now')<datetime(expires_at)",
                         (json.dumps(proposal),tid,session["session_token"],row["revision"]))
        if upd.rowcount!=1:
            raise mission.MissionError("Search form review changed.",409)
        mission._event(conn,mid,"browser.search_reviewed",tid,
                       {"method":"GET","action":candidate["action"]})
    return live.get(source,mid,tid)

def submit_search(source,mid,tid,*,proposal_id):
    session,row=_lease(source,mid,tid)
    _budget(row)
    review=json.loads(row["pending_form_json"] or "{}")
    if review.get("id")!=proposal_id or review.get("revision")!=row["revision"]:
        raise mission.MissionError("Search approval expired or was already used.",409)
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        updated=conn.execute("UPDATE agent_mission_browser_takeover_v4 SET "
                            "pending_form_json='{}',actions_used=actions_used+1,revision=revision+1 "
                            "WHERE task_id=? AND session_token=? AND mode='owner' "
                            "AND pending_form_json=? AND revision=? "
                            "AND datetime('now')<datetime(expires_at)",
                            (tid,session["session_token"],row["pending_form_json"],row["revision"]))
        if updated.rowcount!=1:
            raise mission.MissionError("Search action already claimed or expired.",409)
        conn.execute("UPDATE agent_mission_live_browser_v2 SET status='navigating',"
                     "pending_proposal_json='{}' WHERE task_id=? AND status='live'",(tid,))
    try:
        result=actor.execute(tid,"search_get",{
            "index":review["index"],"fingerprint":review["fingerprint"]})
        _lease_after_action(source,mid,tid,session)
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            live._store_snapshot(conn,tid,mid,session["session_token"],result,
                                 status="navigating",navigation=True)
            mission._event(conn,mid,"browser.search_submitted",tid,
                           {"method":"GET","approved_action":review["action"]})
    except Exception:
        actor.close_session(tid)
        with db() as conn:
            conn.execute("UPDATE agent_mission_live_browser_v2 SET status='stopped' "
                         "WHERE task_id=? AND session_token=? AND status='navigating'",
                         (tid,session["session_token"]))
        raise
    return live.get(source,mid,tid)
