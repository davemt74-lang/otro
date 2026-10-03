"""1G5 installed exercises over existing evidence. Never captures or repairs.

Receipts are owner reports with checked software prerequisites, not hardware
certification. Keep local conversation/model/device identifiers out of exports.
"""
from __future__ import annotations
import hashlib,json,secrets,threading,re
from datetime import datetime,timezone
from ..database import db
from . import tracky_native_managed_session as managed,tracky_native_camera as native
from . import tracky_native_certification as cert,tracky_agent_eyes_acceptance as exercises
from . import tracky_agent_eyes_context as context,tracky_agent_eyes_scene as scene
from . import tracky_agent_eyes_shared_scene as shared,tracky_physical_context as physical

CONTRACT='tracky.agent-eyes.release-acceptance.v1g5'
TEST_KEY='tracky_agent_eyes_release_acceptance_v1g5'
_BOOT=secrets.token_hex(16)
_LOCK=threading.RLock()
STAGES=('local_chat','cloud_scene','cloud_expiry','cloud_revocation','restart_begin','restart_reset','disconnect_seen','reconnect_seen')
LABELS={'local_chat':'Local Agent Chat','cloud_scene':'Cloud scene delivery','cloud_expiry':'Cloud scene expiry','cloud_revocation':'Cloud sharing revocation','restart_reset':'Restart consent reset','reconnect_seen':'Disconnect/reconnect'}
GUIDANCE={
 'local_chat':'Use an owner chat with Agent Eyes context enabled and Cloud inference off. Inspect a recent permitted observation in the response, then record your report.',
 'cloud_scene':'Optionally enable scene sharing. Inspect the checked scene on Cloud after its acknowledgment, then record your report.',
 'cloud_expiry':'After recording Cloud delivery from a completed one-observation session, do not start another observation. Wait past the original 60-second limit and for the expired state to be acknowledged. Inspect Cloud for cleared scene meaning.',
 'cloud_revocation':'After recording Cloud delivery, turn sharing off and wait for acknowledgment. Inspect Cloud for sharing off and no scene meaning.',
 'restart_begin':'Complete the existing Stop/privacy/presence exercises, stop the camera, then save a restart checkpoint. Restart HomeServer yourself.',
 'restart_reset':'After restarting, inspect that capture has not resumed, camera/scene review is reset and sharing is off. Record this check before accepting another camera review.',
 'disconnect_seen':'With an existing pairing, interrupt connectivity yourself and wait for the normal Tracky sync to report a failure. Record this checkpoint.',
 'reconnect_seen':'Restore connectivity yourself. Wait for a later successful Tracky sync and any pending sharing state to be acknowledged, then record your report.'}

class ReleaseAcceptanceError(RuntimeError):
 def __init__(self,message,status_code=409):super().__init__(message);self.status_code=status_code

def _digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def _now():return datetime.now(timezone.utc).isoformat()
def _time(value):return physical._parse_datetime(value)

def _sources():
 worker=managed.status();approval=cert.status();model=native.model_preflight()
 exercise=exercises.status(approval=approval,worker=worker,exposure=physical.provider_exposure())
 try:binding=scene.binding()
 except scene.SceneError:binding={}
 current=context.projection();sharing=shared.status();sync=physical.sync_status()
 return {'worker':worker,'approval':approval,'model':model,'exercise':exercise,'binding':binding,
  'context':current,'sharing':sharing,'sync':sync,'paired':bool(physical.load_https_session()),
  'device':cert._device_fingerprint(),'detector':str(model.get('model_sha256') or ''),
  'request':hashlib.sha256(str(worker.get('last_completed_request_id') or '').encode()).hexdigest()}

def _receipts(s):
 with db() as c:rows=c.execute('SELECT evidence_json FROM runtime_certification_runs WHERE test_key=? ORDER BY rowid DESC LIMIT 100',(TEST_KEY,)).fetchall()
 result=[]
 for row in rows:
  try:
   v=json.loads(row['evidence_json']);signature=v.pop('receipt_digest')
   if signature!=_digest(v) or v.get('stage') not in STAGES or v.get('owner_reported') is not True or v.get('hardware_certified') is not False:continue
   if not s['device'] or len(s['detector'])!=64 or v.get('device')!=s['device'] or v.get('detector')!=s['detector']:continue
   stamp=_time(v.get('recorded_at'))
   if not stamp or stamp>datetime.now(timezone.utc):continue
   result.append(v)
  except (ValueError,TypeError,KeyError,AttributeError):continue
 return result

def _last(receipts,stage,s,*,prior_process=False):
 for v in receipts:
  if v['stage']!=stage:continue
  if not prior_process and v.get('boot')!=_BOOT:continue
  if stage not in {'restart_begin','restart_reset','disconnect_seen','reconnect_seen'} and v.get('scene_binding','')!=s['binding'].get('binding',''):continue
  return v
 return None

def _ready(stage,s,receipts):
 current=s['context'];sharing=s['sharing'];sync=s['sync'];worker=s['worker']
 reviewed=s['model'].get('model_integrity_verified') is True and len(s['detector'])==64 and s['approval'].get('owner_accepted_current_run') is True
 idle=not s['exercise'].get('shared_camera_busy')
 recent=reviewed and current.get('state')=='recent_observation' and bool(re.fullmatch(r'[a-f0-9]{16}',str(current.get('request_fingerprint') or '')))
 successful=_time(sync.get('last_success_at'))
 if successful and successful>datetime.now(timezone.utc):successful=None
 if stage=='local_chat':return bool(recent)
 if stage=='cloud_scene':
  observed=_time(current.get('observed_at'))
  return bool(recent and current.get('scene') and s['paired'] and sharing.get('enabled') is True and sharing.get('state')=='available' and sharing.get('delivery')=='acknowledged' and successful and observed and successful>=observed)
 if stage in {'cloud_expiry','cloud_revocation'}:
  sent=_last(receipts,'cloud_scene',s)
  if not sent or not s['paired'] or sharing.get('delivery')!='acknowledged' or sharing.get('revision',0)<=sent['evidence']['revision']:return False
  if not successful or successful<=_time(sent['recorded_at']):return False
  if stage=='cloud_revocation':return sharing.get('enabled') is False and sharing.get('state')=='revoked'
  observed=_time(sent['evidence'].get('observed_at'))
  return bool(observed and (datetime.now(timezone.utc)-observed).total_seconds()>=60 and s['request']==sent['evidence']['request'] and worker.get('last_observed_at')==sent['evidence']['observed_at'] and sharing.get('state')=='unavailable' and sharing.get('reason')=='observation_expired')
 if stage=='restart_begin':return bool(reviewed and idle and s['exercise'].get('owner_exercise_complete'))
 if stage=='restart_reset':
  before=_last(receipts,'restart_begin',s,prior_process=True)
  return bool(before and before.get('boot')!=_BOOT and idle and not worker.get('active') and not s['approval'].get('owner_accepted_current_run') and not s['binding'] and sharing.get('enabled') is False)
 if stage=='disconnect_seen':
  failed=_time(sync.get('last_failure_at'))
  return bool(reviewed and idle and s['paired'] and sync.get('last_error') and failed and failed<=datetime.now(timezone.utc))
 if stage=='reconnect_seen':
  before=_last(receipts,'disconnect_seen',s,prior_process=True)
  failed=_time((before or {}).get('evidence',{}).get('failure_at'))
  return bool(before and s['paired'] and successful and failed and successful>failed and not sync.get('last_error') and sharing.get('delivery')!='pending')
 return False

def _inspection(s,receipts):
 return _digest({'boot':_BOOT,'device':s['device'],'detector':s['detector'],'binding':s['binding'].get('binding',''),'request':s['request'],'revision':s['sharing'].get('revision',0),'delivery':s['sharing'].get('delivery'),'enabled':s['sharing'].get('enabled'),'success_at':s['sync'].get('last_success_at'),'failure_at':s['sync'].get('last_failure_at'),'ready':[stage for stage in STAGES if _ready(stage,s,receipts)]})

def _report(s,receipts):
 checks=[]
 def add(key,label,complete,guidance,source='checked_software_prerequisites',optional=False,group='local'):
  checks.append({'key':key,'label':label,'state':'recorded' if complete else 'pending','source':source,'optional':optional,'group':group,'guidance':guidance})
 add('camera_review','Current camera/model review',s['approval'].get('owner_accepted_current_run') is True and s['model'].get('model_integrity_verified') is True and len(s['detector'])==64,'Complete the installed camera review in Agent Chat; model changes require a new review.')
 for key,label in [('owner_stop','Owner Stop'),('privacy_revocation','Camera privacy'),('presence_lease','Owner presence timeout')]:
  add(key,label,key in s['exercise'].get('completed_steps',[]),'Use the existing installed-device exercise controls and inspect camera release.','existing_owner_exercise')
 add('scene_review','Optional scene/model review',bool(s['binding']),'Use the existing local scene model test and owner acceptance controls before including scene understanding.','existing_owner_review',True,'scene')
 for key in ('local_chat','restart_reset','cloud_scene','cloud_expiry','cloud_revocation','reconnect_seen'):
  add(key,LABELS[key],bool(_last(receipts,key,s)),GUIDANCE[key],'owner_report_with_checked_prerequisites',key.startswith('cloud_') or key=='reconnect_seen','cloud' if key.startswith('cloud_') or key=='reconnect_seen' else 'local')
 required=[c for c in checks if not c['optional']]
 return {'contract':CONTRACT,'checked_at':_now(),'checks':checks,
  'local_owner_exercises_complete':all(c['state']=='recorded' for c in required),
  'cloud_owner_exercises_complete':all(c['state']=='recorded' for c in checks if c['group']=='cloud'),
  'scene_review_complete':bool(s['binding']),
  'current_camera_review_required':not s['approval'].get('owner_accepted_current_run'),
  'sharing_enabled':s['sharing'].get('enabled') is True,'sharing_delivery':s['sharing'].get('delivery','not_requested'),
  'owner_reported_only':True,'independent_hardware_certified':False,'capture_authority':False,
  'automatic_recovery':False,'unattended_perception_allowed':False,
  'scope':'Local installation exercises; Cloud exercises are optional and never enable sharing.',
  'recordable_stages':[stage for stage in STAGES if _ready(stage,s,receipts)],
  'inspection_token':_inspection(s,receipts),
  'expected_fingerprint':s['context'].get('request_fingerprint','') if s['context'].get('state')=='recent_observation' else ''}

def status():
 try:
  s=_sources();return _report(s,_receipts(s))
 except (ValueError,TypeError,KeyError) as exc:
  raise ReleaseAcceptanceError('Installed acceptance status could not be checked.',503) from exc

def redacted(report):
 return {k:report[k] for k in ('contract','checked_at','checks','local_owner_exercises_complete','cloud_owner_exercises_complete','scene_review_complete','current_camera_review_required','sharing_enabled','sharing_delivery','owner_reported_only','independent_hardware_certified','capture_authority','automatic_recovery','unattended_perception_allowed','scope')}

def agent_summary():return redacted(status())

def record(*,stage,consent,owner_observed,inspection_token,expected_fingerprint='',conversation_id=''):
 if stage not in STAGES:raise ReleaseAcceptanceError('Select a supported installed exercise.',422)
 if consent is not True or owner_observed is not True:raise ReleaseAcceptanceError('Explicit owner inspection and approval are required.',403)
 # Match the existing shared-scene -> managed-worker lock order.
 with _LOCK,shared._LOCK,managed._LOCK:
  s=_sources();receipts=_receipts(s)
  if inspection_token!=_inspection(s,receipts):raise ReleaseAcceptanceError('Prerequisites changed; refresh and inspect the selected exercise again.')
  if not _ready(stage,s,receipts):raise ReleaseAcceptanceError(GUIDANCE[stage])
  if stage in {'local_chat','cloud_scene'} and expected_fingerprint!=s['context'].get('request_fingerprint'):raise ReleaseAcceptanceError('Observation changed; refresh the checklist before recording.')
  if stage=='local_chat':
   from . import context_engine
   with db() as c:owner=c.execute("SELECT 1 FROM conversations WHERE id=? AND source_app_key='owner'",(conversation_id,)).fetchone()
   if not owner:raise ReleaseAcceptanceError('Select an existing owner conversation.',403)
   settings=context_engine.get_settings(conversation_id)
   if not (settings.get('include_agent_eyes') is True and settings.get('agent_eyes_local_only') is True and settings.get('cloud_allowed') is False):raise ReleaseAcceptanceError('Enable local Agent Eyes context with Cloud inference off in that owner chat.',403)
  evidence={'revision':s['sharing'].get('revision',0),'observed_at':s['context'].get('observed_at',''),'request':s['request']}
  if stage=='disconnect_seen':evidence={'failure_at':s['sync']['last_failure_at']}
  if stage=='reconnect_seen':evidence={'success_at':s['sync']['last_success_at']}
  if stage=='restart_reset':evidence={'previous_boot':_last(receipts,'restart_begin',s,prior_process=True)['boot'],'sharing_off':True,'review_reset':True,'software_idle':True}
  body={'stage':stage,'boot':_BOOT,'device':s['device'],'detector':s['detector'],'scene_binding':s['binding'].get('binding',''),'recorded_at':_now(),'evidence':evidence,'owner_reported':True,'hardware_certified':False}
  # Stable per-stage/current evidence identity prevents duplicate clicks from
  # creating unlimited receipts; repeat observations or checkpoints replace it.
  identity=_digest({k:v for k,v in body.items() if k!='recorded_at'})
  body['receipt_digest']=_digest(body)
  with db() as c:c.execute('INSERT OR IGNORE INTO runtime_certification_runs(id,test_key,status,duration_ms,evidence_json) VALUES(?,?,?,?,?)',('eyes-release-'+identity[:32],TEST_KEY,'not_verified',0,json.dumps(body,sort_keys=True,separators=(',',':'))))
 return status()
