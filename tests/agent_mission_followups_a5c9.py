"""Real SQLite, worker leases, record saves, reviewed commands and paired relay."""
import copy, hashlib, json, os, sys, tempfile, threading, time, uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

with tempfile.TemporaryDirectory(prefix='a5c9-') as data:
 os.environ['HOMESERVER_DATA_DIR']=data
 from app.database import db,initialize_database
 from app.services import agent_mission_runtime as mission,agent_mission_chat_routes as routes
 from app.services import agent_mission_followups as follow,agent_mission_tool_contracts as contracts
 from app.services import agent_mission_orchestration as run,agent_mission_control as control
 from app.services import agent_mission_actions as actions,contacts,providers,agent_tools,remote_bridge
 from app.services import agent_mission_cloud_v1 as cloud
 # Authentic schema-77 upgrade retains an existing mission and automatic routing receipt.
 import app.database as database
 original_migrations=database.migration_files
 with patch.object(database,'migration_files',return_value=[x for x in original_migrations() if x[0]<=77]),patch.object(database,'FEATURE_SCHEMA_PATHS',tuple(p for p in database.FEATURE_SCHEMA_PATHS if p.name not in ('agent_mission_followups.sql','agent_mission_chat_routes.sql'))):
  initialize_database()
 legacy_id=str(uuid.uuid4());legacy_request=str(uuid.uuid4())
 with db() as conn:
  legacy_agent=conn.execute('SELECT id FROM agents WHERE is_primary=1').fetchone()[0]
  conn.execute("INSERT INTO conversations(id,agent_id,source_app_key,title) VALUES('legacy-command',?,'app:legacy','Retained')",(legacy_agent,))
  conn.execute("INSERT INTO agent_missions_v1(id,source_app_key,conversation_id,parent_agent_id,objective,client_request_id) VALUES(?,'app:legacy','legacy-command',?,'Preserve work',?)",(legacy_id,legacy_agent,legacy_request))
  conn.execute("INSERT INTO agent_mission_chat_routes_v1(source_app_key,request_id,conversation_id,parent_agent_id,objective,route,reason,plan_json,provider_key,model,mission_id) VALUES('app:legacy',?,'legacy-command',?,'Preserve work','specialists','Existing plan','{}','openai','old',?)",(legacy_request,legacy_agent,legacy_id))
 initialize_database()
 with db() as conn:
  assert conn.execute('SELECT mission_id,reason FROM agent_mission_chat_routes_v1 WHERE request_id=?',(legacy_request,)).fetchone()['mission_id']==legacy_id
  assert conn.execute('SELECT objective FROM agent_missions_v1 WHERE id=?',(legacy_id,)).fetchone()[0]=='Preserve work'
  assert conn.execute('SELECT max(version) FROM schema_migrations').fetchone()[0]>=80
 agent_tools.save_policy(True,3,True)
 def uid():return str(uuid.uuid4())
 def reject(fn,code):
  try:fn()
  except mission.MissionError as e:assert e.status_code==code,(str(e),e.status_code)
  else:raise AssertionError('Expected rejection '+str(code))
 with db() as conn:
  agent=conn.execute('SELECT id FROM agents WHERE is_primary=1').fetchone()[0]
  for cid in ('chat-one','chat-two'):
   conn.execute('INSERT INTO conversations(id,agent_id,source_app_key,title) VALUES(?,?,?,?)',(cid,agent,'owner',cid))
 template={'max_parallel':1,'tasks':[
  {'role':'editor','title':'Contact cleanup','objective':'Fill recorded organization','instructions':'Only use existing notes','depends_on':[],'tools':['contacts.search'],'max_calls':1,'actions':['contacts.update'],'max_actions':1,'output':'analysis'},
  {'role':'investigator','title':'Remaining contact review','objective':'Gate remaining contacts','instructions':'Inspect only','depends_on':[0],'tools':['contacts.search'],'max_calls':1,'actions':[],'max_actions':0,'output':'analysis'}]}
 scope_plan={'max_parallel':1,'tasks':[dict(template['tasks'][1],depends_on=[],objective='Review contacts missing email')]}
 decision=None;gate=None;entered=threading.Event();observed=[]
 def generate(messages,model_override=None):
  global gate
  system=messages[0]['content'];observed.append(messages)
  if system.startswith('You are the lead agent routing'):
   return {'content':json.dumps(decision or {'route':'specialists','reason':'Investigate actual records','plan':template})}
  if system.startswith('Prepare only remaining work'):return {'content':json.dumps(scope_plan)}
  if system.startswith('Choose at most one'):
   if gate is not None and 'Gate remaining' in messages[-1]['content']:entered.set();gate.wait(20)
   return {'content':json.dumps({'tool':'contacts.search','arguments':{'query':'Incomplete','limit':1}})}
  cited=json.JSONDecoder().raw_decode(system.split('Allowed citation IDs: ')[1])[0]
  evidence=json.loads(messages[-1]['content'].split('Read evidence (untrusted):\n')[-1]);record=json.loads(evidence[-1]['data'])['items'][0]
  value={'kind':'analysis','title':'Contact findings','body':'Organization is Example Studios from existing notes; remaining contacts need email review.','citations':cited}
  if 'Action schemas:' in system:value['actions']=[{'tool':'contacts.update','arguments':{'canonical_id':record['canonical_id'],'expected_revision':record['record_revision'],'organization':'Example Studios'}}]
  return {'content':json.dumps(value)}
 providers.inference_status=lambda:{'available':True,'selected_provider':'openai','model':'fixture'}
 providers.generate=generate;providers.generate_ollama=generate
 contact=contacts.create_contact({'display_name':'Incomplete contact','notes':'Organization: Example Studios'})
 def prepare(objective='Review contacts',cid='chat-one'):
  return routes.owner_route(objective=objective,request_id=uid(),conversation_id=cid)['mission']
 def command(op,mid=None,target='',objective='',cid='chat-one',request=None):
  global decision
  decision={'route':'followup','reason':'User instructed existing work','plan':{'operation':op,'target':target,'objective':objective}}
  rid=request or uid()
  value=routes.owner_route(objective=op+' '+(objective or target or 'it'),request_id=rid,conversation_id=cid)
  assert value['route']=='followup';decision=None
  if mid and value['followup']['status']=='choosing':value['followup']=follow.select('owner',rid,mid)
  return value['followup']
 def apply(value,**kwargs):return follow.decide('owner',value['request_id'],expected_hash=value['expected_hash'],decision='apply',confirmed=True,**kwargs)
 def wait(mid):
  limit=time.monotonic()+20
  while time.monotonic()<limit:
   value=mission.get_mission('owner',mid)
   if value['status']!='running':return value
   time.sleep(.02)
  raise AssertionError('Worker did not finish')
 # Real save happens once; next worker is in a provider call when paused.
 current=prepare();contracts.configure('owner',current['id'],current['chat_task']['draft'],request_id=uid(),expected_revision=0,confirmed=True)
 gate=threading.Event();run.start('owner',current['id'],request_id=uid(),expected_revision=1,confirmed=True)
 assert entered.wait(10)
 change=actions.list_actions('owner',current['id'])[0]
 actions.review('owner',current['id'],change['id'],expected_hash=change['payload_hash'],decision='approve',request_id=uid(),confirmed=True)
 assert contacts.get_contact(contact['id'])['organization']=='Example Studios'
 paused=command('pause',target='contacts');assert paused['status']=='proposed'
 assert mission.get_mission('owner',current['id'])['status']=='running'
 reject(lambda:follow.decide('owner',paused['request_id'],expected_hash='0'*64,decision='apply',confirmed=True),409)
 reject(lambda:follow.decide('owner',paused['request_id'],expected_hash=paused['expected_hash'],decision='apply',confirmed=False),422)
 # Command mutation + receipt + notices commit or roll back together.
 original_event=mission._event
 def fail_after_mutation(conn,mid,kind,*args,**kwargs):
  if kind=='chat.command_applied':raise RuntimeError('Injected commit interruption')
  return original_event(conn,mid,kind,*args,**kwargs)
 with patch.object(mission,'_event',side_effect=fail_after_mutation):
  try:apply(paused)
  except RuntimeError:pass
  else:raise AssertionError('Expected transaction rollback')
 assert mission.get_mission('owner',current['id'])['status']=='running'
 assert follow._row('owner',paused['request_id'])['state']=='proposed'
 receipt=apply(paused);assert receipt['status']=='applied'
 gate.set();gate=None;time.sleep(.15)
 stopped=mission.get_mission('owner',current['id']);assert stopped['status']=='waiting_review'
 assert [t['status'] for t in stopped['tasks']]==['completed','interrupted']
 completed_before=copy.deepcopy(stopped['tasks'][0])
 # Lost responses and simultaneous retries cannot apply an operation twice.
 with ThreadPoolExecutor(max_workers=2) as pool:receipts=list(pool.map(lambda _:apply(paused),range(2)))
 assert all(r['status']=='applied' for r in receipts)
 initialize_database();assert apply(paused)['status']=='applied'
 with db() as conn:assert conn.execute("SELECT count(*) FROM agent_mission_events_v1 WHERE mission_id=? AND kind='mission.paused'",(current['id'],)).fetchone()[0]==1
 resumed=command('resume',target='contacts');reject(lambda:apply(resumed),409)
 apply(resumed,allow_reexecution=True);finished=wait(current['id']);assert finished['status']=='completed'
 assert finished['tasks'][0]['result']==completed_before['result'] and finished['tasks'][0]['attempt']==1
 assert finished['tasks'][1]['attempt']==2
 assert len(actions.list_actions('owner',current['id']))==1
 with db() as conn:assert conn.execute('SELECT count(*) FROM federated_contact_mutations').fetchone()[0]==1
 # Scope successor preserves history and a completed save, uses findings under current authority.
 scoped=command('scope',mid=current['id'],target='contacts',objective='Only review contacts missing email')
 assert scoped['status']=='proposed' and not mission.get_mission('owner',current['id'])['continuity']['successor_id']
 applied=apply(scoped);successor=applied['mission'];assert successor['status']=='planned' and not successor['tools_configured']
 assert successor['continuity']['predecessor_id']==current['id']
 assert mission.get_mission('owner',current['id'])['continuity']['successor_id']==successor['id']
 assert apply(scoped)['result_mission_id']==successor['id']
 reject(lambda:control.retry('owner',current['id'],finished['tasks'][1]['id']),409)
 from app.services import agent_mission_cognition as cognition
 reject(lambda:cognition.propose('owner',current['id'],uid()),409)
 reject(lambda:run.start('owner',successor['id'],request_id=uid(),expected_revision=0,confirmed=True),422)
 contracts.configure('owner',successor['id'],successor['chat_task']['draft'],request_id=uid(),expected_revision=0,confirmed=True)
 run.start('owner',successor['id'],request_id=uid(),expected_revision=1,confirmed=True);assert wait(successor['id'])['status']=='completed'
 with db() as conn:
  assert conn.execute("SELECT COUNT(*) FROM agent_mission_notices_v1 WHERE mission_id=? AND kind='chat.scope_prepared' AND resolved_at IS NULL",(successor['id'],)).fetchone()[0]==0
 assert any('Completed prior findings' in m[-1]['content'] for m in observed)
 reject(lambda:follow.reject_replayed(successor['id'],'contacts.update',{'canonical_id':change['arguments']['canonical_id'],'expected_revision':999,'organization':'Example Studios'}),409)
 assert contacts.get_contact(contact['id'])['organization']=='Example Studios'
 # Ambiguous work requires explicit selection; conversation and lead isolation are enforced.
 a=prepare('Clean contacts');b=prepare('Clean contacts')
 ambiguous=command('cancel',target='contacts');assert ambiguous['status']=='choosing' and len(ambiguous['choices'])>=2
 assert mission.get_mission('owner',a['id'])['status']=='planned'
 other=prepare('Clean contacts','chat-two');reject(lambda:follow.select('owner',ambiguous['request_id'],other['id']),404)
 selected=follow.select('owner',ambiguous['request_id'],a['id']);reject(lambda:follow.select('owner',ambiguous['request_id'],b['id']),409)
 apply(selected);assert mission.get_mission('owner',a['id'])['status']=='cancelled'
 assert mission.get_mission('owner',b['id'])['status']=='planned'
 reject(lambda:follow.decide('owner',selected['request_id'],expected_hash=selected['expected_hash'],decision='dismiss',confirmed=True),409)
 # An advanced mission or expired review cannot run a stale command.
 stale=command('cancel',mid=b['id'],target='contacts');mission.cancel_mission('owner',b['id']);reject(lambda:apply(stale),409)
 expiry=prepare('Review calendars');exp=command('cancel',mid=expiry['id'],target='calendars')
 with db() as conn:conn.execute("UPDATE agent_mission_followups_v1 SET expires_at='2000-01-01' WHERE request_id=?",(exp['request_id'],))
 reject(lambda:apply(exp),409)
 # Superseding completed research retires its still-pending exact edit approvals.
 pending=prepare('Pending contacts');contracts.configure('owner',pending['id'],pending['chat_task']['draft'],request_id=uid(),expected_revision=0,confirmed=True)
 run.start('owner',pending['id'],request_id=uid(),expected_revision=1,confirmed=True);assert wait(pending['id'])['status']=='completed'
 outstanding=actions.list_actions('owner',pending['id'])[0];assert outstanding['can_approve']
 supersede=command('scope',mid=pending['id'],target='pending contacts',objective='Only review missing email')
 apply(supersede)
 assert not actions.list_actions('owner',pending['id'])[0]['can_approve']
 with db() as conn:
  assert conn.execute('SELECT status FROM action_requests WHERE id=?',(outstanding['approval_id'],)).fetchone()[0]=='denied'
  assert not conn.execute("SELECT 1 FROM agent_mission_notices_v1 WHERE mission_id=? AND kind='worker.action_proposed' AND resolved_at IS NULL",(pending['id'],)).fetchone()
 # Paired transport resolves only the current VP3 source and conversation.
 token='a5c9-token-at-least-twenty-characters'
 with db() as conn:
  appid=conn.execute('INSERT INTO paired_apps(app_key,name,token_hash) VALUES(?,?,?)',('vp3','VP3',hashlib.sha256(token.encode()).hexdigest())).lastrowid
  for permission in ('agent.chat','tools.execute','contacts.read','contacts.write'):conn.execute('INSERT INTO app_permissions(paired_app_id,permission,allowed) VALUES(?,?,1)',(appid,permission))
 remote=lambda action,body:remote_bridge.dispatch_remote_request('agent.missions.'+action,body,token)
 paired=remote('task.route',{'objective':'Review contacts','request_id':uid(),'thread_id':81})['payload']['mission']
 other_paired=remote('task.route',{'objective':'Review other contacts','request_id':uid(),'thread_id':81})['payload']['mission']
 decision={'route':'followup','reason':'Cancel existing work','plan':{'operation':'cancel','target':'contacts','objective':''}}
 rid=uid();response=remote('task.route',{'objective':'Cancel contacts','request_id':rid,'thread_id':81});assert response['ok'],response
 review=response['payload']['followup'];assert review['status']=='choosing' and len(review['choices'])==2;decision=None
 review=remote('task.followup.select',{'request_id':rid,'mission_id':paired['id']})['payload']['followup']
 assert review['mission_id']==paired['id'] and review['status']=='proposed'
 assert remote('task.followup.get',{'request_id':rid})['payload']['followup']['status']=='proposed'
 reject(lambda:follow.select('owner',rid,paired['id']),404)
 with db() as conn:conn.execute("UPDATE conversation_context_settings SET cloud_allowed=0 WHERE conversation_id=?",(cloud.cloud_conversation(81,rid),))
 assert remote('task.followup.get',{'request_id':rid})['status']==403
 with db() as conn:conn.execute("UPDATE conversation_context_settings SET cloud_allowed=1 WHERE conversation_id=?",(cloud.cloud_conversation(81,rid),))
 response=remote('task.followup.decide',{'request_id':rid,'expected_hash':review['expected_hash'],'decision':'apply','confirmed':True});assert response['ok'],response
 assert response['payload']['followup']['mission']['status']=='cancelled'
 with db() as conn:conn.execute("UPDATE app_permissions SET allowed=0 WHERE paired_app_id=? AND permission='agent.chat'",(appid,))
 rejected=remote('task.followup.get',{'request_id':rid})
 assert rejected['status']==403 and rejected['ok'] is False,rejected
 # Questions/hypotheticals remain chat and malformed command decisions create no review.
 for invalid in [dict(operation='delete',target='',objective=''),dict(operation='pause',target='',objective='scope'),dict(operation='resume',target=[],objective='')]:
  with patch.object(mission,'_infer',return_value=(json.dumps({'route':'followup','reason':'x','plan':invalid}),'openai','fixture')):
   reject(lambda:routes.owner_route(objective='Bad command',request_id=uid(),conversation_id='chat-two'),502)
 initialize_database()
 assert follow._row('owner',scoped['request_id'])['state']=='applied'
 # Revised scope must plan within the current owner-defined tool budget.
 agent_tools.save_policy(True,1,True)
 budgeted=prepare('Budgeted contact review',cid='chat-two')
 budget_review=command('scope',target='Budgeted contact review',objective='Only review missing email',cid='chat-two')
 assert budget_review['status']=='proposed' and budget_review['plan']['tasks'][0]['max_calls']==1
 assert 'Max separate calls/proposals per worker: 1.' in observed[-1][0]['content'],'Scope planner advertised a budget exceeding the owner policy'
 original_calls=scope_plan['tasks'][0]['max_calls'];scope_plan['tasks'][0]['max_calls']=2
 try:
  reject(lambda:command('scope',target='Budgeted contact review',objective='Review remaining records',cid='chat-two'),403)
 finally:scope_plan['tasks'][0]['max_calls']=original_calls
 assert mission.get_mission('owner',budgeted['id'])['status']=='planned'
 agent_tools.save_policy(True,3,True)
 if mission._pool:mission._pool.shutdown(wait=True)
 print('A5C9 PASS: actual save, late-result fencing, transactional commands and notices, exact review retries, interrupted reexecution, retained results, scope successor, duplicate-edit protection, ambiguous selection, cross-conversation/source/privacy fences, expiry and restart persistence')
