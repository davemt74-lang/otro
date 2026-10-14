"""Reviewed refresh uses Cloud's existing research and Knowledge authorities."""
from __future__ import annotations
import hashlib,json,re
from urllib.parse import urlsplit,urlunsplit
import httpx
from . import project_knowledge_r5 as r5, workspace_sync, agent_routing, native_workspaces
from .https_bridge_session import load_https_session, https_session_matches
from .remote_identity import load_or_create_remote_identity

CONTRACT='vp3.knowledge-actions.r8'

def request(body:dict,client:httpx.Client|None=None)->dict:
    action=body.get('action')
    if action not in {'options','suggest','prepare','get','decide','converse','validate_draft'}:
        raise workspace_sync.WorkspaceSyncError('Unknown reviewed refresh operation.')
    conversation=body.get('local_conversation');agent=body.get('parent_agent_id') or 0
    if not isinstance(conversation,str) or type(agent) is not int:
        raise workspace_sync.WorkspaceSyncError('Choose a saved conversation and current lead.')
    required={'task.create':'memory.read','contacts.update':'contacts.read','knowledge.update':'knowledge.search'}
    def authorize():
        r5.authorized(conversation,agent)
        permissions=workspace_sync._authority()['permissions']
        if 'knowledge.search' not in permissions:
            raise workspace_sync.WorkspaceSyncError('Current Knowledge permission is required.',403)
        if action in {'suggest','prepare','decide','validate_draft'} and (body.get('operation') not in required or required[body['operation']] not in permissions):
            raise workspace_sync.WorkspaceSyncError('Current destination permission is required for this action.',403)
        return permissions
    agent=int(agent_routing.resolve_agent('owner',agent or None,owner=True)['id']);authorize();session=load_https_session();suffix='/api/homeserver-https-poll-v1300.php'
    if not session or not session['endpoint'].endswith(suffix):
        raise workspace_sync.WorkspaceSyncError('Connect the matching Cloud refresh release.',409)
    allowed={'action','local_conversation','parent_agent_id','references','state_revision','operation','target_key','fields','instruction','request_id','id','expected_hash','decision','confirmed','draft_fingerprint','target_revision'}
    payload={k:v for k,v in body.items() if k in allowed}
    payload['parent_agent_id']=agent
    if action=='converse':payload['allowed_operations']=[op for op,permission in required.items() if permission in authorize()]
    if len(json.dumps(payload).encode())>32000:raise workspace_sync.WorkspaceSyncError('Refresh request is too large.',413)
    token=session['session_token'];device=load_or_create_remote_identity()['device_id']
    def send(c):return c.post(session['endpoint'][:-len(suffix)]+'/api/knowledge-actions-r8.php',json=payload,
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
    if result.get('context_key')!=expected:
        raise workspace_sync.WorkspaceSyncError('The canonical action conversation changed.',409)
    permissions=authorize()
    allowed=[op for op,permission in required.items() if permission in permissions]
    if action=='options':
        result['allowed_operations']=allowed
        result['targets']={op:items for op,items in result.get('targets',{}).items() if op in allowed}
        result['actions']=[item for item in result.get('actions',[]) if not item.get('preview') or item['preview'].get('operation') in allowed]
    for item in [result.get('action')]:
        if isinstance(item,dict) and item.get('preview') and item['preview'].get('operation') not in allowed:
            raise workspace_sync.WorkspaceSyncError('Destination permission changed.',403)
    if action=='converse':
        draft=result.get('conversation_draft')
        if not isinstance(draft,dict) or draft.get('contract')!='vp3.knowledge-actions.r9' or draft.get('context_key')!=expected or not isinstance(draft.get('drafts'),list) or len(draft['drafts'])>3 or any(not isinstance(p,dict) or p.get('operation') not in allowed for p in draft['drafts']):
            raise workspace_sync.WorkspaceSyncError('Invalid or unauthorized conversational draft.',403)
    if action=='validate_draft' and not result.get('action') and (not isinstance(result.get('draft'),dict) or result['draft'].get('operation') not in allowed):
        raise workspace_sync.WorkspaceSyncError('Destination permission changed.',403)
    if action=='decide':workspace_sync.wake()
    return result


def receipts()->list[dict]:
    if not workspace_sync.settings()['enabled']:return []
    a=workspace_sync._authority()
    if not a['active'] or not a['scope'].get('cloud_allowed') or not {'agent.chat','knowledge.search'}.issubset(a['permissions']):return []
    owner=workspace_sync.settings()['peer_id'].rsplit('|',1)[-1];result=[]
    for dataset,permission,table,owner_field in [('agents','memory.read','agent_memory_items','user_id'),('contacts','contacts.read','crm_contacts','owner_user_id'),('knowledge','knowledge.search','knowledge_items','created_by_user_id')]:
        if permission not in a['permissions']:continue
        snapshot=native_workspaces.snapshot(dataset)
        if snapshot['state']!='available':continue
        for r in snapshot['records']:
            k=r['data'];revision=r.get('record_revision','')
            if r['table']!=table or str(k.get(owner_field))!=owner or not re.fullmatch('[a-f0-9]{64}',revision):continue
            if table=='agent_memory_items':
                try:meta=json.loads(k.get('metadata_json') or '{}')
                except (ValueError,TypeError):continue
                if meta.get('source_kind')!='knowledge_action_r8':continue
            if table=='knowledge_items' and k.get('knowledge_scope')!='personal':continue
            key=table+':'+r['source_id']
            result.append({'table':'knowledge_action_receipts_r8','source_id':key,'data':{'record_key':key,'record_revision':revision}})
    return result
