"""Owner-only projections of complete replicas; no native rows or jobs are created."""
from __future__ import annotations

import hashlib
import json
from urllib.parse import urlencode, urljoin, urlsplit

import httpx

from ..database import db
from . import workspace_sync as sync
from .https_bridge_session import SESSION_LOCK, load_https_session

ROOT_TABLES = {
    'contacts': {'crm_contacts', 'vp3_agent_contacts', 'user_relationships'},
    'knowledge': {'knowledge_items', 'artist_transcript_folders_v177'},
    'calendar': {'user_calendar_events'},
    'crm': {'crm_leads', 'crm_tasks', 'crm_activities'},
    'products': {'agent_commerce_products_v800'},
    'transcriptions': {'artist_transcript_sessions_v172'},
    'meetings': {'video_meetings'},
    'schedules': {'agent_scheduling_schedules', 'agent_scheduling_bookings', 'agent_scheduling_event_types'},
}
SOURCE_TABLES = {'crm_contacts', 'knowledge_items', 'artist_transcript_folders_v177', 'user_calendar_events',
                 'artist_transcript_sessions_v172', 'agent_commerce_products_v800', 'agent_scheduling_schedules',
                 'agent_scheduling_bookings', 'agent_scheduling_event_types', 'video_meetings'}


def snapshot(dataset: str) -> dict:
    if dataset not in sync.DATASETS:
        raise sync.WorkspaceSyncError('Unknown workspace dataset.')
    with SESSION_LOCK:
        state = sync.settings()
        session = load_https_session()
        authority = sync._authority()
        if not session or not authority['active'] or not authority['scope'].get('cloud_allowed', False) or state['session_hash'] != hashlib.sha256(session['session_token'].encode()).hexdigest():
            return {'records': [], 'files': [], 'state': 'not_paired', 'synced_at': None}
        with db() as connection:
            row = connection.execute('SELECT body_json,synced_at FROM workspace_sync_snapshots WHERE peer_id=? AND dataset=?', (state['peer_id'], dataset)).fetchone()
        value = json.loads(row['body_json']) if row else {'records': [], 'files': []}
        return {**value, 'synced_at': row['synced_at'] if row else None, 'state': 'available' if row else 'waiting'}


def items(dataset: str, query: str = '', offset: int = 0, limit: int = 50) -> dict:
    payload = snapshot(dataset)
    roots = ROOT_TABLES.get(dataset)
    rows = [row for row in payload['records'] if roots is None or row['table'] in roots]
    terms = query.casefold().split()
    rows = [row for row in rows if all(term in json.dumps(row['data'], ensure_ascii=False).casefold() for term in terms)]
    projected = []
    for row in rows[max(0, offset):max(0, offset) + max(1, min(500, limit))]:
        data = sync._safe(row['data'])
        key = row['table'] + ':' + row['source_id']
        title = next((data[k] for k in ('title', 'name', 'display_name', 'subject', 'folder_name', 'email') if data.get(k)), row['source_id'])
        content = '\n'.join(str(data[k]) for k in ('description', 'content_text', 'content', 'text', 'summary', 'notes') if data.get(k))
        source_route = '/api/v1/control/workspace-sync/source/' + dataset + '?' + urlencode({'key': key}) if row['table'] in SOURCE_TABLES else None
        fields = {k: str(data[k])[:2000] for k in ('display_name', 'first_name', 'last_name', 'email', 'phone', 'organization', 'company', 'relationship', 'location', 'start_at_utc', 'end_at_utc', 'status', 'price_cents', 'sku', 'file_type', 'created_at', 'updated_at') if data.get(k) is not None}
        projected.append({**fields, 'id': 'cloud:' + dataset + ':' + key, 'table': row['table'], 'source_id': row['source_id'], 'record_key': key,
                          'dataset': dataset, 'title': str(title)[:240], 'content': content[:2000], 'snippet': content[:900],
                          'authority_source': 'vp3_cloud', 'source_label': 'VP3 Cloud', 'read_only': True,
                          'source_route': source_route, 'synced_at': payload['synced_at'],
                          'attachments': [{'name': f['name'], 'sha256': f['sha256'], 'size_bytes': f['size_bytes']} for f in payload.get('files', []) if f['record_key'] == key]})
    return {'items': projected, 'count': len(rows), 'dataset': dataset, 'synced_at': payload['synced_at'], 'state': payload['state'], 'copied_schedules_execute': False}


def search(query: str, dataset: str = '', limit: int = 8, *, datasets: tuple[str, ...] | None = None) -> list[dict]:
    if not query.strip() or len(query) > 240:
        raise sync.WorkspaceSyncError('Enter a workspace query of 1 to 240 characters.')
    if (dataset and dataset not in sync.DATASETS) or (datasets and any(name not in sync.DATASETS for name in datasets)):
        raise sync.WorkspaceSyncError('Unknown workspace dataset.')
    import re
    stop = {'what','when','where','which','about','show','tell','have','does','please','with','from','your','this','that','the','and','for','my','me','is','in','of','to','a'}
    terms = [term for term in re.findall(r'\w+', query.casefold()) if term not in stop]
    if not terms:
        return []
    matches = []
    for name in (dataset,) if dataset else (datasets or sync.DATASETS):
        payload = snapshot(name)
        for row in payload['records']:
            data = sync._safe(row['data'])
            text = json.dumps(data, ensure_ascii=False)
            lowered = text.casefold()
            hits = sum(term in lowered for term in terms)
            if not hits:
                continue
            key = row['table'] + ':' + row['source_id']
            title = str(next((data[k] for k in ('title','name','display_name','subject') if data.get(k)), key))[:240]
            position = min(lowered.find(term) for term in terms if term in lowered)
            item = {'id':'cloud:'+name+':'+key,'dataset':name,'record_key':key,'title':title,
                    'authority_source':'vp3_cloud','synced_at':payload['synced_at'],
                    'excerpt':text[max(0,position-300):max(0,position-300)+1600]}
            matches.append((hits,item))
    matches.sort(key=lambda row:row[0], reverse=True)
    return [row[1] for row in matches[:max(1, min(20, limit))]]


def source_url(dataset: str, key: str, client: httpx.Client | None = None) -> str:
    # Check the current account's copy, then revalidate native ownership on Cloud.
    with SESSION_LOCK:
        payload = snapshot(dataset)
        if not any(row['table'] + ':' + row['source_id'] == key for row in payload['records']):
            raise sync.WorkspaceSyncError('Source record unavailable. Refresh this workspace.')
        session = load_https_session()
        state = sync.settings()
        peer = state['peer_id']
    own = client is None
    try:
        if client is None:
            client = httpx.Client(timeout=12.0, follow_redirects=False)
        result = sync._request(client, session, {'action': 'source', 'dataset': dataset, 'key': key})
        path = result.get('source_path')
        if not isinstance(path, str) or not re_source_path(path):
            raise sync.WorkspaceSyncError('This record has no native Cloud editor.')
        with SESSION_LOCK:
            if not sync._binding_active(session) or load_https_session() != session or sync.settings()['peer_id'] != peer or peer.rsplit('|', 1)[-1] != result.get('account_id'):
                raise sync.WorkspaceSyncError('Pairing changed. Refresh this workspace.')
        url = urljoin(session['endpoint'], '../' + path)
        if urlsplit(url).netloc != urlsplit(session['endpoint']).netloc:
            raise sync.WorkspaceSyncError('Invalid native workspace route.')
        return url
    except (httpx.HTTPError, ValueError) as exc:
        raise sync.WorkspaceSyncError('Cloud editor unavailable. Try again when Cloud is reachable.') from exc
    finally:
        if own and client is not None:
            client.close()


def re_source_path(path: str) -> bool:
    import re
    return bool(re.fullmatch(r'[a-z0-9-]+\.php(?:\?[a-zA-Z0-9_=&%.-]+)?', path))
