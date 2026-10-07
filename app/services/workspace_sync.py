"""Background, account-bound workspace replicas. Native data remains authoritative.

A replica is readable offline; it never becomes an execution queue or a second
schedule runner. Network traffic and optional errors cannot block bridge polling.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import re
import threading
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit
from typing import Any

import httpx

from ..config import settings as app_settings
from ..database import db
from .https_bridge_session import SESSION_LOCK, load_https_session
from .remote_identity import load_or_create_remote_identity

CONTRACT = 'vp3.workspace-sync.v1'
CHUNK_BYTES = 65536
ASSET_CHUNK_BYTES = 1048576
MAX_BYTES = 64 * 1024 * 1024
DATASETS = ('profile','contacts','crm','knowledge','transcriptions','calendar','schedules','meetings',
            'products','orders','agents','chats','notifications','music','workspace_other','artist_workspace')
OUTBOUND = ('contacts','knowledge','transcriptions','calendar','schedules','meetings','agents','chats','notifications')
_SECRET = re.compile(r'password|secret|token|credential|ciphertext|api_key|private_key|authorization|cookie|session_key|invite_key|download_key|encrypted|_enc$|bearer|signed_url|jwt|nonce|embedding|descriptor|voice_signature|raw_audio|face_samples', re.I)
_STOP = threading.Event()
_WAKE = threading.Event()
_THREAD: threading.Thread | None = None
_RUN_LOCK = threading.Lock()
_LIFECYCLE_LOCK = threading.Lock()
_ASSET_VERIFIED: dict[str,tuple[int,int]] = {}


class WorkspaceSyncError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def settings() -> dict:
    with db() as connection:
        row = connection.execute('SELECT * FROM workspace_sync_settings WHERE id=1').fetchone()
    value = dict(row)
    value['enabled'] = bool(value['enabled'])
    return value


def set_enabled(enabled: bool) -> dict:
    # The same lock orders opt-out against a final replica commit/upload.
    with SESSION_LOCK, db() as connection:
        connection.execute('UPDATE workspace_sync_settings SET enabled=?,updated_at=? WHERE id=1',(int(enabled),now()))
    _WAKE.set()
    return status()


def status() -> dict:
    from . import workspace_actions
    state = settings()
    with db() as connection:
        rows = connection.execute('SELECT dataset,record_count,synced_at FROM workspace_sync_snapshots WHERE peer_id=? ORDER BY dataset',(state['peer_id'],)).fetchall()
        deliveries = connection.execute('SELECT dataset,synced_at FROM workspace_sync_delivery WHERE peer_id=?',(state['peer_id'],)).fetchall()
    session = load_https_session()
    paired = session is not None
    verified = bool(session and state['session_hash']==hashlib.sha256(session['session_token'].encode()).hexdigest())
    return {'contract':CONTRACT,'enabled':state['enabled'],'paired':paired,
            'state':'paused' if not state['enabled'] else ('not_paired' if not paired else ('retrying' if state['last_error'] else ('synced' if verified and state['last_success_at'] else 'waiting'))),
            'last_attempt_at':state['last_attempt_at'],'last_success_at':state['last_success_at'] if verified else None,
            'last_error':state['last_error'],'cloud_datasets':[dict(row) for row in rows] if verified else [],
            'homeserver_datasets':[dict(row) for row in deliveries] if verified else [],
            'supported_datasets':list(DATASETS),'source_authority_preserved':True,'copied_schedules_execute':False,
            'edits':workspace_actions.status()}


def _safe(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, child in value.items():
            if _SECRET.search(str(key)) or key in ('source_path','root_path','device_path','stored_name'):
                continue
            if isinstance(child, str) and (str(key).endswith('_json') or key=='metadata'):
                try:
                    decoded = json.loads(child)
                    if isinstance(decoded, (dict,list)):
                        child = json.dumps(_safe(decoded),ensure_ascii=False,separators=(',',':'))
                except (ValueError, TypeError):
                    pass
            out[key] = _safe(child)
        return out
    if isinstance(value, list):
        return [_safe(child) for child in value]
    return value


def _authority() -> dict:
    from . import app_scopes, knowledge_collections
    with db() as connection:
        row=connection.execute("SELECT id FROM paired_apps WHERE app_key='vp3' AND status='active'").fetchone()
        if row is None:
            return {'active':False,'permissions':[],'scope':{}}
        permissions=connection.execute('SELECT permission FROM app_permissions WHERE paired_app_id=? AND allowed=1 ORDER BY permission',(row['id'],)).fetchall()
    return {'active':True,'permissions':[item['permission'] for item in permissions],'scope':app_scopes.get_scope(row['id']),'app_id':row['id'],'collection_scope':knowledge_collections.app_collection_scope(row['id'])}


def _authority_hash() -> str:
    return hashlib.sha256(json.dumps(_authority(),sort_keys=True,separators=(',',':')).encode()).hexdigest()


def local_snapshot(dataset: str) -> dict:
    selectors = {
        'contacts':[('contacts','1=1')],
        'knowledge':[('knowledge_items','1=1'),('knowledge_collections','1=1'),('knowledge_collection_items','1=1')],
        'calendar':[('local_calendar_events','1=1'),('tasks','1=1')],
        'schedules':[('automation_routines','1=1'),('automation_rules','1=1')],
        'agents':[('agents','1=1')],
        'chats':[('conversations',"source_app_key='owner'"),('conversation_messages',"conversation_id IN (SELECT id FROM conversations WHERE source_app_key='owner')")],
        'notifications':[('notifications','1=1')],
    }
    authority=_authority()
    fingerprint=hashlib.sha256(json.dumps(authority,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    required={'contacts':{'contacts.read'},'knowledge':{'knowledge.search'},'transcriptions':{'knowledge.search'},'calendar':{'events.read','tasks.read'},'schedules':{'awareness.read'},'meetings':{'knowledge.search'},'agents':{'memory.read'},'chats':{'agent.chat'},'notifications':{'notifications.read'}}
    allowed=authority['active'] and authority['scope'].get('cloud_allowed',False) and required.get(dataset,set()).issubset(set(authority['permissions']))
    if dataset in ('transcriptions','meetings'):
        from . import app_scopes
        allowed=allowed and app_scopes.knowledge_kind_allowed(authority['scope'],'transcript' if dataset=='transcriptions' else 'meeting')
    records=[]
    with db() as connection:
        connection.execute('BEGIN')
        if not allowed:
            pass
        elif dataset=='transcriptions':
            # Existing per-document privacy remains authoritative. Active capture is never shared.
            from . import local_transcription_sessions
            rows=connection.execute("SELECT id FROM local_transcription_sessions WHERE cloud_share=1 AND status='completed' ORDER BY id").fetchall()
            for row in rows:
                payload=local_transcription_sessions.get(row['id'],paired=True)['session']
                records.append({'table':'local_transcription_sessions','source_id':str(row['id']),'data':_safe(payload)})
        elif dataset=='meetings':
            # Owner-created meeting cards already contain bounded, inspectable text summaries.
            rows=connection.execute("SELECT id,metadata_json,created_at FROM conversation_messages WHERE source_app_key='owner' AND model='vp3-meeting-intelligence' ORDER BY id").fetchall()
            for row in rows:
                card=json.loads(row['metadata_json'] or '{}')
                records.append({'table':'meeting_cards','source_id':str(row['id']),'data':_safe({**card,'created_at':row['created_at']})})
        else:
            for table,predicate in selectors.get(dataset,[]):
                if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
                    continue
                for row in connection.execute(f'SELECT * FROM {table} WHERE {predicate}').fetchall():
                    if dataset=='knowledge' and table=='knowledge_items':
                        from . import knowledge_collection_policy
                        if not knowledge_collection_policy.filter_items_for_app(authority['app_id'],[dict(row)]):
                            continue
                    if dataset=='knowledge' and table in ('knowledge_collections','knowledge_collection_items'):
                        # Folder associations are projected from the same allowed item set.
                        from . import knowledge_collection_policy
                        keys=knowledge_collection_policy.allowed_collection_keys(authority['app_id'])
                        if table=='knowledge_collections' and keys is not None and row['collection_key'] not in keys:continue
                        if table=='knowledge_collection_items':
                            item=connection.execute('SELECT id,kind FROM knowledge_items WHERE id=?',(row['knowledge_item_id'],)).fetchone()
                            collection=connection.execute('SELECT collection_key FROM knowledge_collections WHERE id=?',(row['collection_id'],)).fetchone()
                            if item is None or not knowledge_collection_policy.filter_items_for_app(authority['app_id'],[dict(item)]):continue
                            if keys is not None and (collection is None or collection['collection_key'] not in keys):continue
                    data=_safe(dict(row))
                    records.append({'table':table,'source_id':str(data.get('id',hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest())),'data':data})
    records.sort(key=lambda row:(row['table'],row['source_id']))
    body=json.dumps({'contract':CONTRACT,'source':'homeserver','dataset':dataset,'records':records},ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
    if len(body)>MAX_BYTES:
        raise WorkspaceSyncError('Workspace dataset exceeds transfer capacity; previous copy preserved.')
    return {'body':body,'revision':hashlib.sha256(body).hexdigest(),'record_count':len(records),'byte_count':len(body),'authority_fingerprint':fingerprint}


def _validate_snapshot(raw: bytes, dataset: str, revision: str) -> dict:
    if len(raw)>MAX_BYTES or hashlib.sha256(raw).hexdigest()!=revision:
        raise WorkspaceSyncError('Workspace checksum mismatch; previous copy preserved.')
    try:
        data=json.loads(raw)
    except (ValueError,UnicodeDecodeError) as exc:
        raise WorkspaceSyncError('Invalid workspace document.') from exc
    if not isinstance(data,dict) or data.get('contract')!=CONTRACT or data.get('source')!='cloud' or data.get('dataset')!=dataset or not isinstance(data.get('records'),list):
        raise WorkspaceSyncError('Invalid workspace snapshot.')
    seen=set()
    for row in data['records']:
        if not isinstance(row,dict) or not isinstance(row.get('table'),str) or not isinstance(row.get('source_id'),str) or not isinstance(row.get('data'),dict):
            raise WorkspaceSyncError('Invalid workspace record.')
        key=(row['table'],row['source_id'])
        if key in seen:
            raise WorkspaceSyncError('Duplicate workspace record.')
        seen.add(key)
    return data


def apply_snapshot(peer_id: str, dataset: str, raw: bytes, revision: str) -> None:
    if dataset not in DATASETS:
        raise WorkspaceSyncError('Unknown workspace dataset.')
    data=_validate_snapshot(raw,dataset,revision)
    with db() as connection:
        connection.execute('INSERT INTO workspace_sync_snapshots(peer_id,dataset,revision,body_json,record_count,synced_at) VALUES(?,?,?,?,?,?) '
                           'ON CONFLICT(peer_id,dataset) DO UPDATE SET revision=excluded.revision,body_json=excluded.body_json,record_count=excluded.record_count,synced_at=excluded.synced_at',
                           (peer_id,dataset,revision,raw.decode('utf-8'),len(data['records']),now()))


def asset_dir() -> Path:
    return app_settings.data_dir / 'workspace-assets'


def _download_assets(client: httpx.Client, session: dict, dataset: str, raw: bytes, revision: str) -> None:
    payload=_validate_snapshot(raw,dataset,revision)
    files=payload.get('files',[])
    if not isinstance(files,list):
        raise WorkspaceSyncError('Invalid attachment manifest.')
    directory=asset_dir();directory.mkdir(parents=True,exist_ok=True)
    for asset in files:
        if not isinstance(asset,dict) or not isinstance(asset.get('sha256'),str) or not re.fullmatch('[a-f0-9]{64}',asset['sha256']) or not isinstance(asset.get('asset_id'),str) or not re.fullmatch('[a-f0-9]{64}',asset['asset_id']) or type(asset.get('size_bytes')) is not int or not 0<=asset['size_bytes']<=4*1024**3:
            raise WorkspaceSyncError('Invalid attachment identity.')
        target=directory / asset['sha256']
        size=asset['size_bytes']
        if target.is_file() and target.stat().st_size==size:
            stat=target.stat();identity=(stat.st_size,stat.st_mtime_ns)
            if _ASSET_VERIFIED.get(asset['sha256'])==identity:
                continue
            with target.open('rb') as handle:
                if hashlib.file_digest(handle,'sha256').hexdigest()==asset['sha256']:
                    _ASSET_VERIFIED[asset['sha256']]=identity
                    continue
        descriptor,staged=tempfile.mkstemp(prefix='transfer-',dir=directory)
        digest=hashlib.sha256()
        try:
            with os.fdopen(descriptor,'wb') as handle:
                for offset in range(0,size,ASSET_CHUNK_BYTES) if size else [0]:
                    result=_request(client,session,{'action':'asset','dataset':dataset,'revision':revision,'asset_id':asset['asset_id'],'offset':offset})
                    if result.get('asset_id')!=asset['asset_id'] or result.get('sha256')!=asset['sha256'] or result.get('revision')!=revision or result.get('offset')!=offset or result.get('total_bytes')!=size:
                        raise WorkspaceSyncError('Attachment transfer identity changed.')
                    try:chunk=base64.b64decode(result['chunk'],validate=True)
                    except (ValueError,TypeError,KeyError) as exc:raise WorkspaceSyncError('Invalid attachment chunk.') from exc
                    if len(chunk)!=min(ASSET_CHUNK_BYTES,size-offset):
                        raise WorkspaceSyncError('Attachment chunk size mismatch.')
                    handle.write(chunk);digest.update(chunk)
                handle.flush();os.fsync(handle.fileno())
            if digest.hexdigest()!=asset['sha256']:
                raise WorkspaceSyncError('Attachment checksum mismatch; previous file preserved.')
            with SESSION_LOCK:
                if not _binding_active(session):
                    raise WorkspaceSyncError('Pairing changed before attachment commit.')
                os.replace(staged,target)
                stat=target.stat();_ASSET_VERIFIED[asset['sha256']]=(stat.st_size,stat.st_mtime_ns)
        finally:
            Path(staged).unlink(missing_ok=True)


def asset(sha256: str) -> tuple[Path,str]:
    if not re.fullmatch('[a-f0-9]{64}',sha256):
        raise WorkspaceSyncError('Invalid attachment identity.')
    state=settings();session=load_https_session()
    authority=_authority()
    if not session or not authority['active'] or not authority['scope'].get('cloud_allowed',False) or state['session_hash']!=hashlib.sha256(session['session_token'].encode()).hexdigest():
        raise WorkspaceSyncError('Attachment unavailable for the current pairing.')
    with db() as connection:
        rows=connection.execute('SELECT body_json FROM workspace_sync_snapshots WHERE peer_id=?',(state['peer_id'],)).fetchall()
    for row in rows:
        for item in json.loads(row['body_json']).get('files',[]):
            if item.get('sha256')==sha256:
                path=asset_dir()/sha256
                if path.is_file():
                    return path,Path(item.get('name') or 'attachment').name
    raise WorkspaceSyncError('Attachment unavailable.')


def _prune_assets(peer: str) -> None:
    referenced=set()
    with db() as connection:
        rows=connection.execute('SELECT body_json FROM workspace_sync_snapshots WHERE peer_id=?',(peer,)).fetchall()
    for row in rows:
        referenced.update(asset['sha256'] for asset in json.loads(row['body_json']).get('files',[]))
    directory=asset_dir()
    if directory.is_dir():
        for path in directory.iterdir():
            if path.is_file() and re.fullmatch('[a-f0-9]{64}',path.name) and path.name not in referenced:
                path.unlink(missing_ok=True);_ASSET_VERIFIED.pop(path.name,None)


def _binding_active(session: dict) -> bool:
    authority=_authority()
    return not _STOP.is_set() and settings()['enabled'] and load_https_session()==session and authority['active'] and authority['scope'].get('cloud_allowed',False)


def _request(client: httpx.Client, session: dict, body: dict) -> dict:
    if not _binding_active(session):
        raise WorkspaceSyncError('Workspace connection changed; retry with the current pairing.')
    url=urljoin(session['endpoint'],'homeserver-workspace-sync-v1.php')
    # Derive from the already validated relay directory; never accept a remote URL override.
    a,b=urlsplit(session['endpoint']),urlsplit(url)
    if (a.scheme,a.netloc)!=(b.scheme,b.netloc):
        raise WorkspaceSyncError('Workspace endpoint is invalid.')
    device=load_or_create_remote_identity()['device_id']
    response=client.post(url,json=body,headers={'Authorization':'Bearer '+session['session_token'],
      'X-VP3-HOMESERVER-SESSION':session['session_token'],'X-HomeServer-Device':device})
    if response.status_code==404:
        raise WorkspaceSyncError('Update VP3 Cloud to enable workspace sync.')
    if not response.is_success:
        raise WorkspaceSyncError(f'Workspace sync unavailable ({response.status_code}); previous copy preserved.',response.status_code)
    if len(response.content)>1500000:
        raise WorkspaceSyncError('Workspace response exceeds transfer limits.')
    data=response.json()
    if not isinstance(data,dict) or data.get('ok') is not True or data.get('contract')!=CONTRACT:
        raise WorkspaceSyncError('Invalid workspace sync response.')
    return data


def sync_once(client: httpx.Client | None = None) -> dict:
    if not _RUN_LOCK.acquire(blocking=False):
        return {'busy':True}
    own_client=client is None
    try:
        session=load_https_session()
        if not session or not settings()['enabled']:
            return {'skipped':True}
        with db() as connection:
            connection.execute('UPDATE workspace_sync_settings SET last_attempt_at=? WHERE id=1',(now(),))
        if client is None:
            client=httpx.Client(timeout=12.0,follow_redirects=False)
        catalog=_request(client,session,{'action':'catalog'})
        account=catalog.get('account_id')
        if not isinstance(account,str) or not re.fullmatch(r'[1-9][0-9]{0,19}',account) or catalog.get('datasets')!=list(DATASETS):
            raise WorkspaceSyncError('Cloud workspace catalog is incompatible.')
        origin=urlsplit(session['endpoint'])
        peer=f'{origin.scheme}://{origin.netloc}|{account}'
        with SESSION_LOCK:
            if not _binding_active(session):
                raise WorkspaceSyncError('Workspace pairing changed.')
            with db() as connection:
                state=settings()
                if state['peer_id']!=peer:
                    # New account never sees the previous account's offline workspace.
                    connection.execute('DELETE FROM workspace_sync_snapshots')
                    connection.execute('DELETE FROM workspace_sync_delivery')
                    connection.execute('UPDATE workspace_sync_settings SET peer_id=?,last_success_at=NULL WHERE id=1',(peer,))
                connection.execute('UPDATE workspace_sync_settings SET session_hash=? WHERE id=1',(hashlib.sha256(session['session_token'].encode()).hexdigest(),))
        remote_deliveries={row['dataset']:row['revision'] for row in catalog.get('homeserver',[]) if isinstance(row,dict) and row.get('dataset') in DATASETS}
        from . import workspace_actions
        workspace_actions.deliver(client,session)
        for dataset in DATASETS:
            manifest=_request(client,session,{'action':'prepare','dataset':dataset})
            revision=manifest.get('revision');total=manifest.get('byte_count')
            if not isinstance(revision,str) or not re.fullmatch('[a-f0-9]{64}',revision) or type(total) is not int or not 1<=total<=MAX_BYTES:
                raise WorkspaceSyncError('Invalid workspace manifest.')
            with db() as connection:
                before=connection.execute('SELECT revision,body_json FROM workspace_sync_snapshots WHERE peer_id=? AND dataset=?',(peer,dataset)).fetchone()
            if before is None or before['revision']!=revision:
                raw=bytearray()
                for offset in range(0,total,CHUNK_BYTES):
                    result=_request(client,session,{'action':'pull','dataset':dataset,'revision':revision,'offset':offset})
                    if result.get('revision')!=revision or result.get('offset')!=offset or result.get('total_bytes')!=total:
                        raise WorkspaceSyncError('Workspace transfer identity changed.')
                    try:
                        chunk=base64.b64decode(result['chunk'],validate=True)
                    except (KeyError,ValueError,TypeError) as exc:
                        raise WorkspaceSyncError('Invalid workspace chunk.') from exc
                    if len(chunk)!=min(CHUNK_BYTES,total-offset):
                        raise WorkspaceSyncError('Workspace chunk size mismatch.')
                    raw.extend(chunk)
                with SESSION_LOCK:
                    if not _binding_active(session):
                        raise WorkspaceSyncError('Workspace pairing changed before commit.')
                _download_assets(client,session,dataset,bytes(raw),revision)
                with SESSION_LOCK:
                    if not _binding_active(session):
                        raise WorkspaceSyncError('Pairing changed before dataset commit.')
                    apply_snapshot(peer,dataset,bytes(raw),revision)
            else:
                _download_assets(client,session,dataset,before['body_json'].encode('utf-8'),revision)
                with SESSION_LOCK:
                    if not _binding_active(session):
                        raise WorkspaceSyncError('Workspace pairing changed.')
                    with db() as connection:
                        connection.execute('UPDATE workspace_sync_snapshots SET synced_at=? WHERE peer_id=? AND dataset=?',(now(),peer,dataset))
            workspace_actions.reconcile(peer,dataset,json.loads(bytes(raw).decode('utf-8') if before is None or before['revision']!=revision else before['body_json']))
            if dataset in OUTBOUND:
                snapshot=local_snapshot(dataset)
                if remote_deliveries.get(dataset)!=snapshot['revision']:
                    result={}
                    for offset in range(0,snapshot['byte_count'],CHUNK_BYTES):
                        # The Cloud revalidates account authority at commit. No network
                        # request holds the local session lock or blocks a heartbeat.
                        if snapshot['authority_fingerprint']!=_authority_hash():
                            raise WorkspaceSyncError('Workspace permissions changed; restarting synchronization.')
                        result=_request(client,session,{'action':'push','dataset':dataset,'revision':snapshot['revision'],
                              'offset':offset,'total_bytes':snapshot['byte_count'],'chunk':base64.b64encode(snapshot['body'][offset:offset+CHUNK_BYTES]).decode('ascii')})
                        if result.get('revision')!=snapshot['revision']:
                            raise WorkspaceSyncError('Workspace receipt identity mismatch.')
                    if result.get('committed') is not True:
                        raise WorkspaceSyncError('Cloud has not acknowledged the complete workspace dataset.')
                with SESSION_LOCK:
                    if not _binding_active(session):
                        raise WorkspaceSyncError('Workspace pairing changed before receipt.')
                    with db() as connection:
                        connection.execute('INSERT INTO workspace_sync_delivery(peer_id,dataset,revision,synced_at) VALUES(?,?,?,?) '
                          'ON CONFLICT(peer_id,dataset) DO UPDATE SET revision=excluded.revision,synced_at=excluded.synced_at',(peer,dataset,snapshot['revision'],now()))
        _prune_assets(peer)
        with SESSION_LOCK:
            if not _binding_active(session):
                raise WorkspaceSyncError('Workspace pairing changed before completion.')
            with db() as connection:
                connection.execute("UPDATE workspace_sync_settings SET last_success_at=?,last_error='' WHERE id=1",(now(),))
        return {'ok':True,'dataset_count':len(DATASETS)}
    except Exception as exc:
        # Avoid secret-bearing provider/network exception messages in UI or reports.
        message=str(exc) if isinstance(exc,WorkspaceSyncError) else 'Workspace sync temporarily unavailable; retrying automatically.'
        try:
            with db() as connection:
                connection.execute('UPDATE workspace_sync_settings SET last_error=? WHERE id=1',(message,))
        except Exception:
            pass  # A damaged/unavailable DB must not terminate the independent retry worker.
        return {'ok':False,'error':message}
    finally:
        if own_client and client is not None:
            client.close()
        _RUN_LOCK.release()


def records(dataset: str, query: str='', offset: int=0, limit: int=50, *, detail_key: str='') -> dict:
    if dataset not in DATASETS:
        raise WorkspaceSyncError('Unknown workspace dataset.')
    state=settings()
    session=load_https_session()
    authority=_authority()
    if not session or not authority['active'] or not authority['scope'].get('cloud_allowed',False) or state['session_hash']!=hashlib.sha256(session['session_token'].encode()).hexdigest():
        return {'items':[],'count':0,'dataset':dataset,'source':'cloud','offline':True,'state':'not_paired'}
    with db() as connection:
        row=connection.execute('SELECT body_json,synced_at FROM workspace_sync_snapshots WHERE peer_id=? AND dataset=?',(state['peer_id'],dataset)).fetchone()
    payload=json.loads(row['body_json']) if row else {}
    items=payload.get('records',[])
    files=payload.get('files',[])
    if detail_key:
        items=[item for item in items if item['table']+':'+item['source_id']==detail_key]
    if query:
        items=[item for item in items if query.casefold() in json.dumps(item['data'],ensure_ascii=False).casefold()]
    page=items[max(0,offset):max(0,offset)+max(1,min(limit,100))]
    if not detail_key:
        page=[{'table':item['table'],'source_id':item['source_id'],'title':str(next((item['data'][key] for key in ('title','name','display_name','workspace_name','folder_name','subject','order_number','email') if item['data'].get(key)),item['source_id']))[:240],
               'preview':str(next((item['data'][key] for key in ('description','content_text','content','message','text','summary','notes') if item['data'].get(key)),''))[:300],
               'updated_at':item['data'].get('updated_at') or item['data'].get('created_at')} for item in page]
    selected_keys={item['table']+':'+item['source_id'] for item in page}
    attachments=[{'name':file['name'],'sha256':file['sha256'],'size_bytes':file['size_bytes'],'record_key':file['record_key']} for file in files if file['record_key'] in selected_keys]
    return {'items':page,'attachments':attachments,'count':len(items),'dataset':dataset,
            'source':'cloud','read_only':True,'synced_at':row['synced_at'] if row else None,'offline':True}


def _run() -> None:
    retry=5
    while not _STOP.is_set():
        outcome=sync_once()
        interval=60 if outcome.get('ok') or outcome.get('skipped') else retry
        retry=5 if outcome.get('ok') else min(retry*2,300)
        _WAKE.wait(interval)
        _WAKE.clear()


def start() -> None:
    global _THREAD
    with _LIFECYCLE_LOCK:
        if _THREAD and _THREAD.is_alive():
            return
        _STOP.clear();_WAKE.clear()
        _THREAD=threading.Thread(target=_run,name='homeserver-workspace-sync',daemon=True)
        _THREAD.start()


def stop() -> None:
    _STOP.set();_WAKE.set()
    thread=_THREAD
    if thread and thread is not threading.current_thread():
        thread.join(timeout=2.0)


def wake() -> None:
    _WAKE.set()
