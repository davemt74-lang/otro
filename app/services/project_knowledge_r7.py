"""Reviewed refresh uses Cloud's existing research and Knowledge authorities."""
from __future__ import annotations
import hashlib,json,re
from urllib.parse import urlsplit,urlunsplit
import httpx
from . import project_knowledge_r5 as r5, workspace_sync, agent_routing, native_workspaces
from .https_bridge_session import load_https_session, https_session_matches
from .remote_identity import load_or_create_remote_identity

CONTRACT='vp3.project-knowledge.r7'

def request(body:dict,client:httpx.Client|None=None)->dict:
    action=body.get('action')
    if action not in {'list','prepare','get','start','apply','cancel'}:
        raise workspace_sync.WorkspaceSyncError('Unknown reviewed refresh operation.')
    conversation=body.get('local_conversation');agent=body.get('parent_agent_id') or 0
    if not isinstance(conversation,str) or type(agent) is not int:
        raise workspace_sync.WorkspaceSyncError('Choose a saved conversation and current lead.')
    def authorize():r5.authorized(conversation,agent)
    agent=int(agent_routing.resolve_agent('owner',agent or None,owner=True)['id']);authorize();session=load_https_session();suffix='/api/homeserver-https-poll-v1300.php'
    if not session or not session['endpoint'].endswith(suffix):
        raise workspace_sync.WorkspaceSyncError('Connect the matching Cloud refresh release.',409)
    allowed={'action','local_conversation','parent_agent_id','reference','state_revision','review_days','runtime_id','request_id','project_id','folder_id','id','expected_hash','confirmed'}
    payload={k:v for k,v in body.items() if k in allowed}
    payload['parent_agent_id']=agent
    if len(json.dumps(payload).encode())>32000:raise workspace_sync.WorkspaceSyncError('Refresh request is too large.',413)
    token=session['session_token'];device=load_or_create_remote_identity()['device_id']
    def send(c):return c.post(session['endpoint'][:-len(suffix)]+'/api/project-knowledge-r7.php',json=payload,
        headers={'Authorization':'Bearer '+token,'X-HomeServer-Device':device,'Accept':'application/json'})
    try:
        if client is not None:response=send(client)
        else:
            with httpx.Client(timeout=30.0,follow_redirects=False) as c:response=send(c)
        result=response.json()
    except (httpx.HTTPError,ValueError) as exc:
        raise workspace_sync.WorkspaceSyncError('Cloud refresh is unavailable. Retry after reconnecting.',503) from exc
    authorize()
    if not https_session_matches(token):raise workspace_sync.WorkspaceSyncError('Cloud pairing changed.',409)
    if not response.is_success or not isinstance(result,dict) or not result.get('ok') or result.get('contract')!=CONTRACT:
        message=str(result.get('error') or 'Update the matching reviewed refresh release.')[:300] if isinstance(result,dict) else 'Invalid refresh response.'
        raise workspace_sync.WorkspaceSyncError(message,response.status_code if response.status_code in {401,403,409,410,422} else 502)
    origin=urlsplit(session['endpoint']);base=urlunsplit((origin.scheme,origin.netloc,'','',''))
    result['cloud_origin']=base
    expected='home:'+hashlib.sha256(device.encode()).hexdigest()[:32]+':'+str(agent)+':'+conversation
    contexts=[]
    if 'state' in result:contexts.append(result['state'].get('context_key'))
    for refresh in ([result['refresh']] if 'refresh' in result else result.get('runs',[])):
        contexts.append(refresh.get('research',{}).get('context_key'))
    if not contexts or any(c!=expected for c in contexts):
        raise workspace_sync.WorkspaceSyncError('The canonical refresh conversation changed.',409)
    if action=='apply':workspace_sync.wake()
    return result

def receipts()->list[dict]:
    if not workspace_sync.settings()['enabled']:return []
    a=workspace_sync._authority()
    if not a['active'] or not a['scope'].get('cloud_allowed') or not {'agent.chat','knowledge.search'}.issubset(a['permissions']):return []
    snapshot=native_workspaces.snapshot('knowledge')
    if snapshot['state']!='available':return []
    owner=workspace_sync.settings()['peer_id'].rsplit('|',1)[-1];result=[]
    for r in snapshot['records']:
        k=r['data'];revision=r.get('record_revision','')
        if (r['table']=='knowledge_items' and k.get('knowledge_scope')=='personal'
            and str(k.get('created_by_user_id'))==owner and str(k.get('content_text','')).startswith('Reviewed Knowledge refresh (untrusted source evidence)\n')
            and re.fullmatch('[a-f0-9]{64}',revision)):
            result.append({'table':'knowledge_refresh_receipts_r7','source_id':r['source_id'],
                'data':{'knowledge_id':r['source_id'],'knowledge_revision':revision}})
    return result
