"""Synthetic checked prerequisites remain owner reports, never camera certification."""
import os,sys,tempfile,json,hashlib
from pathlib import Path
from datetime import datetime as RealTime,timedelta,timezone
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory() as root:
 os.environ['HOMESERVER_DATA_DIR']=root;os.environ['VP3_OS_HARDWARE_ADAPTER']='disabled'
 from fastapi.testclient import TestClient
 from app.runtime import app
 from app.database import db
 from app.security import OWNER_CONTROL_TOKEN
 from app.services import tracky_agent_eyes_release_acceptance as release,context_engine
 from app.services.tasks import scheduler
 base='/api/v1/control/onboarding/visual/agent-eyes/release-acceptance';headers={'X-Requested-With':'XMLHttpRequest'}
 now=RealTime.now(timezone.utc);observed=(now-timedelta(seconds=1)).isoformat()
 s={'worker':{'last_completed_request_id':'request-synthetic','last_observed_at':observed,'active':False},'approval':{'owner_accepted_current_run':True},'model':{'model_integrity_verified':True},'exercise':{'shared_camera_busy':False,'owner_exercise_complete':True,'completed_steps':['owner_stop','privacy_revocation','presence_lease']},'binding':{'binding':'b'*64},'context':{'state':'recent_observation','request_fingerprint':'a'*16,'observed_at':observed,'scene':{'objects':['SECRET_OBJECT']}},'sharing':{'enabled':True,'state':'available','reason':'recent_observation','delivery':'acknowledged','revision':1},'sync':{'last_success_at':now.isoformat(),'last_failure_at':None,'last_error':''},'paired':True,'device':'SECRET_DEVICE','detector':'d'*64,'request':hashlib.sha256(b'request-synthetic').hexdigest()}
 with TestClient(app) as client:
  scheduler.stop();assert client.get(base).status_code==401
  assert client.post('/__owner/session',headers={'X-HomeServer-Owner':OWNER_CONTROL_TOKEN}).status_code==200
  with db() as c:
   aid=c.execute('SELECT id FROM agents WHERE is_primary=1').fetchone()[0]
   c.execute("INSERT INTO conversations(id,agent_id,source_app_key,title) VALUES('owner-chat',?,'owner','PRIVATE_CHAT')",(aid,))
   c.execute("INSERT INTO conversations(id,agent_id,source_app_key,title) VALUES('other-chat',?,'other','PRIVATE_CHAT')",(aid,))
  with patch.object(release,'_sources',return_value=s):
   def payload(stage,**extra):return {'stage':stage,'consent':True,'owner_observed':True,'inspection_token':client.get(base).json()['inspection_token'],'expected_fingerprint':'a'*16,**extra}
   def send(stage,**extra):return client.post(base+'/record',headers=headers,json=payload(stage,**extra))
   start=client.get(base);assert start.headers['cache-control']=='no-store'
   assert start.json()['local_owner_exercises_complete'] is False
   assert client.post(base+'/record',json=payload('local_chat')).status_code==403
   assert send('local_chat',conversation_id='other-chat').status_code==403
   assert send('local_chat',conversation_id='owner-chat').status_code==403
   context_engine.update_settings('owner-chat',include_agent_eyes=True,cloud_allowed=False,include_memory=False,include_knowledge=False,include_contacts=False,max_context_chars=4000)
   assert send('local_chat',conversation_id='owner-chat',expected_fingerprint='e'*16).status_code==409
   assert send('local_chat',conversation_id='owner-chat',owner_observed=False).status_code==403
   assert send('local_chat',conversation_id='owner-chat',inspection_token='e'*64).status_code==409
   assert send('local_chat',conversation_id='owner-chat').status_code==200
   assert send('local_chat',conversation_id='owner-chat').status_code==200
   with db() as c:assert c.execute('SELECT COUNT(*) FROM runtime_certification_runs WHERE test_key=?',(release.TEST_KEY,)).fetchone()[0]==1
   assert send('cloud_scene').status_code==200
   assert send('cloud_expiry').status_code==409
   later=now+timedelta(seconds=65)
   class Later(RealTime):
    @classmethod
    def now(cls,tz=None):return later if tz else later.replace(tzinfo=None)
   with patch.object(release,'datetime',Later):
    s['context']={'state':'stale','reason':'observation_expired'};s['sharing'].update(state='unavailable',reason='observation_expired',revision=2);s['sync']['last_success_at']=later.isoformat()
    old_request=s['request'];s['request']='f'*64
    assert send('cloud_expiry').status_code==409
    s['request']=old_request;assert send('cloud_expiry').status_code==200
    s['sharing'].update(enabled=False,state='revoked',reason='owner_revoked',revision=3)
    s['sync']['last_success_at']=(later-timedelta(seconds=1)).isoformat();assert send('cloud_revocation').status_code==200
    s['exercise']['shared_camera_busy']=True;assert send('restart_begin').status_code==409
    s['exercise']['shared_camera_busy']=False;assert send('restart_begin').status_code==200
    assert send('restart_reset').status_code==409
    with patch.object(release,'_BOOT','new-process'):
     s['approval']['owner_accepted_current_run']=False;s['binding']={}
     assert send('restart_reset').status_code==200
     assert client.get(base).json()['local_owner_exercises_complete'] is False
     s['approval']['owner_accepted_current_run']=True;s['binding']={'binding':'new-vision-review'}
     s['context']={'state':'recent_observation','request_fingerprint':'a'*16,'observed_at':later.isoformat()}
     assert send('local_chat',conversation_id='owner-chat').status_code==200
     assert client.get(base).json()['local_owner_exercises_complete'] is True
     s['sync'].update(last_error='SECRET_REMOTE_ERROR',last_failure_at=(later-timedelta(seconds=3)).isoformat(),last_success_at=(later-timedelta(seconds=5)).isoformat())
     assert send('disconnect_seen').status_code==200
     assert send('reconnect_seen').status_code==409
     s['sync'].update(last_error='',last_success_at=(later-timedelta(seconds=1)).isoformat())
     s['sharing']['delivery']='pending';assert send('reconnect_seen').status_code==409
     s['sharing']['delivery']='acknowledged';assert send('reconnect_seen').status_code==200
     report=client.get(base+'/report');export=report.json();assert 'attachment' in report.headers['content-disposition']
     for value in ['SECRET_OBJECT','SECRET_DEVICE','PRIVATE_CHAT','SECRET_REMOTE_ERROR','d'*64,'inspection_token','expected_fingerprint','scene_binding']:
      assert value not in json.dumps(export),value
     assert export['independent_hardware_certified'] is False and export['capture_authority'] is False
     old=s['detector'];s['detector']='e'*64;assert client.get(base).json()['local_owner_exercises_complete'] is False;s['detector']=old
     with db() as c:
      row=c.execute('SELECT id,evidence_json FROM runtime_certification_runs WHERE test_key=? ORDER BY rowid DESC LIMIT 1',(release.TEST_KEY,)).fetchone();bad=json.loads(row['evidence_json']);bad['boot']='tampered';c.execute('UPDATE runtime_certification_runs SET evidence_json=? WHERE id=?',(json.dumps(bad),row['id']))
     assert 'reconnect_seen' in [c['key'] for c in client.get(base).json()['checks'] if c['state']=='pending']
 print('TRACKY_AGENT_EYES_RELEASE_ACCEPTANCE_V1G5: owner/CSRF, local chat isolation, token/fingerprint races, canonical cloud expiry/revocation, idle restart/reset, later reconnect, receipt scopes/idempotency/tamper and redaction PASS')
