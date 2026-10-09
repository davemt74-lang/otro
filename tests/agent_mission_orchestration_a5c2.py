"""Real scheduler, SQLite receipts and native reads; only inference is stubbed."""
import hashlib, json, os, sys, tempfile, threading, time, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

with tempfile.TemporaryDirectory(prefix='a5c2-') as tmp:
    os.environ['HOMESERVER_DATA_DIR'] = tmp
    from app.database import db, initialize_database
    from app.services import agent_mission_runtime as mission, agent_mission_tool_contracts as contracts
    from app.services import agent_mission_orchestration as run, agent_mission_execution as inference
    from app.services import agent_mission_control as control, agent_tools, app_scopes, knowledge_collections
    initialize_database()
    mission._route = lambda *args: ('ollama', 'fixture', False)
    agent_tools.save_policy(True, 3)
    with db() as conn:
        pid = conn.execute('SELECT id FROM agents WHERE is_primary=1').fetchone()[0]
        for source in ('owner', 'app:vp3'):
            cid = source + '-orchestration'
            conn.execute('INSERT INTO conversations(id,agent_id,source_app_key,title) VALUES(?,?,?,?)', (cid,pid,source,'Orchestration'))
            conn.execute('INSERT INTO conversation_context_settings(conversation_id,cloud_allowed) VALUES(?,1)', (cid,))
        appid = conn.execute('INSERT INTO paired_apps(app_key,name,token_hash) VALUES(?,?,?)', ('vp3','VP3',hashlib.sha256(b'fixture').hexdigest())).lastrowid
        for key in ('agent.chat', 'tools.execute', 'knowledge.search', 'contacts.read', 'tasks.read'):
            conn.execute('INSERT INTO app_permissions(paired_app_id,permission,allowed) VALUES(?,?,1)', (appid,key))
        conn.execute("INSERT INTO knowledge_items(title,content) VALUES('A5C evidence','A5C bounded research fixture')")

    def uid(): return str(uuid.uuid4())
    def deny(fn, code):
        try: fn()
        except mission.MissionError as error:
            assert error.status_code == code, (str(error), error.status_code)
        else: raise AssertionError('Expected rejection '+str(code))
    def prepare(source='owner', count=1, tools=None, budget=1, parallel=2):
        tasks = [{'role':'research','title':'Worker '+str(i),'objective':'Research A5C','depends_on':[]} for i in range(count)]
        if count == 3: tasks[-1]['depends_on'] = [0,1]
        record = mission.create_mission(source, conversation_id=source+'-orchestration', objective='Bounded research', client_request_id=uid(), parent_agent_id=pid, owner=source=='owner', tasks=tasks)
        assignment = {'max_parallel':parallel,'assignments':[{'task_id':t['id'],'tools':(tools if tools is not None else ['knowledge.search']) if i<2 else [],'max_calls':budget if i<2 else 0,'output':'sources' if i<2 else 'document','browser_url':''} for i,t in enumerate(record['tasks'])]}
        contracts.configure(source,record['id'],assignment,request_id=uid(),expected_revision=0,confirmed=True)
        return record,assignment
    def wait(mid, source='owner'):
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            r=mission.get_mission(source,mid)
            if r['status'] not in ('running','planned'): return r
            time.sleep(.02)
        raise AssertionError('Mission did not stop '+mid)
    def start(record, source='owner', request=None):
        return run.start(source,record['id'],request_id=request or uid(),expected_revision=1,confirmed=True)
    def successful(source,cid,tid,messages):
        system=messages[0]['content']
        if system.startswith('Choose at most one'):
            tool=json.loads(system.split('Assigned tools: ')[1])[0]
            return json.dumps({'tool':tool,'arguments':{'query':'A5C','limit':1}}),'ollama','fixture'
        citations=json.loads(system.split('Allowed citation IDs: ')[1])
        kind='document' if '"kind":"document"' in system else 'sources'
        return json.dumps({'kind':kind,'title':'A5C report','body':'Findings from supplied evidence.','citations':citations}),'ollama','fixture'
    inference.execute=successful

    # Two independent workers overlap; synthesis runs only after both finish.
    record,_=prepare(count=3); gate=threading.Barrier(2,timeout=10); observed=[]
    def parallel(source,cid,tid,messages):
        if messages[0]['content'].startswith('Choose at most one'):
            observed.append(tid); gate.wait()
        return successful(source,cid,tid,messages)
    inference.execute=parallel
    request=uid();start(record,request=request); start(record,request=request)
    result=wait(record['id']);assert result['status']=='completed',result
    assert len(observed)==2 and all(t['attempt']==1 for t in result['tasks'])
    assert [t['read_calls_used'] for t in result['tasks']]==[1,1,0]
    final=json.loads(result['tasks'][-1]['result']);assert len(final['citations'])==2
    assert result['tools_enabled'] and result['authority_current'] and not result['verified']
    assert len(run.status('owner',record['id'])['calls'])==2
    deny(lambda:run.start('owner',record['id'],request_id=request,expected_revision=2,confirmed=True),409)
    deny(lambda:run.status('app:other',record['id']),404)
    deny(lambda:run.output(json.dumps({'kind':'sources','title':'X','body':'X','citations':[uid()]}),'sources',set()),502)

    # All registered native read paths execute through the existing audited tools.
    inference.execute=successful
    for tool in ('contacts.search','tasks.list'):
        candidate,_=prepare(tools=[tool]);start(candidate)
        assert wait(candidate['id'])['status']=='completed'
    candidate,_=prepare();before=len(run.status('owner',candidate['id'])['calls'])
    inference.execute=lambda *args:(json.dumps({'tool':'tasks.create','arguments':{'title':'Forbidden'}}),'ollama','fixture')
    start(candidate);assert wait(candidate['id'])['status']=='failed'
    assert len(run.status('owner',candidate['id'])['calls'])==before

    # Malformed output fails; retries cannot replenish a spent read budget.
    candidate,_=prepare()
    def malformed(source,cid,tid,messages):
        if messages[0]['content'].startswith('Choose at most one'): return successful(source,cid,tid,messages)
        return 'not JSON','ollama','fixture'
    inference.execute=malformed;start(candidate);assert wait(candidate['id'])['status']=='failed'
    inference.execute=successful
    control.retry('owner',candidate['id'],candidate['tasks'][0]['id'])
    result=wait(candidate['id']);assert result['status']=='completed' and result['tasks'][0]['read_calls_used']==1
    # Ambiguous durable claims from a crash consume the remaining budget.
    candidate,_=prepare();dispatch=mission._dispatch;mission._dispatch=lambda *_:None;start(candidate)
    tid=candidate['tasks'][0]['id'];lease=uid()
    with db() as conn:conn.execute("UPDATE agent_mission_tasks_v1 SET status='running',lease_id=? WHERE id=?",(lease,tid))
    run.claim('owner',candidate['id'],tid,lease,'knowledge.search',{'query':'A5C'})
    deny(lambda:run.claim('owner',candidate['id'],tid,lease,'knowledge.search',{'query':'A5C'}),409)
    control.pause('owner',candidate['id']);control.resume('owner',candidate['id'],allow_reexecution=True)
    mission._dispatch=dispatch;mission._dispatch(candidate['id'])
    assert wait(candidate['id'])['tasks'][0]['read_calls_used']==1

    # Pause, cancellation, review expiry and revocation each fence late inference.
    for case in ('pause','cancel','expiry','permission','restored','generation','scope'):
        source='app:vp3' if case in ('permission','restored','generation','scope') else 'owner'
        candidate,_=prepare(source=source);entered=threading.Event();release=threading.Event()
        def blocked(source,cid,tid,messages):
            entered.set();assert release.wait(10)
            return successful(source,cid,tid,messages)
        inference.execute=blocked;start(candidate,source);assert entered.wait(10)
        if case=='pause':control.pause(source,candidate['id'])
        elif case=='cancel':mission.cancel_mission(source,candidate['id'])
        elif case=='expiry':
            with db() as conn:conn.execute("UPDATE agent_mission_tool_contracts_v1 SET expires_at=datetime('now','-1 second') WHERE mission_id=?",(candidate['id'],))
        elif case in ('permission','restored'):
            with db() as conn:conn.execute("UPDATE app_permissions SET allowed=0 WHERE paired_app_id=? AND permission='knowledge.search'",(appid,))
            if case=='restored':
                with db() as conn:conn.execute("UPDATE app_permissions SET allowed=1 WHERE paired_app_id=? AND permission='knowledge.search'",(appid,))
        elif case=='generation':
            with db() as conn:conn.execute("UPDATE paired_apps SET token_hash='rotated' WHERE id=?",(appid,))
        else:app_scopes.save_scope(appid,{'cloud_allowed':True,'tool_names':['contacts.search']})
        release.set()
        # Joining the executor drains the late callback, including its dispatch.
        mission._pool.shutdown(wait=True);mission._pool=None
        result=mission.get_mission(source,candidate['id']);assert not result['result'] and not result['tasks'][0]['result']
        assert not run.status(source,candidate['id'])['calls']
        if case in ('permission','restored','generation','scope'):assert not result['authority_current']
        if case=='pause':
            deny(lambda:control.resume(source,candidate['id']),409)
            inference.execute=successful;control.resume(source,candidate['id'],allow_reexecution=True)
            assert wait(candidate['id'],source)['status']=='completed'
        with db() as conn:
            conn.execute("UPDATE app_permissions SET allowed=1 WHERE paired_app_id=?",(appid,))
            conn.execute('UPDATE paired_apps SET token_hash=? WHERE id=?',(hashlib.sha256(b'fixture').hexdigest(),appid))
        app_scopes.save_scope(appid,{'cloud_allowed':True})

    # Previously completed results are hidden when collection grants change.
    inference.execute=successful;candidate,_=prepare(source='app:vp3');start(candidate,'app:vp3')
    assert wait(candidate['id'],'app:vp3')['result']
    with db() as conn:conn.execute("INSERT INTO knowledge_collections(collection_key,name) VALUES('restricted','Restricted')")
    knowledge_collections.set_app_collection_scope(appid,['restricted'])
    assert not mission.get_mission('app:vp3',candidate['id'])['result']
    knowledge_collections.set_app_collection_scope(appid,[])

    # Both canonical UI routes preserve the mission's original app authority.
    from app.agent_mission_browser_api import OwnerWorkspaceOperation, owner_workspace
    from app.agent_mission_tools_api import StartTools
    from app.services import agent_mission_cloud_v1 as relay
    from fastapi import HTTPException
    from pydantic import ValidationError
    candidate,_=prepare(source='app:vp3')
    assert owner_workspace(OwnerWorkspaceOperation(action='tools.get',mission_id=candidate['id']))['tools']['revision']==1
    assert relay.execute('tools.status',{'mission_id':candidate['id']})['orchestration']['started'] is False
    with db() as conn:conn.execute("UPDATE app_permissions SET allowed=0 WHERE paired_app_id=? AND permission='knowledge.search'",(appid,))
    try:owner_workspace(OwnerWorkspaceOperation(action='tools.start',mission_id=candidate['id'],expected_revision=1,request_id=uid(),confirmed=True))
    except HTTPException as error:assert error.status_code==403
    else:raise AssertionError('Native owner workspace elevated an app grant')
    with db() as conn:conn.execute("UPDATE app_permissions SET allowed=1 WHERE paired_app_id=? AND permission='knowledge.search'",(appid,))
    for invalid in ({'confirmed':'true'},{'expected_revision':True},{'shell':'whoami'}):
        try:StartTools(**({'confirmed':True,'expected_revision':1,'request_id':uid()}|invalid))
        except ValidationError:pass
        else:raise AssertionError('Start API accepted a malformed approval')
    with db() as conn:conn.execute("UPDATE conversation_context_settings SET cloud_allowed=0 WHERE conversation_id='app:vp3-orchestration'")
    deny(lambda:relay.execute('tools.status',{'mission_id':candidate['id']}),403)
    with db() as conn:conn.execute("UPDATE conversation_context_settings SET cloud_allowed=1 WHERE conversation_id='app:vp3-orchestration'")

    # Browser approval is separate; model URL arguments cannot choose navigation.
    candidate,assignment=prepare(tools=['browser.read']);assignment['assignments'][0]['browser_url']='https://example.com/'
    contracts.configure('owner',candidate['id'],assignment,request_id=uid(),expected_revision=1,confirmed=True)
    deny(lambda:run.start('owner',candidate['id'],request_id=uid(),expected_revision=2,confirmed=True),409)
    deny(lambda:run.arguments('browser.read',{'url':'https://other.example/'},assignment['assignments'][0]),422)
    tid=candidate['tasks'][0]['id']
    with db() as conn:
        conn.execute("INSERT INTO agent_mission_browser_v1(task_id,mission_id,source_app_key,pinned_ip,approved_origin,current_url,expires_at) VALUES(?,?,'owner','93.184.216.34','https://example.com','https://example.com/',datetime('now','+10 minutes'))",(tid,candidate['id']))
        conn.execute("INSERT INTO agent_mission_browser_takeover_v4(task_id,mission_id,source_app_key,session_token,mode,expires_at) VALUES(?,?,'owner','fixture','owner',datetime('now','+5 minutes'))",(tid,candidate['id']))
    mission._dispatch=lambda *_:None
    deny(lambda:run.start('owner',candidate['id'],request_id=uid(),expected_revision=2,confirmed=True),409)
    with db() as conn:conn.execute("UPDATE agent_mission_browser_takeover_v4 SET mode='agent' WHERE task_id=?",(tid,))
    run.start('owner',candidate['id'],request_id=uid(),expected_revision=2,confirmed=True)
    run.check('owner',candidate['id'])
    with db() as conn:conn.execute("UPDATE agent_mission_browser_v1 SET status='closed' WHERE task_id=?",(tid,))
    deny(lambda:run.check('owner',candidate['id']),403)
    assert not mission.get_mission('owner',candidate['id'])['authority_current']
    mission._dispatch=dispatch
    if mission._pool:mission._pool.shutdown(wait=True)
    print('A5C2 PASS: native audited reads, DAG concurrency, durable starts and cumulative budgets, strict outputs/citations, interruption fences, pairing/scope/collection revocation, separate browser grants and owner takeover')
