"""A5C8 real SQLite lifecycle, worker fences, recovery and notification isolation."""
import hashlib, os, sys, tempfile, threading, time, uuid
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix='a5c8-') as data:
 os.environ['HOMESERVER_DATA_DIR']=data
 from app.database import db,initialize_database
 from app.services import agent_mission_runtime as mission,agent_mission_progress as progress
 from app.services import agent_mission_control as control,agent_mission_cloud_v1 as cloud
 from app.services import providers,context_engine,agent_tools,activity_center
 initialize_database();agent_tools.save_policy(True,3,True)
 providers.inference_status=lambda:{'available':True,'selected_provider':'openai','model':'fixture'}
 def uid():return str(uuid.uuid4())
 with db() as conn:
  agent=conn.execute('SELECT id FROM agents WHERE is_primary=1').fetchone()[0]
  for source in ('owner','app:vp3','app:other'):
   conn.execute('INSERT INTO conversations(id,agent_id,source_app_key,title) VALUES(?,?,?,?)',(source,agent,source,source))
  for key in ('vp3','other'):
   appid=conn.execute('INSERT INTO paired_apps(app_key,name,token_hash) VALUES(?,?,?)',(key,key,hashlib.sha256(uid().encode()).hexdigest())).lastrowid
   conn.execute('INSERT INTO app_permissions(paired_app_id,permission,allowed) VALUES(?,?,1)',(appid,'agent.chat'))
 def make(source='owner'):
  return mission.create_mission(source,conversation_id=source,objective='Read recorded notes',client_request_id=uid(),parent_agent_id=agent,owner=source=='owner',tasks=[{'role':'reviewer','title':'Notes reviewer','objective':'Read notes','instructions':'No writes','depends_on':[]}])
 def deny(fn,code):
  try:fn()
  except mission.MissionError as exc:assert exc.status_code==code,(str(exc),exc.status_code)
  else:raise AssertionError('Expected rejection')
 def start_fake(m):
  with patch.object(mission,'_dispatch'):
   mission.start_mission(m['source_app_key'],m['id'])
  tid=m['tasks'][0]['id'];lease=uid()
  with db() as conn:
   conn.execute("UPDATE agent_mission_tasks_v1 SET status='running',lease_id=?,attempt=1,started_at=CURRENT_TIMESTAMP WHERE id=?",(lease,tid))
   mission._event(conn,m['id'],'task.started',tid)
  return tid,lease
 m=make();mid=m['id'];assert m['progress']['state']=='awaiting_review'
 n=progress.notifications('owner')['items'][0]
 assert n['kind']=='mission.planned' and n['level']=='action_required'
 assert len(activity_center.list_activity(category='agent')['items'])>=1
 with db() as conn:
  conn.execute('BEGIN IMMEDIATE');before=conn.execute('SELECT count(*) FROM agent_mission_notices_v1').fetchone()[0]
  mission._event(conn,mid,'mission.started');conn.rollback()
 with db() as conn:assert conn.execute('SELECT count(*) FROM agent_mission_notices_v1').fetchone()[0]==before,'Events and notifications roll back together'
 tid,lease=start_fake(m);progress.touch(mid,tid,lease,'choosing_read')
 current=mission.get_mission('owner',mid);assert current['progress']['state']=='running'
 assert current['tasks'][0]['activity']['stage']=='Choosing a read'
 assert current['tasks'][0]['activity']['last_activity_at']
 # Observe a real old timestamp; a stale callback can never fence a fresh lease.
 with db() as conn:conn.execute("UPDATE agent_mission_progress_v1 SET updated_at=datetime('now','-6 minutes') WHERE task_id=?",(tid,))
 assert mission.get_mission('owner',mid)['progress']['state']=='stalled'
 assert progress.sweep()==1 and progress.sweep()==0
 current=mission.get_mission('owner',mid);assert current['status']=='waiting_review' and current['progress']['state']=='stalled'
 deny(lambda:progress.touch(mid,tid,lease,'composing'),409)
 deny(lambda:control.resume('owner',mid),409)
 with patch.object(mission,'_dispatch'):control.resume('owner',mid,allow_reexecution=True)
 newlease=uid()
 with db() as conn:
  conn.execute("UPDATE agent_mission_tasks_v1 SET status='running',lease_id=?,started_at=CURRENT_TIMESTAMP WHERE id=?",(newlease,tid));mission._event(conn,mid,'task.started',tid)
 cutoff='2099-01-01 00:00:00'
 assert not mission.interrupt_for_review(mid,'worker_stalled',task_id=tid,lease=lease,stale_before=cutoff)
 assert not mission.interrupt_for_review(mid,'worker_stalled',task_id=tid,lease=newlease,stale_before='2000-01-01 00:00:00')
 # A late result cannot replace a resumed lease or append notices.
 with db() as conn:task=dict(conn.execute('SELECT * FROM agent_mission_tasks_v1 WHERE id=?',(tid,)).fetchone());before=conn.execute('SELECT count(*) FROM agent_mission_notices_v1').fetchone()[0]
 mission._commit_model_only(mid,tid,lease,task,'completed','OLD',None,'openai','fixture')
 with db() as conn:assert conn.execute('SELECT count(*) FROM agent_mission_notices_v1').fetchone()[0]==before
 mission._commit_model_only(mid,tid,newlease,task,'completed','CURRENT',None,'openai','fixture')
 with db() as conn:mission._finalize(conn,mid)
 assert mission.get_mission('owner',mid)['status']=='completed'
 notice=next(n for n in progress.notifications('owner')['items'] if n['kind']=='mission.completed')
 read=progress.mark_read('owner',notice['id']);assert next(n for n in read['items'] if n['id']==notice['id'])['read_at']
 progress.mark_read('owner',notice['id']);initialize_database()
 assert next(n for n in progress.notifications('owner')['items'] if n['id']==notice['id'])['read_at']
 # Cancellation and restart create exactly one lifecycle notice per transition.
 interrupted=make();itid,ilease=start_fake(interrupted);assert mission.recover_interrupted()==1;assert mission.recover_interrupted()==0
 assert next(n for n in progress.notifications('owner')['items'] if n['mission_id']==interrupted['id'])['kind']=='mission.recovery_review_required'
 cancelled=make();mission.cancel_mission('owner',cancelled['id']);mission.cancel_mission('owner',cancelled['id'])
 assert sum(n['mission_id']==cancelled['id'] and n['kind']=='mission.cancelled' for n in progress.notifications('owner')['items'])==1
 # Terminal interruption retires obsolete approval prompts, preserving their history.
 for ending in ('cancelled','failed'):
  stale=make();stid,slease=start_fake(stale)
  with db() as conn:mission._event(conn,stale['id'],'worker.action_proposed',stid,{'action_id':uid()})
  if ending=='cancelled':mission.cancel_mission('owner',stale['id'])
  else:
   with db() as conn:
    conn.execute("UPDATE agent_missions_v1 SET status='failed' WHERE id=?",(stale['id'],))
    mission._event(conn,stale['id'],'mission.failed')
  history=[n for n in progress.notifications('owner')['items'] if n['mission_id']==stale['id']]
  assert next(n for n in history if n['kind']=='worker.action_proposed')['resolved_at']
  assert next(n for n in history if n['kind']=='mission.'+ending)['resolved_at'] is None
 # Source/privacy/permission fences apply to feed, notifications and read actions.
 cm=make('app:vp3');other=make('app:other')
 notices=cloud.execute('notifications.list',{})['notifications'];assert notices['items'] and all(n['mission_id']==cm['id'] for n in notices['items'])
 cid=notices['items'][0]['id'];deny(lambda:progress.mark_read('app:other',cid),404)
 assert all(r['id']==cm['id'] for r in cloud.execute('progress.feed',{})['items'])
 with db() as conn:conn.execute("UPDATE conversation_context_settings SET cloud_allowed=0 WHERE conversation_id='app:vp3'")
 assert not cloud.execute('notifications.list',{})['notifications']['items']
 assert cloud.execute('progress.feed',{})['items'][0]['private'] and cloud.execute('progress.feed',{})['items'][0]['progress'] is None
 deny(lambda:cloud.execute('notifications.read',{'notice_id':cid}),403)
 with db() as conn:conn.execute("UPDATE app_permissions SET allowed=0 WHERE permission='agent.chat' AND paired_app_id=(SELECT id FROM paired_apps WHERE app_key='vp3')")
 deny(lambda:cloud.execute('progress.feed',{}),403);deny(lambda:cloud.execute('notifications.list',{}),403)
 # Newer finished missions cannot push still-running work out of the live feed.
 live=make();start_fake(live)
 for _ in range(10):x=make();mission.cancel_mission('owner',x['id'])
 assert live['id'] in [x['id'] for x in progress.feed('owner')['items']]
 progress.start();assert progress._thread.is_alive();progress.start();progress.stop();assert not progress._thread.is_alive()
 print('A5C8 PASS: persisted stages, atomic lifecycle notices/readback, source/privacy/permission fences, active history, watchdog, exact stale-lease fencing, reviewed recovery, late-result rejection and restart durability')
