"""Specialists prepare immutable changes; the existing approval executor applies them."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime,timezone

from ..database import atomic_write, db
from . import agent_mission_runtime as mission, agent_mission_tool_contracts as contracts
from . import approvals, agent_tools, tools, tool_authority, app_scopes, action_policy
from .https_bridge_session import SESSION_LOCK

ACTION_TOOLS = ('contacts.create', 'contacts.update', 'knowledge.create', 'knowledge.update',
                'tasks.create', 'tasks.update', 'calendar.create', 'calendar.update', 'workspace.update')
CREATORS = {
    'contacts.create': approvals.create_contact_create_request,
    'contacts.update': approvals.create_contact_update_request,
    'knowledge.create': approvals.create_knowledge_create_request,
    'knowledge.update': approvals.create_knowledge_update_request,
    'tasks.create': approvals.create_task_create_request,
    'tasks.update': approvals.create_task_update_request,
    'calendar.create': approvals.create_calendar_create_request,
    'calendar.update': approvals.create_calendar_update_request,
    'workspace.update': approvals.create_workspace_update_request,
}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def capabilities(source, snapshot):
    permissions, owner = contracts.authority(source, snapshot)
    policy = agent_tools.get_policy()
    if not policy['enabled'] or not policy['allow_write_proposals']:
        return []
    scope = app_scopes.get_scope_for_source(source) if not owner else {}
    available = {t['key'] for t in tools.list_tools(permissions, owner=owner) if t['available'] and t['mode'] == 'write'}
    return [key for key in ACTION_TOOLS if key in available and (owner or
            (app_scopes.tool_allowed(scope, key) and action_policy.resolve_policy_for_source(source, key)['policy_mode'] != action_policy.SENSITIVE_HIGH_IMPACT))]


def schemas(assignment):
    result = {}
    for key in assignment.get('actions', []):
        schema = json.loads(json.dumps(tools.TOOL_DEFINITIONS[key]['input_schema']))
        schema['properties'].pop('mutation_id', None)
        schema['required'] = [k for k in schema.get('required', []) if k != 'mutation_id']
        result[key] = schema
    return result


def validate(value, assignment):
    if not isinstance(value, list) or len(value) > assignment.get('max_actions', 0):
        raise mission.MissionError('Specialist action budget exceeded.', 502)
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {'tool', 'arguments'}:
            raise mission.MissionError('Invalid specialist change structure.', 502)
        key, args = entry['tool'], entry['arguments']
        if type(key) is not str or key not in assignment.get('actions', []):
            raise mission.MissionError('Specialist requested an unassigned action.', 403)
        if not isinstance(args, dict) or set(args) - schemas(assignment)[key]['properties'].keys():
            raise mission.MissionError('Change arguments include unsupported fields.', 502)
        if len(json.dumps(args, ensure_ascii=False).encode()) > 12000:
            raise mission.MissionError('Keep each specialist change under 12 KB.', 502)
    return value


def linked(approval_id):
    with db() as conn:
        return conn.execute('SELECT 1 FROM agent_mission_actions_v1 WHERE approval_id=?', (approval_id,)).fetchone() is not None


def stage(source, mid, tid, lease, entries):
    """Called only in the task-result transaction, with SESSION_LOCK held first."""
    from . import agent_mission_orchestration as run
    snapshot, assignment = run.check(source, mid, tid, lease)
    validate(entries, assignment)
    permissions, owner = contracts.authority(source, snapshot)
    with db() as conn:
        review = conn.execute('SELECT revision,expires_at FROM agent_mission_tool_contracts_v1 WHERE mission_id=?', (mid,)).fetchone()
        used = conn.execute('SELECT COUNT(*) FROM agent_mission_actions_v1 WHERE task_id=?', (tid,)).fetchone()[0]
        if used + len(entries) > assignment.get('max_actions', 0):
            raise mission.MissionError('Cumulative specialist action budget exhausted.', 409)
        for ordinal, entry in enumerate(entries):
            key = entry['tool']
            if key not in capabilities(source, snapshot):
                raise mission.MissionError('Specialist action permission changed.', 403)
            args = dict(entry['arguments'])
            args['mutation_id'] = str(uuid.uuid5(uuid.NAMESPACE_URL, 'mission-action:' + tid + ':' + str(ordinal)))
            if not owner:
                tool_authority.require_current_app(source, key, args, permissions, approval=True)
            kwargs = {'owner': owner}
            if key == 'tasks.create':
                kwargs['created_by_type'] = 'agent'
            created = CREATORS[key](source, args, **kwargs)
            rid = created['result']['request_id']
            row = conn.execute('SELECT arguments_json FROM action_requests WHERE id=?', (rid,)).fetchone()
            digest = fingerprint(json.loads(row['arguments_json']))
            aid = str(uuid.uuid4())
            expiry=datetime.fromisoformat(review['expires_at']).replace(tzinfo=timezone.utc).isoformat()
            conn.execute('UPDATE action_requests SET expires_at=? WHERE id=?', (expiry, rid))
            conn.execute('INSERT INTO agent_mission_actions_v1(id,mission_id,task_id,source_app_key,ordinal,action_key,approval_id,payload_hash,contract_revision) VALUES(?,?,?,?,?,?,?,?,?)',
                         (aid, mid, tid, source, ordinal, key, rid, digest, review['revision']))
            mission._event(conn, mid, 'worker.action_proposed', tid, {'action_id': aid, 'tool': key, 'approval_id': rid})


def authorize(approval_id):
    """Fresh mission fences apply even through generic or federated approval routes."""
    from . import agent_mission_orchestration as run
    with db() as conn:
        row = conn.execute('SELECT a.*,r.arguments_json,r.action_key AS request_tool,r.source_app_key AS request_source FROM agent_mission_actions_v1 a JOIN action_requests r ON r.id=a.approval_id WHERE a.approval_id=?', (approval_id,)).fetchone()
    if not row:
        return
    try:
        snapshot, value = run.check(row['source_app_key'], row['mission_id'])
        task = next(t for t in snapshot['tasks'] if t['id'] == row['task_id'])
        assignment = next(a for a in value['assignments'] if a['task_id'] == row['task_id'])
        with db() as conn:
            revision = conn.execute('SELECT revision FROM agent_mission_tool_contracts_v1 WHERE mission_id=?', (row['mission_id'],)).fetchone()[0]
        if (snapshot['status'] not in ('running', 'completed', 'partial') or task['status'] != 'completed'
                or revision != row['contract_revision'] or row['action_key'] not in assignment.get('actions', [])
                or row['action_key'] not in capabilities(row['source_app_key'], snapshot)
                or row['request_tool'] != row['action_key'] or row['request_source'] != row['source_app_key']
                or fingerprint(json.loads(row['arguments_json'])) != row['payload_hash']):
            raise mission.MissionError('Specialist change is no longer authorized.', 403)
    except (mission.MissionError, StopIteration, ValueError) as exc:
        raise approvals.ApprovalError('Specialist review expired, changed or was interrupted. Prepare a new mission.', 403) from exc


def list_actions(source, mid):
    snapshot = mission.get_mission(source, mid)
    contracts.authority(source, snapshot)
    if not snapshot['authority_current']:
        return []
    approvals._expire_pending()
    with db() as conn:
        rows = conn.execute('SELECT a.*,r.status,r.arguments_json,r.expires_at,r.decided_at,r.executed_at,r.execution_tool_run_id FROM agent_mission_actions_v1 a JOIN action_requests r ON r.id=a.approval_id WHERE a.mission_id=? AND a.source_app_key=? ORDER BY a.created_at,a.task_id,a.ordinal', (mid, source)).fetchall()
    outcome_map = {item['id']: item for item in snapshot.get('action_summaries', [])}
    output = []
    for row in rows:
        actionable = row['status'] == 'pending'
        if actionable:
            try:
                authorize(row['approval_id'])
            except approvals.ApprovalError:
                actionable = False
        output.append({key: row[key] for key in ('id', 'task_id', 'action_key', 'approval_id', 'payload_hash', 'status', 'created_at', 'expires_at', 'decided_at', 'executed_at', 'execution_tool_run_id')} |
                      {'arguments': {k: v for k, v in json.loads(row['arguments_json']).items() if not k.startswith('_')},
                       'can_approve': actionable, 'destination': 'VP3 Cloud (queued for sync)' if row['action_key'] == 'workspace.update' else 'HomeServer',
                       'owner_review_required': True, **{k: v for k, v in outcome_map.get(row['id'], {}).items() if k not in ('id', 'task_id', 'action_key', 'status')}})
    return output


@atomic_write
def _approve(approval_id):
    authorize(approval_id)
    try:
        with db():
            result=approvals._approve_request(approval_id)
        return result,None
    except approvals.ApprovalError as exc:
        with db() as conn:
            conn.execute("UPDATE action_requests SET status='failed',error='Specialist change could not execute. Refresh the source record.',executed_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'",(approval_id,))
            row=conn.execute('SELECT mission_id,task_id,id FROM agent_mission_actions_v1 WHERE approval_id=?',(approval_id,)).fetchone()
            mission._event(conn,row['mission_id'],'worker.action_failed',row['task_id'],{'action_id':row['id'],'approval_id':approval_id})
        return None,exc


def guarded_approve(approval_id):
    # Always acquire the sync lock before SQLite; Cloud queue normalization uses it.
    with SESSION_LOCK:
        result,error=_approve(approval_id)
    if error:
        raise error
    return result


def review(source, mid, action_id, *, expected_hash, decision, request_id, confirmed, local_owner=False):
    if confirmed is not True or decision not in ('approve', 'deny') or type(expected_hash) is not str:
        raise mission.MissionError('Review the exact change and explicitly confirm your decision.', 422)
    try:
        if type(request_id) is not str or str(uuid.UUID(request_id)) != request_id:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise mission.MissionError('Canonical review request UUID required.', 422) from None
    with SESSION_LOCK:
        result,error=_review(source, mid, action_id, expected_hash, decision, request_id, local_owner)
    if error:
        raise mission.MissionError(str(error),error.status_code)
    return result


@atomic_write
def _review(source, mid, action_id, expected_hash, decision, request_id, local_owner):
    snapshot = mission.get_mission(source, mid)
    contracts.authority(source, snapshot)
    reviewer = 'owner' if local_owner else source
    if not local_owner and source != 'owner':
        with db() as conn:
            permitted = conn.execute("SELECT 1 FROM paired_apps a JOIN app_permissions p ON p.paired_app_id=a.id WHERE a.app_key=? AND a.status='active' AND p.permission='approvals.review' AND p.allowed=1", (source[4:],)).fetchone()
        if not permitted:
            raise mission.MissionError('Current federated approval permission is required.', 403)
    with db() as conn:
        row = conn.execute('SELECT * FROM agent_mission_actions_v1 WHERE id=? AND mission_id=? AND source_app_key=?', (action_id, mid, source)).fetchone()
        if not row:
            raise mission.MissionError('Specialist change not found.', 404)
        if expected_hash != row['payload_hash']:
            raise mission.MissionError('Change revision differs from the reviewed preview.', 409)
        prior = conn.execute('SELECT * FROM agent_mission_action_reviews_v1 WHERE reviewer=? AND request_id=?', (reviewer, request_id)).fetchone()
        if prior:
            if prior['action_id'] != action_id or prior['decision'] != decision or prior['payload_hash'] != expected_hash:
                raise mission.MissionError('Review request belongs to a different decision.', 409)
            return list_actions(source, mid),None
        try:
            if decision == 'approve':
                approvals.approve_request(row['approval_id'])
            else:
                approvals.deny_request(row['approval_id'])
        except approvals.ApprovalError as exc:
            return list_actions(source,mid),exc
        conn.execute('INSERT INTO agent_mission_action_reviews_v1(reviewer,request_id,action_id,payload_hash,decision) VALUES(?,?,?,?,?)', (reviewer, request_id, action_id, expected_hash, decision))
        mission._event(conn, mid, 'worker.action_' + ('approved' if decision == 'approve' else 'denied'), row['task_id'], {'action_id': action_id, 'approval_id': row['approval_id']})
    return list_actions(source, mid),None
