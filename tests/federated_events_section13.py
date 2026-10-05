from __future__ import annotations
import json,os,sys,tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
tmp=tempfile.TemporaryDirectory();os.environ['HOMESERVER_DATA_DIR']=tmp.name;os.environ['VP3_OS_HARDWARE_ADAPTER']='disabled'
from app.database import db,initialize_database
from app.services import tracky_federated_automation as fa,tracky_federation_sync as sync,tracky_site_topology as topo
initialize_database()
HOME='11111111-1111-4111-8111-111111111111';DEVICE='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';owner={'actor_type':'owner','actor_id':'owner'}
topo.register_site(site_id=HOME,label='Home');topo.register_device(device_id=DEVICE,label='Home Node',site_id=HOME,hardware_profile='Node',trust_state='trusted',roles=['site_authority'],capabilities={'site_authority_eligible':True});topo.claim_site_authority(site_id=HOME,device_id=DEVICE);sync.set_local_site_id(HOME)
definition=fa.create_definition({'automation_id':'fa:test','revision':1,'idempotency_key':'fa:test:1','name':'Test','origin_site_id':HOME,'state':'active','trigger':{'kind':'event','source_site_id':HOME,'event_key':'room.occupied','requires_fresh_world_state':False},'steps':[{'step_id':'check','action_type':'data_operation','authority_site_id':HOME,'target_site_id':HOME,'action_key':'world.validate','required_permissions':['semantic_world_read']}]},actor=owner)
event={'event_id':'event-1','event_type':'room.occupied','confidence':1,'occurred_at':datetime.now(timezone.utc).isoformat()}
with db() as c:
 c.execute('INSERT INTO tracky_physical_events(event_id,sequence_no,event_type,confidence,occurred_at,event_json) VALUES (?,?,?,?,?,?)',('event-1',1,event['event_type'],1,event['occurred_at'],json.dumps(event)))
 c.execute("CREATE TRIGGER fail_trigger_receipt BEFORE INSERT ON tracky_federated_automation_trigger_receipts BEGIN SELECT RAISE(ABORT,'receipt failure'); END;")
try:fa.process_physical_trigger_events(['event-1']);raise AssertionError('Failed trigger receipt accepted')
except Exception as exc:assert 'receipt failure' in str(exc)
with db() as c:
 assert c.execute('SELECT COUNT(*) FROM tracky_federated_automation_runs').fetchone()[0]==0,'Run escaped receipt rollback'
 c.execute('DROP TRIGGER fail_trigger_receipt')
with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:fa.process_physical_trigger_events(['event-1']),range(2)))
assert sorted(x[0]['decision'] for x in results)==['accepted','duplicate']
with db() as c:assert c.execute('SELECT COUNT(*) FROM tracky_federated_automation_runs').fetchone()[0]==1
run_id=next(x[0]['run_id'] for x in results if x[0]['decision']=='accepted');dispatch=fa.create_step_dispatch(run_id,'check',actor=owner)
receipt={'protocol':fa.EXECUTION_PROTOCOL,'receipt_id':'receipt-1','dispatch_id':dispatch['dispatch_id'],'run_id':run_id,'step_id':'check','attempt':dispatch['attempt'],'authority_site_id':HOME,'authority_epoch':dispatch['authority_epoch'],'status':'completed','result':{},'completed_at_ms':fa._now_ms()}
with db() as c:c.execute("CREATE TRIGGER fail_execution_event BEFORE INSERT ON tracky_federated_automation_events WHEN NEW.event_kind='execution.receipt' BEGIN SELECT RAISE(ABORT,'event failure'); END;")
try:fa.apply_execution_receipt(receipt,actor=owner);raise AssertionError('Failed execution event accepted')
except Exception as exc:assert 'event failure' in str(exc)
assert fa.get_run(run_id)['steps'][0]['state']=='ready'
with db() as c:c.execute('DROP TRIGGER fail_execution_event')
assert fa.apply_execution_receipt(receipt,actor=owner)['state']=='completed'
assert fa.apply_execution_receipt(receipt,actor=owner)['state']=='completed'
with db() as c:assert c.execute("SELECT COUNT(*) FROM tracky_federated_automation_events WHERE event_kind='execution.receipt'").fetchone()[0]==1
try:fa.apply_execution_receipt({**receipt,'status':'failed'},actor=owner);raise AssertionError('Changed terminal receipt accepted')
except fa.FederatedAutomationError as exc:assert exc.status_code==409
run=fa.create_run({'automation_id':definition['automation_id'],'idempotency_key':'cancel-test'},actor=owner);dispatch=fa.create_step_dispatch(run['run_id'],'check',actor=owner);fa.cancel_run(run['run_id'],actor=owner)
try:fa.apply_execution_receipt({**receipt,'run_id':run['run_id'],'dispatch_id':dispatch['dispatch_id']},actor=owner);raise AssertionError('Cancelled run resurrected')
except fa.FederatedAutomationError as exc:assert exc.status_code==409
assert fa.get_run(run['run_id'])['state']=='cancelled'
# A dispatch claim commits before calling any executor, without holding its transaction.
import threading
from app.services import local_automation as local,room_device_automation as devices
fa.federated_data.reconciliation_state=lambda _: {'needs_reconciliation':False}
devices.upsert_room('office','Office');devices.upsert_provider('fixture','Fixture','test',executable=True,status='connected');devices.upsert_device('lamp','fixture','lamp','Lamp','light',room_key='office',controllable=True,state={'power':'off'})
local.upsert_routine('routine','Routine',approval_mode='ask_every_time',steps=[{'device_key':'lamp','command':'on','arguments':{}}])
fa.create_definition({'automation_id':'fa:execute','revision':1,'idempotency_key':'fa:execute:1','name':'Execute','origin_site_id':HOME,'state':'active','trigger':{'kind':'manual'},'steps':[{'step_id':'routine','action_type':'local_routine','authority_site_id':HOME,'target_site_id':HOME,'action_key':'routine','required_permissions':['device_control']}]},actor=owner)
run=fa.create_run({'automation_id':'fa:execute','idempotency_key':'execute-1'},actor=owner);dispatch=fa.create_step_dispatch(run['run_id'],'routine',actor=owner)
with ThreadPoolExecutor(2) as pool:replayed=list(pool.map(lambda _:fa.create_step_dispatch(run['run_id'],'routine',actor=owner),range(2)))
assert all(x['dispatch_id']==dispatch['dispatch_id'] for x in replayed),'Dispatch creation minted duplicate execution attempts'
entered=threading.Event();release=threading.Event();original=local.run_routine;calls=0
def controlled(*args,**kwargs):
 global calls
 calls+=1;entered.set();assert release.wait(5);return original(*args,**kwargs)
local.run_routine=controlled
with ThreadPoolExecutor(2) as pool:
 first=pool.submit(fa.execute_step_dispatch,dispatch,executor_device_id=DEVICE,permission_grants=['device_control'],actor=owner)
 assert entered.wait(5)
 try:fa.execute_step_dispatch(dispatch,executor_device_id=DEVICE,permission_grants=['device_control'],actor=owner);raise AssertionError('Concurrent executor duplicated dispatch')
 except fa.FederatedAutomationError as exc:assert exc.status_code==409
 finally:release.set()
 receipt=first.result()
assert calls==1 and receipt['status']=='completed'
assert fa.execute_step_dispatch(dispatch,executor_device_id=DEVICE,permission_grants=['device_control'],actor=owner)==receipt
assert calls==1 and devices.list_actions(10)==[], 'Dispatch bypassed pending owner approval'
local.run_routine=original
with db() as c:
 assert c.execute("SELECT COUNT(*) FROM tracky_federated_automation_execution_receipts WHERE dispatch_id=?",(dispatch['dispatch_id'],)).fetchone()[0]==1
 assert c.execute("SELECT COUNT(*) FROM tracky_federated_automation_events WHERE event_kind='step.executed'").fetchone()[0]==1
# Unknown outcome is retained for review rather than invoking a device again.
run=fa.create_run({'automation_id':'fa:execute','idempotency_key':'uncertain'},actor=owner);dispatch=fa.create_step_dispatch(run['run_id'],'routine',actor=owner);fa._claim_execution_dispatch(dispatch)
try:fa.execute_step_dispatch(dispatch,executor_device_id=DEVICE,permission_grants=['device_control'],actor=owner);raise AssertionError('Uncertain dispatch automatically repeated')
except fa.FederatedAutomationError as exc:assert exc.status_code==409
print('SECTION13_FEDERATED_EVENTS=PASS')
