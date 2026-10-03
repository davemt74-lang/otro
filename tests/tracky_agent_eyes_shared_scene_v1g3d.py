"""Semantic consent, corrections, sync exclusion, freshness and receipt races."""
import json,os,sys,tempfile
from pathlib import Path
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory() as root:
 os.environ['HOMESERVER_DATA_DIR']=root;os.environ['VP3_OS_HARDWARE_ADAPTER']='disabled'
 from fastapi.testclient import TestClient
 from app.runtime import app
 from app.database import db
 from app.security import OWNER_CONTROL_TOKEN
 from app.services import tracky_agent_eyes_shared_scene as shared,tracky_agent_eyes_context as context,tracky_agent_eyes_scene as scene,tracky_physical_context as physical,room_device_automation as devices,brain,context_engine
 from app.services.tasks import scheduler
 fixtures=json.loads((Path(__file__).parent/'fixtures/tracky-shared-scene-v1g3d.json').read_text())
 for fixture in fixtures:assert shared.fingerprint(fixture['summary'])==fixture['fingerprint']
 current={'state':'recent_observation','reason':'recent_observation','request_fingerprint':'a'*16,'observed_at':datetime.now(timezone.utc).isoformat(),'scene':{'room_id':'studio','objects':['chair','cup'],'setting':'indoor','lighting':'bright'}}
 def projection():
  if current.get('scene'):return {**current,'scene':shared.decorate(current['scene'],current['request_fingerprint'])}
  return dict(current)
 with TestClient(app) as client:
  scheduler.stop();route='/api/v1/control/onboarding/visual/agent-eyes/scene/share'
  assert client.get(route).status_code==401
  client.post('/__owner/session',headers={'X-HomeServer-Owner':OWNER_CONTROL_TOKEN})
  assert client.get(route).headers['cache-control']=='no-store'
  assert shared.snapshot() is None and not shared.pending()
  with patch.object(context,'projection',side_effect=projection),patch.object(scene,'binding',return_value={}),patch.object(scene,'check_binding'),patch.object(devices,'list_devices',return_value=[{'category':'light','last_seen_at':datetime.now(timezone.utc).isoformat(),'state':{'on':True,'secret':'SECRET'}}]):
   assert client.post(route,json={'enabled':True,'consent':True}).status_code==403
   headers={'X-Requested-With':'XMLHttpRequest'}
   assert client.post(route,json={'enabled':'true','consent':True},headers=headers).status_code==422
   assert client.post(route,json={'enabled':True,'consent':False},headers=headers).status_code==403
   assert client.post(route,json={'enabled':True,'consent':True},headers=headers).status_code==200
   first=shared.snapshot();assert first['summary']['objects']==['chair','cup'] and shared.pending()
   assert 'SECRET' not in json.dumps(first) and 'device_context' not in first['summary']
   # Current chat must be owner-owned and permanently local before corrections.
   chat_id='shared-scene-chat'
   with db() as connection:
    agent_id=connection.execute('SELECT id FROM agents WHERE is_primary=1').fetchone()[0]
    connection.execute("INSERT INTO conversations(id,agent_id,source_app_key,title) VALUES(?,?,'owner','Scene')",(chat_id,agent_id))
   correction='/api/v1/control/conversations/'+str(chat_id)+'/agent-eyes-correction'
   payload={'object_label':'cup','present':False,'expected_fingerprint':'a'*16}
   assert client.post(correction,json=payload,headers=headers).status_code==403
   context_engine.update_settings(chat_id,include_agent_eyes=True,include_memory=False,include_knowledge=False,include_contacts=False,cloud_allowed=False,max_context_chars=12000)
   assert client.post(correction,json=payload).status_code==403
   assert client.post(correction,json={**payload,'present':'false'},headers=headers).status_code==422
   assert client.post(correction,json={**payload,'expected_fingerprint':'b'*16},headers=headers).status_code==409
   # owner_status expects coarse category metadata as supplied by real projection.
   current.update(contract=context.CONTRACT,freshness_limit_seconds=60,possible_face_regions='none',age_seconds=0)
   corrected=client.post(correction,json=payload,headers=headers);assert corrected.status_code==200,corrected.text
   report=corrected.json()['agent_eyes_context']['scene']['owner_corrections'][0]
   assert report['source']=='owner_report' and report['conflicts_with_camera'] and report['present'] is False
   corrected_share=shared.snapshot();assert corrected_share['revision']>first['revision']
   shared.record_current()
   assert any(r['predicate']=='possibly_visible_in' for r in physical.current_context()['world_state'])
   with patch.object(physical,'remote_identity_metadata',return_value={'device_id':'test-site'}):
    package=physical._cloud_payload();assert package['payload']['agent_scene_share']==corrected_share
    assert not any(shared.reserved(r) for r in package['payload']['world_state'])
    assert 'device_context' not in json.dumps(package['payload']['agent_scene_share'])
   receipt=lambda s:{'accepted':True,'site_id':'test-site','revision':s['revision'],'fingerprint':s['fingerprint'],'state':s['summary']['state']}
   assert not shared.acknowledge(first,receipt(first),site_id='test-site')
   for change in ({'accepted':1},{'revision':True},{'fingerprint':'b'*64},{'state':'revoked'}):
    assert not shared.acknowledge(corrected_share,{**receipt(corrected_share),**change},site_id='test-site')
   assert shared.acknowledge(corrected_share,receipt(corrected_share),site_id='test-site') and not shared.pending()
   # Revocation in flight cannot be acknowledged by an earlier active receipt.
   shared.set_sharing(enabled=False,consent=True)
   assert not shared.acknowledge(corrected_share,receipt(corrected_share),site_id='test-site')
   revoked=shared.snapshot();assert revoked['summary']['state']=='revoked' and 'objects' not in revoked['summary']
   assert shared.pending() and not any(shared.reserved(r) for r in physical.current_context()['world_state'])
   assert not shared.acknowledge(revoked,receipt(revoked),site_id='wrong-site')
   assert shared.acknowledge(revoked,receipt(revoked),site_id='test-site')
   # Semantic pending state schedules the existing sync, while preserving backoff.
   shared.set_sharing(enabled=True,consent=True)
   with patch.object(physical,'sync_status',return_value={'pending_events':0,'next_retry_at':None}):assert physical.sync_due()
   with patch.object(physical,'sync_status',return_value={'pending_events':0,'next_retry_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()}):assert not physical.sync_due()
   active=shared.snapshot();current.clear();current.update(state='stale',reason='observation_expired')
   assert not shared.acknowledge(active,receipt(active),site_id='test-site'),'Expired observation receipt acknowledged as current'
   assert shared.snapshot()['summary']['state']=='unavailable'
   # A restarted process closes prior process consent; no camera read is needed.
   shared._PROCESS_CONSENT=False
   with patch.object(context,'projection',side_effect=AssertionError('Revoked snapshot must not read camera')):
    assert shared.snapshot()['summary']['state']=='revoked'
   # Backup rollback repair advances only the currently desired revocation.
   old=shared.snapshot();newer={**receipt(old),'accepted':False,'revision':old['revision']+10}
   assert not shared.acknowledge(old,newer,site_id='test-site')
   repaired=shared.snapshot();assert repaired['revision']==newer['revision']+1 and repaired['summary']['state']=='revoked'
   assert client.post(correction,json=payload,headers=headers).status_code==409
 print('TRACKY_SHARED_SCENE_V1G3D: consent, correction owner isolation, canonical graph, upload exclusion, revocation/freshness races, restart, backup revision repair PASS')
