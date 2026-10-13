"""Real canonical Knowledge replica/Chat context and paired reviewed-refresh fences."""
import os,sys,json,hashlib,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix='knowledge-r7-') as data:
    os.environ['HOMESERVER_DATA_DIR']=data
    from app.database import db,initialize_database
    from app.services import workspace_sync as sync,agent_routing,context_engine,canonical_context,project_knowledge_r7 as r7
    from app.services.https_bridge_session import save_https_session
    import httpx
    initialize_database();agent=agent_routing.resolve_agent('owner',None,owner=True)
    token='r7-session-000000000000000000000000000000';peer='http://127.0.0.1|7';endpoint='http://127.0.0.1/installed/vp3/api/homeserver-https-poll-v1300.php'
    save_https_session(endpoint,token)
    with db() as c:
        c.execute("INSERT INTO conversations(id,agent_id,source_app_key,title) VALUES('r7-chat',?,'owner','Reviewed refresh')",(agent['id'],))
        app=c.execute("INSERT INTO paired_apps(app_key,name,token_hash,status) VALUES('vp3','VP3',?,'active')",(hashlib.sha256(b'fixture').hexdigest(),)).lastrowid
        for p in ('agent.chat','knowledge.search'):c.execute('INSERT INTO app_permissions(paired_app_id,permission,allowed) VALUES(?,?,1)',(app,p))
        c.execute('UPDATE workspace_sync_settings SET peer_id=?,session_hash=?,enabled=1 WHERE id=1',(peer,hashlib.sha256(token.encode()).hexdigest()))
    settings=context_engine.ensure_settings('r7-chat')
    if path:=os.environ.get('VP3_KNOWLEDGE_R7_EXPORT'):
        e=json.loads(Path(path).read_text())
    else:
        content='Reviewed Knowledge refresh (untrusted source evidence)\nOriginal source: https://supplier.example/prices\n\nObserved: Flour price — 25.50 [C1]\n[C1] supplier.example · pinned version receipt-version · evidence dated 2026-10-13'
        k={'id':42,'created_by_user_id':7,'knowledge_scope':'personal','folder_id':1,'title':'Reviewed flour price','content_text':content,'file_type':'personal_note','file_name':'personal-refresh.txt','visibility':'private','is_published':1,'created_at':'2026-10-13','updated_at':'2026-10-13'}
        revision=hashlib.sha256(json.dumps(k,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        e={'id':42,'revision':revision,'content':content,'knowledge':{'contract':sync.CONTRACT,'source':'cloud','dataset':'knowledge','records':[{'table':'knowledge_items','source_id':'42','data':k,'record_revision':revision}],'available_tables':['knowledge_items'],'unavailable_tables':[],'files':[]}}
    raw=json.dumps(e['knowledge'],ensure_ascii=False).encode();sync.apply_snapshot(peer,'knowledge',raw,hashlib.sha256(raw).hexdigest())
    receipts=r7.receipts();assert any(r['data']=={'knowledge_id':str(e['id']),'knowledge_revision':e['revision']} for r in receipts)
    outbound=json.loads(sync.local_snapshot('chats')['body']);assert any(r['table']=='knowledge_refresh_receipts_r7' for r in outbound['records'])
    ctx=canonical_context.build_authorized_context(agent_id=agent['id'],query='flour',source_app_key='owner',permissions=set(),owner=True,include_memory=False,include_knowledge=True,include_contacts=False,settings=settings,max_context_chars=12000,cloud_allowed=True,include_collaboration=False)
    assert '25.50' in str(ctx.bundle.knowledge) and 'receipt-version' in str(ctx.bundle.knowledge),'Reviewed canonical note missing from actual Chat context'
    calls=[];wrong=False;revoke=False;replace=False
    def cloud(request):
        assert request.url.path=='/installed/vp3/api/project-knowledge-r7.php'
        payload=json.loads(request.content);calls.append(payload)
        assert 'owner_user_id' not in payload and 'knowledge_content' not in payload and 'query' not in payload
        key='home:'+hashlib.sha256(request.headers['X-HomeServer-Device'].encode()).hexdigest()[:32]+':'+str(agent['id'])+':r7-chat'
        if wrong:key+='-other'
        if revoke:
            with db() as c:c.execute("UPDATE app_permissions SET allowed=0 WHERE paired_app_id=? AND permission='agent.chat'",(app,))
        if replace:save_https_session(endpoint,'replacement-session-00000000000000000000')
        return httpx.Response(200,json={'ok':True,'contract':r7.CONTRACT,'state':{'context_key':key},'items':[],'runs':[]})
    body={'action':'list','local_conversation':'r7-chat','parent_agent_id':agent['id'],'review_days':30,'owner_user_id':8,'knowledge_content':'Injected','query':'Private notes'}
    def denied(fn):
        try:fn()
        except sync.WorkspaceSyncError:return
        raise AssertionError('R7 authority fence accepted revoked/mismatched response')
    with httpx.Client(transport=httpx.MockTransport(cloud)) as client:
        assert r7.request(body,client)['cloud_origin']=='http://127.0.0.1'
        wrong=True;denied(lambda:r7.request(body,client));wrong=False
        revoke=True;denied(lambda:r7.request(body,client));revoke=False
        assert not r7.receipts()
        with db() as c:c.execute("UPDATE app_permissions SET allowed=1 WHERE paired_app_id=? AND permission='agent.chat'",(app,))
        replace=True;denied(lambda:r7.request(body,client));replace=False;save_https_session(endpoint,token)
        for flag in ('cloud_allowed','include_knowledge'):
            before=len(calls)
            with db() as c:c.execute('UPDATE conversation_context_settings SET '+flag+"=0 WHERE conversation_id='r7-chat'")
            denied(lambda:r7.request(body,client));assert len(calls)==before,'Private conversation exported refresh pointers'
            with db() as c:c.execute('UPDATE conversation_context_settings SET '+flag+"=1 WHERE conversation_id='r7-chat'")
    with db() as c:c.execute("UPDATE app_permissions SET allowed=0 WHERE paired_app_id=? AND permission='knowledge.search'",(app,))
    assert not r7.receipts(),'Revoked Knowledge grant still emitted receipts'
    with db() as c:c.execute("UPDATE app_permissions SET allowed=1 WHERE paired_app_id=? AND permission='knowledge.search'",(app,))
    sync.set_enabled(False);assert not r7.receipts();denied(lambda:r7.request(body));sync.set_enabled(True)
    with db() as c:assert c.execute('SELECT COUNT(*) FROM knowledge_items').fetchone()[0]==0,'Replica created competing native Knowledge items'
    print('PASS: R7 canonical Cloud Knowledge in native SQLite/Chat, exact outbound revision receipt, current pairing/privacy/permission and conversation fences, pointer-only requests and no duplicate Knowledge.')
