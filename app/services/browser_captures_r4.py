"""Owner Chat reads canonical Chrome annotations from existing account-bound replicas."""
from __future__ import annotations
import json
from urllib.parse import urlsplit
from uuid import UUID
from ..database import db
from . import agent_routing, app_scopes, context_engine, native_workspaces, workspace_sync


def captures(conversation_id: str, agent_id: int) -> dict:
    if not conversation_id:
        raise workspace_sync.WorkspaceSyncError('Open or start a Chat conversation before using shared captures.', 409)
    try:
        parent=agent_routing.resolve_agent('owner',agent_id or None,owner=True)
        agent_routing.validate_conversation_agent('owner',conversation_id,int(parent['id']))
    except agent_routing.AgentRoutingError as exc:
        raise workspace_sync.WorkspaceSyncError('This conversation or lead is unavailable.',403) from exc
    settings=context_engine.ensure_settings(conversation_id)
    if not settings['cloud_allowed'] or not settings['include_knowledge']:
        raise workspace_sync.WorkspaceSyncError('This Chat excludes shared Cloud Knowledge.',403)
    with db() as connection:
        paired=connection.execute("SELECT a.id FROM paired_apps a JOIN app_permissions p ON p.paired_app_id=a.id WHERE a.app_key='vp3' AND a.status='active' AND p.permission='agent.chat' AND p.allowed=1").fetchone()
    if not paired or not app_scopes.get_scope(int(paired['id'])).get('cloud_allowed',False):
        raise workspace_sync.WorkspaceSyncError('Cloud pairing or Chat permission is unavailable.',403)
    if not workspace_sync.settings()['enabled']:
        raise workspace_sync.WorkspaceSyncError('Workspace synchronization is paused.',403)
    payload=native_workspaces.snapshot('chats')
    by_table={}
    for row in payload['records']:
        by_table.setdefault(row['table'],{})[row['source_id']]=row['data']
    messages=by_table.get('chat_messages',{})
    conversations=by_table.get('chat_conversations',{})
    shares=by_table.get('browser_shares_v2010',{})
    owner=workspace_sync.settings()['peer_id'].rsplit('|',1)[-1]
    result=[]
    for delivery in by_table.get('browser_capture_deliveries_r4',{}).values():
        share=shares.get(str(delivery.get('browser_share_id')))
        message=messages.get(str(delivery.get('chat_message_id')))
        conversation=conversations.get(str(delivery.get('conversation_id')))
        if not share or not message or not conversation or share.get('deleted_at'):
            continue
        if any(str(row.get(field))!=owner for row,field in [(delivery,'owner_user_id'),(share,'sender_user_id'),(conversation,'user_id')]):
            continue
        if str(message.get('conversation_id'))!=str(delivery.get('conversation_id')):
            continue
        try:
            identifier=str(share['public_id']);assert str(UUID(identifier))==identifier
            source=urlsplit(str(share.get('source_url') or ''))
            assert source.scheme in ('http','https') and source.netloc and not source.username and not source.password
            tags=json.loads(delivery.get('tags_json') or '[]')
        except (KeyError,ValueError,TypeError,AssertionError):
            continue
        result.append({'id':identifier,'title':str(share.get('source_title') or source.hostname)[:512],
            'source_url':source.geturl(),'selection':str(share.get('selected_text') or '')[:16000],
            'note':str(share.get('user_note') or '')[:4096],'captured_at':share.get('captured_at'),
            'project_id':str(delivery.get('project_public_id') or ''),'folder_id':int(delivery.get('folder_id') or 0),
            'knowledge_id':int(delivery.get('knowledge_id') or 0),'cloud_conversation_id':int(delivery.get('conversation_id') or 0),
            'tags':[str(t)[:40] for t in tags[:20]] if isinstance(tags,list) else [],
            'authority_source':'vp3_cloud','record_key':'browser_shares_v2010:'+str(delivery['browser_share_id'])})
    result.sort(key=lambda item:(str(item['captured_at']),item['id']),reverse=True)
    return {'contract':'vp3.browser-capture.r4','items':result[:50],'synced_at':payload['synced_at'],
        'state':payload['state'],'source_authority_preserved':True,'local_records_created':False}
