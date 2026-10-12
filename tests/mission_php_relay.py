"""Deterministic auth/transport fixtures around the actual Cloud PHP mission endpoint."""
import json, os, shutil, socket, subprocess, threading, time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request,urlopen

class CloudRelay:
    def __init__(self, cloud_root, data, dispatch):
        self.cloud_root=Path(cloud_root);self.data=Path(data);self.dispatch=dispatch
    def __enter__(self):
        dispatch=self.dispatch
        class Bridge(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                result=dispatch(body['operation'],body['payload'])
                encoded=json.dumps(result).encode();self.send_response(200)
                self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(encoded)))
                self.end_headers();self.wfile.write(encoded)
        self.bridge=ThreadingHTTPServer(('127.0.0.1',0),Bridge)
        threading.Thread(target=self.bridge.serve_forever,daemon=True).start()
        root=self.data/'cloud';(root/'api').mkdir(parents=True);(root/'includes').mkdir()
        for name in ('agent-missions-cloud-v1.php','extension-integration-diagnostics-v1.php'):
            shutil.copyfile(self.cloud_root/'api'/name,root/'api'/name)
        shutil.copyfile(self.cloud_root/'includes/agent-integration-diagnostics-v1.php',root/'includes/agent-integration-diagnostics-v1.php')
        (root/'VP3_RELEASE.json').write_text(json.dumps({'mission_lifecycle_section':'A5C11','minimum_mission_schema':79,'commit':'a'*40}))
        (root/'includes/bootstrap.php').write_text("""<?php
function require_permission($p){}
function has_permission($p,$u){return true;}
function current_user(){return ['id'=>12];}
function csrf_token(){return 'certification';}
""")
        (root/'includes/extension-device-auth-v2001.php').write_text("""<?php
function vp3_extension_apply_cors_v2001(){}
function vp3_extension_session_authenticate_v2001($pdo){return ($_SERVER['HTTP_AUTHORIZATION']??'')==='Bearer fixture-device-token'?['user_id'=>12,'capabilities'=>['agent.message']]:null;}
function vp3_extension_session_has_capability_v2001($session,$cap){return in_array($cap,$session['capabilities'],true);}
function vp3_extension_user_for_permission_v2001($pdo,$uid){return ['id'=>$uid];}
""")
        (root/'includes/homeserver-https-relay-v1300.php').write_text("""<?php
function db(){static $pdo=null;if(!$pdo){$pdo=new PDO('sqlite::memory:',null,null,[PDO::ATTR_DEFAULT_FETCH_MODE=>PDO::FETCH_ASSOC]);$pdo->exec('CREATE TABLE homeserver_https_requests(public_id TEXT,status TEXT,response_status INTEGER,response_json TEXT)');}return $pdo;}
function homeserver_https_v1300_status($user){return ['connected'=>true];}
function homeserver_https_v1300_queue($user,$op,$payload){
 $context=stream_context_create(['http'=>['method'=>'POST','header'=>'Content-Type: application/json','content'=>json_encode(['operation'=>$op,'payload'=>$payload]),'timeout'=>20]]);
 $r=json_decode(file_get_contents(getenv('A5C11_BRIDGE'),false,$context),true,64,JSON_THROW_ON_ERROR);
 $id=bin2hex(random_bytes(16));db()->prepare('INSERT INTO homeserver_https_requests VALUES(?,?,?,?)')->execute([$id,$r['ok']?'completed':'failed',$r['status'],json_encode($r['payload'])]);return $id;
}
function homeserver_https_v1300_remote_operation($user,$op,$payload=[]){return homeserver_https_v1300_wait(homeserver_https_v1300_queue($user,$op,$payload));}
""")
        relay=(self.cloud_root/'includes/homeserver-https-relay-v1300.php').read_text()
        start=relay.index('function homeserver_https_v1300_wait(');end=relay.index('function homeserver_https_v1300_remote_operation(',start)
        with (root/'includes/homeserver-https-relay-v1300.php').open('a') as stream:stream.write(relay[start:end])
        with socket.socket() as reserve:reserve.bind(('127.0.0.1',0));self.port=reserve.getsockname()[1]
        self.log=(self.data/'php.log').open('w')
        self.process=subprocess.Popen([shutil.which('php'),'-S','127.0.0.1:'+str(self.port),'-t',str(root)],env={**os.environ,'A5C11_BRIDGE':'http://127.0.0.1:'+str(self.bridge.server_port)},stdout=self.log,stderr=self.log)
        for _ in range(100):
            try:
                with socket.create_connection(('127.0.0.1',self.port),timeout=.1):break
            except OSError:time.sleep(.02)
        else:raise AssertionError('PHP relay did not start')
        return self
    def request(self,action,body):
        req=Request('http://127.0.0.1:'+str(self.port)+'/api/agent-missions-cloud-v1.php',data=json.dumps({'action':action,'csrf_token':'certification',**body}).encode(),headers={'Content-Type':'application/json'})
        return self.read(req)
    def extension_diagnostic(self,authenticated=True):
        req=Request('http://127.0.0.1:'+str(self.port)+'/api/extension-integration-diagnostics-v1.php',headers={'Authorization':'Bearer fixture-device-token' if authenticated else '','X-VP3-Contract-Version':'1'})
        return self.read(req)
    def read(self,req):
        try:response=urlopen(req,timeout=60)
        except HTTPError as error:response=error
        with response:return {'status':response.status,'ok':200<=response.status<300,'payload':json.load(response)}
    def __exit__(self,*unused):
        self.process.terminate();self.process.wait(timeout=10);self.log.close();self.bridge.shutdown();self.bridge.server_close()
