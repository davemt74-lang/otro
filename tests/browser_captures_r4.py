"""Real SQLite, account binding and production replica readers; optional real Cloud export."""
import os,sys,json,hashlib,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix='capture-r4-') as data:
    os.environ['HOMESERVER_DATA_DIR']=data
    from app.database import db,initialize_database
    from app.services import workspace_sync as sync,native_workspaces,agent_routing,context_engine,app_scopes
    from app.services import browser_captures_r4 as captures
    from app.services.https_bridge_session import save_https_session
    initialize_database()
    agent=agent_routing.resolve_agent('owner',None,owner=True)
    token='fixture-session-000000000000000000000000000';peer='http://127.0.0.1|7'
    endpoint='http://127.0.0.1/installed/vp3/api/homeserver-https-poll-v1300.php';save_https_session(endpoint,token)
    with db() as c:
        c.execute("INSERT INTO conversations(id,agent_id,source_app_key,title) VALUES('capture-chat',?,'owner','Annotations')",(agent['id'],))
        app=c.execute("INSERT INTO paired_apps(app_key,name,token_hash,status) VALUES('vp3','VP3',?,'active')",(hashlib.sha256(b'fixture').hexdigest(),)).lastrowid
        c.execute("INSERT INTO app_permissions(paired_app_id,permission,allowed) VALUES(?,'agent.chat',1)",(app,))
        c.execute('UPDATE workspace_sync_settings SET peer_id=?,session_hash=?,enabled=1 WHERE id=1',(peer,hashlib.sha256(token.encode()).hexdigest()))
    context_engine.ensure_settings('capture-chat')
    export=Path(os.environ['VP3_CAPTURE_R4_EXPORT']) if os.environ.get('VP3_CAPTURE_R4_EXPORT') else None
    if export:
        snapshots=json.loads(export.read_text())
    else:
        identifier='33333333-3333-4333-8333-333333333333'
        rows=[('chat_conversations',42,{'id':42,'user_id':7,'user_agent_id':None,'title':'Supplier research'}),
            ('chat_messages',55,{'id':55,'conversation_id':42,'role':'user','message':'Source capture'}),
            ('browser_shares_v2010',1,{'id':1,'public_id':identifier,'sender_user_id':7,'source_url':'https://supplier.example/prices','source_title':'Supplier pricing','selected_text':'Flour costs 24.50','user_note':'Compare costs','captured_at':'2026-10-13 05:00:00','deleted_at':None}),
            ('browser_capture_deliveries_r4',1,{'id':1,'owner_user_id':7,'browser_share_id':1,'conversation_id':42,'chat_message_id':55,'knowledge_id':1,'folder_id':1,'project_public_id':'project','tags_json':'["supplier","pricing"]'})]
        snapshots={'chats':{'contract':sync.CONTRACT,'source':'cloud','dataset':'chats','records':[{'table':t,'source_id':str(i),'data':d} for t,i,d in rows],'files':[]},
            'knowledge':{'contract':sync.CONTRACT,'source':'cloud','dataset':'knowledge','records':[{'table':'knowledge_items','source_id':'1','data':{'id':1,'title':'Supplier pricing','content_text':'Flour costs 24.50','folder_id':1,'created_by_user_id':7}},{'table':'artist_transcript_folders_v177','source_id':'1','data':{'id':1,'folder_name':'Suppliers','created_by_user_id':7}}],'files':[]}}
    for dataset,payload in snapshots.items():
        raw=json.dumps(payload,ensure_ascii=False).encode();sync.apply_snapshot(peer,dataset,raw,hashlib.sha256(raw).hexdigest())
    result=captures.captures('capture-chat',agent['id'])
    assert result['items'] and not result['local_records_created']
    from app.services import canonical_context
    context=canonical_context.build_authorized_context(agent_id=agent['id'],query='Supplier flour costs',source_app_key='owner',permissions=set(),owner=True,include_memory=False,include_knowledge=True,include_contacts=False,settings=context_engine.ensure_settings('capture-chat'),max_context_chars=6000,cloud_allowed=True,include_collaboration=False)
    assert any(ref.get('dataset')=='knowledge' and ref.get('kind')=='cloud_workspace' for ref in context.source_refs)
    assert '24.50' in str(context.bundle.knowledge)
    for item in result['items']:
        assert item['authority_source']=='vp3_cloud' and item['folder_id']==1 and item['knowledge_id']>0
        assert item['tags']==['supplier','pricing'] and 'Flour costs 24.50' in item['selection']
        row=sync.records('knowledge',detail_key='knowledge_items:'+str(item['knowledge_id']))['items'][0]
        assert row['data']['folder_id']==1 and '24.50' in row['data']['content_text']
    with db() as c:
        assert c.execute('SELECT COUNT(*) FROM knowledge_items').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM conversation_messages').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM automation_routines').fetchone()[0]==0
    def denied(call):
        try:call()
        except sync.WorkspaceSyncError:return
        raise AssertionError('Private/revoked captures were exposed')
    for flag in ('cloud_allowed','include_knowledge'):
        with db() as c:c.execute('UPDATE conversation_context_settings SET '+flag+"=0 WHERE conversation_id='capture-chat'")
        denied(lambda:captures.captures('capture-chat',agent['id']))
        with db() as c:c.execute('UPDATE conversation_context_settings SET '+flag+"=1 WHERE conversation_id='capture-chat'")
    denied(lambda:captures.captures('another-chat',agent['id']))
    sync.set_enabled(False);denied(lambda:captures.captures('capture-chat',agent['id']));sync.set_enabled(True)
    with db() as c:c.execute("UPDATE app_permissions SET allowed=0 WHERE paired_app_id=? AND permission='agent.chat'",(app,))
    denied(lambda:captures.captures('capture-chat',agent['id']))
    with db() as c:c.execute("UPDATE app_permissions SET allowed=1 WHERE paired_app_id=? AND permission='agent.chat'",(app,))
    save_https_session(endpoint,'replacement-session-00000000000000000000000')
    assert captures.captures('capture-chat',agent['id'])['items']==[]
    save_https_session(endpoint,token)
    bad=json.loads(json.dumps(snapshots['chats']))
    for row in bad['records']:
        if row['table']=='browser_shares_v2010':row['data']['sender_user_id']=8
    raw=json.dumps(bad).encode();sync.apply_snapshot(peer,'chats',raw,hashlib.sha256(raw).hexdigest())
    assert captures.captures('capture-chat',agent['id'])['items']==[]
    print('PASS: '+('real Cloud export -> ' if export else '')+'production replica -> native capture/Knowledge reads, canonical references/folders/tags, no duplicate native items/jobs, Chat privacy, pairing replacement, revoked grants and owner isolation.')
