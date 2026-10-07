"""Native views use account-bound full replicas without creating executable jobs."""
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

with tempfile.TemporaryDirectory(prefix='native-workspaces-') as folder:
    os.environ['HOMESERVER_DATA_DIR'] = folder
    import httpx
    from app.database import db, initialize_database
    from app.services import native_workspaces as native, workspace_sync as sync, tools, app_scopes
    from app.services.https_bridge_session import save_https_session

    initialize_database()
    endpoint = 'http://127.0.0.1/tenant/api/homeserver-https-poll-v1300.php'
    token = 'a' * 64
    peer = 'http://127.0.0.1|1'
    save_https_session(endpoint, token)
    with db() as connection:
        connection.execute("INSERT INTO paired_apps(app_key,name,token_hash,status) VALUES('vp3','VP3',?,'active')", ('f'*64,))
        connection.execute('UPDATE workspace_sync_settings SET peer_id=?,session_hash=? WHERE id=1', (peer, hashlib.sha256(token.encode()).hexdigest()))
        connection.execute("INSERT INTO contacts(display_name,notes) VALUES('Same name','Local original')")

    def replica(dataset, records):
        raw = json.dumps({'contract':sync.CONTRACT,'source':'cloud','dataset':dataset,'records':records,'files':[]}, ensure_ascii=False).encode()
        sync.apply_snapshot(peer, dataset, raw, hashlib.sha256(raw).hexdigest())

    long_text = 'Complete 中文 document ' * 10000 + ' tailneedle evidence 💡'
    replica('knowledge',[{'table':'knowledge_items','source_id':'1','data':{'title':'Cloud evidence','content_text':long_text}}])
    replica('contacts',[{'table':'crm_contacts','source_id':str(i),'data':{'name':'Same name','email':f'person{i}@example.invalid','notes':'Full native notes'}} for i in range(1,122)])
    replica('schedules',[{'table':'agent_scheduling_schedules','source_id':'1','data':{'name':'Daily cloud schedule','is_active':1}}])
    assert native.items('contacts')['count'] == 121
    assert len(native.items('contacts')['items']) == 50
    assert native.items('contacts',offset=100)['items'][0]['source_id'] == '101'
    assert native.items('contacts','person121')['items'][0]['id'] == 'cloud:contacts:crm_contacts:121'
    assert 'tailneedle' in native.search('Find tailneedle','knowledge')[0]['excerpt']
    assert len(native.search('tailneedle','knowledge')[0]['excerpt']) <= 1600
    assert native.items('knowledge','tailneedle')['count'] == 1
    assert sync.records('knowledge',detail_key='knowledge_items:1')['items'][0]['data']['content_text'] == long_text
    with db() as connection:
        assert connection.execute('SELECT count(*) FROM contacts').fetchone()[0] == 1
        assert connection.execute('SELECT count(*) FROM automation_routines').fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM local_calendar_events').fetchone()[0] == 0
    assert native.items('schedules')['copied_schedules_execute'] is False

    account = '1'
    route = 'contacts.php?edit_cloud=121'
    change_pair = False
    revoke = False
    def source(request):
        assert str(request.url) == 'http://127.0.0.1/tenant/api/homeserver-workspace-sync-v1.php'
        assert json.loads(request.content) == {'action':'source','dataset':'contacts','key':'crm_contacts:121'}
        if change_pair:
            save_https_session(endpoint,'b'*64)
        if revoke:
            with db() as connection:
                connection.execute("UPDATE paired_apps SET status='revoked' WHERE app_key='vp3'")
        return httpx.Response(200,json={'ok':True,'contract':sync.CONTRACT,'account_id':account,'source_path':route})
    def denied(call):
        try:
            call()
        except (sync.WorkspaceSyncError,tools.ToolError):
            return
        raise AssertionError('Authority failure was accepted')

    with httpx.Client(transport=httpx.MockTransport(source)) as client:
        assert native.source_url('contacts','crm_contacts:121',client) == 'http://127.0.0.1/tenant/contacts.php?edit_cloud=121'
        account = '2'
        denied(lambda:native.source_url('contacts','crm_contacts:121',client))
        account = '1'
        route = '//outside.invalid/steal'
        denied(lambda:native.source_url('contacts','crm_contacts:121',client))
        route = 'contacts.php?edit_cloud=121'
        change_pair = True
        denied(lambda:native.source_url('contacts','crm_contacts:121',client))
        assert native.items('contacts')['count'] == 0
        assert native.search('Same name') == []
        save_https_session(endpoint,token)
        change_pair = False
        revoke = True
        denied(lambda:native.source_url('contacts','crm_contacts:121',client))
        assert native.items('contacts')['count'] == 0
        assert sync.records('contacts')['count'] == 0
        with db() as connection:
            connection.execute("UPDATE paired_apps SET status='active' WHERE app_key='vp3'")
        revoke = False
        with db() as connection:
            app_id=connection.execute("SELECT id FROM paired_apps WHERE app_key='vp3'").fetchone()[0]
        app_scopes.save_scope(app_id,{'cloud_allowed':False})
        assert native.items('contacts')['count'] == 0
        assert sync.records('contacts')['count'] == 0
        assert native.search('tailneedle') == []
        denied(lambda:native.source_url('contacts','crm_contacts:121',client))
        app_scopes.save_scope(app_id,{'cloud_allowed':True})
        sync.set_enabled(False)
        assert native.items('contacts')['count'] == 121  # offline copies still readable
        denied(lambda:native.source_url('contacts','crm_contacts:121',client))
        sync.set_enabled(True)

    owner_result = tools.execute_tool('owner','workspace.search',{'query':'tailneedle'},owner=True)
    assert 'tailneedle' in json.dumps(owner_result)
    denied(lambda:tools.execute_tool('vp3','workspace.search',{'query':'tailneedle'}, {'tools.execute','knowledge.search'}))

    # Owner control middleware protects both details and the new source redirect.
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    with TestClient(app) as client:
        assert client.get('/api/v1/control/workspace-sync/native/contacts').status_code == 401
        assert client.get('/api/v1/control/workspace-sync/source/contacts?key=crm_contacts:121').status_code == 401
        assert client.post('/__owner/session',headers={'X-HomeServer-Owner':OWNER_CONTROL_TOKEN}).status_code == 200
        assert client.get('/api/v1/control/workspace-sync/native/contacts?q=person121').json()['items'][0]['read_only']
        assert client.get('/api/v1/control/contacts?q=person121').json()['items'][0]['email'] == 'person121@example.invalid'
        assert client.get('/api/v1/control/knowledge?q=tailneedle').json()['items'][0]['record_key'] == 'knowledge_items:1'
        from app.services import providers
        prompts=[]
        def answer(messages,model_override=None):
            prompts.append(messages)
            return {'provider':'ollama','model':'native-test','content':'Checked synced evidence.'}
        client.put('/api/v1/control/provider',json={'base_url':'http://127.0.0.1:9','model':'native-test','enabled':True})
        with patch.object(providers,'generate_ollama',side_effect=answer):
            assert client.post('/api/v1/control/chat',json={'message':'What does tailneedle say?'}).status_code == 200
            assert 'VP3 Cloud' in json.dumps(prompts[-1]) and 'tailneedle' in json.dumps(prompts[-1])
            assert client.post('/api/v1/control/chat',json={'message':'tailneedle '+'long question '*100}).status_code == 200
            assert client.post('/api/v1/control/chat',json={'message':'tailneedle','include_knowledge':False,'include_contacts':False}).status_code == 200
            assert 'tailneedle evidence' not in json.dumps(prompts[-1])
            assert client.post('/api/v1/control/chat',json={'message':'tailneedle','cloud_allowed':False}).status_code == 200
            assert 'tailneedle evidence' not in json.dumps(prompts[-1])
        with patch.object(native,'source_url',return_value='http://127.0.0.1/tenant/contacts.php?edit_cloud=121'):
            response=client.get('/api/v1/control/workspace-sync/source/contacts?key=crm_contacts:121',follow_redirects=False)
            assert response.status_code == 303 and response.headers['cache-control'] == 'no-store'
        assert client.put('/api/v1/control/contacts/cloud:contacts:crm_contacts:121',json={'display_name':'wrong'}).status_code == 422
    print('Native projections, full-document Agent evidence, pagination, source authority, owner isolation and no duplicate jobs PASS')
