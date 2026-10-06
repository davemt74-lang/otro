"""Real SQLite plus the production background transport; no native executors replaced."""
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix='workspace-sync-') as folder:
    os.environ['HOMESERVER_DATA_DIR']=folder
    import httpx
    from app.database import initialize_database,db
    from app.services import workspace_sync as sync,local_transcription_sessions as transcripts,app_scopes,knowledge_collections
    from app.services.https_bridge_session import save_https_session,load_https_session
    initialize_database()
    with db() as connection:
        connection.execute("INSERT INTO paired_apps(app_key,name,token_hash,status) VALUES('vp3','VP3 Cloud',?,'active')",('f'*64,))
        app_id=connection.execute("SELECT id FROM paired_apps WHERE app_key='vp3'").fetchone()[0]
        for permission in ['knowledge.search','contacts.read','events.read','tasks.read','awareness.read','memory.read','agent.chat','notifications.read']:
            connection.execute('INSERT INTO app_permissions(paired_app_id,permission,allowed) VALUES(?,?,1)',(app_id,permission))
    endpoint='http://127.0.0.1/api/homeserver-https-poll-v1300.php'
    token='a'*64
    save_https_session(endpoint,token)
    asset=b'%PDF fixture\n'+bytes(range(256))*5000
    digest=hashlib.sha256(asset).hexdigest()
    content='Complete document 💡\n'+'中文 evidence\n'*18000
    files=[{'asset_id':'d'*64,'record_key':'knowledge_items:42','name':'own-document.pdf','sha256':digest,'size_bytes':len(asset),'field':'file_path'}]
    def document(dataset,rows=None,attachments=None):
        value={'contract':sync.CONTRACT,'source':'cloud','dataset':dataset,'records':rows or [],'files':attachments or []}
        return json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()
    exports={dataset:document(dataset) for dataset in sync.DATASETS}
    exports['knowledge']=document('knowledge',[{'table':'knowledge_items','source_id':'42','data':{'title':'Knowledge record','content_text':content,'created_at':'2026-10-06 19:49:00'}}],files)
    exports['products']=document('products',[{'table':'agent_commerce_products_v800','source_id':'7','data':{'title':'My product','price_cents':2500,'sku':'SKU-7'}}])
    received={};stages={};calls=[];account='1';failure=None;swap=False;asset_calls=0
    def serve(request):
        global asset_calls
        assert str(request.url)=='http://127.0.0.1/api/homeserver-workspace-sync-v1.php'
        assert request.headers['Authorization']=='Bearer '+load_https_session()['session_token']
        body=json.loads(request.content);calls.append(body)
        action=body['action'];dataset=body.get('dataset')
        out={'ok':True,'contract':sync.CONTRACT,'account_id':account}
        if action=='catalog':
            out.update(datasets=list(sync.DATASETS),homeserver=[{'dataset':key,'revision':value['revision']} for key,value in received.items()])
        elif action=='prepare':
            if failure=='outage' and dataset=='calendar':return httpx.Response(503,json={'ok':False})
            raw=exports[dataset];out.update(revision=hashlib.sha256(raw).hexdigest(),byte_count=len(raw),record_count=len(json.loads(raw)['records']))
        elif action=='pull':
            raw=exports[dataset];offset=body['offset'];chunk=raw[offset:offset+sync.CHUNK_BYTES]
            if failure=='corrupt' and dataset=='knowledge':chunk=b'x'+chunk[1:]
            out.update(revision=body['revision'],offset=offset,total_bytes=len(raw),chunk=base64.b64encode(chunk).decode())
            if swap and dataset=='knowledge' and offset+sync.CHUNK_BYTES>=len(raw):save_https_session(endpoint,'b'*64)
        elif action=='asset':
            asset_calls+=1;offset=body['offset'];chunk=asset[offset:offset+sync.ASSET_CHUNK_BYTES]
            if failure=='asset-corrupt':chunk=b'x'+chunk[1:]
            out.update(revision=body['revision'],asset_id=body['asset_id'],sha256=digest,offset=offset,total_bytes=len(asset),chunk=base64.b64encode(chunk).decode())
        elif action=='push':
            chunks=stages.setdefault((dataset,body['revision']),{});chunks[body['offset']]=base64.b64decode(body['chunk'],validate=True)
            raw=b''.join(chunks[key] for key in sorted(chunks));complete=len(raw)==body['total_bytes']
            if complete:
                assert hashlib.sha256(raw).hexdigest()==body['revision'];received[dataset]={'revision':body['revision'],'body':raw}
            out.update(revision=body['revision'],committed=complete)
        else:raise AssertionError(action)
        return httpx.Response(200,json=out)
    client=httpx.Client(transport=httpx.MockTransport(serve))
    assert sync.settings()['enabled'] is True
    first=sync.sync_once(client);assert first.get('ok'),first
    state=sync.status();assert state['state']=='synced' and len(state['cloud_datasets'])==len(sync.DATASETS)
    details=sync.records('knowledge',detail_key='knowledge_items:42')
    assert details['items'][0]['data']['content_text']==content
    listing=sync.records('knowledge');assert len(json.dumps(listing).encode())<3000 and 'data' not in listing['items'][0]
    path,name=sync.asset(digest);assert path.read_bytes()==asset and name=='own-document.pdf'
    assert sync.records('products')['items'][0]['title']=='My product'
    assert received['knowledge'] and sync.status()['last_success_at']
    asset_calls_before=asset_calls
    sync.sync_once(client);assert asset_calls==asset_calls_before,'Unchanged verified attachments are not transferred again'
    with db() as connection:before=connection.execute("SELECT body_json FROM workspace_sync_snapshots WHERE dataset='knowledge'").fetchone()[0]
    # Changed content with damaged packet cannot replace the usable document.
    exports['knowledge']=document('knowledge',[{'table':'knowledge_items','source_id':'42','data':{'title':'Changed','content_text':content+' appended'}}],files)
    failure='corrupt';last_success=sync.status()['last_success_at'];failed=sync.sync_once(client)
    assert not failed.get('ok') and sync.status()['last_success_at']==last_success
    with db() as connection:assert connection.execute("SELECT body_json FROM workspace_sync_snapshots WHERE dataset='knowledge'").fetchone()[0]==before
    failure=None;assert sync.sync_once(client)['ok']
    assert sync.records('knowledge')['items'][0]['title']=='Changed'
    # Missing/corrupt original file is verified and recovered without changing record IDs.
    path.write_bytes(b'bad');sync._ASSET_VERIFIED.clear();failure='asset-corrupt'
    assert not sync.sync_once(client)['ok']
    assert path.read_bytes()==b'bad' and not list(sync.asset_dir().glob('transfer-*'))
    failure=None;assert sync.sync_once(client)['ok'];assert path.read_bytes()==asset
    # Opt-out is immediate and does not start a background network request.
    call_count=len(calls);sync.set_enabled(False);assert sync.sync_once(client).get('skipped') and len(calls)==call_count
    sync.set_enabled(True)
    # Default future completed transcripts share text; explicit local-only and existing privacy remain.
    sid=transcripts.start('Automatic')['session']['id'];transcripts.append(sid,'Completed words','e'*32);assert transcripts.stop(sid)['session']['cloud_shared']
    private=transcripts.start('Private',cloud_sync=False)['session']['id'];transcripts.append(private,'Private words','f'*32);assert not transcripts.stop(private)['session']['cloud_shared']
    assert sync.sync_once(client)['ok']
    mirrored=json.loads(received['transcriptions']['body']);assert all(isinstance(row['source_id'],str) for row in mirrored['records']);assert 'Completed words' in str(mirrored) and 'Private words' not in str(mirrored)
    # Kind and collection scope changes are part of the outgoing authority generation.
    before_fingerprint=sync._authority_hash()
    app_scopes.save_scope(app_id,{'knowledge_kinds':['note']})
    assert sync._authority_hash()!=before_fingerprint
    assert json.loads(sync.local_snapshot('transcriptions')['body'])['records']==[]
    app_scopes.save_scope(app_id,{})
    before_fingerprint=sync._authority_hash()
    knowledge_collections.set_app_collection_scope(app_id,['general'])
    assert sync._authority_hash()!=before_fingerprint
    knowledge_collections.set_app_collection_scope(app_id,[])
    transcripts.share_with_cloud(sid,False);assert sync.sync_once(client)['ok'];assert json.loads(received['transcriptions']['body'])['records']==[]
    # Fresh local permissions clear content rather than retaining an outgoing stale snapshot.
    with db() as connection:
        connection.execute("INSERT INTO contacts(display_name,notes) VALUES('Contact','Safe note')")
        connection.execute("UPDATE app_permissions SET allowed=0 WHERE paired_app_id=? AND permission='contacts.read'",(app_id,))
    assert sync.sync_once(client)['ok'];assert json.loads(received['contacts']['body'])['records']==[]
    # An accepted old token response cannot overwrite a replacement pairing.
    exports['knowledge']=document('knowledge',[{'table':'knowledge_items','source_id':'99','data':{'title':'Old token reply','content_text':content}}])
    swap=True;assert not sync.sync_once(client)['ok'];assert sync.records('knowledge')['items']==[]
    swap=False;account='2';exports['knowledge']=document('knowledge')
    assert sync.sync_once(client)['ok'];assert sync.records('knowledge')['items']==[]
    with db() as connection:assert not connection.execute("SELECT 1 FROM workspace_sync_snapshots WHERE peer_id LIKE '%|1'").fetchone()
    # A network outage cannot claim complete sync or damage existing categories.
    failure='outage';success=sync.status()['last_success_at'];assert not sync.sync_once(client)['ok'];assert sync.status()['last_success_at']==success
    failure=None;assert sync.sync_once(client)['ok']
    initialize_database() # fresh plus restart/idempotent migration
    assert sync.settings()['enabled'] is True
    client.close()
    print('Workspace auto sync, complete records/assets, replay/hash/outage, account replacement, privacy, permissions, deletes and restart PASS')
