from __future__ import annotations
import json,os,sys,tempfile,threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
tmp=tempfile.TemporaryDirectory(prefix='vp3-section13-');os.environ['HOMESERVER_DATA_DIR']=tmp.name;os.environ['VP3_OS_HARDWARE_ADAPTER']='disabled'
from app.database import db,initialize_database
from app.services import approvals,local_automation as local,room_device_automation as devices
from app.services import agent_workflow_automation as automation,agent_workflow_automation_runtime as runtime
initialize_database()
def count(table,where='1'):
    with db() as c:return c.execute(f'SELECT COUNT(*) FROM {table} WHERE {where}').fetchone()[0]
def rejects(fn):
    try:fn()
    except Exception:return
    raise AssertionError('Expected mutation to reject')
devices.upsert_room('office','Office');devices.upsert_provider('fixture','Fixture','test',executable=True,status='connected')
devices.upsert_device('lamp','fixture','lamp','Lamp','light',room_key='office',controllable=True,state={'power':'off'})
local.upsert_routine('two','Two',approval_mode='ask_every_time',steps=[{'device_key':'lamp','command':'on','arguments':{}},{'device_key':'lamp','command':'off','arguments':{}}])
original=approvals.create_device_command_request;calls=0
def fail_second(*a,**k):
    global calls
    calls+=1
    if calls==2:raise RuntimeError('forced second-step failure')
    return original(*a,**k)
before=count('action_requests');approvals.create_device_command_request=fail_second
rejects(lambda:local.run_routine('two',source_kind='fixture'));assert count('action_requests')==before,'Partial routine left approval requests'
assert count('automation_rule_executions')==0,'Failed routine committed an execution'
approvals.create_device_command_request=original
local.upsert_rule('daily','Daily',routine_key='two',trigger_kind='daily',trigger={'hour':8,'minute':0},cooldown_seconds=0)
due=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
with db() as c:c.execute('UPDATE automation_rules SET next_run_at=? WHERE rule_key=?',(due,'daily'))
calls=0;approvals.create_device_command_request=fail_second
rejects(lambda:local.evaluate_rule('daily'));assert local.get_rule('daily')['next_run_at']==due,'Failed rule consumed its trigger'
assert count('action_requests')==before
approvals.create_device_command_request=original
with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:local.evaluate_rule('daily'),range(2)))
assert sum(bool(x['fired']) for x in results)==1,'Concurrent daily trigger fired twice'
assert count('action_requests')==before+2
assert devices.list_actions(10)==[],'Rule bypassed owner approval'
# The actual SQLite timestamps use a space, while scheduler cutoffs use ISO T.
local.update_settings(enabled=True,poll_seconds=15,max_actions_per_run=12,max_rule_fires_per_minute=1)
local.upsert_rule('manual','Manual',routine_key='two',trigger_kind='manual',cooldown_seconds=0)
assert local.evaluate_rule('manual',force_manual=True)['reason']=='rate_limit','Timestamp formats bypassed the rate limit'
with db() as c:
    rid=local.get_routine('two')['id']
    sid=c.execute("INSERT INTO automation_app_suggestions(routine_id,app_key,action_key,arguments_json,risk,source_kind) VALUES (?,'fixture','on','{}','write','fixture')",(rid,)).lastrowid
def real_pending_request(*a,**k):return original('automation:suggestion',{'device_key':'lamp','command':'on','arguments':{}},owner=True)
saved_app_request=approvals.create_app_action_request;approvals.create_app_action_request=real_pending_request
with db() as c:c.execute("CREATE TRIGGER fail_suggestion BEFORE UPDATE ON automation_app_suggestions BEGIN SELECT RAISE(ABORT,'receipt failure'); END;")
before=count('action_requests');rejects(lambda:local.decide_app_suggestion(sid,'accept'));assert count('action_requests')==before,'Failed decision left an approval request'
with db() as c:c.execute('DROP TRIGGER fail_suggestion')
def decide(_):
    try:return local.decide_app_suggestion(sid,'accept')['decision']
    except local.LocalAutomationError as e:assert e.status_code==409;return 'stale'
with ThreadPoolExecutor(2) as pool:outcomes=list(pool.map(decide,range(2)))
assert sorted(outcomes)==['accept','stale'] and count('action_requests')==before+1
approvals.create_app_action_request=saved_app_request
# Production run claims and completion writes: an older lease cannot write back.
now=datetime.now(timezone.utc)
with db() as c:
    agent=c.execute('SELECT id,name FROM agents WHERE is_primary=1 LIMIT 1').fetchone()
    c.execute("INSERT INTO conversations(id,agent_id,source_app_key,title,status) VALUES ('lease',?,'owner','Lease','active')",(agent['id'],))
    plan=c.execute("INSERT INTO agent_team_plans(source_app_key,conversation_id,parent_agent_id,parent_agent_name,objective,members_json,context_json,permission_snapshot_json,cloud_used,status,decided_at) VALUES ('owner','lease',?,?,'Lease','[]','{}','[]',0,'approved',CURRENT_TIMESTAMP)",(agent['id'],agent['name'])).lastrowid
    aid=c.execute("INSERT INTO agent_workflow_automations(source_app_key,conversation_id,plan_id,trigger_type,trigger_config_json,next_run_at,max_steps) VALUES ('owner','lease',?,'interval','{\"every_seconds\":300}',?,1)",(plan,(now-timedelta(seconds=1)).isoformat())).lastrowid
claim=automation._claim_time(aid,now);old=runtime._reserve_run(claim['run_id'],now,allow_stale_running=False)
new=runtime._reserve_run(claim['run_id'],now+timedelta(seconds=61),allow_stale_running=True)
assert old and new and old['lease_stamp']!=new['lease_stamp']
assert automation._finish_run(old,status='error',error='late',disable=True) is False,'Old lease overwrote takeover'
with db() as c:
    assert c.execute('SELECT status FROM agent_workflow_automation_runs WHERE id=?',(claim['run_id'],)).fetchone()[0]=='running'
    assert c.execute('SELECT enabled FROM agent_workflow_automations WHERE id=?',(aid,)).fetchone()[0]==1
with db() as c:c.execute("CREATE TRIGGER fail_finished_notification BEFORE INSERT ON notifications BEGIN SELECT RAISE(ABORT,'notification failure'); END;")
rejects(lambda:automation._finish_run(new,status='completed'))
with db() as c:
    assert c.execute('SELECT status FROM agent_workflow_automation_runs WHERE id=?',(claim['run_id'],)).fetchone()[0]=='running','Notification failure committed completion'
    c.execute('DROP TRIGGER fail_finished_notification')
assert automation._finish_run(new,status='completed') is True
before=count('notifications');assert automation._finish_run(new,status='error',disable=True) is False
assert count('notifications')==before,'Repeated completion duplicated notification'
later=now+timedelta(seconds=301);pending=automation._claim_time(aid,later)
reserved=runtime._reserve_run(pending['run_id'],later,allow_stale_running=False)
automation.set_automation_enabled('owner',aid,False,now=later)
assert automation._finish_run(reserved,status='completed') is False,'Disabled automation accepted a late result'
with db() as c:
    assert c.execute('SELECT last_status FROM agent_workflow_automations WHERE id=?',(aid,)).fetchone()[0]=='disabled'
    assert c.execute('SELECT status FROM agent_workflow_automation_runs WHERE id=?',(pending['run_id'],)).fetchone()[0]=='conflict'
# Stop must retain a busy thread reference so Start cannot create a second worker.
class BusyThread:
    def is_alive(self):return True
    def join(self,timeout=None):pass
fake=BusyThread();local._THREAD=fake;local.stop();local.start();assert local._THREAD is fake and local._STOP.is_set()
local._THREAD=None;local._STOP.clear()
scheduler=runtime.WorkflowAutomationRuntimeScheduler();scheduler._thread=fake;scheduler.stop();scheduler.start();assert scheduler._thread is fake and scheduler._stop.is_set()
print('SECTION13_EVENTS_AUTOMATION=PASS')
