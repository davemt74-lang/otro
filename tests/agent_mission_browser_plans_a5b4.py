"""Real SQLite acceptance for bounded reads, form preparation and owner fencing."""
import json,os,sys,tempfile,uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix='vp3-browser-plans-') as tmp:
    os.environ['HOMESERVER_DATA_DIR']=tmp
    from app.database import db,initialize_database
    from app.services import agent_mission_runtime as mission,agent_mission_browser as grants
    from app.services import agent_mission_live_browser as live,agent_mission_browser_plans as plans
    from app.services import agent_browser_live_actor as actor,agent_browser_policy as policy
    from app.services import agent_mission_browser_takeover as owner
    from app.agent_mission_browser_api import ManualControl,SearchReview,OwnerWorkspaceOperation,owner_workspace
    from pydantic import ValidationError
    initialize_database()
    with db() as conn:
        pid=int(conn.execute('SELECT id FROM agents WHERE is_primary=1').fetchone()[0])
        conn.execute('INSERT INTO conversations(id,agent_id,source_app_key,title) VALUES(?,?,?,?)',('browser-plan',pid,'owner','Adaptive browser'))
        conn.execute('INSERT OR IGNORE INTO conversation_context_settings(conversation_id) VALUES(?)',('browser-plan',))
    mission._route=lambda *args:('openai','fixture',False)
    policy.pinned_public_ipv4=lambda host:'93.184.215.14'
    running=set();commands=[]
    form={'index':0,'fingerprint':'b'*24,'action':'https://example.com/search','label':'Search','method':'GET'}
    def frame(url):
        return {'url':url,'page_title':'Research','text_snapshot':'Public evidence','image_base64':'',
                'links':[{'url':'https://example.com/failed','title':'Broken report'},
                         {'url':'https://example.com/report','title':'Report'}],
                'controls':[],'search_forms':[form]}
    def opened(tid,*args):running.add(tid);return frame('https://example.com/search')
    def execute(tid,kind,payload=''):
        commands.append((tid,kind,payload))
        if kind=='review_search':return {'id':payload['review_id'],'index':0,'fingerprint':'b'*24,'action':form['action'],'method':'GET','payload_hash':'c'*64,'query':'public evidence','destination':'https://example.com/search?q=public+evidence'}
        if kind=='navigate' and payload.endswith('/failed'):raise mission.MissionError('Navigation failed',502)
        return frame(payload if kind=='navigate' else 'https://example.com/search')
    actor.open_session=opened;actor.execute=execute;actor.active=lambda tid:tid in running
    actor.close_session=lambda tid:running.discard(tid)
    def create(n):
        record=mission.create_mission('owner',conversation_id='browser-plan',parent_agent_id=pid,owner=True,
            objective='Find public reports',client_request_id='plan-'+str(n),
            tasks=[{'role':'research','title':'Research','objective':'Find public reports','depends_on':[]}])
        mid=record['id'];tid=record['tasks'][0]['id']
        grants.authorize('owner',mid,tid,'https://example.com/search');live.start('owner',mid,tid)
        return mid,tid
    decisions=iter([{'kind':'navigate','index':0,'query':'','reason':'Inspect report'},
                    {'kind':'navigate','index':1,'query':'','reason':'Try alternate report'},
                    {'kind':'search','index':0,'query':'public evidence','reason':'Search reports'}])
    mission._infer=lambda *args:(json.dumps(next(decisions)),'openai','fixture')
    mid,tid=create(1);request_id=str(uuid.uuid4())
    result=plans.run('owner',mid,tid,request_id=request_id,confirmed=True,background=False)
    assert result['status']=='waiting_approval',result
    assert [x['outcome'] for x in result['history']]==['failed','completed','awaiting_owner_approval']
    assert [x[1] for x in commands]==['navigate','navigate','prepare_search']
    assert not any(x[1]=='search_get' for x in commands),'Agent submitted a form without approval'
    assert live.get('owner',mid,tid)['session_active'],'Recovery lost the session'
    assert live.get('owner',mid,tid)['visit_count']==3,'Failed reads must consume budget'
    before=len(commands)
    plans.run('owner',mid,tid,request_id=request_id,confirmed=True,background=False)
    assert len(commands)==before,'Plan retry repeated browser commands'
    owner.acquire('owner',mid,tid)
    review=owner.review_search('owner',mid,tid,index=0,fingerprint='b'*24)
    owner.submit_search('owner',mid,tid,proposal_id=review['owner_takeover']['pending_form']['id'])
    assert plans.status('owner',mid,tid)['status']=='completed','Approved submission left a stale approval state'
    assert plans.status('owner',mid,tid)['prepared_form']=={}
    owner.release('owner',mid,tid)
    with db() as conn:
        journal=str(conn.execute('SELECT group_concat(metadata_json) FROM agent_mission_events_v1 WHERE mission_id=?',(mid,)).fetchone()[0])
    assert 'public evidence' not in journal,'Prepared query leaked into journal'
    # A real provider interleaving claims owner control before an agent step.
    mid2,tid2=create(2)
    with db() as conn:token=conn.execute('SELECT session_token FROM agent_mission_live_browser_v2 WHERE task_id=?',(tid2,)).fetchone()[0]
    def takeover_during_inference(*args):
        owner.acquire('owner',mid2,tid2)
        return json.dumps({'kind':'navigate','index':1,'query':'','reason':'Late choice'}),'openai','fixture'
    mission._infer=takeover_during_inference
    before=len(commands)
    result=plans.run('owner',mid2,tid2,request_id=str(uuid.uuid4()),confirmed=True,background=False)
    assert result['status']=='waiting_owner' and len(commands)==before
    lease=owner.status('owner',mid2,tid2)['lease_id'];owner.release('owner',mid2,tid2,lease_id=lease)
    with db() as conn:assert conn.execute('SELECT session_token FROM agent_mission_live_browser_v2 WHERE task_id=?',(tid2,)).fetchone()[0]==token
    # Revocation between model response and the command prevents dispatch.
    def revoke_during_inference(*args):
        grants.revoke('owner',mid2,tid2)
        return json.dumps({'kind':'navigate','index':1,'query':'','reason':'Stale choice'}),'openai','fixture'
    mission._infer=revoke_during_inference
    plans.run('owner',mid2,tid2,request_id=str(uuid.uuid4()),confirmed=True,background=False)
    assert len(commands)==before
    # Recovery records interruption without automatically replaying any step.
    mid3,tid3=create(3);rid=str(uuid.uuid4())
    plans._begin('owner',mid3,tid3,rid);assert plans.recover_interrupted()==1
    assert plans.status('owner',mid3,tid3)['status']=='interrupted'
    plans.run('owner',mid3,tid3,request_id=rid,confirmed=True,background=False)
    assert len(commands)==before
    plans._record('owner',mid3,tid3,rid,'failed',[])
    assert plans.status('owner',mid3,tid3)['status']=='interrupted','Late worker overwrote interruption'
    for url in ('https://example.com/checkout','https://example.com/search?action=delete','https://example.com/publish/search'):
        try:policy.read_navigation(url);raise AssertionError('Consequential read target accepted')
        except mission.MissionError as exc:assert exc.status_code==403
    try:ManualControl(index=0,fingerprint='a'*24,kind='fill',value='draft',confirmed=True);raise AssertionError('API allowed missing retry identity')
    except ValidationError:pass
    overview=owner_workspace(OwnerWorkspaceOperation(action='list'))
    assert overview['ok'] and len(overview['items'])==3
    try:plans.status('app:other',mid3,tid3);raise AssertionError('Cross-source plan read')
    except mission.MissionError as exc:assert exc.status_code==404
    mission.shutdown()
    print('A5B4_PLAN PASS: bounded execution, alternate navigation recovery, prepare-only forms, takeover fencing, revocation, restart and retry prevention')
