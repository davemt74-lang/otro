"""Resolve canonical Cloud scopes and source revisions; store only conversation preferences."""
from __future__ import annotations
import hashlib
import json
from urllib.parse import urlsplit, urlunsplit
import httpx
from ..database import db
from . import brain, project_knowledge_r5 as r5, workspace_sync, app_scopes, knowledge_collection_policy, agent_routing
from .https_bridge_session import load_https_session, https_session_matches
from .remote_identity import load_or_create_remote_identity

CONTRACT='vp3.project-knowledge.r6'

def _delegate_authority(source: str, permissions: set[str], cloud_allowed: bool) -> None:
    authority=workspace_sync._authority()
    app_id=knowledge_collection_policy.app_id_for_source(source)
    scope=app_scopes.get_scope(app_id) if app_id else {}
    if (not cloud_allowed or source!='app:vp3' or 'knowledge.search' not in permissions
        or not authority['active'] or app_id!=authority.get('app_id')
        or 'knowledge.search' not in authority['permissions'] or not scope.get('cloud_allowed')
        or not app_scopes.knowledge_kind_allowed(scope,'note')
        or knowledge_collection_policy.allowed_collection_keys(app_id) is not None):
        raise workspace_sync.WorkspaceSyncError('Project Knowledge delegation is not authorized.',403)

def request(body: dict, *, delegated: tuple[str,set[str],bool]|None=None, client: httpx.Client|None=None) -> dict:
    if body.get('action') not in {'options','set_context','resolve'}:
        raise workspace_sync.WorkspaceSyncError('Unknown conversation Knowledge action.')
    conversation=str(body.get('local_conversation') or '');agent_id=body.get('parent_agent_id') or 0
    def authorize():
        if delegated:_delegate_authority(*delegated)
        else:r5.authorized(conversation,agent_id)
    authorize()
    session=load_https_session();suffix='/api/homeserver-https-poll-v1300.php'
    if not session or not session['endpoint'].endswith(suffix):
        raise workspace_sync.WorkspaceSyncError('Connect the matching Cloud release.',409)
    allowed={'action','local_conversation','parent_agent_id','conversation_id','agent_id','scope','expected_revision','state_revision','attachments','query','q'}
    payload={k:v for k,v in body.items() if k in allowed}
    if delegated is None:
        agent_id=int(agent_routing.resolve_agent('owner',agent_id or None,owner=True)['id'])
        payload['parent_agent_id']=agent_id
    if len(json.dumps(payload).encode())>32000:raise workspace_sync.WorkspaceSyncError('Context request is too large.',413)
    token=session['session_token'];device=load_or_create_remote_identity()['device_id']
    def send(c):return c.post(session['endpoint'][:-len(suffix)]+'/api/project-knowledge-r6.php',json=payload,
        headers={'Authorization':'Bearer '+token,'X-HomeServer-Device':device,'Accept':'application/json'})
    try:
        if client is not None:response=send(client)
        else:
            with httpx.Client(timeout=30.0,follow_redirects=False) as c:response=send(c)
        result=response.json()
    except (httpx.HTTPError,ValueError) as exc:
        raise workspace_sync.WorkspaceSyncError('Cloud context is unavailable. Retry after reconnecting.',503) from exc
    if not https_session_matches(token):raise workspace_sync.WorkspaceSyncError('The Cloud pairing changed.',409)
    authorize()
    if not response.is_success or not isinstance(result,dict) or not result.get('ok') or result.get('contract')!=CONTRACT:
        error=str(result.get('error') or 'Update the matching Project Knowledge release.')[:300] if isinstance(result,dict) else 'Invalid context response.'
        raise workspace_sync.WorkspaceSyncError(error,response.status_code if response.status_code in {401,403,409,410,422} else 502)
    origin=urlsplit(session['endpoint']);base=urlunsplit((origin.scheme,origin.netloc,'','',''))
    result['cloud_origin']=base
    for s in result.get('sources',[]):
        if isinstance(s.get('url'),str) and s['url'].startswith('/') and not s['url'].startswith('//'):s['url']=base+s['url']
    if 'state' in result:
        expected='cloud:'+str(payload['conversation_id']) if int(payload.get('conversation_id') or 0)>0 else 'home:'+hashlib.sha256(device.encode()).hexdigest()[:32]+':'+str(payload.get('parent_agent_id') or 0)+':'+conversation
        if result['state'].get('context_key')!=expected:raise workspace_sync.WorkspaceSyncError('The canonical conversation context changed.',409)
    if body['action']=='set_context' and delegated is None:
        with db() as c:
            c.execute('INSERT INTO conversation_knowledge_context_r6(conversation_id,agent_id,peer_id,mode) VALUES(?,?,?,?) ON CONFLICT(conversation_id) DO UPDATE SET agent_id=excluded.agent_id,peer_id=excluded.peer_id,mode=excluded.mode,updated_at=CURRENT_TIMESTAMP',
                (conversation,agent_id,workspace_sync.settings()['peer_id'],result['state']['scope']['mode']))
    return result

def owner_packet(conversation: str, agent_id: int, query: str, selection: dict|None) -> tuple[dict|None,dict|None]:
    with db() as c:row=c.execute('SELECT * FROM conversation_knowledge_context_r6 WHERE conversation_id=?',(conversation,)).fetchone()
    if row and row['mode']!='all' and (row['peer_id']!=workspace_sync.settings()['peer_id'] or row['agent_id']!=agent_id):
        raise workspace_sync.WorkspaceSyncError('Choose a Knowledge context for the current pairing and lead.',409)
    if selection is None and (not row or row['mode']=='all'):return None,None
    selection=dict(selection or {})
    args={'action':'resolve','local_conversation':conversation,'parent_agent_id':agent_id,'query':query,
        'attachments':selection.get('attachments',[])}
    if 'state_revision' in selection:args['state_revision']=selection['state_revision']
    packet=request(args);validate_packet(packet,query)
    return packet,args

def delegated_packet(pointer: dict, query: str, source: str, permissions: set[str], cloud_allowed: bool) -> tuple[dict,dict]:
    if pointer.get('query')!=query:raise workspace_sync.WorkspaceSyncError('The context question changed.',409)
    args={**pointer,'action':'resolve'}
    packet=request(args,delegated=(source,permissions,cloud_allowed));validate_packet(packet,query)
    if packet['fingerprint']!=pointer.get('fingerprint'):raise workspace_sync.WorkspaceSyncError('The delegated source revisions changed.',409)
    return packet,args

def validate_packet(packet: dict,query: str) -> None:
    if packet.get('query_hash')!=hashlib.sha256(query.encode()).hexdigest() or not isinstance(packet.get('sources'),list) or len(packet['sources'])>6 or not isinstance(packet.get('context_text'),str) or len(packet['context_text'])>6000:
        raise workspace_sync.WorkspaceSyncError('The canonical context packet is invalid.',409)
    for i,s in enumerate(packet['sources'],1):
        try:
            u=urlsplit(s['url'])
            if u.scheme not in {'http','https'} or not u.hostname or u.username or u.password or s['label']!='P'+str(i):raise ValueError()
        except (ValueError,KeyError,TypeError) as exc:raise workspace_sync.WorkspaceSyncError('A source citation is invalid.',409) from exc

def apply_packet(canonical, packet: dict, budget: int) -> None:
    if not packet.get('active'):return
    text=packet['context_text']
    if canonical.total_context_chars+len(text)>budget:raise workspace_sync.WorkspaceSyncError('Increase the context budget or attach fewer sources.',422)
    if text:
        canonical.bundle.knowledge.append({'id':'project-context-r6','title':packet['state']['title'],'content':text,'kind':'project_context'})
        canonical.bundle.context_chars+=len(text)
    for s in packet['sources']:
        canonical.source_refs.append({'kind':'knowledge','source_kind':s['kind'],'id':'r6:'+s['kind']+':'+s['id'],'title':s['title'],
            'label':s['label'],'source_url':s['url'],'captured_at':s['captured_at'],'record_revision':s['revision'],
            'sync':s.get('sync',{}),'excerpt':s['excerpt']})
    canonical.provenance.append({'kind':'project_knowledge_r6','source':'paired_cloud','fingerprint':packet['fingerprint'],'scope':packet['state']['scope']})
    canonical.budget['used_chars']=canonical.total_context_chars+len(text)
    canonical.budget['max_context_chars']=budget;canonical.budget['remaining_chars']=budget-canonical.total_context_chars
    canonical.effective_settings['max_context_chars']=budget
    canonical.effective_settings['include_knowledge']=packet['state']['scope']['mode']!='off'
    canonical.bundle.settings['include_knowledge']=packet['state']['scope']['mode']!='off'

def recheck(packet: dict, args: dict, *, delegated: tuple[str,set[str],bool]|None=None) -> None:
    fresh=request(args,delegated=delegated)
    if fresh.get('fingerprint')!=packet['fingerprint']:
        raise workspace_sync.WorkspaceSyncError('Project Knowledge changed during the answer. Review current sources.',409)

def public(packet: dict|None) -> dict|None:
    if packet is None:return None
    return {k:packet[k] for k in ['contract','state','active','sources','fingerprint','cloud_origin']}

def requires_cloud(conversation: str) -> bool:
    with db() as c:row=c.execute('SELECT mode FROM conversation_knowledge_context_r6 WHERE conversation_id=?',(conversation,)).fetchone()
    return bool(row and row['mode'] in {'project','folder'})

def local_mode(conversation: str) -> str:
    with db() as c:row=c.execute('SELECT mode FROM conversation_knowledge_context_r6 WHERE conversation_id=?',(conversation,)).fetchone()
    return str(row['mode']) if row else 'all'
