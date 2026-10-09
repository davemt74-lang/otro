"""Real scheduler, approvals and SQLite writes; only model inference is controlled."""
import hashlib, json, os, sys, tempfile, threading, time, uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

with tempfile.TemporaryDirectory(prefix='a5c3-') as tmp:
    os.environ['HOMESERVER_DATA_DIR']=tmp
    from app.database import db,initialize_database
    from app.services import agent_mission_runtime as mission,agent_mission_tool_contracts as contracts
    from app.services import agent_mission_orchestration as run,agent_mission_actions as changes
    from app.services import agent_mission_execution as inference,agent_mission_control as control
    from app.services import agent_tools,approvals,contacts,tools,app_scopes
    initialize_database()
    mission._route=lambda *args:('ollama','fixture',False)
    agent_tools.save_policy(True,3,True)
    with db() as conn:
        agent=conn.execute('SELECT id FROM agents WHERE is_primary=1').fetchone()[0]
        for source in ('owner','app:vp3'):
            conn.execute('INSERT INTO conversations(id,agent_id,source_app_key,title) VALUES(?,?,?,?)',(source+'-actions',agent,source,'Specialist changes'))
            conn.execute('INSERT INTO conversation_context_settings(conversation_id,cloud_allowed) VALUES(?,1)',(source+'-actions',))
        appid=conn.execute('INSERT INTO paired_apps(app_key,name,token_hash) VALUES(?,?,?)',('vp3','VP3',hashlib.sha256(b'fixture').hexdigest())).lastrowid
        for key in ('agent.chat','tools.execute','contacts.read','contacts.write','knowledge.search','knowledge.write','tasks.read','tasks.write','events.read','events.write','approvals.review'):
            conn.execute('INSERT INTO app_permissions(paired_app_id,permission,allowed) VALUES(?,?,1)',(appid,key))

    def uid():return str(uuid.uuid4())
    def count(table):
        with db() as conn:return conn.execute('SELECT count(*) FROM '+table).fetchone()[0]
    def denied(fn,code):
        try:fn()
        except (mission.MissionError,approvals.ApprovalError) as e:assert e.status_code==code,(str(e),e.status_code)
        else:raise AssertionError('Expected rejection '+str(code))
    def prepare(key='contacts.create',source='owner',budget=1,read_tools=None):
        record=mission.create_mission(source,conversation_id=source+'-actions',objective='Prepare exact reviewed changes',client_request_id=uid(),parent_agent_id=agent,owner=source=='owner',tasks=[{'role':'editor','title':'Editor','objective':'Prepare a useful change','depends_on':[]}])
        assignment={'max_parallel':1,'assignments':[{'task_id':record['tasks'][0]['id'],'tools':read_tools or [],'max_calls':1 if read_tools else 0,'output':'analysis','browser_url':'','actions':[key],'max_actions':budget}]}
        contracts.configure(source,record['id'],assignment,request_id=uid(),expected_revision=0,confirmed=True)
        return record,assignment
    def generated(entries):
        def call(source,cid,tid,messages):
            if messages[0]['content'].startswith('Choose at most one'):
                key=json.loads(messages[0]['content'].split('Assigned tools: ')[1])[0]
                args={'query':'Alice','limit':1} if key=='contacts.search' else {'limit':1}
                return json.dumps({'tool':key,'arguments':args}),'ollama','fixture'
            ids=json.loads(messages[0]['content'].split('Allowed citation IDs: ')[1].split('\n')[0])
            return json.dumps({'kind':'analysis','title':'Changes ready for review','body':'These changes have not been applied.','citations':ids,'actions':entries}),'ollama','fixture'
        return call
    def finish(record,entries,source='owner'):
        inference.execute=generated(entries)
        run.start(source,record['id'],request_id=uid(),expected_revision=1,confirmed=True)
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            current=mission.get_mission(source,record['id'])
            if current['status'] not in ('planned','running'):return current
            time.sleep(.02)
        raise AssertionError('Mission failed to finish')
    def entry(action_key='contacts.create',**args):return {'tool':action_key,'arguments':args or {'display_name':'Alice','email':'alice@example.test'}}
    def review(record,change,source='owner',decision='approve',request=None,local=False):
        return changes.review(source,record['id'],change['id'],expected_hash=change['payload_hash'],decision=decision,request_id=request or uid(),confirmed=True,local_owner=local)

    # Permission to propose is opt-in and independent from the read budget.
    agent_tools.save_policy(True,3,False)
    denied(lambda:prepare(),403)
    agent_tools.save_policy(True,3,True)
    record,_=prepare();before=count('contacts')
    assert finish(record,[entry()])['status']=='completed'
    pending=changes.list_actions('owner',record['id'])[0]
    assert pending['status']=='pending' and pending['can_approve'] and count('contacts')==before,pending
    denied(lambda:changes.review('owner',record['id'],pending['id'],expected_hash='0'*64,decision='approve',request_id=uid(),confirmed=True),409)
    request=uid();assert review(record,pending,request=request)[0]['status']=='executed'
    assert count('contacts')==before+1
    assert review(record,pending,request=request)[0]['status']=='executed' and count('contacts')==before+1
    denied(lambda:review(record,pending),409)
    denied(lambda:review(record,pending,source='app:vp3'),404)
    assert mission.get_mission('owner',record['id'])['action_summaries'][0]['status']=='executed'

    # Concurrent replay is one durable native write and one review receipt.
    from concurrent.futures import ThreadPoolExecutor
    record,_=prepare();finish(record,[entry(display_name='Concurrent review')])
    pending=changes.list_actions('owner',record['id'])[0];request=uid();before=count('contacts')
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts=list(pool.map(lambda _:review(record,pending,request=request),range(2)))
    assert all(r[0]['status']=='executed' for r in receipts) and count('contacts')==before+1

    # Owner workspace approval atomically queues delivery; it never edits the replica.
    from unittest.mock import patch
    from app.services import workspace_actions
    binding={'peer_id':'fixture-peer','session_hash':'s'*64,'authority_hash':'a'*64}
    replica={'record_revision':'b'*64,'data':{'display_name':'Original Cloud contact'}}
    with patch.object(workspace_actions,'binding',return_value=binding), patch.object(workspace_actions,'record',return_value=replica), patch.object(workspace_actions.sync,'wake'):
        record,_=prepare('workspace.update');before=count('workspace_sync_actions')
        args={'dataset':'contacts','key':'crm_contacts:1','expected_revision':'b'*64,'fields':{'display_name':'Reviewed Cloud edit'}}
        assert finish(record,[entry('workspace.update',**args)])['status']=='completed'
        pending=changes.list_actions('owner',record['id'])[0]
        assert pending['destination']=='VP3 Cloud (queued for sync)' and '_binding' not in pending['arguments']
        assert count('workspace_sync_actions')==before
        request=uid();review(record,pending,request=request);review(record,pending,request=request)
        assert count('workspace_sync_actions')==before+1 and replica['data']['display_name']=='Original Cloud contact'
        with db() as conn:
            queued=conn.execute('SELECT state,fields_json FROM workspace_sync_actions ORDER BY rowid DESC LIMIT 1').fetchone()
        assert queued['state']=='queued' and json.loads(queued['fields_json'])==args['fields']
    denied(lambda:prepare('workspace.update',source='app:vp3'),403)

    # Rejection, all registered native creates, exact targets, and revision fencing.
    record,_=prepare();finish(record,[entry(display_name='Rejected')]);pending=changes.list_actions('owner',record['id'])[0]
    assert review(record,pending,decision='deny')[0]['status']=='denied'
    denied(lambda:review(record,pending,request=request),409)
    for key,args,table in [('tasks.create',{'title':'Specialist task'},'tasks'),('knowledge.create',{'title':'Specialist draft','content':'Reviewed document content'},'knowledge_items'),('calendar.create',{'title':'Specialist calendar','start_at':'2030-01-02T10:00:00Z','end_at':'2030-01-02T11:00:00Z'},'local_calendar_events')]:
        record,_=prepare(key);before=count(table)
        result=finish(record,[entry(key,**args)]);assert result['status']=='completed',result
        pending=changes.list_actions('owner',record['id'])[0];assert count(table)==before
        review(record,pending);assert count(table)==before+1
    original=contacts.create_federated_contact({'display_name':'Alice revision','email':'old@example.test','mutation_id':uid()})
    args={'canonical_id':original['canonical_id'],'expected_revision':original['record_revision'],'email':'new@example.test'}
    record,_=prepare('contacts.update',read_tools=['contacts.search']);assert finish(record,[entry('contacts.update',**args)])['status']=='completed'
    pending=changes.list_actions('owner',record['id'])[0]
    contacts.update_contact(original['id'],{'display_name':'Alice changed concurrently','email':'concurrent@example.test'})
    denied(lambda:review(record,pending),409)
    assert changes.list_actions('owner',record['id'])[0]['status']=='failed'
    assert contacts.get_federated_contact(original['id'])['email']=='concurrent@example.test'

    # Invalid later changes roll back the whole result and every earlier proposal.
    record,_=prepare(budget=2);before=count('action_requests')
    assert finish(record,[entry(),entry('contacts.create',shell='forbidden')])['status']=='failed'
    assert count('action_requests')==before and not changes.list_actions('owner',record['id'])
    record,_=prepare(budget=2);before=count('action_requests')
    assert finish(record,[entry(),entry('contacts.create',display_name='')])['status']=='failed'
    assert count('action_requests')==before
    record,_=prepare();assert finish(record,[entry('contacts.delete')])['status']=='failed'
    record,_=prepare();assert finish(record,[entry(),entry(display_name='Budget exceeded')])['status']=='failed'

    # Generic approval screens must enforce the mission expiry and payload binding.
    for case in ('expiry','tamper','policy','permission','generation'):
        source='app:vp3' if case in ('permission','generation') else 'owner'
        record,_=prepare(source=source);assert finish(record,[entry(display_name=case)],source)['status']=='completed'
        pending=changes.list_actions(source,record['id'])[0];before=count('contacts')
        with db() as conn:
            if case=='expiry':conn.execute("UPDATE agent_mission_tool_contracts_v1 SET expires_at=datetime('now','-1 second') WHERE mission_id=?",(record['id'],))
            elif case=='tamper':conn.execute('UPDATE action_requests SET arguments_json=? WHERE id=?',(json.dumps({'display_name':'tampered'}),pending['approval_id']))
            elif case=='permission':conn.execute("UPDATE app_permissions SET allowed=0 WHERE paired_app_id=? AND permission='contacts.write'",(appid,))
            elif case=='generation':conn.execute("UPDATE paired_apps SET token_hash='rotated' WHERE id=?",(appid,))
        if case=='policy':agent_tools.save_policy(True,3,False)
        denied(lambda:approvals.approve_request(pending['approval_id']),403)
        assert count('contacts')==before
        agent_tools.save_policy(True,3,True)
        with db() as conn:
            conn.execute('UPDATE app_permissions SET allowed=1 WHERE paired_app_id=?',(appid,))
            conn.execute('UPDATE paired_apps SET token_hash=? WHERE id=?',(hashlib.sha256(b'fixture').hexdigest(),appid))

    # Local owner reviews keep original app authority; Cloud review also needs its grant.
    with db() as conn:conn.execute("UPDATE app_permissions SET allowed=0 WHERE paired_app_id=? AND permission='approvals.review'",(appid,))
    record,_=prepare(source='app:vp3');finish(record,[entry(display_name='Cloud review')],'app:vp3')
    pending=changes.list_actions('app:vp3',record['id'])[0]
    denied(lambda:review(record,pending,source='app:vp3'),403)
    assert review(record,pending,source='app:vp3',local=True)[0]['status']=='executed'
    with db() as conn:conn.execute("UPDATE app_permissions SET allowed=1 WHERE paired_app_id=? AND permission='approvals.review'",(appid,))

    # Interruptions while inference is in flight cannot leave actionable proposals.
    for operation in ('pause','cancel'):
        record,_=prepare();entered=threading.Event();release=threading.Event();before=count('action_requests')
        fn=generated([entry(display_name='Late result')])
        def blocked(*args):
            entered.set();assert release.wait(10);return fn(*args)
        inference.execute=blocked;run.start('owner',record['id'],request_id=uid(),expected_revision=1,confirmed=True);assert entered.wait(10)
        if operation=='pause':control.pause('owner',record['id'])
        else:mission.cancel_mission('owner',record['id'])
        release.set();mission._pool.shutdown(wait=True);mission._pool=None
        assert count('action_requests')==before and not changes.list_actions('owner',record['id'])

    # Canonical UI and Cloud relay expose review receipts without changing source identity.
    from app.agent_mission_browser_api import OwnerWorkspaceOperation,owner_workspace
    from app.services import agent_mission_cloud_v1 as relay
    record,_=prepare(source='app:vp3');finish(record,[entry(display_name='UI receipt')],'app:vp3')
    pending=relay.execute('actions.list',{'mission_id':record['id']})['actions'][0]
    assert owner_workspace(OwnerWorkspaceOperation(action='actions.list',mission_id=record['id']))['actions'][0]['id']==pending['id']
    reviewed=relay.execute('actions.review',{'mission_id':record['id'],'action_id':pending['id'],'expected_hash':pending['payload_hash'],'decision':'approve','request_id':uid(),'confirmed':True})
    assert reviewed['actions'][0]['status']=='executed'
    if mission._pool:mission._pool.shutdown(wait=True)
    print('A5C3 PASS: actual native writes, exact previews, cumulative budgets, replay receipts, source authority, late-result fences, generic approval protection and all-or-nothing staging')
