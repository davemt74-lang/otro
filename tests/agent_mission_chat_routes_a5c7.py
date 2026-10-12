"""Real routing, scheduler, native reads/approvals and verified outcomes."""
import copy,hashlib,json,os,sys,tempfile,threading,time,uuid
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix='a5c7-') as data:
 os.environ['HOMESERVER_DATA_DIR']=data
 from app.database import db,initialize_database
 from app.services import agent_mission_chat_routes as routes,agent_mission_runtime as mission
 from app.services import agent_mission_tool_contracts as contracts,agent_mission_orchestration as run
 from app.services import agent_mission_actions as actions,agent_mission_control as control
 from app.services import contacts,providers,agent_tools,remote_bridge,agent_mission_cloud_v1 as cloud
 from types import SimpleNamespace
 def automatic_prepare(**kwargs):
  result=routes.owner_route(**kwargs);assert result['route']=='specialists';return result['mission']
 chat=SimpleNamespace(owner_prepare=automatic_prepare)
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
  if system.startswith('You are the lead agent routing'):return {'content':json.dumps({'route':'specialists','reason':'Recorded contacts require investigation and reviewed corrections','plan':override or template})}
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
 objective='Review my contacts and fix incomplete records';request=uid()
 record=chat.owner_prepare(objective=objective,request_id=request,parent_agent_id=agent)
 assert record['status']=='planned' and not record['tools_enabled'] and not record['tools_configured']
 assert record['chat_task']['provider_key']=='openai' and record['chat_task']['model']=='fixture-openai'
 assert len(record['tasks'])==2 and record['tasks'][1]['depends_on']==[record['tasks'][0]['id']]
 draft=record['chat_task']['draft'];assert draft['assignments'][1]['actions']==['contacts.update']
 assert not contacts.get_contact(original['id'])['organization']
 deny(lambda:mission.start_mission('owner',record['id']),409)
 deny(lambda:run.start('owner',record['id'],request_id=uid(),expected_revision=0,confirmed=True),422)
 deny(lambda:contracts.configure('owner',record['id'],draft,request_id=uid(),expected_revision=0,confirmed=False),422)
 before=len(calls)
 assert chat.owner_prepare(objective=objective,request_id=request,parent_agent_id=agent)['id']==record['id'] and len(calls)==before
 with ThreadPoolExecutor(max_workers=2) as pool:repeated=list(pool.map(lambda _:chat.owner_prepare(objective=objective,request_id=request,parent_agent_id=agent),range(2)))
 assert all(r['id']==record['id'] for r in repeated)
 deny(lambda:chat.owner_prepare(objective='Different intent',request_id=request,parent_agent_id=agent),409)
 initialize_database();assert mission.get_mission('owner',record['id'])['chat_task']['draft']==draft
 contracts.configure('owner',record['id'],draft,request_id=uid(),expected_revision=0,confirmed=True)
 def wait(mid,source='owner'):
  deadline=time.monotonic()+15
  while time.monotonic()<deadline:
   current=mission.get_mission(source,mid)
   if current['status']!='running':return current
   time.sleep(.02)
  raise AssertionError('Mission did not finish')
 run.start('owner',record['id'],request_id=uid(),expected_revision=1,confirmed=True)
 current=wait(record['id']);assert current['status']=='completed',current
 assert all(t['provider_key']=='openai' and t['model']=='fixture-openai' for t in current['tasks'])
 assert current['tasks'][1]['started_at']>=current['tasks'][0]['completed_at']
 change=actions.list_actions('owner',record['id'])[0]
 assert change['outcome_state']=='awaiting_review' and current['completion_report']['state']=='awaiting_review'
 assert not contacts.get_contact(original['id'])['organization']
 approved_id=uid();saved=actions.review('owner',record['id'],change['id'],expected_hash=change['payload_hash'],decision='approve',request_id=approved_id,confirmed=True)
 assert saved[0]['outcome_state']=='verified' and saved[0]['verified_at']
 assert contacts.get_contact(original['id'])['organization']=='Example Studios'
 actions.review('owner',record['id'],change['id'],expected_hash=change['payload_hash'],decision='approve',request_id=approved_id,confirmed=True)
 with db() as conn:assert conn.execute('SELECT count(*) FROM federated_contact_mutations').fetchone()[0]==1
 assert mission.get_mission('owner',record['id'])['completion_report']['execution_verified']
 for mutation,code in [(lambda p:p['tasks'][1].update(actions=['contacts.delete']),422),(lambda p:p['tasks'][1].update(tools=[],max_calls=0),502),(lambda p:p['tasks'][0].update(tools=['browser.read']),502),(lambda p:p.update(max_parallel=5),422),(lambda p:p['tasks'][1].update(depends_on=[1]),422),(lambda p:p['tasks'][0].update(secret='unsupported'),502)]:
  override=copy.deepcopy(template);mutation(override);deny(lambda:chat.owner_prepare(objective=objective,request_id=uid()),code)
 override=None
 deny(lambda:chat.owner_prepare(objective=objective,request_id='not-a-uuid'),422)
 with patch.object(providers,'generate',return_value={'content':'malformed'}):deny(lambda:chat.owner_prepare(objective=objective,request_id=uid()),502)
 agent_tools.save_policy(True,3,False);deny(lambda:chat.owner_prepare(objective=objective,request_id=uid()),403);agent_tools.save_policy(True,3,True)
 cancelled=chat.owner_prepare(objective=objective,request_id=uid());mission.cancel_mission('owner',cancelled['id']);assert not actions.list_actions('owner',cancelled['id'])
 interrupted=chat.owner_prepare(objective=objective,request_id=uid())
 contracts.configure('owner',interrupted['id'],interrupted['chat_task']['draft'],request_id=uid(),expected_revision=0,confirmed=True)
 gate=threading.Event();entered.clear();run.start('owner',interrupted['id'],request_id=uid(),expected_revision=1,confirmed=True)
 assert entered.wait(10);assert mission.recover_interrupted()>=1;gate.set();time.sleep(.1)
 assert mission.get_mission('owner',interrupted['id'])['status']=='waiting_review' and not actions.list_actions('owner',interrupted['id'])
 deny(lambda:control.resume('owner',interrupted['id'],allow_reexecution=False),409)
 control.resume('owner',interrupted['id'],allow_reexecution=True);assert wait(interrupted['id'])['status']=='completed';assert len(actions.list_actions('owner',interrupted['id']))==1
 # Concurrent first preparation persists one whole plan, not mixed worker drafts.
 fresh_id=uid()
 with ThreadPoolExecutor(max_workers=2) as pool:fresh=list(pool.map(lambda _:chat.owner_prepare(objective=objective,request_id=fresh_id),range(2)))
 assert fresh[0]['id']==fresh[1]['id'] and fresh[0]['chat_task']['draft']==fresh[1]['chat_task']['draft']
 with db() as conn:assert conn.execute('SELECT count(*) FROM agent_mission_tasks_v1 WHERE mission_id=?',(fresh[0]['id'],)).fetchone()[0]==2
 # Actual local-only routing never selects the external provider.
 providers.save_ollama('http://127.0.0.1:11434','fixture-local',True)
 with db() as conn:
  conn.execute("INSERT INTO conversations(id,agent_id,source_app_key,title) VALUES('private-task',?,'owner','Private task')",(agent,))
  conn.execute("INSERT INTO conversation_context_settings(conversation_id,cloud_allowed) VALUES('private-task',0)")
 local=chat.owner_prepare(objective=objective,request_id=uid(),conversation_id='private-task')
 assert local['chat_task']['provider_key']=='ollama' and local['chat_task']['model']=='fixture-local'
 # Provider/privacy setup must not commit its caller's transaction.
 with db() as conn:
  conn.execute('BEGIN IMMEDIATE');conn.execute("UPDATE contacts SET organization='Uncommitted' WHERE id=?",(original['id'],))
  assert mission._route('owner','private-task')==('ollama','fixture-local',True)
  conn.rollback()
 assert contacts.get_contact(original['id'])['organization']=='Example Studios'
 cloud_request=uid()
 def remote(action,body):return remote_bridge.dispatch_remote_request('agent.missions.'+action,body,token)
 response=remote('task.route',{'objective':objective,'request_id':cloud_request,'thread_id':81});assert response['ok'],response
 paired=response['payload']['mission'];assert paired['chat_task']['draft']
 assert remote('task.route',{'objective':objective,'request_id':cloud_request,'thread_id':81})['payload']['mission']['id']==paired['id']
 assert remote('start',{'mission_id':paired['id']})['status']==409
 with patch.object(cloud,'_cloud_export_allowed',return_value=False):assert cloud._projection(mission.get_mission('app:vp3',paired['id']),detailed=True)['chat_task'] is None
 with db() as conn:conn.execute("UPDATE app_permissions SET allowed=0 WHERE paired_app_id=? AND permission='contacts.write'",(appid,))
 deny(lambda:contracts.configure('app:vp3',paired['id'],paired['chat_task']['draft'],request_id=uid(),expected_revision=0,confirmed=True),403)
 with db() as conn:conn.execute("UPDATE app_permissions SET allowed=0 WHERE paired_app_id=? AND permission='agent.chat'",(appid,))
 assert mission.get_mission('app:vp3',paired['id'])['chat_task'] is None
 # Direct chat persists no mission and reuses selected conversation history.
 with db() as conn:
  conn.execute("INSERT INTO conversations(id,agent_id,source_app_key,title) VALUES('routing-history',?,'owner','History')",(agent,))
  conn.execute("INSERT INTO conversation_messages(conversation_id,role,content) VALUES('routing-history','user','selected context')")
 direct_id=uid();observed=[]
 def direct_infer(source,cid,messages):
  observed.append(messages)
  return json.dumps({'route':'chat','reason':'Can answer directly','plan':None}),'openai','fixture-openai'
 with patch.object(mission,'_infer',side_effect=direct_infer):
  direct=routes.owner_route(objective='Hello',request_id=direct_id,conversation_id='routing-history')
  assert direct['route']=='chat' and 'mission' not in direct
  assert routes.owner_route(objective='Hello',request_id=direct_id,conversation_id='routing-history')==direct
 assert len(observed)==1 and observed[0][1]['content']=='selected context'
 with db() as conn:assert not conn.execute('SELECT 1 FROM agent_missions_v1 WHERE client_request_id=?',(direct_id,)).fetchone()
 # Source-scoped request identities cannot change intent or become manual work.
 deny(lambda:routes.owner_route(objective='Different',request_id=direct_id,conversation_id='routing-history'),409)
 from app.services import agent_mission_chat_tasks as manual
 deny(lambda:manual.owner_prepare(objective='Hello',request_id=direct_id,conversation_id='routing-history'),409)
 deny(lambda:mission.create_mission('owner',conversation_id='routing-history',objective='Hello',client_request_id=direct_id,owner=True,tasks=template['tasks']),409)
 # A persisted decision survives a crash before mission preparation without another inference.
 recover_id=uid()
 with patch.object(routes.tasks,'prepare',side_effect=RuntimeError('process interruption')):
  try:routes.owner_route(objective=objective,request_id=recover_id)
  except RuntimeError:pass
  else:raise AssertionError('Expected interruption')
 with patch.object(mission,'_infer',side_effect=AssertionError('Recovery must use saved plan')):
  recovered=routes.owner_route(objective=objective,request_id=recover_id)
 initialize_database()
 with patch.object(mission,'_infer',side_effect=AssertionError('Replay must not infer')):
  assert routes.owner_route(objective=objective,request_id=recover_id)['mission']['id']==recovered['mission']['id']
 with db() as conn:assert conn.execute("SELECT count(*) FROM agent_mission_events_v1 WHERE mission_id=? AND kind='chat.auto_delegation'",(recovered['mission']['id'],)).fetchone()[0]==1
 # Two conflicting simultaneous model decisions use the first committed decision.
 race_id=uid();barrier=threading.Barrier(2);counter=iter(['chat','specialists']);lock=threading.Lock()
 def conflicting(source,cid,messages):
  with lock:choice=next(counter)
  barrier.wait(10)
  return json.dumps({'route':choice,'reason':choice,'plan':template if choice=='specialists' else None}),'openai','fixture-openai'
 with patch.object(mission,'_infer',side_effect=conflicting):
  with ThreadPoolExecutor(max_workers=2) as pool:choices=list(pool.map(lambda _:routes.owner_route(objective=objective,request_id=race_id),range(2)))
 assert choices[0]['route']==choices[1]['route'] and choices[0]['reason']==choices[1]['reason']
 with db() as conn:assert conn.execute('SELECT count(*) FROM agent_missions_v1 WHERE client_request_id=?',(race_id,)).fetchone()[0]<=1
 # Invalid or contradictory routing cannot create a mission or silently become chat.
 for decision in [{'route':'invalid','reason':'x','plan':None},{'route':'chat','reason':'x','plan':template},{'route':'chat','reason':'','plan':None},{'route':'chat','reason':'x','plan':None,'extra':True}]:
  with patch.object(mission,'_infer',return_value=(json.dumps(decision),'openai','fixture-openai')):
   deny(lambda:routes.owner_route(objective='Hello',request_id=uid()),502)
 # Revocation during inference is checked before committing a new decision.
 revoke_id=uid()
 with db() as conn:conn.execute("UPDATE app_permissions SET allowed=1 WHERE paired_app_id=?",(appid,))
 revoke_cid=cloud._ensure_conversation(88,revoke_id,'Hello')
 def revoked(source,cid,messages):
  with db() as conn:conn.execute("UPDATE app_permissions SET allowed=0 WHERE paired_app_id=? AND permission='agent.chat'",(appid,))
  return json.dumps({'route':'chat','reason':'x','plan':None}),'openai','fixture-openai'
 with patch.object(mission,'_infer',side_effect=revoked):deny(lambda:routes.route('app:vp3',objective='Hello',request_id=revoke_id,conversation_id=revoke_cid),403)
 with db() as conn:assert not conn.execute('SELECT 1 FROM agent_mission_chat_routes_v1 WHERE request_id=?',(revoke_id,)).fetchone()
 with db() as conn:conn.execute("UPDATE app_permissions SET allowed=1 WHERE paired_app_id=? AND permission='agent.chat'",(appid,))
 with patch.object(cloud,'_cloud_export_allowed',return_value=False):deny(lambda:cloud.execute('task.route',{'objective':'Hello','request_id':uid(),'thread_id':89}),403)
 if mission._pool:mission._pool.shutdown(wait=True)
 print('A5C7 PASS: automatic routing, context, durable recovery, conflicting decisions, permission fences;  selected provider, dependency reads, approved contact edits, verified completion, durable retries, planner boundaries, restart leases, cancellation, scoped relay and privacy')
