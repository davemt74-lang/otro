"""Reviewed recurring specialist plans; each run retains separate edit approval."""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..database import atomic_write, db
from . import agent_mission_runtime as mission, agent_mission_tool_contracts as contracts
from . import agent_mission_orchestration as orchestration, app_scopes, context_engine

_stop = threading.Event()
_thread = None
_guard = threading.Lock()
MAX_ACTIVE = 20


def _uuid(value):
    try:
        if type(value) is not str or str(uuid.UUID(value)) != value: raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise mission.MissionError('Canonical schedule operation UUID required.', 422) from None
    return value


def _now():
    return datetime.now(timezone.utc)


def _stamp(value):
    return value.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _source(source):
    if source == 'owner': return
    with db() as conn:
        row = conn.execute("SELECT 1 FROM paired_apps a JOIN app_permissions p ON p.paired_app_id=a.id WHERE a.app_key=? AND a.status='active' AND p.permission='agent.chat' AND p.allowed=1", (source.removeprefix('app:'),)).fetchone()
    if not source.startswith('app:') or not row:
        raise mission.MissionError('Schedule application permission was revoked.', 403)


def _visible(source, template):
    return source == 'owner' or bool(app_scopes.get_scope_for_source(source).get('cloud_allowed', False)
        and context_engine.get_settings(template['conversation_id']).get('cloud_allowed', False))


def validate_timing(value):
    keys = {'frequency','weekday','hour','minute','timezone'}
    if not isinstance(value, dict) or set(value) != keys:
        raise mission.MissionError('Choose frequency, weekday, local time and timezone.', 422)
    if value['frequency'] not in ('daily','weekly'):
        raise mission.MissionError('Schedule must be daily or weekly.', 422)
    for key, maximum in [('weekday',6),('hour',23),('minute',59)]:
        if type(value[key]) is not int or not 0 <= value[key] <= maximum:
            raise mission.MissionError('Schedule local time is invalid.', 422)
    if type(value['timezone']) is not str or len(value['timezone']) > 80:
        raise mission.MissionError('Choose an IANA timezone.', 422)
    try: ZoneInfo(value['timezone'])
    except (ZoneInfoNotFoundError, ValueError):
        raise mission.MissionError('Unknown schedule timezone.', 422) from None
    return dict(value)


def next_due(timing, after):
    """One slot per local date: first fold only; nonexistent wall times are skipped."""
    zone = ZoneInfo(timing['timezone'])
    local = after.astimezone(zone)
    for offset in range(15):
        day = local.date() + timedelta(days=offset)
        if timing['frequency'] == 'weekly' and day.weekday() != timing['weekday']: continue
        candidate = datetime(day.year,day.month,day.day,timing['hour'],timing['minute'],tzinfo=zone,fold=0)
        utc = candidate.astimezone(timezone.utc)
        if utc.astimezone(zone).replace(tzinfo=None) != candidate.replace(tzinfo=None): continue
        if utc > after: return _stamp(utc)
    raise mission.MissionError('No valid schedule slot found.', 422)


def _lookup(source, sid, *, local_owner=False):
    if not local_owner: _source(source)
    with db() as conn:
        row = conn.execute('SELECT * FROM agent_mission_schedules_v1 WHERE id=? AND source_app_key=?', (sid,source)).fetchone()
    if not row: raise mission.MissionError('Schedule not found for this source.', 404)
    return dict(row)


def get(source, sid, *, local_owner=False):
    row = _lookup(source, sid, local_owner=local_owner); template = json.loads(row['template_json'])
    visible = local_owner or _visible(source, template)
    with db() as conn:
        runs = [dict(r) for r in conn.execute('SELECT r.*,m.status AS mission_status,m.updated_at AS mission_updated_at,m.completed_at FROM agent_mission_schedule_runs_v1 r LEFT JOIN agent_missions_v1 m ON m.id=r.mission_id WHERE r.schedule_id=? ORDER BY r.due_at DESC LIMIT 10', (sid,))]
    return {k:row[k] for k in ('id','template_mission_id','timezone','frequency','weekday','hour','minute','status','revision','next_run_at','last_run_at','created_at','updated_at')} | {
        'objective':template['objective'] if visible else 'Private HomeServer schedule',
        'last_error':row['last_error'] if visible else '', 'private':not visible,
        'runs':runs if visible else [], 'requires_edit_approval':True,
        'overlap_policy':'skip while work or changes await completion; no catch-up burst'}


def owner_list():
    with db() as conn:
        rows=[dict(r) for r in conn.execute('SELECT id,source_app_key FROM agent_mission_schedules_v1 ORDER BY created_at DESC,id DESC LIMIT 40')]
    return [get(row['source_app_key'],row['id'],local_owner=True) for row in rows]


def list_schedules(source):
    _source(source)
    with db() as conn:
        ids = [r[0] for r in conn.execute('SELECT id FROM agent_mission_schedules_v1 WHERE source_app_key=? ORDER BY created_at DESC,id DESC LIMIT 40', (source,))]
    return [get(source, sid) for sid in ids]


@atomic_write
def create(source, mid, timing, *, request_id, expected_revision, confirmed):
    _source(source); _uuid(request_id); timing = validate_timing(timing)
    if confirmed is not True or type(expected_revision) is not int or expected_revision < 1:
        raise mission.MissionError('Confirm the reviewed plan and its current revision.', 422)
    payload_hash = orchestration._hash({'mission_id':mid,'timing':timing,'revision':expected_revision})
    with db() as conn:
        prior = conn.execute('SELECT id,payload_hash FROM agent_mission_schedules_v1 WHERE source_app_key=? AND request_id=?', (source,request_id)).fetchone()
        if prior:
            if prior['payload_hash'] != payload_hash: raise mission.MissionError('Schedule request already belongs to another plan.', 409)
            return get(source, prior['id'])
        if conn.execute("SELECT COUNT(*) FROM agent_mission_schedules_v1 WHERE source_app_key=? AND status!='cancelled'", (source,)).fetchone()[0] >= MAX_ACTIVE:
            raise mission.MissionError('Cancel a schedule before adding another; limit is 20.', 409)
    snapshot = mission.get_mission(source, mid)
    contract, value = orchestration._contract(source, snapshot)
    if contract['revision'] != expected_revision or snapshot['status'] != 'planned' or not snapshot.get('chat_task'):
        raise mission.MissionError('Review a newly prepared Chat task before scheduling.', 409)
    if not _visible(source, snapshot): raise mission.MissionError('Private schedules stay on HomeServer.', 403)
    if any('browser.read' in a['tools'] for a in value['assignments']):
        raise mission.MissionError('Recurring browser grants require per-run review; remove browser access.', 422)
    ids = {t['id']:i for i,t in enumerate(snapshot['tasks'])}
    tasks = [{'role':t['role'],'title':t['title'],'objective':t['objective'],'instructions':t['instructions'],
              'depends_on':[ids[d] for d in t['depends_on']]} for t in snapshot['tasks']]
    draft = json.loads(json.dumps(value))
    for entry in draft['assignments']: entry['task_id'] = str(ids[entry['task_id']])
    template = {'conversation_id':snapshot['conversation_id'],'parent_agent_id':snapshot['parent_agent_id'],
                'objective':snapshot['objective'],'tasks':tasks,'draft':draft}
    sid = str(uuid.uuid4()); now = _now()
    with db() as conn:
        conn.execute('INSERT INTO agent_mission_schedules_v1(id,source_app_key,template_mission_id,request_id,payload_hash,template_json,authority_hash,timezone,frequency,weekday,hour,minute,next_run_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (sid,source,mid,request_id,payload_hash,json.dumps(template),orchestration.authority_hash(source,snapshot),timing['timezone'],timing['frequency'],timing['weekday'],timing['hour'],timing['minute'],next_due(timing,now)))
        mission._event(conn,mid,'schedule.created',detail={'schedule_id':sid,'timezone':timing['timezone'],'requires_edit_approval':True})
    return get(source,sid)


@atomic_write
def change(source, sid, action, *, request_id, expected_revision, confirmed, local_owner=False):
    _uuid(request_id); row = _lookup(source,sid,local_owner=local_owner)
    if confirmed is not True or type(expected_revision) is not int or action not in ('pause','resume','cancel'):
        raise mission.MissionError('Confirm the current schedule operation.', 422)
    digest = orchestration._hash({'id':sid,'action':action,'revision':expected_revision})
    with db() as conn:
        prior = conn.execute('SELECT * FROM agent_mission_schedule_requests_v1 WHERE source_app_key=? AND request_id=?', (source,request_id)).fetchone()
        if prior:
            if prior['schedule_id'] != sid or prior['payload_hash'] != digest: raise mission.MissionError('Operation request belongs to another schedule.',409)
            return get(source,sid,local_owner=local_owner)
        if row['revision'] != expected_revision: raise mission.MissionError('Schedule changed; refresh and review it.',409)
        if row['status'] == 'cancelled' or (action == 'resume' and row['status'] != 'paused'):
            raise mission.MissionError('Schedule cannot be resumed; prepare a new reviewed plan.',409)
        if action == 'resume':
            snapshot = mission.get_mission(source,row['template_mission_id'])
            if orchestration.authority_hash(source,snapshot) != row['authority_hash'] or not _visible(source,snapshot):
                raise mission.MissionError('Schedule authority changed; prepare and review a new plan.',403)
        state = {'pause':'paused','resume':'active','cancel':'cancelled'}[action]
        next_run = next_due(row,_now()) if action == 'resume' else row['next_run_at']
        conn.execute('UPDATE agent_mission_schedules_v1 SET status=?,revision=revision+1,next_run_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?', (state,next_run,sid))
        conn.execute('INSERT INTO agent_mission_schedule_requests_v1 VALUES(?,?,?,?)', (source,request_id,sid,digest))
        mission._event(conn,row['template_mission_id'],'schedule.'+state,detail={'schedule_id':sid,'current_run_unchanged':True})
    return get(source,sid,local_owner=local_owner)


@atomic_write
def _launch(sid, now):
    with db() as conn:
        row = conn.execute("SELECT * FROM agent_mission_schedules_v1 WHERE id=? AND status='active' AND next_run_at<=?", (sid,_stamp(now))).fetchone()
        if not row: return None
        row = dict(row); source = row['source_app_key']; due = row['next_run_at']
        rid = str(uuid.uuid5(uuid.NAMESPACE_URL,'schedule:'+sid+':'+due))
        if conn.execute('SELECT 1 FROM agent_mission_schedule_runs_v1 WHERE schedule_id=? AND due_at=?', (sid,due)).fetchone(): return None
        snapshot = mission.get_mission(source,row['template_mission_id'])
        if orchestration.authority_hash(source,snapshot) != row['authority_hash'] or not _visible(source,snapshot):
            raise mission.MissionError('Current specialist authority changed. Prepare a new reviewed schedule.',403)
        previous = conn.execute('SELECT mission_id FROM agent_mission_schedule_runs_v1 WHERE schedule_id=? AND mission_id IS NOT NULL ORDER BY due_at DESC LIMIT 1', (sid,)).fetchone()
        if not previous and snapshot['status'] != 'planned': previous = {'mission_id':snapshot['id']}
        if previous:
            previous_mission = mission.get_mission(source,previous['mission_id'])
            waiting = previous_mission['status'] in ('planned','running','waiting_review') or any(a['outcome_state'] in ('awaiting_review','queued','sending','applied','unverified','blocked') for a in previous_mission.get('action_summaries',[]))
            if waiting:
                conn.execute("INSERT INTO agent_mission_schedule_runs_v1(id,schedule_id,due_at,status,reason) VALUES(?,?,?,'skipped','Previous run or changes still awaiting completion')", (rid,sid,due))
                conn.execute('UPDATE agent_mission_schedules_v1 SET next_run_at=?,last_run_at=?,last_error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?', (next_due(row,now),_stamp(now),'Previous run or changes still awaiting completion',sid))
                return None
        template = json.loads(row['template_json'])
        provider,model,_ = mission._route(source,template['conversation_id'])
        created = mission.create_mission(source,conversation_id=template['conversation_id'],objective=template['objective'],client_request_id=rid,parent_agent_id=template['parent_agent_id'],owner=source=='owner',tasks=template['tasks'],preparation={'draft':template['draft'],'provider_key':provider,'model':model})
        mid = created['id']
        approved = contracts.configure(source,mid,created['chat_task']['draft'],request_id=str(uuid.uuid5(uuid.UUID(rid),'assign')),expected_revision=0,confirmed=True)
        orchestration._start(source,mid,str(uuid.uuid5(uuid.UUID(rid),'start')),approved['revision'],True)
        conn.execute("INSERT INTO agent_mission_schedule_runs_v1(id,schedule_id,due_at,mission_id,status) VALUES(?,?,?,?,'started')", (rid,sid,due,mid))
        conn.execute('UPDATE agent_mission_schedules_v1 SET next_run_at=?,last_run_at=?,last_error=\'\',updated_at=CURRENT_TIMESTAMP WHERE id=?', (next_due(row,now),_stamp(now),sid))
        mission._event(conn,mid,'schedule.run_started',detail={'schedule_id':sid,'due_at':due,'missed_slots':'coalesced','requires_edit_approval':True})
        return mid


def tick():
    now = _now()
    with db() as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM agent_mission_schedules_v1 WHERE status='active' AND next_run_at<=? ORDER BY next_run_at LIMIT 20", (_stamp(now),))]
    for sid in ids:
        try:
            mid = _launch(sid,now)
            if mid: mission._dispatch(mid)
        except Exception:
            # Never retry inference or worker writes automatically after an interruption.
            with db() as conn:
                conn.execute('BEGIN IMMEDIATE')
                row=conn.execute("SELECT next_run_at FROM agent_mission_schedules_v1 WHERE id=? AND status='active' AND next_run_at<=?",(sid,_stamp(now))).fetchone()
                if row:
                    rid=str(uuid.uuid5(uuid.NAMESPACE_URL,'schedule:'+sid+':'+row['next_run_at']))
                    conn.execute("INSERT OR IGNORE INTO agent_mission_schedule_runs_v1(id,schedule_id,due_at,status,reason) VALUES(?,?,?,'blocked','Current authority or preparation requires review')",(rid,sid,row['next_run_at']))
                    conn.execute("UPDATE agent_mission_schedules_v1 SET status='blocked',revision=revision+1,last_error='Current authority or run preparation needs review. Prepare a new schedule.',updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='active'", (sid,))


def start():
    global _thread
    with _guard:
        if _thread and _thread.is_alive(): return
        _stop.clear()
        def run():
            while not _stop.is_set():
                try: tick()
                except Exception: pass  # A transient database failure leaves the durable slot intact.
                _stop.wait(30)
        _thread = threading.Thread(target=run,name='specialist-schedules',daemon=True); _thread.start()


def stop():
    _stop.set()
    if _thread: _thread.join(timeout=5)
