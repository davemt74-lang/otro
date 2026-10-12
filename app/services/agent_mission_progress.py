"""Observable progress, fail-closed stall detection and durable mission notices."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

from ..database import db, reuse_write_transaction, atomic_write

STALL_SECONDS = 300
STAGES = {'started': 'Starting specialist', 'choosing_read': 'Choosing a read',
          'reading': 'Reading assigned data', 'composing': 'Preparing findings'}
NOTICES = {
 'chat.command_proposed': ('Mission command ready for review','action_required'),
 'chat.scope_prepared': ('Revised specialist plan ready for review','action_required'),
 'mission.planned': ('Specialist plan ready for review', 'action_required'),
 'mission.started': ('Specialists started', 'info'),
 'orchestration.started': ('Specialists started', 'info'),
 'mission.resumed': ('Specialists resumed', 'info'),
 'mission.retry_authorized': ('Specialist retry started', 'info'),
 'worker.action_proposed': ('Specialist change needs approval', 'action_required'),
 'worker.action_failed': ('Specialist change needs attention', 'error'),
 'mission.completed': ('Specialist research completed', 'success'),
 'mission.partial': ('Specialist research partially completed', 'warning'),
 'mission.failed': ('Specialist mission failed', 'error'),
 'mission.cancelled': ('Specialist mission cancelled', 'info'),
 'mission.paused': ('Specialist mission paused', 'info'),
 'mission.recovery_review_required': ('Interrupted mission needs review', 'action_required'),
 'mission.runtime_review_required': ('Interrupted mission needs review', 'action_required'),
 'orchestration.authority_blocked': ('Mission permissions need review', 'action_required'),
}
_stop = threading.Event()
_thread = None
_guard = threading.Lock()
_log = logging.getLogger(__name__)


def _age(value):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return max(0, (datetime.now(timezone.utc) - dt.replace(tzinfo=timezone.utc)).total_seconds())
    except (ValueError, TypeError):
        return None


def record(conn, event_id, mid, kind, tid, detail):
    """Join the lifecycle transaction: no notification can precede its event."""
    row = conn.execute('SELECT source_app_key FROM agent_missions_v1 WHERE id=?', (mid,)).fetchone()
    if not row:
        return
    if tid and kind in ('task.started', 'worker.read_claimed', 'worker.read_completed', 'worker.progress'):
        task = conn.execute("SELECT lease_id FROM agent_mission_tasks_v1 WHERE id=? AND mission_id=? AND status='running'", (tid, mid)).fetchone()
        if task and task['lease_id']:
            stage = ('started' if kind == 'task.started' else 'reading' if kind == 'worker.read_claimed' else
                     'composing' if kind == 'worker.read_completed' else detail.get('stage'))
            if stage in STAGES:
                conn.execute('INSERT INTO agent_mission_progress_v1(task_id,lease_id,stage) VALUES(?,?,?) '
                             'ON CONFLICT(task_id) DO UPDATE SET lease_id=excluded.lease_id,stage=excluded.stage,updated_at=CURRENT_TIMESTAMP',
                             (tid, task['lease_id'], stage))
    if kind in ('mission.started', 'orchestration.started', 'mission.resumed', 'mission.retry_authorized', 'mission.cancelled'):
        conn.execute("UPDATE agent_mission_notices_v1 SET resolved_at=CURRENT_TIMESTAMP WHERE mission_id=? AND resolved_at IS NULL AND kind IN ('mission.planned','chat.scope_prepared','mission.paused','mission.recovery_review_required','mission.runtime_review_required','orchestration.authority_blocked')", (mid,))
    if kind in ('chat.command_applied','chat.command_dismissed') and detail.get('request_id'):
        conn.execute("UPDATE agent_mission_notices_v1 SET resolved_at=CURRENT_TIMESTAMP WHERE mission_id=? AND kind='chat.command_proposed' AND resolved_at IS NULL AND event_id IN (SELECT id FROM agent_mission_events_v1 WHERE mission_id=? AND json_extract(metadata_json,'$.request_id')=?)",(mid,mid,detail['request_id']))
    if (kind in ('worker.action_approved','worker.action_denied','worker.action_failed','worker.action_outcome')
            and detail.get('action_id') and not (kind == 'worker.action_outcome' and detail.get('state') == 'awaiting_review')):
        conn.execute("UPDATE agent_mission_notices_v1 SET resolved_at=CURRENT_TIMESTAMP WHERE mission_id=? AND kind='worker.action_proposed' AND resolved_at IS NULL AND event_id IN (SELECT id FROM agent_mission_events_v1 WHERE mission_id=? AND json_extract(metadata_json,'$.action_id')=?)", (mid,mid,detail['action_id']))
    if kind == 'worker.action_outcome' and detail.get('action_id'):
        conn.execute("UPDATE agent_mission_notices_v1 SET resolved_at=CURRENT_TIMESTAMP WHERE mission_id=? AND kind='worker.action_outcome' AND resolved_at IS NULL AND event_id IN (SELECT id FROM agent_mission_events_v1 WHERE mission_id=? AND json_extract(metadata_json,'$.action_id')=?)", (mid,mid,detail['action_id']))
    if kind in ('mission.cancelled', 'mission.failed', 'chat.scope_superseded'):
        conn.execute("UPDATE agent_mission_notices_v1 SET resolved_at=CURRENT_TIMESTAMP WHERE mission_id=? AND kind IN ('worker.action_proposed','chat.command_proposed') AND resolved_at IS NULL", (mid,))
    conn.execute('UPDATE notifications SET dismissed_at=COALESCE(dismissed_at,CURRENT_TIMESTAMP) WHERE id IN (SELECT notification_id FROM agent_mission_notices_v1 WHERE mission_id=? AND resolved_at IS NOT NULL)', (mid,))
    if kind == 'worker.action_outcome' and detail.get('state') == 'verified':
        title, level = 'Specialist change saved and verified', 'success'
    elif kind == 'worker.action_outcome' and detail.get('state') in ('conflict', 'blocked', 'failed', 'superseded', 'missing', 'unverified', 'expired'):
        title, level = 'Specialist change needs verification review', 'action_required'
    elif kind in NOTICES:
        title, level = NOTICES[kind]
        if kind == 'mission.runtime_review_required' and detail.get('reason') == 'worker_stalled':
            title = 'Stalled specialist needs review'
    else:
        return
    notification_id = None
    if row['source_app_key'] == 'owner':
        from . import activity_center
        with reuse_write_transaction():
            notice = activity_center.emit_notification(source='agent-missions', source_key='agent-missions',
                source_kind='agent', category='agent', title=title, level=level,
                body='Open the mission in Agent Chat. Research completion and saved changes are reported separately.',
                event_key=kind, dedupe_key='mission-event:' + str(event_id),
                action_payload={'type': 'mission', 'mission_id': mid})
        notification_id = notice.get('id')
    conn.execute('INSERT INTO agent_mission_notices_v1(event_id,mission_id,source_app_key,kind,title,level,notification_id) VALUES(?,?,?,?,?,?,?)',
                 (event_id, mid, row['source_app_key'], kind, title, level, notification_id))


def touch(mid, tid, lease, stage):
    from . import agent_mission_runtime as mission
    if stage not in STAGES:
        raise ValueError('Unknown specialist stage')
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        current = conn.execute("SELECT 1 FROM agent_mission_tasks_v1 t JOIN agent_missions_v1 m ON m.id=t.mission_id WHERE t.id=? AND t.mission_id=? AND t.lease_id=? AND t.status='running' AND m.status='running'", (tid, mid, lease)).fetchone()
        if not current:
            raise mission.MissionError('Worker lease was interrupted or revoked.', 409)
        mission._event(conn, mid, 'worker.progress', tid, {'stage': stage})


def attach(snapshot):
    with db() as conn:
        progress = {r['task_id']: dict(r) for r in conn.execute('SELECT p.* FROM agent_mission_progress_v1 p JOIN agent_mission_tasks_v1 t ON t.id=p.task_id WHERE t.mission_id=?', (snapshot['id'],))}
    stalled = False
    for task in snapshot['tasks']:
        item = progress.get(task['id'], {})
        last = task.get('completed_at') or item.get('updated_at') or task.get('started_at') or task.get('created_at')
        overdue = task['status'] == 'running' and (_age(last) is None or _age(last) >= STALL_SECONDS)
        stalled |= overdue
        task['activity'] = {'state': 'stalled' if overdue else task['status'],
                            'stage': STAGES.get(item.get('stage'), 'Working') if task['status']=='running' else ('Waiting for a prerequisite' if task['status']=='queued' and task.get('depends_on') else task['status'].replace('_',' ').capitalize()),
                            'last_activity_at': last, 'stall_after_seconds': STALL_SECONDS}
    state = snapshot['status']
    if not snapshot.get('authority_current', True): state = 'blocked'
    elif stalled: state = 'stalled'
    elif state == 'waiting_review':
        event = next((e for e in reversed(snapshot.get('events', [])) if e['kind'] in ('mission.paused','mission.recovery_review_required','mission.runtime_review_required')), {})
        state = 'stalled' if event.get('metadata', {}).get('reason') == 'worker_stalled' else 'interrupted'
    elif (snapshot.get('completion_report') or {}).get('state') == 'awaiting_review': state = 'awaiting_approval'
    elif state in ('completed', 'partial') and (snapshot.get('completion_report') or {}).get('state') in ('awaiting_delivery', 'attention'):
        state = snapshot['completion_report']['state']
    elif state == 'planned': state = 'awaiting_review'
    snapshot['progress'] = {'state': state, 'checked_at': datetime.now(timezone.utc).isoformat(),
                            'completed_workers': sum(t['status']=='completed' for t in snapshot['tasks']),
                            'total_workers': len(snapshot['tasks']), 'automatic_replay': False}
    return snapshot


def sweep():
    """Only a matched, still-overdue lease may be fenced. Never replay it."""
    from . import agent_mission_runtime as mission
    cutoff = (datetime.now(timezone.utc)-timedelta(seconds=STALL_SECONDS)).strftime('%Y-%m-%d %H:%M:%S')
    with db() as conn:
        rows = conn.execute("SELECT t.id,t.mission_id,t.lease_id FROM agent_mission_tasks_v1 t JOIN agent_missions_v1 m ON m.id=t.mission_id LEFT JOIN agent_mission_progress_v1 p ON p.task_id=t.id AND p.lease_id=t.lease_id WHERE t.status='running' AND m.status='running' AND COALESCE(p.updated_at,t.started_at,t.created_at)<=? LIMIT 100", (cutoff,)).fetchall()
    return sum(mission.interrupt_for_review(r['mission_id'], 'worker_stalled', task_id=r['id'], lease=r['lease_id'], stale_before=cutoff) for r in rows)


def _allowed(source, snapshot):
    if source == 'owner': return True
    from . import app_scopes, context_engine
    return bool(snapshot.get('authority_current', True) and app_scopes.get_scope_for_source(source).get('cloud_allowed',False)
                and context_engine.get_settings(snapshot['conversation_id']).get('cloud_allowed',False))


def notifications(source, *, owner=False):
    from . import agent_mission_runtime as mission, agent_mission_schedules as schedules
    if not owner: schedules._source(source)
    with db() as conn:
        rows = conn.execute("SELECT n.*,COALESCE(n.read_at,x.read_at,x.dismissed_at,x.archived_at) AS effective_read_at FROM agent_mission_notices_v1 n LEFT JOIN notifications x ON x.id=n.notification_id " +
                            ('' if owner else 'WHERE n.source_app_key=? ') + 'ORDER BY CASE WHEN COALESCE(n.read_at,x.read_at,n.resolved_at,x.dismissed_at,x.archived_at) IS NULL THEN 0 ELSE 1 END,n.id DESC LIMIT 200', () if owner else (source,)).fetchall()
    items = []; cached = {}
    for row in rows:
        mid = row['mission_id']
        if mid not in cached: cached[mid] = mission.get_mission(row['source_app_key'], mid)
        if not owner and not _allowed(source, cached[mid]): continue
        items.append({'id': row['id'], 'mission_id': mid, 'title': row['title'], 'kind': row['kind'],
                      'level': row['level'], 'created_at': row['created_at'], 'read_at': row['effective_read_at'],
                      'resolved_at': row['resolved_at']})
    return {'items': sorted(items,key=lambda n:(bool(n['read_at'] or n['resolved_at']),-n['id']))[:50], 'unread': sum(not n['read_at'] and not n['resolved_at'] for n in items),
            'has_more': len(items)>50, 'count_is_lower_bound':len(rows)==200 and all(not r['effective_read_at'] and not r['resolved_at'] for r in rows), 'checked_at': datetime.now(timezone.utc).isoformat()}


@atomic_write
def mark_read(source, notice_id, *, owner=False):
    from . import agent_mission_runtime as mission, agent_mission_schedules as schedules
    if type(notice_id) is not int or notice_id < 1: raise mission.MissionError('Notification ID is invalid.', 422)
    if not owner: schedules._source(source)
    with db() as conn:
        row = conn.execute('SELECT * FROM agent_mission_notices_v1 WHERE id=?'+('' if owner else ' AND source_app_key=?'), (notice_id,) if owner else (notice_id,source)).fetchone()
        if not row: raise mission.MissionError('Notification not found.',404)
        snapshot = mission.get_mission(row['source_app_key'],row['mission_id'])
        if not owner and not _allowed(source,snapshot): raise mission.MissionError('Private notifications stay on HomeServer.',403)
        conn.execute('UPDATE agent_mission_notices_v1 SET read_at=COALESCE(read_at,CURRENT_TIMESTAMP) WHERE id=?',(notice_id,))
        if row['notification_id']:
            conn.execute('UPDATE notifications SET read_at=COALESCE(read_at,CURRENT_TIMESTAMP) WHERE id=?',(row['notification_id'],))
    return notifications(source,owner=owner)


def feed(source, *, owner=False):
    from . import agent_mission_runtime as mission, agent_mission_schedules as schedules
    if not owner: schedules._source(source)
    with db() as conn:
        rows = conn.execute('SELECT id,source_app_key FROM agent_missions_v1 '+('' if owner else 'WHERE source_app_key=? ')+
                            "ORDER BY CASE WHEN status IN ('running','waiting_review') THEN 0 ELSE 1 END,created_at DESC,id DESC LIMIT 8", () if owner else (source,)).fetchall()
    return {'items': [mission.get_mission(r['source_app_key'],r['id']) for r in rows],
            'notifications': notifications(source,owner=owner), 'checked_at': datetime.now(timezone.utc).isoformat()}


def start():
    global _thread
    with _guard:
        if _thread and _thread.is_alive(): return
        _stop.clear()
        def run():
            while not _stop.is_set():
                try: sweep()
                except Exception: _log.warning('Mission progress watchdog needs attention')
                _stop.wait(15)
        _thread = threading.Thread(target=run,name='mission-progress',daemon=True);_thread.start()


def stop():
    _stop.set()
    if _thread: _thread.join(timeout=5)
