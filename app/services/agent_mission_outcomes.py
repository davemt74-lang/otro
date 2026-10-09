"""Receipt-bound readback and bounded lead reports; verification never repeats a write."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import Counter
from datetime import datetime, timezone

from ..database import atomic_write, db

NATIVE = {
    'contacts': ('federated_contact_mutations', 'contact'),
    'knowledge': ('federated_knowledge_mutations', 'knowledge'),
    'tasks': ('federated_task_mutations', 'task'),
    'calendar': ('federated_calendar_mutations', 'event'),
}
WAITING = {'awaiting_review', 'queued', 'sending', 'applied'}
ATTENTION = {'conflict', 'blocked', 'failed', 'superseded', 'missing', 'unverified', 'expired'}
MESSAGES = {
    'awaiting_review': 'Waiting for your review.',
    'verified': 'Saved record matches the execution receipt.',
    'queued': 'Waiting for Cloud delivery; the original change ID is retained.',
    'sending': 'Cloud delivery is in progress.',
    'applied': 'Cloud acknowledged the write; waiting for synchronized readback.',
    'superseded': 'The record changed after this write. Review its current version.',
    'missing': 'The saved record is no longer available.',
    'unverified': 'A valid execution receipt and matching readback are required.',
    'conflict': 'The source changed before delivery. Prepare a newly reviewed edit.',
    'blocked': 'Current pairing or permissions block delivery.',
    'failed': 'The change failed. Review the source before preparing another edit.',
    'expired': 'Review expired before execution.',
    'rejected': 'You rejected this change.',
    'cancelled': 'This change was cancelled.',
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def _native(row, args):
    from . import contacts, knowledge, task_calendar_continuity as continuity
    kind = row['action_key'].split('.')[0]
    table, field = NATIVE[kind]
    with db() as conn:
        receipt = conn.execute(f'SELECT * FROM {table} WHERE source_app_key=? AND mutation_id=?',
                               (row['source_app_key'], args.get('mutation_id'))).fetchone()
        run = conn.execute('SELECT tool_key,source_app_key,status FROM tool_runs WHERE id=?', (row['execution_tool_run_id'],)).fetchone()
    payload = {k: v for k, v in args.items() if k != 'mutation_id'}
    if (not receipt or not run or run['status'] != 'completed' or run['tool_key'] != row['action_key']
            or run['source_app_key'] != row['source_app_key'] or receipt['action_key'] != row['action_key']
            or receipt['request_hash'] != digest({'action': row['action_key'], 'payload': payload})):
        return 'unverified', '', ''
    saved = json.loads(receipt['result_json']).get(field, {})
    canonical, revision = receipt['canonical_id'], saved.get('record_revision', '')
    if saved.get('canonical_id') != canonical or not re.fullmatch('[a-f0-9]{64}', revision):
        return 'unverified', '', ''
    if kind == 'contacts':
        current = contacts.get_federated_contact_by_canonical(canonical)
    elif kind == 'knowledge':
        current = knowledge.get_federated_knowledge_by_canonical(canonical)
    elif kind == 'tasks':
        current = continuity.get_federated_task_by_canonical(canonical)
    else:
        native = continuity._calendar_row(continuity._calendar_id_from_canonical(canonical))
        current = continuity.federated_calendar_item(native) if native else None
    if not current:
        return 'missing', revision, ''
    observed = current.get('record_revision', '')
    return ('verified' if current.get('canonical_id') == canonical and observed == revision else 'superseded'), revision, observed


def _cloud(row, args):
    from . import workspace_actions
    # Read the already-authorized replica directly. Taking SESSION_LOCK from
    # get_mission's existing write transactions would reverse the lock order.
    with db() as conn:
        receipt = conn.execute('SELECT * FROM workspace_sync_actions WHERE mutation_id=?', (args.get('mutation_id'),)).fetchone()
        if not receipt:
            return 'unverified', '', ''
        binding = args.get('_binding', {})
        raw = {k: v for k, v in args.items() if k != '_binding'}
        if (any(receipt[k] != binding.get(k) for k in ('peer_id', 'session_hash', 'authority_hash'))
                or receipt['request_hash'] != digest(raw) or receipt['dataset'] != args.get('dataset')
                or receipt['record_key'] != args.get('key')
                or receipt['expected_revision'] != args.get('expected_revision')
                or json.loads(receipt['fields_json']) != args.get('fields')):
            return 'unverified', '', ''
        run = conn.execute('SELECT tool_key,source_app_key,status FROM tool_runs WHERE id=?', (row['execution_tool_run_id'],)).fetchone()
        if not run or run['status'] != 'completed' or run['tool_key'] != row['action_key'] or run['source_app_key'] != row['source_app_key']:
            return 'unverified', '', ''
        # binding() reads authority without acquiring SESSION_LOCK. Its failure
        # is conservative: an old replica cannot certify a new pairing.
        try:
            if workspace_actions.binding() != binding:
                return 'blocked', receipt['receipt_revision'], ''
        except workspace_actions.WorkspaceActionError:
            return 'blocked', receipt['receipt_revision'], ''
        state, revision = receipt['state'], receipt['receipt_revision']
        if state != 'synced':
            return state, revision, ''
        snapshot = conn.execute('SELECT body_json FROM workspace_sync_snapshots WHERE peer_id=? AND dataset=?',
                                (receipt['peer_id'], receipt['dataset'])).fetchone()
    if not snapshot:
        return 'unverified', revision, ''
    target = next((r for r in json.loads(snapshot['body_json']).get('records', [])
                   if r['table'] + ':' + r['source_id'] == receipt['record_key']), None)
    observed = target.get('record_revision', '') if target else ''
    if not target:
        return 'missing', revision, ''
    return ('verified' if re.fullmatch('[a-f0-9]{64}', revision) and observed == revision else 'superseded'), revision, observed


@atomic_write
def assess(snapshot):
    """Called after current source visibility is established, without recursive lookups."""
    from . import agent_mission_runtime as mission
    if not snapshot.get('authority_current', True):
        return [], None
    with db() as conn:
        rows = conn.execute('SELECT a.*,r.status,r.arguments_json,r.action_key AS request_tool,r.source_app_key AS request_source,r.execution_tool_run_id,r.executed_at,r.expires_at FROM agent_mission_actions_v1 a JOIN action_requests r ON r.id=a.approval_id WHERE a.mission_id=? AND a.source_app_key=? ORDER BY a.created_at,a.id',
                            (snapshot['id'], snapshot['source_app_key'])).fetchall()
        contract = conn.execute("SELECT revision,datetime(expires_at)>datetime('now') AS active FROM agent_mission_tool_contracts_v1 WHERE mission_id=?", (snapshot['id'],)).fetchone()
    output = []
    for row in rows:
        revision = observed = ''
        try:
            args = json.loads(row['arguments_json'])
            intact = (row['payload_hash'] == digest(args) and row['request_tool'] == row['action_key'] and row['request_source'] == row['source_app_key'])
            if not intact:
                state = 'unverified'
            elif row['status'] == 'executed':
                state, revision, observed = _cloud(row, args) if row['action_key'] == 'workspace.update' else _native(row, args)
            elif row['status'] == 'pending':
                expiry = datetime.fromisoformat(row['expires_at']).replace(tzinfo=timezone.utc)
                state = 'expired' if expiry <= datetime.now(timezone.utc) else 'awaiting_review'
            else:
                state = {'denied': 'rejected', 'executing': 'sending'}.get(row['status'], row['status'])
        except (ValueError, TypeError, KeyError, LookupError):
            state = 'unverified'
        except RuntimeError:
            # Native readers may report a removed canonical record.
            state = 'unverified'
        with db() as conn:
            prior = conn.execute('SELECT * FROM agent_mission_action_outcomes_v1 WHERE action_id=?', (row['id'],)).fetchone()
            changed = not prior or (prior['state'], prior['receipt_revision'], prior['observed_revision']) != (state, revision, observed)
            conn.execute("INSERT INTO agent_mission_action_outcomes_v1(action_id,state,receipt_revision,observed_revision,verified_at) VALUES(?,?,?,?,CASE WHEN ?='verified' THEN CURRENT_TIMESTAMP END) ON CONFLICT(action_id) DO UPDATE SET state=excluded.state,receipt_revision=excluded.receipt_revision,observed_revision=excluded.observed_revision,checked_at=CURRENT_TIMESTAMP,updated_at=CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE updated_at END,verified_at=CASE WHEN excluded.state='verified' AND (state!='verified' OR receipt_revision!=excluded.receipt_revision) THEN CURRENT_TIMESTAMP ELSE verified_at END",
                         (row['id'], state, revision, observed, state, changed))
            if changed:
                mission._event(conn, snapshot['id'], 'worker.action_outcome', row['task_id'], {'action_id': row['id'], 'state': state})
            saved = conn.execute('SELECT * FROM agent_mission_action_outcomes_v1 WHERE action_id=?', (row['id'],)).fetchone()
        output.append({'id': row['id'], 'task_id': row['task_id'], 'action_key': row['action_key'], 'status': row['status'],
                       'outcome_state': state, 'outcome_message': MESSAGES.get(state, 'Refresh the source status.'),
                       'created_at': row['created_at'], 'executed_at': row['executed_at'], 'checked_at': saved['checked_at'],
                       'verified_at': saved['verified_at'], 'outcome_updated_at': saved['updated_at'],
                       'execution_tool_run_id': row['execution_tool_run_id'],
                       'receipt_revision': revision, 'observed_revision': observed,
                       'can_recover': state == 'queued' and row['action_key'] == 'workspace.update' and bool(contract and contract['active'] and contract['revision'] == row['contract_revision']) and snapshot['status'] in ('running', 'completed', 'partial')})
    counts = Counter(r['outcome_state'] for r in output)
    waiting, attention = sum(counts[k] for k in WAITING), sum(counts[k] for k in ATTENTION)
    workers_pending = any(t['status'] in ('queued', 'running', 'paused') for t in snapshot['tasks'])
    workers_failed = sum(t['status'] in ('failed', 'blocked') for t in snapshot['tasks'])
    state = 'in_progress' if workers_pending else 'awaiting_review' if counts['awaiting_review'] else 'awaiting_delivery' if waiting else 'attention' if attention or workers_failed else 'complete'
    if snapshot['status'] in ('cancelled', 'paused'):
        state = snapshot['status']
    report = {'state': state, 'total_changes': len(output), 'verified_changes': counts['verified'], 'waiting_changes': waiting,
              'attention_changes': attention, 'rejected_changes': counts['rejected'], 'cancelled_changes': counts['cancelled'], 'failed_workers': workers_failed,
              'execution_verified': bool(output) and counts['verified'] == len(output),
              'summary': f"{counts['verified']} of {len(output)} changes verified; {waiting} waiting; {attention} need attention; {counts['rejected']} rejected; {counts['cancelled']} cancelled. {workers_failed} workers failed.",
              'actions': [{'action_id': r['id'], 'task_id': r['task_id'], 'state': r['outcome_state'], 'checked_at': r['checked_at']} for r in output],
              'content_verified': False}
    return output, report


def recover(source, mid, action_id, *, expected_hash, request_id, confirmed, local_owner=False):
    from . import agent_mission_actions as changes, agent_mission_runtime as mission, workspace_sync
    from .https_bridge_session import SESSION_LOCK
    if confirmed is not True or type(request_id) is not str:
        raise mission.MissionError('Explicit confirmation and a recovery request ID are required.', 422)
    try:
        if str(uuid.UUID(request_id)) != request_id:
            raise ValueError()
    except (ValueError, AttributeError):
        raise mission.MissionError('Canonical recovery request UUID required.', 422) from None
    with SESSION_LOCK:
        requested = _recover(source, mid, action_id, expected_hash, request_id, local_owner)
    if requested:
        workspace_sync.wake()
    return changes.list_actions(source, mid)


@atomic_write
def _recover(source, mid, action_id, expected_hash, request_id, local_owner):
    from . import agent_mission_runtime as mission, agent_mission_tool_contracts as contracts, agent_mission_actions as changes, workspace_actions, approvals
    snapshot = mission.get_mission(source, mid)
    contracts.authority(source, snapshot)
    reviewer = 'owner' if local_owner else source
    if source != 'owner':
        raise mission.MissionError('Cloud workspace delivery recovery requires its HomeServer owner.', 403)
    with db() as conn:
        row = conn.execute('SELECT a.*,r.arguments_json,r.status FROM agent_mission_actions_v1 a JOIN action_requests r ON r.id=a.approval_id WHERE a.id=? AND a.mission_id=? AND a.source_app_key=?', (action_id, mid, source)).fetchone()
        if not row:
            raise mission.MissionError('Specialist change not found.', 404)
        if row['payload_hash'] != expected_hash:
            raise mission.MissionError('Change differs from the reviewed recovery.', 409)
        try:
            changes.authorize(row['approval_id'])
        except approvals.ApprovalError as exc:
            raise mission.MissionError(str(exc), exc.status_code) from exc
        args = json.loads(row['arguments_json'])
        if row['action_key'] != 'workspace.update' or row['status'] != 'executed':
            raise mission.MissionError('Recovery only resumes an already-approved Cloud delivery.', 409)
        try:
            current = workspace_actions.binding()
        except workspace_actions.WorkspaceActionError as exc:
            raise mission.MissionError(str(exc), exc.status_code) from exc
        if args.get('_binding') != current:
            raise mission.MissionError('Pairing or permissions changed. Review the source.', 403)
        prior = conn.execute('SELECT * FROM agent_mission_action_recoveries_v1 WHERE reviewer=? AND request_id=?', (reviewer, request_id)).fetchone()
        if prior:
            if prior['action_id'] != action_id or prior['payload_hash'] != expected_hash:
                raise mission.MissionError('Recovery request belongs to another change.', 409)
            return False
        queue = conn.execute('SELECT * FROM workspace_sync_actions WHERE mutation_id=?', (args.get('mutation_id'),)).fetchone()
        if (not queue or queue['state'] != 'queued' or any(queue[k] != current[k] for k in current)
                or row['payload_hash'] != digest(args)
                or queue['request_hash'] != digest({k: v for k, v in args.items() if k != '_binding'})
                or queue['dataset'] != args.get('dataset') or queue['record_key'] != args.get('key')
                or queue['expected_revision'] != args.get('expected_revision')
                or json.loads(queue['fields_json']) != args.get('fields')):
            raise mission.MissionError('Only the unchanged queued delivery can be resumed. Refresh its outcome.', 409)
        # No state reset or mutation is performed. The existing single-flight
        # outbox owns dispatch and uses the same durable mutation ID.
        conn.execute('INSERT INTO agent_mission_action_recoveries_v1(reviewer,request_id,action_id,payload_hash) VALUES(?,?,?,?)', (reviewer, request_id, action_id, expected_hash))
        mission._event(conn, mid, 'worker.action_recovery_requested', row['task_id'], {'action_id': action_id})
    return True
