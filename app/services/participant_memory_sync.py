"""Account-bound, revision-checked participant text through workspace pairing.

No browser enrollment, recording, provider binding or recognition consent enters
this queue. It remains an ordinary owner edit queue, never an execution queue.
"""
from __future__ import annotations
import json
import re
import uuid

from ..database import db
from . import workspace_sync as sync, workspace_actions
from .https_bridge_session import SESSION_LOCK


def binding() -> dict:
    current = workspace_actions.binding()
    authority = sync._authority()
    if not {'contacts.read', 'memory.read'}.issubset(authority['permissions']):
        raise sync.WorkspaceSyncError('Participant sync needs the existing contacts and memory permissions.', 403)
    prefixes=authority['scope'].get('memory_key_prefixes',[])
    if prefixes and not any('participant:'.startswith(prefix) for prefix in prefixes):
        raise sync.WorkspaceSyncError('Participant sync is outside the paired memory scope.',403)
    return current


def page(after: str = '') -> dict:
    with SESSION_LOCK:
        current = binding()
        peer = current['peer_id']
        with db() as connection:
            keys = connection.execute('SELECT profile_key FROM participant_memory_cache WHERE peer_id=? AND profile_key>? UNION SELECT profile_key FROM participant_memory_outbox WHERE peer_id=? AND profile_key>? ORDER BY profile_key LIMIT 100', (peer, after, peer, after)).fetchall()
            selected = [row['profile_key'] for row in keys]
            placeholders=','.join('?' for _ in selected) or 'NULL'
            rows = connection.execute('SELECT profile_key,body_json FROM participant_memory_cache WHERE peer_id=? AND profile_key IN ('+placeholders+')', (peer,*selected)).fetchall()
            pending = connection.execute('SELECT profile_key,local_id,body_json,status FROM participant_memory_outbox WHERE peer_id=? AND profile_key IN ('+placeholders+')', (peer,*selected)).fetchall()
            state = connection.execute('SELECT last_success_at,last_error,initial_complete FROM participant_memory_state WHERE peer_id=?', (peer,)).fetchone()
    return {'peer_id': peer, 'participants': [json.loads(row['body_json']) for row in rows],
            'next_after': selected[-1] if len(selected) == 100 else '',
            'pending': [{'key': row['profile_key'], 'local_id': row['local_id'], 'change': json.loads(row['body_json']), 'status': row['status']} for row in pending],
            'enabled': sync.settings()['enabled'], 'last_success_at': state['last_success_at'] if state else None,
            'initial_complete': bool(state and state['initial_complete']),
            'error': state['last_error'] if state else '', 'authentication_authority': False}


def stage(body: dict) -> dict:
    if not isinstance(body, dict) or set(body) - {'peer_id', 'changes'} or not isinstance(body.get('changes'), list) or len(body['changes']) > 25:
        raise sync.WorkspaceSyncError('Invalid participant change batch.')
    changes = []
    for raw in body['changes']:
        if not isinstance(raw, dict) or set(raw) != {'key', 'local_id', 'expected_revision', 'name', 'note', 'deleted', 'resolve'}:
            raise sync.WorkspaceSyncError('Only participant names, notes and deletions may sync.')
        if not isinstance(raw['key'], str) or not re.fullmatch('[a-z0-9-]{12,32}', raw['key']) or not isinstance(raw['local_id'], str) or not 1 <= len(raw['local_id']) <= 100:
            raise sync.WorkspaceSyncError('Invalid participant reference.')
        if not isinstance(raw['expected_revision'], str) or (raw['expected_revision'] and not re.fullmatch('[a-f0-9]{64}', raw['expected_revision'])):
            raise sync.WorkspaceSyncError('Participant revision is required.')
        if type(raw['deleted']) is not bool or type(raw['resolve']) is not bool or not isinstance(raw['name'], str) or not isinstance(raw['note'], str) or len(raw['name']) > 120 or len(raw['note']) > 1000 or (not raw['deleted'] and not raw['name'].strip()):
            raise sync.WorkspaceSyncError('Participant details are invalid or too long.')
        changes.append(raw)
    with SESSION_LOCK:
        current = binding()
        if body.get('peer_id') != current['peer_id']:
            raise sync.WorkspaceSyncError('Cloud account changed. Previous participant edits remain separate.', 409)
        with db() as connection:
            for raw in changes:
                previous = connection.execute('SELECT * FROM participant_memory_outbox WHERE peer_id=? AND profile_key=?', (current['peer_id'], raw['key'])).fetchone()
                if previous and previous['local_id'] != raw['local_id']:
                    raise sync.WorkspaceSyncError('This Cloud participant is already linked to another local participant.', 409)
                data = {key: raw[key] for key in ('key', 'expected_revision', 'name', 'note', 'deleted')}
                if previous:
                    old = json.loads(previous['body_json'])
                    if all(old.get(key) == value for key, value in data.items()):
                        continue
                    if previous['status'] != 'queued' and not raw['resolve']:
                        continue  # Keep both versions until an explicit owner resolution.
                data['change_id'] = uuid.uuid4().hex
                connection.execute('INSERT INTO participant_memory_outbox(peer_id,profile_key,local_id,change_id,body_json,status) VALUES(?,?,?,?,?,?) '
                    'ON CONFLICT(peer_id,profile_key) DO UPDATE SET change_id=excluded.change_id,body_json=excluded.body_json,status=excluded.status',
                    (current['peer_id'], raw['key'], raw['local_id'], data['change_id'], json.dumps(data, ensure_ascii=False), 'queued'))
    sync.wake()
    return {'queued': True}


def discard(body: dict) -> dict:
    # Explicit "Use Cloud" resolution removes only the exact reviewed local edit.
    with SESSION_LOCK:
        current = binding()
        if set(body) != {'peer_id', 'key', 'change_id'} or body['peer_id'] != current['peer_id']:
            raise sync.WorkspaceSyncError('Participant connection changed.', 409)
        with db() as connection:
            result=connection.execute('DELETE FROM participant_memory_outbox WHERE peer_id=? AND profile_key=? AND change_id=?', (current['peer_id'], body['key'], body['change_id']))
            if result.rowcount!=1:
                raise sync.WorkspaceSyncError('A newer participant edit exists. Review both versions again.',409)
    return {'discarded': True}


def deliver(client, session: dict, peer: str) -> None:
    """One bounded page per background cycle; failures do not block workspace sync."""
    try:
        with SESSION_LOCK:
            current = binding()
            if current['peer_id'] != peer or not sync._binding_active(session) or not sync.settings()['enabled']:
                return
            with db() as connection:
                state = connection.execute('SELECT * FROM participant_memory_state WHERE peer_id=?', (peer,)).fetchone()
                queued = connection.execute("SELECT * FROM participant_memory_outbox WHERE peer_id=? AND status='queued' ORDER BY profile_key LIMIT 25", (peer,)).fetchall()
                keys = connection.execute('SELECT profile_key FROM participant_memory_cache WHERE peer_id=? AND profile_key>? ORDER BY profile_key LIMIT 100', (peer, state['verify_cursor'] if state else '')).fetchall()
        result = sync._request(client, session, {'action': 'participants', 'changes': [json.loads(row['body_json']) for row in queued],
                           'keys': [row['profile_key'] for row in keys], 'after': state['pull_cursor'] if state else 0})
        if result.get('account_id') != peer.rsplit('|', 1)[1] or not isinstance(result.get('participants'), list) or len(result['participants']) > 200 or type(result.get('next_after')) is not int or result['next_after'] < 0:
            raise sync.WorkspaceSyncError('Invalid participant synchronization response.', 503)
        results = result.get('results')
        if not isinstance(results, list) or len(results) != len(queued):
            raise sync.WorkspaceSyncError('Participant receipts are incomplete.', 503)
        def valid(row):
            return isinstance(row, dict) and set(row) == {'key', 'id', 'name', 'note', 'deleted', 'revision'} and isinstance(row['key'], str) and re.fullmatch('[a-z0-9-]{12,32}', row['key']) and isinstance(row['id'], str) and isinstance(row['name'], str) and len(row['name']) <= 120 and isinstance(row['note'], str) and len(row['note']) <= 1000 and type(row['deleted']) is bool and isinstance(row['revision'], str) and re.fullmatch('[a-f0-9]{64}', row['revision'])
        incoming = list(result['participants'])
        by_id = {row['change_id']: row for row in queued}
        seen = set()
        for receipt in results:
            cid = receipt.get('change_id') if isinstance(receipt, dict) else None
            if cid not in by_id or cid in seen or receipt.get('status') not in ('applied', 'conflict', 'protected') or not valid(receipt.get('participant')) or receipt['participant']['key'] != by_id[cid]['profile_key']:
                raise sync.WorkspaceSyncError('Invalid participant change receipt.', 503)
            seen.add(cid)
            incoming.append(receipt['participant'])
        if not all(valid(row) for row in incoming):
            raise sync.WorkspaceSyncError('Invalid participant memory record.', 503)
        with SESSION_LOCK:
            if binding() != current or not sync._binding_active(session) or not sync.settings()['enabled']:
                raise sync.WorkspaceSyncError('Participant pairing changed before commit.', 409)
            with db() as connection:
                for row in incoming:
                    connection.execute('INSERT INTO participant_memory_cache(peer_id,profile_key,body_json) VALUES(?,?,?) ON CONFLICT(peer_id,profile_key) DO UPDATE SET body_json=excluded.body_json', (peer, row['key'], json.dumps(row, ensure_ascii=False)))
                for receipt in results:
                    if receipt['status'] == 'applied':
                        connection.execute('DELETE FROM participant_memory_outbox WHERE peer_id=? AND change_id=?', (peer, receipt['change_id']))
                    else:
                        connection.execute('UPDATE participant_memory_outbox SET status=? WHERE peer_id=? AND change_id=?', (receipt['status'], peer, receipt['change_id']))
                connection.execute("INSERT INTO participant_memory_state(peer_id,pull_cursor,verify_cursor,initial_complete,last_success_at,last_error) VALUES(?,?,?,?,?, '') ON CONFLICT(peer_id) DO UPDATE SET pull_cursor=excluded.pull_cursor,verify_cursor=excluded.verify_cursor,initial_complete=MAX(participant_memory_state.initial_complete,excluded.initial_complete),last_success_at=excluded.last_success_at,last_error=''", (peer, result['next_after'], keys[-1]['profile_key'] if len(keys) == 100 else '', int(result['next_after']==0), sync.now()))
    except Exception as exc:
        message = str(exc) if isinstance(exc, sync.WorkspaceSyncError) else 'Participant sync unavailable; saved edits will retry automatically.'
        with db() as connection:
            connection.execute('INSERT INTO participant_memory_state(peer_id,last_error) VALUES(?,?) ON CONFLICT(peer_id) DO UPDATE SET last_error=excluded.last_error', (peer, message))
