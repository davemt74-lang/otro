"""A5B4: exclusive human takeover and one-time safe search submission.

The agent cannot act while an owner controls a session. The owner may use
existing non-submitting controls or review a single-purpose GET search form.
No passwords, POST, payments, messaging or arbitrary click execution.
"""
from __future__ import annotations
import json
import uuid
import hashlib
from ..database import db, atomic_write
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
        current=conn.execute("SELECT session_token,status FROM agent_mission_live_browser_v2 "
                             "WHERE task_id=?",(tid,)).fetchone()
        good=bool(current and current["status"]!='stopped' and current["session_token"]==row["session_token"] and
                  row["mode"]=="owner" and row["expires_at"] and not conn.execute(
                      "SELECT 1 WHERE datetime('now')>=datetime(?)",(row["expires_at"],)
                  ).fetchone())
        preview=actor.review_status(tid) if good and actor.active(tid) else {}
        return {"mode":"owner" if good else "agent","revision":int(row["revision"]),
                "lease_id":row["lease_id"] if good else None,"review":preview,
                "expires_at":row["expires_at"] if good else None,
                "actions_used":int(row["actions_used"]),"max_actions":MAX_OWNER_ACTIONS,
                "forms":json.loads(row["forms_json"] or "[]") if good else [],
                "pending_form":json.loads(row["pending_form_json"] or "{}") if good else {}}

def guard_agent(source,mid,tid):
    """Agent proposal and approval APIs must all fail during takeover."""
    info=status(source,mid,tid)
    if info["mode"]=="owner":
        raise mission.MissionError("Agent controls are suspended during owner takeover.",409)

@atomic_write
def acquire(source,mid,tid):
    _,_,session=_session(source,mid,tid)
    with db() as conn:
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
        conn.execute("UPDATE agent_mission_live_browser_v2 SET pending_proposal_json='{}',revision=revision+1 "
                     "WHERE task_id=? AND session_token=?",(tid,session["session_token"]))
        conn.execute("UPDATE agent_mission_browser_controls_v3 SET pending_action_json='{}' "
                     "WHERE task_id=?",(tid,))
        mission._event(conn,mid,"browser.owner_took_control",tid)
    return live.get(source,mid,tid)

@atomic_write
def release(source,mid,tid,*,lease_id=None):
    mission.get_mission(source,mid)
    with db() as conn:
        session=conn.execute("SELECT status FROM agent_mission_live_browser_v2 WHERE task_id=?",(tid,)).fetchone()
        if session and session['status']=='navigating':
            raise mission.MissionError('Wait for the active browser action before handback.',409)
        current=conn.execute("SELECT lease_id FROM agent_mission_browser_takeover_v4 WHERE task_id=? AND mission_id=? AND source_app_key=?",(tid,mid,source)).fetchone()
        if lease_id is not None and (not current or lease_id!=current['lease_id']):
            raise mission.MissionError('This takeover lease has changed.',409)
        update=conn.execute("UPDATE agent_mission_browser_takeover_v4 SET mode='agent',"
                            "lease_id='',expires_at=NULL,pending_form_json='{}',"
                            "revision=revision+1,updated_at=CURRENT_TIMESTAMP "
                            "WHERE task_id=? AND mission_id=? AND source_app_key=? AND mode='owner'",
                            (tid,mid,source))
        if update.rowcount!=1:
            raise mission.MissionError("No active owner takeover found.",409)
        mission._event(conn,mid,"browser.owner_released_control",tid)
    result=live.get(source,mid,tid)
    return result

def _budget(row):
    if int(row["actions_used"])>=MAX_OWNER_ACTIONS:
        raise mission.MissionError("Owner action budget exhausted.",409)

def _request_id(value):
    if value is None: return str(uuid.uuid4())
    try:
        if str(uuid.UUID(value)) != value: raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise mission.MissionError("A canonical operation UUID is required.",422)
    return value


def _hash(payload):
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":")).encode()).hexdigest()


@atomic_write
def _claim(source,mid,tid,request_id,payload_hash,*,lease_id=None,proposal_id=None):
    # All authority, budget and retry decisions share this write transaction.
    live._required(source,mid,tid,executable=True)
    with db() as conn:
        receipt=conn.execute("SELECT * FROM agent_mission_browser_receipts_v4 WHERE task_id=? AND request_id=?",(tid,request_id)).fetchone()
        if receipt:
            if receipt['source_app_key']!=source or receipt['mission_id']!=mid or receipt['payload_hash']!=payload_hash:
                raise mission.MissionError("Operation ID belongs to a different browser action.",409)
            return None,None,None,dict(receipt)
    session,row=_lease(source,mid,tid)
    _budget(row)
    if lease_id is not None and lease_id!=row['lease_id']:
        raise mission.MissionError("Owner control lease changed.",409)
    review=json.loads(row['pending_form_json'] or '{}')
    if proposal_id is not None and (review.get('id')!=proposal_id or review.get('revision')!=row['revision']):
        raise mission.MissionError("Search approval expired or was already used.",409)
    with db() as conn:
        # Reserve a navigation BEFORE sending its one-use command. Failure does
        # not refund a request whose remote outcome might be unknown.
        if proposal_id is not None:
            grant=conn.execute("UPDATE agent_mission_browser_v1 SET visit_count=visit_count+1 WHERE task_id=? AND mission_id=? AND status='approved' AND datetime('now')<datetime(expires_at) AND visit_count<?",(tid,mid,live.MAX_VISITS))
            if grant.rowcount!=1: raise mission.MissionError("Browser navigation budget reached.",409)
        conn.execute("INSERT INTO agent_mission_browser_receipts_v4(task_id,request_id,mission_id,source_app_key,session_token,lease_id,payload_hash,status) VALUES(?,?,?,?,?,?,?,'claimed')",(tid,request_id,mid,source,session['session_token'],row['lease_id'],payload_hash))
        conn.execute("UPDATE agent_mission_browser_takeover_v4 SET actions_used=actions_used+1,revision=revision+1,pending_form_json='{}' WHERE task_id=?",(tid,))
        claimed=conn.execute("UPDATE agent_mission_live_browser_v2 SET status='navigating',pending_proposal_json='{}' WHERE task_id=? AND session_token=? AND status='live'",(tid,session['session_token']))
        if claimed.rowcount!=1: raise mission.MissionError("Browser is busy or changed.",409)
    return session,row,review,None


@atomic_write
def _finish(source,mid,tid,request_id,session,row,result,*,navigation):
    context,_=live._required(source,mid,tid,executable=True)
    if context['task']['status']!='queued': raise mission.MissionError("Worker changed during owner operation.",409)
    with db() as conn:
        valid=conn.execute("SELECT 1 FROM agent_mission_browser_takeover_v4 WHERE task_id=? AND session_token=? AND lease_id=? AND mode='owner' AND datetime('now')<datetime(expires_at)",(tid,session['session_token'],row['lease_id'])).fetchone()
        if not valid: raise mission.MissionError("Owner control changed; browser result discarded.",409)
        live._store_snapshot(conn,tid,mid,session['session_token'],result,status='navigating',navigation=navigation,reserved=navigation)
        conn.execute("UPDATE agent_mission_live_browser_v2 SET status='live',revision=revision+?,link_candidates_json=?,pending_proposal_json='{}' WHERE task_id=? AND session_token=?",(int(not navigation),json.dumps(result.get('links',[])[:24]),tid,session['session_token']))
        conn.execute("UPDATE agent_mission_browser_receipts_v4 SET status='succeeded',updated_at=CURRENT_TIMESTAMP WHERE task_id=? AND request_id=?",(tid,request_id))
        if navigation:
            completed=conn.execute("UPDATE agent_mission_browser_plans_v4 SET status='completed',prepared_form_json='{}',updated_at=CURRENT_TIMESTAMP WHERE task_id=? AND session_token=? AND status='waiting_approval'",(tid,session['session_token']))
            if completed.rowcount:mission._event(conn,mid,'browser.plan_completed',tid,{'owner_submitted':True})
        mission._event(conn,mid,'browser.search_submitted' if navigation else 'browser.owner_controlled',tid,{'operation_id':request_id,'method':'GET' if navigation else 'control'})


def _replay(source,mid,tid,receipt):
    if receipt['status']!='succeeded':
        raise mission.MissionError("This action is pending or its outcome is uncertain. It will not be executed again.",409)
    result=live.get(source,mid,tid)
    result['last_owner_operation']={'id':receipt['request_id'],'status':'succeeded','replayed':True}
    return result


def _failed(tid,request_id,session):
    actor.close_session(tid)
    with db() as conn:
        conn.execute("UPDATE agent_mission_browser_receipts_v4 SET status='uncertain',updated_at=CURRENT_TIMESTAMP WHERE task_id=? AND request_id=? AND status='claimed'",(tid,request_id))
        conn.execute("UPDATE agent_mission_live_browser_v2 SET status='stopped',last_error='Action outcome uncertain; the operation cannot be replayed.' WHERE task_id=? AND session_token=? AND status='navigating'",(tid,session['session_token']))


def manual(source,mid,tid,*,index,fingerprint,kind,value,request_id=None,lease_id=None):
    if kind not in ('fill','check','select','toggle'):
        raise mission.MissionError("Unsupported manual control.",403)
    request_id=_request_id(request_id)
    if type(index) is not int or type(fingerprint) is not str or len(fingerprint)!=24:
        raise mission.MissionError('Invalid control identity.',422)
    if type(value) not in (str,int,bool) or (type(value) is str and (len(value)>300 or any(ord(c)<32 for c in value))):
        raise mission.MissionError('Invalid control value.',422)
    payload={'index':index,'fingerprint':fingerprint,'kind':kind,'value':value}
    session,row,_,receipt=_claim(source,mid,tid,request_id,_hash(payload),lease_id=lease_id)
    if receipt: return _replay(source,mid,tid,receipt)
    try:
        result=actor.execute(tid,'interact',payload)
        _finish(source,mid,tid,request_id,session,row,result,navigation=False)
    except Exception:
        _failed(tid,request_id,session);raise
    return live.get(source,mid,tid)


@atomic_write
def _save_review(source,mid,tid,session,row,preview,proposal_id):
    _lease(source,mid,tid)
    proposal={'id':proposal_id,'index':preview['index'],'fingerprint':preview['fingerprint'],
              'action':preview['action'],'method':'GET','payload_hash':preview['payload_hash'],
              'revision':int(row['revision'])}
    with db() as conn:
        updated=conn.execute("UPDATE agent_mission_browser_takeover_v4 SET pending_form_json=? WHERE task_id=? AND session_token=? AND lease_id=? AND mode='owner' AND revision=? AND datetime('now')<datetime(expires_at)",(json.dumps(proposal),tid,session['session_token'],row['lease_id'],row['revision']))
        if updated.rowcount!=1: raise mission.MissionError("Form changed during review.",409)
        mission._event(conn,mid,'browser.search_reviewed',tid,{'method':'GET','action':preview['action']})


def review_search(source,mid,tid,*,index,fingerprint,lease_id=None):
    session,row=_lease(source,mid,tid);_budget(row)
    if lease_id is not None and lease_id!=row['lease_id']: raise mission.MissionError("Takeover lease changed.",409)
    forms=json.loads(row['forms_json'] or '[]')
    if not any(f['index']==index and f['fingerprint']==fingerprint for f in forms):
        raise mission.MissionError("Search form changed; refresh and review it again.",409)
    proposal_id=str(uuid.uuid4())
    preview=actor.execute(tid,'review_search',{'index':index,'fingerprint':fingerprint,'review_id':proposal_id})
    if not isinstance(preview,dict) or not all(k in preview for k in ('payload_hash','action','index','fingerprint','destination','query')):
        raise mission.MissionError("Browser returned an invalid form review.",502)
    _save_review(source,mid,tid,session,row,preview,proposal_id)
    result=live.get(source,mid,tid)
    result['owner_takeover']['review']=preview
    return result


def submit_search(source,mid,tid,*,proposal_id,request_id=None,lease_id=None):
    request_id=_request_id(request_id or proposal_id)
    session,row,review,receipt=_claim(source,mid,tid,request_id,_hash({'proposal_id':proposal_id}),lease_id=lease_id,proposal_id=proposal_id)
    if receipt: return _replay(source,mid,tid,receipt)
    try:
        result=actor.execute(tid,'search_get',{'index':review['index'],'fingerprint':review['fingerprint'],'review_id':proposal_id,'payload_hash':review['payload_hash']})
        _finish(source,mid,tid,request_id,session,row,result,navigation=True)
    except Exception:
        _failed(tid,request_id,session);raise
    return live.get(source,mid,tid)
