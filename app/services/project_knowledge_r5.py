"""Canonical annotation edits and source citations; replicas remain read only."""
from __future__ import annotations
import hashlib
import json
import re
from urllib.parse import urlsplit
from uuid import UUID
import httpx
from ..database import db
from . import agent_routing, app_scopes, context_engine, native_workspaces, workspace_sync
from .https_bridge_session import load_https_session, https_session_matches
from .remote_identity import load_or_create_remote_identity

CONTRACT='vp3.project-knowledge.r5'


def authorized(conversation: str, agent_id: int) -> None:
    if not conversation or len(conversation)>100:
        raise workspace_sync.WorkspaceSyncError('Open a saved Chat conversation before using shared Knowledge.',409)
    try:
        parent=agent_routing.resolve_agent('owner',agent_id or None,owner=True)
        agent_routing.validate_conversation_agent('owner',conversation,int(parent['id']))
    except agent_routing.AgentRoutingError as exc:
        raise workspace_sync.WorkspaceSyncError('The Chat conversation or lead is unavailable.',403) from exc
    settings=context_engine.ensure_settings(conversation)
    if not settings['cloud_allowed'] or not settings['include_knowledge'] or not workspace_sync.settings()['enabled']:
        raise workspace_sync.WorkspaceSyncError('Shared Knowledge is disabled for this conversation.',403)
    authority=workspace_sync._authority()
    if not authority['active'] or not authority['scope'].get('cloud_allowed',False) or 'agent.chat' not in authority['permissions']:
        raise workspace_sync.WorkspaceSyncError('Pairing or Chat permission is unavailable.',403)


def request(body: dict, client: httpx.Client | None=None) -> dict:
    action=body.get('action')
    if action not in {'list','edit'}:raise workspace_sync.WorkspaceSyncError('Unsupported annotation operation.')
    conversation=body.get('local_conversation');agent_id=body.get('parent_agent_id') or 0
    if not isinstance(conversation,str) or type(agent_id) is not int:raise workspace_sync.WorkspaceSyncError('Choose the current Chat conversation.')
    authorized(conversation,agent_id)
    session=load_https_session()
    if not session:raise workspace_sync.WorkspaceSyncError('Connect HomeServer to Cloud.',409)
    suffix='/api/homeserver-https-poll-v1300.php'
    if not session['endpoint'].endswith(suffix):raise workspace_sync.WorkspaceSyncError('The paired endpoint is unsupported.',409)
    allowed={'action','q','tag','project_id','folder_id','after','before','offset','id','note','tags','expected_revision','confirmed'}
    payload={k:v for k,v in body.items() if k in allowed}
    if len(json.dumps(payload).encode())>12000:raise workspace_sync.WorkspaceSyncError('Annotation changes are too large.')
    token=session['session_token'];device=load_or_create_remote_identity()['device_id']
    try:
        def send(c):return c.post(session['endpoint'][:-len(suffix)]+'/api/project-knowledge-r5.php',json=payload,
            headers={'Authorization':'Bearer '+token,'X-HomeServer-Device':device,'Accept':'application/json'})
        if client is not None:response=send(client)
        else:
            with httpx.Client(timeout=30.0,follow_redirects=False) as c:response=send(c)
        result=response.json()
    except (httpx.HTTPError,ValueError) as exc:
        raise workspace_sync.WorkspaceSyncError('Cloud Knowledge is unreachable. Retain these changes and retry after reconnecting.',503) from exc
    if not https_session_matches(token):raise workspace_sync.WorkspaceSyncError('The Cloud pairing changed. Refresh before retrying.',409)
    authorized(conversation,agent_id)
    if not isinstance(result,dict) or not response.is_success or not result.get('ok'):
        raise workspace_sync.WorkspaceSyncError(str(result.get('error','Annotation request failed.'))[:300] if isinstance(result,dict) else 'Invalid Cloud response.',response.status_code if response.status_code in {401,403,409,410,419,422} else 502)
    if result.get('contract')!=CONTRACT:raise workspace_sync.WorkspaceSyncError('Update the matching Cloud release.',409)
    if action=='edit':workspace_sync.wake()
    return result


def replica_items() -> list[dict]:
    if not workspace_sync.settings()['enabled']:return []
    authority=workspace_sync._authority()
    if not authority['active'] or not authority['scope'].get('cloud_allowed',False) or 'agent.chat' not in authority['permissions']:return []
    payload=native_workspaces.snapshot('chats');knowledge=native_workspaces.snapshot('knowledge')
    if payload['state']!='available':return []
    tables={}
    for row in payload['records']:tables.setdefault(row['table'],{})[row['source_id']]=row
    krows={r['source_id']:r for r in knowledge['records'] if r['table']=='knowledge_items'}
    owner=workspace_sync.settings()['peer_id'].rsplit('|',1)[-1];items=[]
    for record in tables.get('browser_capture_deliveries_r4',{}).values():
        d=record['data'];sr=tables.get('browser_shares_v2010',{}).get(str(d.get('browser_share_id')))
        cr=tables.get('chat_conversations',{}).get(str(d.get('conversation_id')))
        mr=tables.get('chat_messages',{}).get(str(d.get('chat_message_id')))
        if not sr or not cr or not mr:continue
        s=sr['data'];c=cr['data'];m=mr['data']
        if s.get('deleted_at') or str(m.get('conversation_id'))!=str(d.get('conversation_id')):continue
        if any(str(r.get(key))!=owner for r,key in ((d,'owner_user_id'),(s,'sender_user_id'),(c,'user_id'))):continue
        try:
            identifier=str(s['public_id']);assert str(UUID(identifier))==identifier
            url=urlsplit(str(s['source_url']));assert url.scheme in {'http','https'} and url.hostname and not url.username and not url.password
            tags=json.loads(d.get('tags_json') or '[]');assert isinstance(tags,list) and len(tags)<=20 and all(isinstance(t,str) and len(t)<=40 for t in tags)
            kid=int(d.get('knowledge_id') or 0);folder=int(d.get('folder_id') or 0)
        except (KeyError,ValueError,TypeError,AssertionError):continue
        item={'id':identifier,'title':str(s.get('source_title') or url.hostname),'source_url':url.geturl(),
            'selection':str(s.get('selected_text') or ''),'note':str(s.get('user_note') or ''),'tags':tags,
            'captured_at':str(s.get('captured_at') or ''),'project_id':str(d.get('project_public_id') or ''),'folder_id':folder,
            'knowledge_id':kid,'source_version_id':int(s.get('source_version_id') or 0),
            'share_key':sr['source_id'],'delivery_revision':record.get('record_revision',''),'share_revision':sr.get('record_revision',''),
            'knowledge_revision':'','synced_at':payload['synced_at']}
        k=krows.get(str(kid));ready=kid==0
        if kid and k:
            kd=k['data'];expected='Captured browser annotation (untrusted source data)\n\n'+item['selection']+'\n\nYour annotation: '+item['note']+'\nSource: '+item['source_url']+'\nCaptured: '+item['captured_at']+' UTC\nCapture: '+identifier+'\nProject: '+item['project_id']+'\nTags: '+', '.join(tags)
            ready=str(kd.get('created_by_user_id'))==owner and kd.get('knowledge_scope')=='personal' and int(kd.get('folder_id') or 0)==folder and str(kd.get('content_text') or '').strip()==expected.strip()
            if ready:item['knowledge_revision']=k.get('record_revision','')
        # Do not cite a mix of old Knowledge and new capture metadata.
        if not ready:continue
        values=[identifier,item['note'],tags,item['project_id'],folder,kid,item['knowledge_revision']]
        item['revision']=hashlib.sha256(json.dumps(values,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        items.append(item)
    return items


def receipts() -> list[dict]:
    return [{'table':'browser_capture_receipts_r5','source_id':i['share_key'],'data':{
        'capture_id':i['id'],'delivery_revision':i['delivery_revision'],'share_revision':i['share_revision'],'knowledge_revision':i['knowledge_revision']}}
        for i in replica_items() if re.fullmatch('[a-f0-9]{64}',i['delivery_revision']) and re.fullmatch('[a-f0-9]{64}',i['share_revision'])]


def search(query: str, limit: int=4) -> list[dict]:
    stop={'what','when','where','which','about','show','tell','have','does','please','with','from','your','this','that','the','and','for','my','me','is','in','of','to','a'}
    terms=[t for t in re.findall(r'\w+',query.casefold()) if len(t)>=3 and t not in stop]
    if not terms:return []
    ranked=[]
    for i in replica_items():
        hay=(i['title']+' '+i['selection']+' '+i['note']+' '+' '.join(i['tags'])).casefold()
        hits=sum(t in hay for t in terms)
        if hits:ranked.append((hits,i))
    ranked.sort(key=lambda row:(row[0],row[1]['captured_at'],row[1]['id']),reverse=True)
    return [i for _,i in ranked[:max(1,min(4,limit))]]


def citation_links(metadata_json: str | None) -> list[dict]:
    try:
        metadata=json.loads(metadata_json or '{}')
        refs=metadata.get('context_sources',[]) if isinstance(metadata,dict) else []
    except (TypeError,ValueError):return []
    if not isinstance(refs,list):return []
    result=[]
    for item in refs:
        if not isinstance(item,dict) or item.get('kind')!='browser_annotation':continue
        try:
            u=urlsplit(str(item.get('source_url') or ''))
            if u.scheme not in {'http','https'} or not u.hostname or u.username or u.password:continue
            if not re.fullmatch('A[1-4]',str(item.get('label') or '')):continue
        except ValueError:continue
        result.append({k:item.get(k,'') for k in ['id','label','title','source_url','captured_at','excerpt','source_version_id']})
        if len(result)>=4:break
    return result
