"""Durable owner edits routed to Cloud with revision checks and replay-safe receipts.

The outbox is an edit delivery queue, never a calendar/schedule execution queue.
Neither pending changes nor remote receipts replace a complete replica.
"""
from __future__ import annotations

import hashlib
import json
import re
from contextvars import ContextVar
from datetime import datetime, timezone
from datetime import timedelta
from zoneinfo import ZoneInfo

import httpx

from ..database import db
from . import native_workspaces as native, workspace_sync as sync
from .https_bridge_session import SESSION_LOCK, load_https_session

FIELDS = {
    'crm_contacts': {'display_name':120, 'organization':190, 'email':190, 'phone':80, 'relationship':80},
    'knowledge_items': {'title':190, 'description':10000, 'content_text':50000},
    'user_calendar_events': {'title':190, 'description':10000, 'location':500, 'date':10, 'start_time':5, 'end_date':10, 'end_time':5, 'timezone':80, 'all_day':0},
}
DATASET_TABLE = {'contacts':'crm_contacts', 'knowledge':'knowledge_items', 'calendar':'user_calendar_events'}
ACTIVE = ('queued', 'sending', 'applied')
agent_datasets: ContextVar[tuple[str, ...] | None] = ContextVar('workspace_agent_datasets',default=None)


class WorkspaceActionError(sync.WorkspaceSyncError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def binding() -> dict:
    state, session = sync.settings(), load_https_session()
    authority = sync._authority()
    if not session or not authority['active'] or not authority['scope'].get('cloud_allowed', False) or not state['peer_id'] or state['session_hash'] != hashlib.sha256(session['session_token'].encode()).hexdigest():
        raise WorkspaceActionError('Refresh synchronization with the current Cloud pairing before editing.', 409)
    with db() as connection:
        app=connection.execute("SELECT token_hash FROM paired_apps WHERE app_key='vp3' AND status='active'").fetchone()
    if app is None:
        raise WorkspaceActionError('Cloud pairing is no longer active.',403)
    fingerprint=hashlib.sha256((sync._authority_hash()+':'+app['token_hash']).encode()).hexdigest()
    return {'peer_id':state['peer_id'], 'session_hash':state['session_hash'], 'authority_hash':fingerprint}


def record(dataset: str, key: str) -> dict:
    allowed=agent_datasets.get()
    if allowed is not None and dataset not in allowed:
        raise WorkspaceActionError('This conversation excludes that Cloud workspace.',403)
    if dataset not in DATASET_TABLE or not re.fullmatch(r'[a-zA-Z0-9_]+:[1-9][0-9]{0,18}', key) or key.split(':')[0] != DATASET_TABLE[dataset]:
        raise WorkspaceActionError('This record uses its original source editor.')
    row = next((row for row in native.snapshot(dataset)['records'] if row['table'] + ':' + row['source_id'] == key), None)
    if row is None:
        raise WorkspaceActionError('Record unavailable. Refresh this workspace.', 409)
    if not re.fullmatch(r'[a-f0-9]{64}', str(row.get('record_revision') or '')):
        raise WorkspaceActionError('Update Cloud and synchronize this record before editing.', 409)
    data = row['data']
    if dataset == 'knowledge' and data.get('knowledge_scope') != 'personal':
        raise WorkspaceActionError('Only personal knowledge can be edited here.', 403)
    if dataset in ('contacts', 'calendar') and data.get('status', 'active') != 'active':
        raise WorkspaceActionError('This record is no longer editable.', 409)
    return row


def normalize(arguments: dict, *, capture_binding: bool = True) -> dict:
    if not isinstance(arguments, dict) or set(arguments) - {'dataset', 'key', 'expected_revision', 'mutation_id', 'fields', '_binding'}:
        raise WorkspaceActionError('Unsupported workspace edit argument.')
    dataset, key = arguments.get('dataset'), arguments.get('key')
    if not isinstance(dataset, str) or not isinstance(key, str):
        raise WorkspaceActionError('Select a source-qualified workspace record.')
    expected, mutation, fields = arguments.get('expected_revision'), arguments.get('mutation_id'), arguments.get('fields')
    if not isinstance(expected, str) or not re.fullmatch(r'[a-f0-9]{64}', expected) or not isinstance(mutation, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{8,128}', mutation):
        raise WorkspaceActionError('A source revision and valid change ID are required.')
    if dataset not in DATASET_TABLE or key.split(':')[0] != DATASET_TABLE[dataset]:
        raise WorkspaceActionError('This record uses its original source editor.')
    limits = FIELDS[DATASET_TABLE[dataset]]
    if not isinstance(fields, dict) or not fields or set(fields) - limits.keys():
        raise WorkspaceActionError('Supply supported changed fields only.')
    for field, value in fields.items():
        if field == 'all_day':
            if type(value) is not bool:
                raise WorkspaceActionError('All-day must be true or false.')
        elif not isinstance(value, str) or len(value) > limits[field]:
            raise WorkspaceActionError('Workspace field value is invalid or too long.')
    if len(json.dumps(fields, ensure_ascii=False).encode()) > 65536:
        raise WorkspaceActionError('Keep each workspace change under 64 KB.')
    result = {'dataset':dataset, 'key':key, 'expected_revision':expected, 'mutation_id':mutation, 'fields':fields}
    if capture_binding:
        with SESSION_LOCK:
            current = binding()
            supplied = arguments.get('_binding')
            if supplied is not None and supplied != current:
                raise WorkspaceActionError('Pairing or permissions changed after this edit was prepared.', 403)
            row = record(dataset, key)
            if row['record_revision'] != expected:
                raise WorkspaceActionError('This record changed. Reload it and review your edits.', 409)
            result['_binding'] = current
    return result


def _public(row: dict) -> dict:
    # Content and pairing fingerprints never appear in Brain/audit summaries.
    return {key:row[key] for key in ('mutation_id','dataset','record_key','state','error','attempts','created_at','updated_at')}


def status() -> dict:
    try:
        current = binding()
    except WorkspaceActionError:
        return {'items':[], 'pending_count':0, 'attention_count':0}
    with db() as connection:
        rows = connection.execute('SELECT * FROM workspace_sync_actions WHERE peer_id=? ORDER BY created_at DESC LIMIT 50', (current['peer_id'],)).fetchall()
        counts = dict(connection.execute('SELECT state,count(*) FROM workspace_sync_actions WHERE peer_id=? GROUP BY state', (current['peer_id'],)).fetchall())
    return {'items':[_public(dict(row)) for row in rows], 'pending_count':sum(counts.get(s,0) for s in ACTIVE), 'attention_count':sum(counts.get(s,0) for s in ('conflict','blocked','failed','superseded'))}


def enqueue(arguments: dict) -> dict:
    # Lock orders queue commits against disconnect/opt-out. No network under it.
    with SESSION_LOCK:
        raw = normalize(arguments, capture_binding=False)
        current = binding()
        if arguments.get('_binding') is not None and arguments['_binding'] != current:
            raise WorkspaceActionError('Pairing or permissions changed after this edit was prepared.', 403)
        encoded = json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        with db() as connection:
            if not connection.in_transaction:
                connection.execute('BEGIN IMMEDIATE')
            existing = connection.execute('SELECT * FROM workspace_sync_actions WHERE mutation_id=?', (raw['mutation_id'],)).fetchone()
            if existing:
                if existing['peer_id'] != current['peer_id'] or existing['session_hash'] != current['session_hash'] or existing['request_hash'] != fingerprint:
                    raise WorkspaceActionError('This change ID has already been used.', 409)
                return {'action':_public(dict(existing)), 'idempotent_replay':True}
            row = record(raw['dataset'], raw['key'])
            if row['record_revision'] != raw['expected_revision']:
                raise WorkspaceActionError('This record changed. Reload it and review your edits.', 409)
            busy = connection.execute("SELECT 1 FROM workspace_sync_actions WHERE peer_id=? AND dataset=? AND record_key=? AND state IN ('queued','sending','applied')", (current['peer_id'],raw['dataset'],raw['key'])).fetchone()
            if busy:
                raise WorkspaceActionError('A change for this record is already pending. Wait for it to synchronize.', 409)
            if connection.execute("SELECT count(*) FROM workspace_sync_actions WHERE state IN ('queued','sending','applied')").fetchone()[0] >= 200:
                raise WorkspaceActionError('Finish or cancel pending workspace edits before adding more.', 409)
            timestamp = sync.now()
            connection.execute("INSERT INTO workspace_sync_actions(mutation_id,peer_id,session_hash,authority_hash,dataset,record_key,expected_revision,fields_json,request_hash,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,'queued',?,?)",
                (raw['mutation_id'],current['peer_id'],current['session_hash'],current['authority_hash'],raw['dataset'],raw['key'],raw['expected_revision'],json.dumps(raw['fields'],ensure_ascii=False),fingerprint,timestamp,timestamp))
    sync.wake()
    return {'action':{'mutation_id':raw['mutation_id'],'dataset':raw['dataset'],'record_key':raw['key'],'state':'queued','error':'','attempts':0,'created_at':timestamp,'updated_at':timestamp}}


def cancel(mutation_id: str) -> dict:
    with SESSION_LOCK:
        current = binding()
        with db() as connection:
            cursor = connection.execute("UPDATE workspace_sync_actions SET state='cancelled',error='',updated_at=? WHERE mutation_id=? AND peer_id=? AND state='queued' AND attempts=0", (sync.now(), mutation_id, current['peer_id']))
            if not cursor.rowcount:
                raise WorkspaceActionError('This change is already sending or complete and cannot be cancelled.', 409)
    return {'cancelled':True}


def editable(dataset: str, key: str) -> dict:
    with SESSION_LOCK:
        binding()
        row = record(dataset, key)
    data=row['data']
    values={field:data.get(field,'') for field in FIELDS[row['table']]}
    if dataset=='contacts':
        values.update(display_name=data.get('name',''),organization=data.get('company',''),relationship=data.get('lifecycle_stage',''))
    if dataset=='calendar':
        try:
            zone=ZoneInfo(data.get('timezone') or 'UTC')
            start=datetime.fromisoformat(data['start_at_utc'].replace('Z','+00:00'))
            end=datetime.fromisoformat(data['end_at_utc'].replace('Z','+00:00'))
            start=(start if start.tzinfo else start.replace(tzinfo=timezone.utc)).astimezone(zone)
            end=(end if end.tzinfo else end.replace(tzinfo=timezone.utc)).astimezone(zone)
            all_day=bool(data.get('all_day'))
            values.update(date=start.strftime('%Y-%m-%d'),start_time=start.strftime('%H:%M'),end_date=(end-timedelta(days=1) if all_day else end).strftime('%Y-%m-%d'),end_time=end.strftime('%H:%M'),all_day=all_day,timezone=str(zone))
        except (ValueError,KeyError):
            raise WorkspaceActionError('Calendar timing is unavailable. Use its Cloud editor.',409)
    return {'record':row, 'editable_values':values, 'editable_fields':FIELDS[row['table']], 'actions':[item for item in status()['items'] if item['dataset']==dataset and item['record_key']==key]}


def _mark(mutation: str, state: str, error: str = '', revision: str = '') -> None:
    with db() as connection:
        connection.execute('UPDATE workspace_sync_actions SET state=?,error=?,receipt_revision=?,updated_at=? WHERE mutation_id=?', (state,error,revision,sync.now(),mutation))


def deliver(client: httpx.Client, session: dict) -> None:
    """Only called under the existing sync worker's single-flight run lock."""
    with db() as connection:
        rows = connection.execute("SELECT * FROM workspace_sync_actions WHERE state IN ('queued','sending') ORDER BY created_at LIMIT 20").fetchall()
    for stored in rows:
        row = dict(stored)
        with SESSION_LOCK:
            if not sync._binding_active(session):
                return
            try:
                live = binding()
            except WorkspaceActionError:
                return
            if any(row[key] != live[key] for key in live):
                _mark(row['mutation_id'],'blocked','Pairing or permissions changed. Review and prepare a new edit.')
                continue
            with db() as connection:
                cursor = connection.execute("UPDATE workspace_sync_actions SET state='sending',attempts=attempts+1,updated_at=? WHERE mutation_id=? AND state IN ('queued','sending')", (sync.now(),row['mutation_id']))
                if not cursor.rowcount:
                    continue  # Owner cancelled before claim.
        body = {'action':'mutate','dataset':row['dataset'],'key':row['record_key'],'expected_revision':row['expected_revision'],'mutation_id':row['mutation_id'],'fields':json.loads(row['fields_json'])}
        try:
            receipt = sync._request(client,session,body)
            if receipt.get('account_id') != row['peer_id'].rsplit('|',1)[-1] or receipt.get('mutation_id') != row['mutation_id'] or receipt.get('record_key') != row['record_key'] or receipt.get('applied') is not True or not re.fullmatch(r'[a-f0-9]{64}',str(receipt.get('record_revision') or '')):
                raise WorkspaceActionError('Invalid Cloud edit acknowledgement.',503)
            with SESSION_LOCK:
                if load_https_session() != session or binding() != live:
                    _mark(row['mutation_id'],'blocked','Pairing changed during delivery. Check the source before preparing another edit.')
                else:
                    _mark(row['mutation_id'],'applied',revision=receipt['record_revision'])
        except sync.WorkspaceSyncError as exc:
            code = getattr(exc,'status_code',0)
            if code == 409:
                _mark(row['mutation_id'],'conflict','Cloud changed this record. Reload it and review your edits.')
            elif code in (401,403,410):
                _mark(row['mutation_id'],'blocked','Cloud permission or pairing is unavailable. Review the connection.')
            elif code in (413,422):
                _mark(row['mutation_id'],'failed','Cloud could not accept these fields. Reload the record and correct the edit.')
            else:
                # A lost receipt may follow a committed write. Retain the same ID.
                _mark(row['mutation_id'],'queued','Waiting for Cloud acknowledgement; retrying the same change automatically.')
                return
        except (httpx.HTTPError,ValueError,TypeError):
            _mark(row['mutation_id'],'queued','Cloud is unreachable; this change will retry automatically.')
            return


def reconcile(peer: str, dataset: str, payload: dict) -> None:
    revisions = {row['table']+':'+row['source_id']:row.get('record_revision') for row in payload['records']}
    with db() as connection:
        rows = connection.execute("SELECT mutation_id,record_key,receipt_revision FROM workspace_sync_actions WHERE peer_id=? AND dataset=? AND state='applied'", (peer,dataset)).fetchall()
        for row in rows:
            matched = revisions.get(row['record_key']) == row['receipt_revision']
            connection.execute("UPDATE workspace_sync_actions SET state=?,error=?,updated_at=? WHERE mutation_id=? AND state='applied'", ('synced' if matched else 'superseded','' if matched else 'Saved in Cloud; the source has since changed or removed this record.',sync.now(),row['mutation_id']))
