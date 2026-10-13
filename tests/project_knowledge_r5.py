"""Real SQLite/replica/context/receipt tests and controlled paired HTTP authority boundaries."""
import os,sys,json,hashlib,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix='knowledge-r5-') as data:
    os.environ['HOMESERVER_DATA_DIR']=data
    from app.database import db,initialize_database
    from app.services import workspace_sync as sync,agent_routing,context_engine,canonical_context,project_knowledge_r5 as r5,brain
    from app.services.https_bridge_session import save_https_session
    import httpx
    initialize_database();agent=agent_routing.resolve_agent('owner',None,owner=True)
    token='fixture-session-000000000000000000000000000';peer='http://127.0.0.1|7';endpoint='http://127.0.0.1/installed/vp3/api/homeserver-https-poll-v1300.php'
    save_https_session(endpoint,token)
    with db() as c:
        c.execute("INSERT INTO conversations(id,agent_id,source_app_key,title) VALUES('r5-chat',?,'owner','Project Knowledge')",(agent['id'],))
        app=c.execute("INSERT INTO paired_apps(app_key,name,token_hash,status) VALUES('vp3','VP3',?,'active')",(hashlib.sha256(b'fixture').hexdigest(),)).lastrowid
        c.execute("INSERT INTO app_permissions(paired_app_id,permission,allowed) VALUES(?,'agent.chat',1)",(app,))
        c.execute('UPDATE workspace_sync_settings SET peer_id=?,session_hash=?,enabled=1 WHERE id=1',(peer,hashlib.sha256(token.encode()).hexdigest()))
    settings=context_engine.ensure_settings('r5-chat')
    export=Path(os.environ['VP3_CAPTURE_R5_EXPORT']) if os.environ.get('VP3_CAPTURE_R5_EXPORT') else Path(__file__).parent/'fixtures'/'project-knowledge-r5-export.json'
    e=json.loads(export.read_text())
    def apply(dataset,payload):
        raw=json.dumps(payload,ensure_ascii=False).encode();sync.apply_snapshot(peer,dataset,raw,hashlib.sha256(raw).hexdigest())
    for d,payload in e['before'].items():apply(d,payload)
    assert len(r5.replica_items())==2
    apply('chats',e['after']['chats'])
    assert len(r5.replica_items())==1,'Partial dataset delivery exposed inconsistent Knowledge'
    apply('knowledge',e['after']['knowledge'])
    item=next(i for i in r5.replica_items() if i['id']==e['item']['id'])
    assert item['note']==e['item']['note'] and item['revision']==e['item']['revision'] and item['tags']==['wholesale','costs'] and item['folder_id']==3
    matched=r5.search('wholesale saffron costs');assert matched and matched[0]['id']==item['id']
    context=canonical_context.build_authorized_context(agent_id=agent['id'],query='wholesale saffron costs',source_app_key='owner',permissions=set(),owner=True,include_memory=False,include_knowledge=True,include_contacts=False,settings=settings,max_context_chars=6000,cloud_allowed=True,include_collaboration=False)
    refs=[r for r in context.source_refs if r.get('kind')=='browser_annotation']
    assert refs and refs[0]['source_url']==item['source_url'] and refs[0]['captured_at']==item['captured_at'] and 'Flour costs' in refs[0]['excerpt']
    assert '[A1]' in str(context.bundle.knowledge) and 'untrusted evidence' in str(context.bundle.knowledge)
    with db() as c:c.execute("INSERT INTO app_permissions(paired_app_id,permission,allowed) VALUES(?,'knowledge.search',1)",(app,))
    delegated=canonical_context.build_authorized_context(agent_id=agent['id'],query='saffron',source_app_key='app:vp3',permissions={'knowledge.search'},owner=False,include_memory=False,include_knowledge=True,include_contacts=False,max_context_chars=6000,cloud_allowed=True,include_collaboration=False)
    assert any(r.get('kind')=='browser_annotation' for r in delegated.source_refs),'Authorized paired delegation lost source annotations'
    without=canonical_context.build_authorized_context(agent_id=agent['id'],query='saffron',source_app_key='app:vp3',permissions=set(),owner=False,include_memory=False,include_knowledge=True,include_contacts=False,max_context_chars=6000,cloud_allowed=True,include_collaboration=False)
    assert not any(r.get('kind')=='browser_annotation' for r in without.source_refs),'Paired Knowledge permission was bypassed'
    metadata=json.dumps({'context_sources':refs})
    assert r5.citation_links(metadata)[0]['source_url']==item['source_url']
    with db() as c:c.execute("INSERT INTO conversation_messages(conversation_id,role,content,source_app_key,metadata_json) VALUES('r5-chat','assistant','Historical source answer','owner',?)",(metadata,))
    assert brain.get_conversation('owner','r5-chat')['messages'][0]['annotation_citations'][0]['id']==item['id']
    outbound=sync.local_snapshot('chats');ack=json.loads(outbound['body'])
    receipt=next(r for r in ack['records'] if r['table']=='browser_capture_receipts_r5' and r['data']['capture_id']==item['id'])
    assert receipt['data']['knowledge_revision']==item['knowledge_revision'] and len(receipt['data']['share_revision'])==64
    if receipt_file:=os.environ.get('VP3_CAPTURE_R5_RECEIPTS'):Path(receipt_file).write_bytes(outbound['body'])
    requests=[]
    def handler(request):
        assert request.url.path=='/installed/vp3/api/project-knowledge-r5.php'
        payload=json.loads(request.content);requests.append(payload);assert 'owner_user_id' not in payload and 'parent_agent_id' not in payload
        return httpx.Response(200,json={'ok':True,'contract':r5.CONTRACT,'items':[e['item']],'count':1})
    args={'action':'list','local_conversation':'r5-chat','parent_agent_id':agent['id'],'owner_user_id':8,'q':'saffron'}
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:assert r5.request(args,client)['items'][0]['id']==item['id']
    def denied(call):
        try:call()
        except sync.WorkspaceSyncError:return
        raise AssertionError('Revoked/private data was exposed')
    def revoked(request):
        with db() as c:c.execute("UPDATE app_permissions SET allowed=0 WHERE paired_app_id=? AND permission='agent.chat'",(app,))
        return httpx.Response(200,json={'ok':True,'contract':r5.CONTRACT,'items':[e['item']]})
    with httpx.Client(transport=httpx.MockTransport(revoked)) as client:denied(lambda:r5.request(args,client))
    assert r5.search('saffron')==[] and r5.receipts()==[]
    with db() as c:c.execute("UPDATE app_permissions SET allowed=1 WHERE paired_app_id=? AND permission='agent.chat'",(app,))
    for flag in ('cloud_allowed','include_knowledge'):
        with db() as c:c.execute('UPDATE conversation_context_settings SET '+flag+"=0 WHERE conversation_id='r5-chat'")
        denied(lambda:r5.request(args))
        private=context_engine.ensure_settings('r5-chat')
        context=canonical_context.build_authorized_context(agent_id=agent['id'],query='saffron',source_app_key='owner',permissions=set(),owner=True,include_memory=False,include_knowledge=True,include_contacts=False,settings=private,max_context_chars=6000,cloud_allowed=True,include_collaboration=False)
        assert not any(r.get('kind')=='browser_annotation' for r in context.source_refs)
        with db() as c:c.execute('UPDATE conversation_context_settings SET '+flag+"=1 WHERE conversation_id='r5-chat'")
    sync.set_enabled(False);assert r5.replica_items()==[];denied(lambda:r5.request(args));sync.set_enabled(True)
    save_https_session(endpoint,'replacement-session-00000000000000000000000');assert r5.search('saffron')==[];save_https_session(endpoint,token)
    bad=json.loads(json.dumps(e['after']['chats']))
    for r in bad['records']:
        if r['table']=='browser_shares_v2010':r['data']['sender_user_id']=8
    apply('chats',bad);assert r5.replica_items()==[]
    with db() as c:
        assert c.execute('SELECT COUNT(*) FROM knowledge_items').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM automation_routines').fetchone()[0]==0
    print('PASS: R5 Cloud export to native SQLite, partial dataset fencing, current source citations in Brain/history, exact outbound receipts, paired request/response revocation, privacy/opt-out/session/owner boundaries and no duplicate native Knowledge/jobs.')
