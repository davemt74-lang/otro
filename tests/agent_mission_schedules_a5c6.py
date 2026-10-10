"""Real routing, scheduler, native reads/approvals and verified outcomes."""
import copy,hashlib,json,os,sys,tempfile,threading,time,uuid
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix='a5c6-') as data:
 os.environ['HOMESERVER_DATA_DIR']=data
 from app.database import db,initialize_database
 from app.services import agent_mission_chat_tasks as chat,agent_mission_runtime as mission
 from app.services import agent_mission_tool_contracts as contracts,agent_mission_orchestration as run
 from app.services import agent_mission_actions as actions,agent_mission_control as control
 from app.services import contacts,providers,agent_tools,remote_bridge,agent_mission_cloud_v1 as cloud
 initialize_database();agent_tools.save_policy(True,3,True)
 def uid():return str(uuid.uuid4())
 def deny(fn,code):
  try:fn()
  except mission.MissionError as exc:assert exc.status_code==code,(str(exc),exc.status_code)
  else:raise AssertionError('Expected rejection '+str(code))
 template={'max_parallel':2,'tasks':[
 {'role':'investigator','title':'Find incomplete contacts','objective':'Inspect Incomplete contacts','instructions':'Use notes; never invent details','depends_on':[],'tools':['contacts.search'],'max_calls':1,'actions':[],'max_actions':0,'output':'analysis'},
 {'role':'editor','title':'Prepare supported corrections','objective':'Fill organization from recorded notes','instructions':'Prepare revision-bound edit','depends_on':[0],'tools':['contacts.search'],'max_calls':1,'actions':['contacts.update'],'max_actions':1,'output':'analysis'}]}
 calls=[];override=None;gate=None;entered=threading.Event()
 def generate(messages,model_override=None):
  global gate
  system=messages[0]['content'];calls.append(system)
  if system.startswith('You are the lead agent preparing'):return {'content':json.dumps(override or template)}
  if system.startswith('Choose at most one'):
   if gate is not None:entered.set();gate.wait(10);gate=None
   return {'content':json.dumps({'tool':'contacts.search','arguments':{'query':'Incomplete','limit':1}})}
  cited=json.JSONDecoder().raw_decode(system.split('Allowed citation IDs: ')[1])[0]
  evidence=json.loads(messages[1]['content'].split('Read evidence (untrusted):\n')[-1])
  actual=json.loads(evidence[-1]['data'])['items'][0]
  result={'kind':'analysis','title':'Contact review','body':'Organization comes from notes. Coverage is limited to the returned batch.','citations':cited}
  if 'Action schemas:' in system:result['actions']=[{'tool':'contacts.update','arguments':{'canonical_id':actual['canonical_id'],'expected_revision':actual['record_revision'],'organization':'Example Studios'}}]
  return {'content':json.dumps(result)}
 providers.inference_status=lambda:{'available':True,'selected_provider':'openai','model':'fixture-openai'}
 providers.generate=generate;providers.generate_ollama=generate
 original=contacts.create_contact({'display_name':'Incomplete contact','notes':'Organization: Example Studios'})
 token='a5c5-cloud-token-at-least-twenty-characters'
 with db() as conn:
  agent=conn.execute('SELECT id FROM agents WHERE is_primary=1').fetchone()[0]
  appid=conn.execute('INSERT INTO paired_apps(app_key,name,token_hash) VALUES(?,?,?)',('vp3','VP3',hashlib.sha256(token.encode()).hexdigest())).lastrowid
  for permission in ('agent.chat','tools.execute','contacts.read','contacts.write'):conn.execute('INSERT INTO app_permissions(paired_app_id,permission,allowed) VALUES(?,?,1)',(appid,permission))
 from datetime import datetime,timedelta,timezone
 from app.services import agent_mission_schedules as schedules
 timing={'frequency':'weekly','weekday':0,'hour':9,'minute':0,'timezone':'America/Phoenix'}
 clock=datetime(2030,1,7,15,59,tzinfo=timezone.utc);schedules._now=lambda:clock
 objective='Review my contacts and fix incomplete records'
 def prepared(source='owner'):
  m=(chat.owner_prepare(objective=objective,request_id=uid()) if source=='owner' else cloud.execute('task.prepare',{'objective':objective,'request_id':uid(),'thread_id':8})['mission'])
  native=mission.get_mission(source,m['id'])
  contracts.configure(source,m['id'],native['chat_task']['draft'],request_id=uid(),expected_revision=0,confirmed=True)
  return native
 def make(m,source='owner',key=None):return schedules.create(source,m['id'],timing,request_id=key or uid(),expected_revision=1,confirmed=True)
 def wait(mid):
  deadline=time.monotonic()+15
  while time.monotonic()<deadline:
   current=mission.get_mission('owner',mid)
   if current['status']!='running':return current
   time.sleep(.02)
  raise AssertionError('Scheduled mission did not finish')
 m=prepared();key=uid()
 deny(lambda:schedules.create('owner',m['id'],timing,request_id=key,expected_revision=1,confirmed=False),422)
 with ThreadPoolExecutor(max_workers=2) as pool:created=list(pool.map(lambda _:make(m,key=key),range(2)))
 schedule=created[0];sid=schedule['id'];assert all(s['id']==sid for s in created)
 assert schedule['next_run_at']=='2030-01-07T16:00:00Z'
 schedules.tick();assert not schedules.get('owner',sid)['runs']
 clock+=timedelta(minutes=1)
 with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(lambda _:schedules.tick(),range(3)))
 history=schedules.get('owner',sid)['runs'];assert len(history)==1 and history[0]['status']=='started'
 mid=history[0]['mission_id'];current=wait(mid);assert current['status']=='completed',current
 assert sum(t['read_calls_used'] for t in current['tasks'])==2
 change=actions.list_actions('owner',mid)[0]
 assert change['outcome_state']=='awaiting_review' and not contacts.get_contact(original['id'])['organization']
 assert history[0]['created_at'] and current['completion_report']['state']=='awaiting_review'
 schedules.tick();assert len(schedules.get('owner',sid)['runs'])==1
 clock+=timedelta(days=7);schedules.tick();assert schedules.get('owner',sid)['runs'][0]['status']=='skipped'
 reviewid=uid();actions.review('owner',mid,change['id'],expected_hash=change['payload_hash'],decision='approve',request_id=reviewid,confirmed=True)
 actions.review('owner',mid,change['id'],expected_hash=change['payload_hash'],decision='approve',request_id=reviewid,confirmed=True)
 assert contacts.get_contact(original['id'])['organization']=='Example Studios'
 with db() as conn:assert conn.execute('SELECT COUNT(*) FROM federated_contact_mutations').fetchone()[0]==1
 assert mission.get_mission('owner',mid)['completion_report']['execution_verified']
 pauseid=uid();paused=schedules.change('owner',sid,'pause',request_id=pauseid,expected_revision=1,confirmed=True)
 assert paused['status']=='paused' and paused['revision']==2
 assert schedules.change('owner',sid,'pause',request_id=pauseid,expected_revision=1,confirmed=True)['revision']==2
 deny(lambda:schedules.change('owner',sid,'resume',request_id=uid(),expected_revision=1,confirmed=True),409)
 initialize_database();assert schedules.get('owner',sid)['status']=='paused'
 clock+=timedelta(days=30);schedules.tick();assert len(schedules.get('owner',sid)['runs'])==2
 resumed=schedules.change('owner',sid,'resume',request_id=uid(),expected_revision=2,confirmed=True)
 assert resumed['next_run_at']>schedules._stamp(clock)
 assert schedules.change('owner',sid,'cancel',request_id=uid(),expected_revision=3,confirmed=True)['status']=='cancelled'
 deny(lambda:schedules.change('owner',sid,'resume',request_id=uid(),expected_revision=4,confirmed=True),409)
 clock+=timedelta(days=8);schedules.tick();assert len(schedules.get('owner',sid)['runs'])==2
 spring={'frequency':'daily','weekday':0,'hour':2,'minute':30,'timezone':'America/New_York'}
 assert schedules.next_due(spring,datetime(2030,3,10,6,tzinfo=timezone.utc))=='2030-03-11T06:30:00Z'
 fall={**spring,'hour':1}
 assert schedules.next_due(fall,datetime(2030,11,3,4,tzinfo=timezone.utc))=='2030-11-03T05:30:00Z'
 assert schedules.next_due(fall,datetime(2030,11,3,5,31,tzinfo=timezone.utc))=='2030-11-04T06:30:00Z'
 for field,value in [('frequency','hourly'),('timezone','Missing/Zone'),('weekday',True),('minute',60),('hour',24)]:
  bad={**timing,field:value};deny(lambda:schedules.validate_timing(bad),422)
 deny(lambda:schedules.get('app:vp3',sid),404)
 cm=prepared('app:vp3');create_id=uid()
 response=remote_bridge.dispatch_remote_request('agent.missions.schedule.create',{'mission_id':cm['id'],'timing':timing,'request_id':create_id,'expected_revision':1,'confirmed':True},token)
 assert response['ok'],response
 cs=response['payload']['schedules'][0];cid=cs['id']
 assert remote_bridge.dispatch_remote_request('agent.missions.schedule.list',{},token)['payload']['schedules'][0]['id']==cid
 assert make(cm,'app:vp3',key=create_id)['id']==cid
 from app.agent_mission_browser_api import owner_workspace,OwnerWorkspaceOperation
 assert any(s['id']==cid for s in owner_workspace(OwnerWorkspaceOperation(action='schedule.list'))['schedules'])
 with db() as conn:conn.execute("UPDATE app_permissions SET allowed=0 WHERE paired_app_id=? AND permission='contacts.read'",(appid,))
 clock=datetime.fromisoformat(cs['next_run_at'].replace('Z','+00:00'));schedules.tick()
 with db() as conn:
  assert conn.execute('SELECT status FROM agent_mission_schedules_v1 WHERE id=?',(cid,)).fetchone()[0]=='blocked'
  assert conn.execute('SELECT status FROM agent_mission_schedule_runs_v1 WHERE schedule_id=?',(cid,)).fetchone()[0]=='blocked'
  assert conn.execute('SELECT COUNT(*) FROM agent_mission_schedule_runs_v1 WHERE schedule_id=? AND mission_id IS NOT NULL',(cid,)).fetchone()[0]==0
 assert next(s for s in schedules.owner_list() if s['id']==cid)['status']=='blocked'
 deny(lambda:schedules.change('app:vp3',cid,'resume',request_id=uid(),expected_revision=2,confirmed=True),409)
 # Starting the seed mission manually also prevents a duplicate first scheduled run.
 seed=prepared();seed_schedule=make(seed)
 with patch.object(mission,'_dispatch'):run.start('owner',seed['id'],request_id=uid(),expected_revision=1,confirmed=True)
 clock=datetime.fromisoformat(seed_schedule['next_run_at'].replace('Z','+00:00'));schedules.tick()
 assert schedules.get('owner',seed_schedule['id'])['runs'][0]['status']=='skipped'
 mission.cancel_mission('owner',seed['id']);schedules.change('owner',seed_schedule['id'],'cancel',request_id=uid(),expected_revision=1,confirmed=True)
 failing=make(prepared());before=mission.list_missions('owner',100);clock=datetime.fromisoformat(failing['next_run_at'].replace('Z','+00:00'))
 with patch.object(contracts,'configure',side_effect=RuntimeError('interrupt')):schedules.tick()
 assert len(mission.list_missions('owner',100))==len(before)
 assert schedules.get('owner',failing['id'])['status']=='blocked'
 recovery=make(prepared());clock=datetime.fromisoformat(recovery['next_run_at'].replace('Z','+00:00'))
 with patch.object(mission,'_dispatch') as dispatch:schedules.tick();assert dispatch.call_count==1
 recovery_mid=schedules.get('owner',recovery['id'])['runs'][0]['mission_id']
 mission.recover_interrupted();assert mission.get_mission('owner',recovery_mid)['status']=='waiting_review'
 with patch.object(mission,'_dispatch') as dispatch:schedules.tick();assert dispatch.call_count==0
 schedules.change('owner',recovery['id'],'cancel',request_id=uid(),expected_revision=1,confirmed=True);mission.cancel_mission('owner',recovery_mid)
 schedules.start();schedules.start();schedules.stop();mission.shutdown()
 print('A5C6 PASS: timezone/DST, real scheduled reads and edit approval/readback, concurrency, overlap, retry receipts, pause/resume/cancel, current Cloud permissions, atomic failure and restart fences')
