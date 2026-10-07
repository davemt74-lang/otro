"""Owner editing, deferred authority, durable retry and independent execution tests."""
import hashlib,json,os,sys,tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix='workspace-actions-') as directory:
    os.environ['HOMESERVER_DATA_DIR']=directory
    import httpx
    from app.database import db,initialize_database,migration_files
    from app.services import workspace_sync as sync,workspace_actions as actions,native_workspaces as native,agent_tools,approvals,tools,app_scopes
    from app.services.https_bridge_session import save_https_session,load_https_session
    # Upgrade from the installed schema must preserve pending owner approvals.
    with patch('app.database.migration_files',return_value=[item for item in migration_files() if item[0]<69]):
        initialize_database()
    preserved='upgrade-pending-approval'
    with db() as c:
        c.execute("INSERT INTO action_requests(id,action_key,source_app_key,actor_type,arguments_json,expires_at) VALUES(?,'tasks.create','owner','owner',?,?)",(preserved,json.dumps({'title':'Keep existing approval'}),'2099-01-01T00:00:00Z'))
    initialize_database()
    with db() as c:
        assert c.execute('SELECT arguments_json,status FROM action_requests WHERE id=?',(preserved,)).fetchone()[0]==json.dumps({'title':'Keep existing approval'})
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
    endpoint='http://127.0.0.1/tenant/api/homeserver-https-poll-v1300.php'
    save_https_session(endpoint,'a'*64)
    peer='http://127.0.0.1|1'
    with db() as c:
        c.execute("INSERT INTO paired_apps(app_key,name,token_hash,status) VALUES('vp3','Cloud',?,'active')",('f'*64,))
        c.execute('UPDATE workspace_sync_settings SET peer_id=?,session_hash=? WHERE id=1',(peer,hashlib.sha256(('a'*64).encode()).hexdigest()))
    revision='1'*64
    def replica(revision=revision):
        raw=json.dumps({'contract':sync.CONTRACT,'source':'cloud','dataset':'contacts','records':[{'table':'crm_contacts','source_id':'1','data':{'name':'Source contact','email':'own@example.invalid','status':'active'},'record_revision':revision}],'files':[]}).encode()
        sync.apply_snapshot(peer,'contacts',raw,hashlib.sha256(raw).hexdigest())
    replica()
    def payload(id='change-0001',expected=revision):return {'dataset':'contacts','key':'crm_contacts:1','expected_revision':expected,'mutation_id':id,'fields':{'display_name':'Reviewed edit 💡'}}
    def denied(call,code=None):
        try:call()
        except (sync.WorkspaceSyncError,tools.ToolError,approvals.ApprovalError,agent_tools.AgentToolError) as e:
            if code is not None:assert e.status_code==code,(str(e),e.status_code)
            return
        raise AssertionError('Denied action accepted')
    # Queueing while sync is paused is an explicit owner action, never an execution.
    sync.set_enabled(False)
    first=actions.enqueue(payload())
    assert first['action']['state']=='queued'
    assert actions.enqueue(payload())['idempotent_replay']
    other=payload();other['fields']={'display_name':'Different'}
    denied(lambda:actions.enqueue(other),409)
    denied(lambda:actions.enqueue(payload('second-0001')),409)
    actions.cancel('change-0001')
    assert actions.status()['items'][0]['state']=='cancelled'
    actions.enqueue(payload('retry-0001'))
    # A fresh Python process sees the same pending edit; no in-memory queue dependency.
    import subprocess
    subprocess.run([sys.executable,'-c',"from app.services.workspace_actions import status; assert status()['pending_count']==1"],check=True,cwd=Path(__file__).resolve().parents[1],env=os.environ.copy())
    sync.set_enabled(True)
    posted=[]
    def transport(request):
        body=json.loads(request.content);posted.append(body)
        assert body['mutation_id']=='retry-0001'
        if len(posted)==1:raise httpx.ReadTimeout('provider-secret-should-not-leak',request=request)
        return httpx.Response(200,json={'ok':True,'contract':sync.CONTRACT,'account_id':'1','mutation_id':body['mutation_id'],'record_key':body['key'],'record_revision':'2'*64,'applied':True})
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        actions.deliver(client,load_https_session())
        assert actions.status()['items'][0]['state']=='queued'
        assert 'provider-secret' not in json.dumps(actions.status())
        denied(lambda:actions.cancel('retry-0001'),409)  # May already be committed remotely.
        actions.deliver(client,load_https_session())
        assert posted[0]==posted[1]
        assert actions.status()['items'][0]['state']=='applied'
    replica('2'*64)
    actions.reconcile(peer,'contacts',native.snapshot('contacts'))
    assert actions.status()['items'][0]['state']=='synced'
    assert actions.enqueue(payload('retry-0001'))['action']['state']=='synced'
    # Conflict is terminal, with an explicit fresh revision/new edit required.
    actions.enqueue(payload('conflict-0001','2'*64))
    with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(409,json={'ok':False,'error':'stale'}))) as client:actions.deliver(client,load_https_session())
    assert actions.status()['items'][0]['state']=='conflict'
    assert actions.status()['attention_count']==1
    # A queued edit never acquires renewed authority after re-pairing or scope change.
    actions.enqueue(payload('pairing-0001','2'*64))
    save_https_session(endpoint,'b'*64)
    with db() as c:c.execute('UPDATE workspace_sync_settings SET session_hash=? WHERE id=1',(hashlib.sha256(('b'*64).encode()).hexdigest(),))
    with httpx.Client(transport=httpx.MockTransport(lambda request: (_ for _ in ()).throw(AssertionError('Old pairing edit was sent')))) as client:actions.deliver(client,load_https_session())
    assert actions.status()['items'][0]['state']=='blocked'
    # Agent changes use normal owner approval and capture authority at proposal time.
    agent_tools.save_policy(True,3,True)
    schemas=agent_tools.model_tool_schemas(owner=True,allow_write_proposals=True)
    assert 'homeserver_workspace_update_request' in {row['function']['name'] for row in schemas}
    assert not any('workspace' in row['function']['name'] for row in agent_tools.model_tool_schemas({'tools.execute'},owner=False,allow_write_proposals=True))
    denied(lambda:agent_tools.execute_model_tool('app:vp3','homeserver_workspace_update_request',payload('agent-0001','2'*64)),None)
    denied(lambda:tools.execute_tool('owner','workspace.update',payload('agent-0001','2'*64),owner=True),403)
    proposal=agent_tools.execute_model_tool('owner','homeserver_workspace_update_request',payload('agent-0001','2'*64),owner=True)
    rid=proposal['result']['request_id']
    assert not any(row['mutation_id']=='agent-0001' for row in actions.status()['items'])
    approved=approvals.approve_request(rid)
    assert any(row['mutation_id']=='agent-0001' and row['state']=='queued' for row in actions.status()['items'])
    denied(lambda:approvals.approve_request(rid),409)
    actions.cancel('agent-0001')
    proposal=agent_tools.execute_model_tool('owner','homeserver_workspace_update_request',payload('agent-stale-0001','2'*64),owner=True)
    with db() as c:c.execute("UPDATE paired_apps SET token_hash=? WHERE app_key='vp3'",('e'*64,))
    denied(lambda:approvals.approve_request(proposal['result']['request_id']),403)
    with db() as c:
        assert c.execute('SELECT status FROM action_requests WHERE id=?',(proposal['result']['request_id'],)).fetchone()[0]=='failed'
    assert not any(row['mutation_id']=='agent-stale-0001' for row in actions.status()['items'])
    token=actions.agent_datasets.set(())
    denied(lambda:actions.editable('contacts','crm_contacts:1'),403)
    assert native.search('Source')==[]
    actions.agent_datasets.reset(token)
    # API owner authentication and request-origin protection, including offline drafts.
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    sync.set_enabled(False)
    with TestClient(app) as client:
        assert client.get('/api/v1/control/workspace-sync/edit/contacts?key=crm_contacts:1').status_code==401
        assert client.post('/__owner/session',headers={'X-HomeServer-Owner':OWNER_CONTROL_TOKEN}).status_code==200
        assert client.get('/api/v1/control/workspace-sync/edit/contacts?key=crm_contacts:1').json()['editable_values']['display_name']=='Source contact'
        assert client.post('/api/v1/control/workspace-sync/edits',json=payload('api-00001','2'*64)).status_code==403
        assert client.post('/api/v1/control/workspace-sync/edits',headers={'X-Requested-With':'XMLHttpRequest','Origin':'https://other.invalid'},json=payload('api-00001','2'*64)).status_code==403
        assert client.post('/api/v1/control/workspace-sync/edits',headers={'X-Requested-With':'XMLHttpRequest'},json=payload('api-00001','2'*64)).status_code==200
        assert client.post('/api/v1/control/workspace-sync/edits',headers={'X-Requested-With':'XMLHttpRequest'},json={**payload('api-00002','2'*64),'fields':{'owner_user_id':'2'}}).status_code==422
    with db() as c:
        assert c.execute('SELECT count(*) FROM local_calendar_events').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM automation_routines').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM contacts').fetchone()[0]==0
    assert sync.status()['copied_schedules_execute'] is False
    print('Durable owner drafts, exact retries, conflict handling, pairing changes, Agent approvals, origin protection and no duplicate native jobs PASS')
