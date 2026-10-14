"""Real owner Chat/history and action proxy; controlled Cloud transport/model outputs."""
import os,sys,json,hashlib,tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix='knowledge-r9-') as data:
    os.environ['HOMESERVER_DATA_DIR']=data
    from app.database import db,initialize_database
    from app.services import workspace_sync as sync,agent_routing,context_engine,context_chat,brain,providers,knowledge_actions_r8 as actions,knowledge_actions_r9 as r9,project_knowledge_r6 as r6
    from app.services.https_bridge_session import save_https_session
    import httpx
    initialize_database();agent=agent_routing.resolve_agent('owner',None,owner=True)
    endpoint='http://127.0.0.1/installed/vp3/api/homeserver-https-poll-v1300.php';token='r9-session-0000000000000000000000000000000';save_https_session(endpoint,token)
    with db() as c:
        c.execute("INSERT INTO conversations(id,agent_id,source_app_key,title) VALUES('r9-chat',?,'owner','Conversational Knowledge')",(agent['id'],))
        app=c.execute("INSERT INTO paired_apps(app_key,name,token_hash,status) VALUES('vp3','VP3',?,'active')",(hashlib.sha256(b'fixture').hexdigest(),)).lastrowid
        for permission in ['agent.chat','knowledge.search','memory.read','contacts.read']:c.execute('INSERT INTO app_permissions(paired_app_id,permission,allowed) VALUES(?,?,1)',(app,permission))
        c.execute('UPDATE workspace_sync_settings SET peer_id=?,session_hash=?,enabled=1 WHERE id=1',('http://127.0.0.1|7',hashlib.sha256(token.encode()).hexdigest()))
    context_engine.ensure_settings('r9-chat');calls=[];normal=[];changed=False;revoke=False;replace=False;bad_operation=False;history_changed=False
    draft={'draft_id':'d'*64,'operation':'knowledge.create','target_key':'new-document:-:0','target_revision':'f'*64,'fields':{'title':'Verify reviewed quote','description':'Historical comparison','content_text':'Check the captured evidence [P1].'},'references':[{'kind':'annotation','id':'33333333-3333-4333-8333-333333333333','revision':'b'*64}],'state_revision':'a'*64,'draft_fingerprint':'c'*64,'evidence':{'sources':[]}}
    original=httpx.Client
    def cloud(request):
        body=json.loads(request.content);calls.append(body)
        key='home:'+hashlib.sha256(request.headers['X-HomeServer-Device'].encode()).hexdigest()[:32]+':'+str(agent['id'])+':r9-chat'
        if request.url.path.endswith('/project-knowledge-r6.php'):
            return httpx.Response(200,json={'ok':True,'contract':r6.CONTRACT,'state':{'scope':{'mode':'all','project_id':'','folder_id':0},'revision':'a'*64,'title':'Knowledge','available':True,'context_key':key},'active':False,'sources':[],'fingerprint':'f'*64,'context_text':'','query_hash':hashlib.sha256(body.get('query','').encode()).hexdigest()})
        assert request.url.path=='/installed/vp3/api/knowledge-actions-r8.php'
        assert not any(k in body for k in ('owner_user_id','knowledge_content','query','evidence'))
        if body['action']=='converse':
            assert body['allowed_operations']==['task.create','contacts.update','knowledge.update','knowledge.create']
            if revoke:
                with db() as c:c.execute("UPDATE app_permissions SET allowed=0 WHERE paired_app_id=? AND permission='contacts.read'",(app,))
            if replace:save_https_session(endpoint,'r9-replacement-session-000000000000000000')
            value={**draft,'operation':'contacts.update'} if bad_operation or revoke else draft
            if 'revision_packet' in body and not (bad_operation or revoke):
                assert body['revision_packet']['context_key']==key
                assert body['revision_packet']['drafts'][0]['draft_id']==body['knowledge_revision']['draft_id']
                if history_changed:
                    with db() as c:c.execute("UPDATE conversation_messages SET metadata_json='{}' WHERE id=(SELECT MAX(id) FROM conversation_messages WHERE role='assistant')")
                assert 'evidence' not in body['revision_packet']['drafts'][0]
                assert body['knowledge_revision']['fields']['content_text']=='My edited text [P1].'
                value={**draft,'draft_id':'e'*64,'revision_root_id':draft['draft_id'],'revision_of':draft['draft_id'],'fields':{'title':'Short brief','description':'Historical comparison','content_text':'Short edited text [P1].'}}
            return httpx.Response(200,json={'ok':True,'contract':actions.CONTRACT,'context_key':key,'conversation_draft':{'contract':r9.CONTRACT,'context_key':key,'answer':'Draft only. Review exact fields.','drafts':[value],'execution':{'source':'vp3_cloud','provider':'fixture','model':'fixture','usage':{'prompt_tokens':20,'completion_tokens':10,'total_tokens':30},'cloud_tokens_debited':30}}})
        assert body['action']=='validate_draft'
        if changed:return httpx.Response(409,json={'ok':False,'error':'Source changed since drafting.'})
        return httpx.Response(200,json={'ok':True,'contract':actions.CONTRACT,'context_key':key,'draft':draft})
    def client(**kwargs):return original(transport=httpx.MockTransport(cloud),**kwargs)
    def generate(messages,**kwargs):normal.append(messages);return {'provider':'ollama','model':'fixture','content':'Ordinary answer.'},kwargs['state']
    status=lambda:{'available':True,'selected_provider':'ollama','model':'fixture','compute_source':'homeserver_local'}
    query='Turn these highlights into a project brief.'
    with patch.object(actions.httpx,'Client',client),patch.object(providers,'inference_status',status),patch.object(brain,'_generate_with_agent_tools',generate):
        result=context_chat.chat('owner',query,'r9-chat',agent_id=agent['id'],owner_tools=True,include_memory=False)
        assert result['knowledge_actions_r9']['drafts'][0]['fields']==draft['fields'] and not normal
        assert result['cloud_tokens_debited']==30 and result['compute_source']=='vp3_cloud' and result['usage']['total_tokens']==30
        saved=brain.get_conversation('owner','r9-chat')['messages'][-1]
        assert saved['knowledge_actions_r9']['drafts'][0]['draft_id']==draft['draft_id']
        with db() as c:
            assert c.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]==0
            assert c.execute('SELECT COUNT(*) FROM knowledge_items').fetchone()[0]==0
        from app.services import knowledge_revisions_r11 as r11
        assert r11.intent('Make it shorter.') and r11.intent('Please rewrite that brief in bullets.')
        assert not r11.intent('How would I make it shorter?') and not r11.intent('Do not make it shorter.')
        selection={'draft_id':draft['draft_id'],'fields':{'title':'Edited brief','description':'Historical comparison','content_text':'My edited text [P1].'},'target_key':draft['target_key']}
        refined=context_chat.chat('owner','Make it shorter.','r9-chat',agent_id=agent['id'],owner_tools=True,include_memory=False,knowledge_revision=selection)
        assert refined['knowledge_actions_r9']['drafts'][0]['revision_root_id']==draft['draft_id'] and not normal
        assert refined['usage']['total_tokens']==30 and refined['compute_source']=='vp3_cloud'
        assert brain.get_conversation('owner','r9-chat')['messages'][-1]['knowledge_actions_r9']['drafts'][0]['draft_id']=='e'*64
        before_calls=len([b for b in calls if b.get('action')=='converse'])
        try:context_chat.chat('owner','Make it shorter.','r9-chat',agent_id=agent['id'],owner_tools=True,include_memory=False,knowledge_revision=selection);raise AssertionError('Stale draft selection exported')
        except brain.BrainError:pass
        assert len([b for b in calls if b.get('action')=='converse'])==before_calls
        history_changed=True
        latest=brain.get_conversation('owner','r9-chat')['messages'][-1]['knowledge_actions_r9']
        fresh={**selection,'draft_id':'e'*64}
        with db() as c:before=c.execute("SELECT COUNT(*) FROM conversation_messages WHERE role='assistant'").fetchone()[0]
        try:context_chat.chat('owner','Make it shorter.','r9-chat',agent_id=agent['id'],owner_tools=True,include_memory=False,knowledge_revision=fresh);raise AssertionError('Changed native Chat history persisted a refinement')
        except brain.BrainError:pass
        with db() as c:
            assert c.execute("SELECT COUNT(*) FROM conversation_messages WHERE role='assistant'").fetchone()[0]==before
            c.execute("UPDATE conversation_messages SET metadata_json=? WHERE id=(SELECT MAX(id) FROM conversation_messages WHERE role='assistant')",(json.dumps({'knowledge_actions_r9':latest}),))
        history_changed=False
        for text in ['How do tasks use Knowledge?','For example, create a task from Knowledge.','Do not create a task from Knowledge.']:
            before=len([b for b in calls if b.get('action')=='converse']);context_chat.chat('owner',text,'r9-chat',agent_id=agent['id'],owner_tools=True,include_memory=False)
            assert len([b for b in calls if b.get('action')=='converse'])==before
        assert len(normal)==3
        for flag in ['include_knowledge','cloud_allowed']:
            with db() as c:c.execute('UPDATE conversation_context_settings SET '+flag+"=0 WHERE conversation_id='r9-chat'")
            calls.clear();before=len(normal);blocked=context_chat.chat('owner','Make it shorter.','r9-chat',agent_id=agent['id'],owner_tools=True,include_memory=False)
            assert not blocked['knowledge_actions_r9']['drafts'] and not calls and len(normal)==before,'Private request exported or called generic tools'
            with db() as c:c.execute('UPDATE conversation_context_settings SET '+flag+"=1 WHERE conversation_id='r9-chat'")
        for mode in ['changed','revoke','replace']:
            changed=mode=='changed';revoke=mode=='revoke';replace=mode=='replace'
            with db() as c:before=c.execute("SELECT COUNT(*) FROM conversation_messages WHERE role='assistant'").fetchone()[0]
            try:context_chat.chat('owner',query,'r9-chat',agent_id=agent['id'],owner_tools=True,include_memory=False);raise AssertionError('Changed authority persisted a draft')
            except brain.BrainError:pass
            with db() as c:
                assert c.execute("SELECT COUNT(*) FROM conversation_messages WHERE role='assistant'").fetchone()[0]==before
                c.execute("UPDATE app_permissions SET allowed=1 WHERE paired_app_id=?",(app,))
            save_https_session(endpoint,token)
        changed=revoke=replace=False
        calls.clear();context_chat.chat('owner','Make it shorter.','r9-chat',agent_id=agent['id'],owner_tools=True,read_only=True,include_memory=False)
        assert not any(b.get('action')=='converse' for b in calls),'Read-only turn proposed an action'
    # Actual Cloud export feeds the existing native workspace, never a second local Knowledge row.
    if path:=os.environ.get('VP3_KNOWLEDGE_R10_EXPORT'):
        e=json.loads(Path(path).read_text())
        raw=json.dumps(e['knowledge'],ensure_ascii=False).encode();sync.apply_snapshot('http://127.0.0.1|7','knowledge',raw,hashlib.sha256(raw).hexdigest())
        from app.services import native_workspaces,canonical_context
        assert native_workspaces.items('knowledge','Owner edited brief')['count']>=1
        assert any(r['data']=={'record_key':e['document']['record_key'],'record_revision':e['document']['record_revision']} for r in actions.receipts())
        settings=context_engine.ensure_settings('r9-chat')
        ctx=canonical_context.build_authorized_context(agent_id=agent['id'],query='Owner edited findings',source_app_key='owner',permissions=set(),owner=True,include_memory=False,include_knowledge=True,include_contacts=False,settings=settings,max_context_chars=12000,cloud_allowed=True,include_collaboration=False)
        assert 'Owner edited findings' in str(ctx.bundle.knowledge)
        with db() as c:assert c.execute('SELECT COUNT(*) FROM knowledge_items').fetchone()[0]==0
    assert r9.intent('Turn these highlights into a project brief.')
    assert not r9.intent('How would research become a brief?')
    print('PASS: R11 short follow-up routing, explicit current edited-draft selection, stale selection rejection before export, bounded proxy, truthful usage and durable real Chat history; retained R10 new-document intent and actual owner Chat/history, paired canonical draft/validation, no generic tools or duplicate native records, truthful Cloud usage, ordinary discussion unchanged, private/read-only isolation, source/destination revocation/session fences and no partial assistant persistence.')
