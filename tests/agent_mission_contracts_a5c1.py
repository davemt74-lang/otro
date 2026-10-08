"""Real SQLite A5C1 capability, source and concurrency acceptance."""
import copy,hashlib,json,os,sys,tempfile,threading,uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

with tempfile.TemporaryDirectory(prefix='a5c1-contracts-') as tmp:
    os.environ['HOMESERVER_DATA_DIR']=tmp
    from app.database import initialize_database,db
    from app.services import agent_mission_runtime as mission,agent_mission_tool_contracts as contracts,agent_tools,app_scopes
    initialize_database()
    mission._route=lambda *args:('ollama','fixture',False)
    agent_tools.save_policy(True,3)
    with db() as conn:
        pid=int(conn.execute('SELECT id FROM agents WHERE is_primary=1').fetchone()[0])
        for source in ('owner','app:vp3'):
            conn.execute('INSERT INTO conversations(id,agent_id,source_app_key,title) VALUES(?,?,?,?)',(source+'-contract',pid,source,'Contracts'))
            conn.execute('INSERT INTO conversation_context_settings(conversation_id,cloud_allowed) VALUES(?,1)',(source+'-contract',))
        app_id=conn.execute('INSERT INTO paired_apps(app_key,name,token_hash) VALUES(?,?,?)',('vp3','VP3',hashlib.sha256(b'fixture-token').hexdigest())).lastrowid
        for key in ('agent.chat','tools.execute','knowledge.search'):
            conn.execute('INSERT INTO app_permissions(paired_app_id,permission,allowed) VALUES(?,?,1)',(app_id,key))
    def create(n,source='owner'):
        return mission.create_mission(source,conversation_id=source+'-contract',objective='Research and produce a report',client_request_id='contract-'+str(n),owner=source=='owner',parent_agent_id=pid,tasks=[{'role':'research','title':'Sources','objective':'Find sources','depends_on':[]},{'role':'writer','title':'Report','objective':'Write report','depends_on':[0]}])
    def profile(record):
        return {'max_parallel':2,'assignments':[{'task_id':t['id'],'tools':['knowledge.search'] if i==0 else [],'max_calls':2 if i==0 else 0,'output':'sources' if i==0 else 'document','browser_url':''} for i,t in enumerate(record['tasks'])]}
    def deny(call,code):
        try:call();raise AssertionError('Expected rejection '+str(code))
        except mission.MissionError as e:assert e.status_code==code,(str(e),e.status_code)
    r=create(1);mid=r['id'];p=profile(r);rid=str(uuid.uuid4())
    out=contracts.configure('owner',mid,p,request_id=rid,expected_revision=0,confirmed=True)
    assert out['active'] and out['revision']==1 and out['execution_enabled'] is False
    assert out['assignments']['assignments'][1]['output']=='document'
    assert contracts.configure('owner',mid,p,request_id=rid,expected_revision=0,confirmed=True)['revision']==1
    deny(lambda:mission.start_mission('owner',mid),409)
    assert mission.get_mission('owner',mid)['status']=='planned','A model-only dispatcher ran a tool mission'
    changed=copy.deepcopy(p);changed['max_parallel']=1
    deny(lambda:contracts.configure('owner',mid,changed,request_id=rid,expected_revision=1,confirmed=True),409)
    rid2=str(uuid.uuid4());contracts.configure('owner',mid,changed,request_id=rid2,expected_revision=1,confirmed=True)
    assert contracts.configure('owner',mid,p,request_id=rid,expected_revision=0,confirmed=True)['revision']==2,'Old request changed current assignments'
    deny(lambda:contracts.configure('owner',mid,p,request_id=str(uuid.uuid4()),expected_revision=1,confirmed=True),409)
    deny(lambda:contracts.get('app:other',mid),404)
    for mutate in (lambda x:x.update(max_parallel=True),lambda x:x['assignments'][0].update(tools=['process.exec']),lambda x:x['assignments'][0].update(max_calls=4),lambda x:x['assignments'][0].update(task_id=r['tasks'][1]['id']),lambda x:x.update(api_key='secret'),lambda x:x['assignments'][0].update(browser_url='https://example.com/checkout')):
        bad=copy.deepcopy(p);mutate(bad)
        deny(lambda:contracts.configure('owner',mid,bad,request_id=str(uuid.uuid4()),expected_revision=2,confirmed=True),422)
    browser=copy.deepcopy(p);browser['assignments'][0].update(tools=['browser.read'],browser_url='https://example.com/checkout')
    deny(lambda:contracts.configure('owner',mid,browser,request_id=str(uuid.uuid4()),expected_revision=2,confirmed=True),403)
    deny(lambda:contracts.configure('owner',mid,p,request_id=str(uuid.uuid4()),expected_revision=2,confirmed=False),422)
    # Competing edits at one revision yield one commit and one stale rejection.
    gate=threading.Barrier(3);outcomes=[]
    def edit():
        gate.wait()
        try:contracts.configure('owner',mid,p,request_id=str(uuid.uuid4()),expected_revision=2,confirmed=True);outcomes.append('saved')
        except mission.MissionError as e:outcomes.append(e.status_code)
    threads=[threading.Thread(target=edit) for _ in range(2)]
    for t in threads:t.start()
    gate.wait()
    for t in threads:t.join(10);assert not t.is_alive()
    assert sorted(outcomes,key=str)==sorted(['saved',409],key=str),outcomes
    cloud=create(2,'app:vp3');cp=profile(cloud)
    contracts.configure('app:vp3',cloud['id'],cp,request_id=str(uuid.uuid4()),expected_revision=0,confirmed=True)
    from app.services import agent_mission_cloud_v1 as relay
    projected=relay.execute('tools.get',{'mission_id':cloud['id']})
    assert projected['contract']==relay.CONTRACT and projected['tools']['revision']==1
    with db() as conn:conn.execute('UPDATE conversation_context_settings SET cloud_allowed=0 WHERE conversation_id=?',('app:vp3-contract',))
    deny(lambda:relay.execute('tools.get',{'mission_id':cloud['id']}),403)
    with db() as conn:conn.execute('UPDATE conversation_context_settings SET cloud_allowed=1 WHERE conversation_id=?',('app:vp3-contract',))
    app_scopes.save_scope(app_id,{'cloud_allowed':True,'tool_names':['contacts.search']})
    assert 'knowledge.search' not in contracts.capabilities('app:vp3',cloud['id'])
    deny(lambda:contracts.configure('app:vp3',cloud['id'],cp,request_id=str(uuid.uuid4()),expected_revision=1,confirmed=True),403)
    app_scopes.save_scope(app_id,{'cloud_allowed':True})
    with db() as conn:conn.execute("UPDATE app_permissions SET allowed=0 WHERE paired_app_id=? AND permission='knowledge.search'",(app_id,))
    deny(lambda:contracts.configure('app:vp3',cloud['id'],cp,request_id=str(uuid.uuid4()),expected_revision=1,confirmed=True),403)
    with db() as conn:conn.execute("UPDATE app_permissions SET allowed=0 WHERE paired_app_id=? AND permission='agent.chat'",(app_id,))
    deny(lambda:contracts.get('app:vp3',cloud['id']),403)
    agent_tools.save_policy(False,3)
    deny(lambda:contracts.configure('owner',mid,p,request_id=str(uuid.uuid4()),expected_revision=3,confirmed=True),403)
    with db() as conn:
        assert conn.execute('SELECT COUNT(*) FROM tool_runs').fetchone()[0]==0,'Contract preparation dispatched a tool'
    print('A5C1 PASS: strict assignments, provider-neutral contracts, dependency binding, source isolation, revocation, CAS concurrency, durable retries and zero dispatch')
