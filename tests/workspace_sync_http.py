"""Production Python worker talking HTTP to the production PHP/PDO exchanger."""
import hashlib,json,os,socket,sqlite3,subprocess,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
CLOUD=Path(os.environ.get('VP3_CLOUD_SOURCE',str(ROOT.parent/'brain-sync-cloud')))
with tempfile.TemporaryDirectory(prefix='workspace-http-') as directory:
    os.environ['HOMESERVER_DATA_DIR']=directory+'/home'
    import httpx
    from app.database import initialize_database,db
    from app.services import workspace_sync as sync,local_transcription_sessions as transcripts
    from app.services.https_bridge_session import save_https_session
    from app.services.remote_identity import load_or_create_remote_identity
    initialize_database()
    with db() as connection:
        connection.execute("INSERT INTO paired_apps(app_key,name,token_hash,status) VALUES('vp3','VP3 Cloud',?,'active')",('f'*64,))
        app_id=connection.execute("SELECT id FROM paired_apps WHERE app_key='vp3'").fetchone()[0]
        for permission in ['knowledge.search','contacts.read','events.read','tasks.read','awareness.read','memory.read','agent.chat','notifications.read']:
            connection.execute('INSERT INTO app_permissions(paired_app_id,permission,allowed) VALUES(?,?,1)',(app_id,permission))
        connection.execute("INSERT INTO contacts(display_name,notes) VALUES('Home contact','Exact local note')")
        connection.execute('INSERT INTO knowledge_items(title,content) VALUES(?,?)',('Home document','Local 完整 💡 '*20000))
    sid=transcripts.start('HTTP transcript')['session']['id']
    transcripts.append(sid,'Automatically synchronized transcript','e'*32)
    transcripts.stop(sid)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    endpoint=f'http://127.0.0.1:{port}/api/homeserver-https-poll-v1300.php'
    save_https_session(endpoint,'a'*64)
    env={**os.environ,'VP3_WORKSPACE_FIXTURE_DIR':directory,'VP3_WORKSPACE_FIXTURE_DEVICE':load_or_create_remote_identity()['device_id']}
    with open(directory+'/php.log','w+') as log:
        server=subprocess.Popen([os.environ.get('PHP_BINARY','php'),'-S',f'127.0.0.1:{port}',str(CLOUD/'tests/workspace_sync_server.php')],cwd=CLOUD,env=env,stdout=log,stderr=log)
        try:
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                if server.poll() is not None:raise AssertionError('PHP fixture failed to start')
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=.2):break
                except OSError:time.sleep(.05)
            outcome=sync.sync_once();assert outcome.get('ok'),outcome
            assert len(sync.status()['cloud_datasets'])==16
            assert 'Cloud contact' in json.dumps(sync.records('contacts'))
            assert 'SKU-HTTP' in json.dumps(sync.records('products',detail_key='agent_commerce_products_v800:1'))
            detail=sync.records('knowledge',detail_key='knowledge_items:1')
            assert detail['items'][0]['data']['content_text']=='完整 💡 '*17000
            file=detail['attachments'][0];path,_=sync.asset(file['sha256'])
            assert path.read_bytes()==b'PDF original fixture '*100000
            assert hashlib.sha256(path.read_bytes()).hexdigest()==file['sha256']
            with sqlite3.connect(directory+'/cloud.sqlite') as cloud:
                body=cloud.execute("SELECT body_json FROM homeserver_workspace_snapshots_v1 WHERE user_id=1 AND source='homeserver' AND dataset='knowledge'").fetchone()[0]
                document=next(row for row in json.loads(body)['records'] if row['table']=='knowledge_items' and row['data'].get('title')=='Home document')
                assert document['data']['content']=='Local 完整 💡 '*20000
                transcript=cloud.execute("SELECT body_json FROM homeserver_workspace_snapshots_v1 WHERE user_id=1 AND source='homeserver' AND dataset='transcriptions'").fetchone()[0]
                assert 'Automatically synchronized transcript' in transcript
                assert cloud.execute("SELECT count(*) FROM homeserver_workspace_snapshots_v1 WHERE user_id=2").fetchone()[0]==0
                cloud.execute("UPDATE agent_commerce_products_v800 SET sku='SKU-UPDATED' WHERE id=1")
            assert sync.sync_once().get('ok')
            assert 'SKU-UPDATED' in json.dumps(sync.records('products',detail_key='agent_commerce_products_v800:1'))
            before=sync.status()['last_success_at']
            with sqlite3.connect(directory+'/cloud.sqlite') as cloud:cloud.execute("UPDATE homeserver_https_sessions SET status='revoked' WHERE user_id=1")
            assert not sync.sync_once().get('ok')
            assert sync.status()['last_success_at']==before
            print('Real Python/PHP HTTP, both-way full UTF-8 records, originals, account isolation, updates and revocation PASS')
        except Exception:
            log.flush();log.seek(0);print(log.read()[-4000:],file=sys.stderr);raise
        finally:
            server.terminate()
            try:server.wait(timeout=5)
            except subprocess.TimeoutExpired:server.kill();server.wait()
            (CLOUD/'uploads'/('workspace-http-'+Path(directory).name+'.bin')).unlink(missing_ok=True)
