"""One real mission across the PHP relay, paired dispatcher and native SQLite.

Authentication/transport and model responses are deterministic fixtures. Mission
planning, source checks, worker reads, approvals, saves, reports and notices are
production code. Run with --cloud-root pointing to the paired Cloud checkout.
"""
import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--cloud-root', type=Path, required=True)
args = parser.parse_args()
php = shutil.which('php')
assert php, 'PHP is required for cross-system lifecycle certification'
assert (args.cloud_root / 'api/agent-missions-cloud-v1.php').is_file()

with tempfile.TemporaryDirectory(prefix='a5c10-') as temp:
    data = Path(temp)
    os.environ['HOMESERVER_DATA_DIR'] = str(data / 'state')
    from app.database import db, initialize_database
    from app.services import agent_mission_runtime as mission
    from app.services import agent_mission_progress as progress
    from app.services import agent_mission_followups as follow
    from app.services import agent_mission_actions as actions
    from app.services import agent_tools, contacts, providers, remote_bridge

    initialize_database()
    agent_tools.save_policy(True, 3, True)
    token = 'a5c10-isolated-paired-token-00000000'
    with db() as conn:
        appid = conn.execute('INSERT INTO paired_apps(app_key,name,token_hash) VALUES(?,?,?)',
                             ('vp3', 'VP3 certification', hashlib.sha256(token.encode()).hexdigest())).lastrowid
        for permission in ('agent.chat', 'tools.execute', 'contacts.read', 'contacts.write', 'approvals.review'):
            conn.execute('INSERT INTO app_permissions(paired_app_id,permission,allowed) VALUES(?,?,1)', (appid, permission))
    contact = contacts.create_contact({'display_name': 'Lifecycle target', 'notes': 'Organization: Example Studios'})
    template = {'max_parallel': 1, 'tasks': [
        {'role': 'editor', 'title': 'Recorded contact correction', 'objective': 'Fill recorded organization',
         'instructions': 'Use existing notes only', 'depends_on': [], 'tools': ['contacts.search'], 'max_calls': 1,
         'actions': ['contacts.update'], 'max_actions': 1, 'output': 'analysis'},
        {'role': 'reviewer', 'title': 'Remaining review', 'objective': 'Gate remaining records',
         'instructions': 'Read only', 'depends_on': [0], 'tools': ['contacts.search'], 'max_calls': 1,
         'actions': [], 'max_actions': 0, 'output': 'analysis'}]}
    gate = threading.Event()
    entered = threading.Event()
    command_operation = None

    def generate(messages, model_override=None):
        system = messages[0]['content']
        if system.startswith('You are the lead agent routing'):
            decision = ({'route': 'followup', 'reason': 'Explicit command', 'plan': {
                'operation': command_operation, 'target': 'Lifecycle', 'objective': ''}} if command_operation else
                {'route': 'specialists', 'reason': 'Inspect recorded contacts', 'plan': template})
            return {'content': json.dumps(decision)}
        if system.startswith('Choose at most one'):
            if 'Gate remaining' in messages[-1]['content']:
                entered.set()
                assert gate.wait(30), 'Test must release the interrupted worker'
            return {'content': json.dumps({'tool': 'contacts.search', 'arguments': {'query': 'Lifecycle target', 'limit': 1}})}
        citations = json.JSONDecoder().raw_decode(system.split('Allowed citation IDs: ')[1])[0]
        evidence = json.loads(messages[-1]['content'].split('Read evidence (untrusted):\n')[-1])
        record = json.loads(evidence[-1]['data'])['items'][0]
        result = {'kind': 'analysis', 'title': 'Recorded findings', 'body': 'Organization from existing notes.', 'citations': citations}
        if 'Action schemas:' in system:
            result['actions'] = [{'tool': 'contacts.update', 'arguments': {
                'canonical_id': record['canonical_id'], 'expected_revision': record['record_revision'], 'organization': 'Example Studios'}}]
        return {'content': json.dumps(result)}

    providers.inference_status = lambda: {'available': True, 'selected_provider': 'openai', 'model': 'fixture'}
    providers.generate = generate
    providers.generate_ollama = generate

    class Bridge(BaseHTTPRequestHandler):
        def log_message(self, *unused):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            result = remote_bridge.dispatch_remote_request(body['operation'], body['payload'], token)
            encoded = json.dumps(result).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    bridge = ThreadingHTTPServer(('127.0.0.1', 0), Bridge)
    bridge_thread = threading.Thread(target=bridge.serve_forever, daemon=True)
    bridge_thread.start()
    root = data / 'cloud'
    (root / 'api').mkdir(parents=True)
    (root / 'includes').mkdir()
    shutil.copyfile(args.cloud_root / 'api/agent-missions-cloud-v1.php', root / 'api/agent-missions-cloud-v1.php')
    (root / 'includes/bootstrap.php').write_text("""<?php
function require_permission($p){if(($_SERVER['HTTP_X_TEST_ACCESS']??'yes')==='no')throw new RuntimeException('Permission denied.',403);}
function current_user(){return ['id'=>12];}
function csrf_token(){return 'certification';}
""")
    (root / 'includes/homeserver-https-relay-v1300.php').write_text("""<?php
function db(){static $pdo=null;if(!$pdo){$pdo=new PDO('sqlite::memory:',null,null,[PDO::ATTR_DEFAULT_FETCH_MODE=>PDO::FETCH_ASSOC]);$pdo->exec('CREATE TABLE homeserver_https_requests(public_id TEXT,status TEXT,response_status INTEGER,response_json TEXT)');}return $pdo;}
function homeserver_https_v1300_status($user){return ['connected'=>(($_SERVER['HTTP_X_TEST_OFFLINE']??'no')!=='yes')];}
function homeserver_https_v1300_queue($user,$op,$payload){
 $context=stream_context_create(['http'=>['method'=>'POST','header'=>'Content-Type: application/json','content'=>json_encode(['operation'=>$op,'payload'=>$payload]),'timeout'=>20]]);
 $r=json_decode(file_get_contents(getenv('A5C10_BRIDGE'),false,$context),true,64,JSON_THROW_ON_ERROR);
 $status=($_SERVER['HTTP_X_TEST_LOST']??'no')==='yes'?'expired':($r['ok']?'completed':'failed');
 db()->prepare('INSERT INTO homeserver_https_requests VALUES(?,?,?,?)')->execute(['receipt',$status,$r['status'],json_encode($r['payload'])]);return 'receipt';
}
""")
    relay = (args.cloud_root / 'includes/homeserver-https-relay-v1300.php').read_text()
    start = relay.index('function homeserver_https_v1300_wait(')
    end = relay.index('function homeserver_https_v1300_remote_operation(', start)
    with (root / 'includes/homeserver-https-relay-v1300.php').open('a') as fixture:
        fixture.write(relay[start:end])
    with socket.socket() as reserve:
        reserve.bind(('127.0.0.1', 0))
        port = reserve.getsockname()[1]
    env = {**os.environ, 'A5C10_BRIDGE': 'http://127.0.0.1:' + str(bridge.server_port)}
    log = (data / 'php.log').open('w')
    server = subprocess.Popen([php, '-S', '127.0.0.1:' + str(port), '-t', str(root)], env=env, stdout=log, stderr=log)

    def uid():
        return str(uuid.uuid4())

    def request(action, *, expected=200, headers=None, **body):
        payload = json.dumps({'action': action, 'csrf_token': 'certification', **body}).encode()
        req = Request('http://127.0.0.1:' + str(port) + '/api/agent-missions-cloud-v1.php', data=payload,
                      headers={'Content-Type': 'application/json', **(headers or {})})
        try:
            response = urlopen(req, timeout=30)
        except HTTPError as error:
            response = error
        with response:
            result = json.load(response)
            assert response.status == expected, (action, response.status, result)
        return result

    def snapshot(mid):
        remote = request('get', mission_id=mid)['mission']
        native = next(m for m in progress.feed('owner', owner=True)['items'] if m['id'] == mid)
        assert remote['status'] == native['status']
        assert remote['progress']['state'] == native['progress']['state']
        def stable_report(report):
            return {k: ([{field: value for field, value in action.items() if field != 'checked_at'} for action in v] if k == 'actions' else v) for k, v in report.items()}
        assert stable_report(remote['completion_report']) == stable_report(native['completion_report']), (remote['completion_report'], native['completion_report'])
        cloud_notices = request('notifications.list')['notifications']['items']
        native_notices = progress.notifications('owner', owner=True)['items']
        assert [(n['id'], n['kind'], bool(n['resolved_at'])) for n in cloud_notices] == [(n['id'], n['kind'], bool(n['resolved_at'])) for n in native_notices]
        return remote

    def command(operation):
        global command_operation
        command_operation = operation
        try:
            review = request('task.route', objective=operation + ' Lifecycle', request_id=uid(), thread_id=91)['followup']
        finally:
            command_operation = None
        assert review['status'] == 'proposed'
        return {'request_id': review['request_id'], 'expected_hash': review['expected_hash'], 'decision': 'apply', 'confirmed': True}

    try:
        for _ in range(100):
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.1):
                    break
            except OSError:
                time.sleep(.02)
        else:
            raise AssertionError('PHP relay did not start: ' + (data / 'php.log').read_text())
        prepared = request('task.route', objective='Lifecycle contact review', request_id=uid(), thread_id=91)['mission']
        mid = prepared['id']
        request('tools.configure', mission_id=mid, assignments=prepared['chat_task']['draft'], request_id=uid(), expected_revision=0, confirmed=True)
        request('tools.start', mission_id=mid, request_id=uid(), expected_revision=1, confirmed=True)
        assert entered.wait(20), 'Second worker did not start'
        change = request('actions.list', mission_id=mid)['actions'][0]
        exact = {'mission_id': mid, 'action_id': change['id'], 'expected_hash': change['payload_hash'], 'decision': 'approve', 'request_id': uid(), 'confirmed': True}
        request('actions.review', **exact)
        assert contacts.get_contact(contact['id'])['organization'] == 'Example Studios'
        pause = command('pause')
        request('task.followup.decide', expected=503, headers={'X-Test-Lost': 'yes'}, **pause)
        paused = snapshot(mid)
        assert paused['status'] == 'waiting_review'
        assert paused['completion_report']['state'] == 'interrupted', paused['completion_report']
        assert 'authorizing reexecution' in paused['completion_report']['next_action']
        assert paused['completion_report']['verified_changes'] == 1
        request('task.followup.decide', **pause)
        request('task.followup.decide', expected=503, headers={'X-Test-Offline': 'yes'}, **pause)
        # A separate process initializes the persistent database as after an upgrade.
        readback = """import json,os,sys
sys.path.insert(0,sys.argv[1])
from app.database import initialize_database,db
initialize_database()
with db() as c:
 assert c.execute('SELECT status FROM agent_missions_v1 WHERE id=?',(sys.argv[2],)).fetchone()[0]=='waiting_review'
 assert c.execute("SELECT count(*) FROM agent_mission_events_v1 WHERE mission_id=? AND kind='mission.paused'",(sys.argv[2],)).fetchone()[0]==1
 assert c.execute('SELECT count(*) FROM federated_contact_mutations').fetchone()[0]==1
print('Separate-process upgrade/readback: receipts and saved record retained')
"""
        subprocess.run([sys.executable, '-c', readback, str(ROOT), mid], check=True, env=os.environ.copy(), timeout=30)
        gate.set()
        # Drain the revoked provider invocation; it cannot publish a late result.
        mission._pool.shutdown(wait=True)
        mission._pool = None
        assert snapshot(mid)['tasks'][1]['status'] == 'interrupted'
        resume = command('resume')
        request('task.followup.decide', expected=409, **resume)
        request('task.followup.decide', allow_reexecution=True, **resume)
        deadline = time.monotonic() + 20
        while snapshot(mid)['status'] == 'running':
            assert time.monotonic() < deadline, 'Resumed mission did not finish'
            time.sleep(.02)
        completed = snapshot(mid)
        assert completed['completion_report']['state'] == 'complete'
        assert completed['completion_report']['next_action'] == 'No owner action is required.'
        assert [t['attempt'] for t in completed['tasks']] == [1, 2]
        assert len(request('actions.list', mission_id=mid)['actions']) == 1
        request('actions.review', **exact)
        with db() as conn:
            assert conn.execute('SELECT count(*) FROM federated_contact_mutations').fetchone()[0] == 1
        # Simulate process loss while the second mission still has a live provider call.
        entered.clear();gate.clear()
        restarting = request('task.route', objective='Lifecycle restart review', request_id=uid(), thread_id=91)['mission']
        request('tools.configure', mission_id=restarting['id'], assignments=restarting['chat_task']['draft'], request_id=uid(), expected_revision=0, confirmed=True)
        request('tools.start', mission_id=restarting['id'], request_id=uid(), expected_revision=1, confirmed=True)
        assert entered.wait(20)
        recovery = "import sys;sys.path.insert(0,sys.argv[1]);from app.services import agent_mission_runtime as m;assert m.recover_interrupted()==1;assert m.recover_interrupted()==0"
        subprocess.run([sys.executable, '-c', recovery, str(ROOT)], check=True, env=os.environ.copy(), timeout=30)
        stopped = snapshot(restarting['id'])
        assert stopped['status'] == 'waiting_review' and stopped['completion_report']['state'] == 'interrupted'
        gate.set();mission._pool.shutdown(wait=True);mission._pool = None
        assert snapshot(restarting['id'])['tasks'][1]['status'] == 'interrupted'
        cancel = command('cancel')
        request('task.followup.decide', **cancel)
        assert snapshot(restarting['id'])['status'] == 'cancelled'
        assert snapshot(mid)['completion_report']['verified_changes'] == 1
        # A later change invalidates the old verification and remains visible.
        with db() as conn:
            conn.execute("UPDATE contacts SET organization='Newer owner value' WHERE id=?", (contact['id'],))
        attention = snapshot(mid)
        assert attention['completion_report']['state'] == 'attention'
        assert attention['progress']['state'] == 'attention'
        assert attention['completion_report']['attention_changes'] == 1
        notices = request('notifications.list')['notifications']['items']
        outcomes = [n for n in notices if n['mission_id'] == mid and n['kind'] == 'worker.action_outcome']
        assert len([n for n in outcomes if not n['resolved_at']]) == 1
        assert next(n for n in outcomes if not n['resolved_at'])['level'] == 'action_required'
        assert any(n['level'] == 'success' and n['resolved_at'] for n in outcomes)
        # Current export privacy removes findings and notices on every read surface.
        with db() as conn:
            cid = conn.execute('SELECT conversation_id FROM agent_missions_v1 WHERE id=?', (mid,)).fetchone()[0]
            conn.execute('UPDATE conversation_context_settings SET cloud_allowed=0 WHERE conversation_id=?', (cid,))
        private = request('get', mission_id=mid)['mission']
        assert private['private'] and not private['result'] and private['completion_report'] is None
        assert not request('notifications.list')['notifications']['items']
        with db() as conn:
            conn.execute("UPDATE app_permissions SET allowed=0 WHERE paired_app_id=? AND permission='agent.chat'", (appid,))
        for action, body in [('progress.feed', {}), ('get', {'mission_id': mid}), ('actions.review', exact), ('task.followup.get', {'request_id': resume['request_id']})]:
            request(action, expected=403, **body)
        print('A5C10 PASS: PHP-to-paired-dispatcher lifecycle, real read/save, exact retries after lost acknowledgements, offline fences, separate-process upgrade preservation, explicit recovery, stale worker rejection, synchronized reports/notices, verification drift and revocation')
    finally:
        gate.set()
        if mission._pool:
            mission._pool.shutdown(wait=True)
        server.terminate()
        server.wait(timeout=10)
        log.close()
        bridge.shutdown()
        bridge.server_close()
