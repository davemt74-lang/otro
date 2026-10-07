"""An unavailable workspace endpoint cannot corrupt the relay restart fixture."""
from pathlib import Path
import json,os,socket,subprocess,sys,tempfile,time
import httpx

ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='relay-routes-') as directory:
    root=Path(directory)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    config=root/'config.json';marker=root/'marker.json';progress=root/'progress.txt';restart=root/'restart.flag'
    config.write_text(json.dumps({'local_token':'L'*64,'session_token':'S'*64}),encoding='utf-8')
    env={**os.environ,'HOMESERVER_HTTPS_RECONNECT_PORT':str(port),'HOMESERVER_HTTPS_RECONNECT_CONFIG':str(config),'HOMESERVER_HTTPS_RECONNECT_MARKER':str(marker),'HOMESERVER_HTTPS_RECONNECT_PROGRESS':str(progress),'HOMESERVER_HTTPS_RECONNECT_RESTART_FLAG':str(restart)}
    with (root/'relay.log').open('wb') as log:
        server=subprocess.Popen([sys.executable,str(ROOT/'tests/mock_https_relay_reconnect.py')],env=env,stdout=log,stderr=log)
        try:
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                if server.poll() is not None:raise AssertionError('Relay fixture exited before startup')
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=.2):break
                except OSError:time.sleep(.05)
            with httpx.Client(base_url=f'http://127.0.0.1:{port}',trust_env=False,timeout=3,headers={'Authorization':'Bearer '+'S'*64,'X-VP3-HomeServer-Session':'S'*64,'X-HomeServer-Device':'hs-route-fixture'}) as client:
                def workspace():
                    response=client.post('/homeserver-workspace-sync-v1.php',json={'contract':'vp3.workspace-sync.v1','action':'catalog'})
                    assert response.status_code==503 and not response.json()['ok']
                def poll(results=[]):
                    response=client.post('/poll',json={'version':'2.4','capabilities':{},'results':results})
                    assert response.status_code==200,response.text
                    return response.json()
                workspace();assert not marker.exists() and progress.read_text(encoding='utf-8')=='listening'
                ping=poll()['requests'][0];assert ping['operation']=='system.ping'
                poll([{'request_id':ping['request_id'],'ok':True,'payload':{'pong':True,'echo':'restart-proof-v22','version':'2.4','app':'vp3'}}])
                assert progress.read_text(encoding='utf-8')=='ping-complete'
                workspace();assert not marker.exists() and progress.read_text(encoding='utf-8')=='ping-complete'
                restart.write_text('restart-now',encoding='utf-8')
                shared=poll()['requests'][0];assert shared['operation']=='shared.context.exchange'
                workspace();assert not marker.exists() and progress.read_text(encoding='utf-8')=='shared-sent'
                poll([{'request_id':shared['request_id'],'ok':True,'payload':{'version':'2.2','cloud_mirror':{'revision':'restart-cloud-revision-1'},'homeserver_snapshot':{'authoritative_source':'homeserver','datasets':{key:[] for key in ['memory','knowledge','contacts','tasks','calendar','files','notifications']}}}}])
                result=json.loads(marker.read_text(encoding='utf-8'))
                assert result['ok'] and result['ping_verified'] and result['shared_verified']
                assert result['workspace_failures']==3 and result['polls']==4
            print('PASS unavailable workspace endpoint preserves authenticated ping, restart and complete shared-context proof')
        finally:
            server.terminate()
            try:server.wait(timeout=10)
            except subprocess.TimeoutExpired:server.kill();server.wait()
