"""Production SQLite/outbox with bounded paired transport and replay/race cases."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix='participant-memory-') as folder:
    os.environ['HOMESERVER_DATA_DIR']=folder
    import httpx
    from app.database import initialize_database, db
    from app.services import workspace_sync as sync, participant_memory_sync as memory
    from app.services.https_bridge_session import save_https_session, load_https_session
    initialize_database()
    with db() as connection:
        connection.execute("INSERT INTO paired_apps(app_key,name,token_hash,status) VALUES('vp3','VP3',?,'active')",('f'*64,))
        app=connection.execute("SELECT id FROM paired_apps WHERE app_key='vp3'").fetchone()[0]
        for permission in ['contacts.read','memory.read']:
            connection.execute('INSERT INTO app_permissions(paired_app_id,permission,allowed) VALUES(?,?,1)',(app,permission))
    save_https_session('http://127.0.0.1/api/homeserver-https-poll-v1300.php','a'*64)
    peer='http://127.0.0.1|1'
    with db() as connection:connection.execute('UPDATE workspace_sync_settings SET peer_id=?,session_hash=? WHERE id=1',(peer,hashlib.sha256(('a'*64).encode()).hexdigest()))
    session=load_https_session();checks=0;remote={};failure=False;swap=False;race=None;requests=[]
    def row(key,name,note='',deleted=False):
        result={'key':key,'id':'7','name':'' if deleted else name,'note':'' if deleted else note,'deleted':deleted}
        result['revision']=hashlib.sha256(json.dumps([key,result['name'],result['note'],deleted],ensure_ascii=False,separators=(',',':')).encode()).hexdigest();return result
    def serve(request):
        global race
        assert request.headers['Authorization']=='Bearer '+'a'*64
        body=json.loads(request.content);requests.append(body);assert body['action']=='participants'
        if failure:return httpx.Response(503,json={'ok':False})
        results=[]
        for change in body['changes']:
            existing=remote.get(change['key']);desired=row(change['key'],change['name'],change['note'],change['deleted'])
            if existing and existing['revision']!=change['expected_revision'] and existing!=desired:
                results.append({'change_id':change['change_id'],'status':'conflict','participant':existing})
            else:
                remote[change['key']]=desired;results.append({'change_id':change['change_id'],'status':'applied','participant':desired})
        if race:callback=race;race=None;callback()
        if swap:save_https_session(session['endpoint'],'b'*64)
        return httpx.Response(200,json={'ok':True,'contract':sync.CONTRACT,'account_id':'1','participants':list(remote.values()),'results':results,'next_after':0})
    client=httpx.Client(transport=httpx.MockTransport(serve))
    def check(ok,name):
        global checks
        assert ok,name;checks+=1;print('PASS '+name)
    def stage(key,name,note='',rev='',resolve=False,deleted=False,local='local-1'):
        return memory.stage({'peer_id':peer,'changes':[{'key':key,'local_id':local,'expected_revision':rev,'name':name,'note':note,'deleted':deleted,'resolve':resolve}]})
    def rejects(callback,name):
        try:callback()
        except sync.WorkspaceSyncError:check(True,name);return
        raise AssertionError(name)
    key='a'*32
    stage(key,'Alex','Remember concise replies')
    check(len(memory.page()['pending'])==1,'owner edit is durable before network delivery')
    failure=True;memory.deliver(client,session,peer)
    check(len(memory.page()['pending'])==1 and memory.page()['error'],'offline failure retains pending text and reports retry status')
    failure=False;memory.deliver(client,session,peer)
    check(not memory.page()['pending'] and memory.page()['participants'][0]['note']=='Remember concise replies','successful receipt commits canonical text and removes only applied edit')
    baseline=remote[key]['revision'];remote[key]=row(key,'Cloud Alex','New Cloud note')
    stage(key,'Offline Alex','Offline note',baseline);memory.deliver(client,session,peer)
    check(memory.page()['pending'][0]['status']=='conflict' and remote[key]['name']=='Cloud Alex','concurrent Cloud changes preserve both versions')
    stage(key,'Accidental overwrite','wrong',baseline)
    check(memory.page()['pending'][0]['change']['name']=='Offline Alex','automatic retry cannot resolve a conflict')
    stage(key,'Offline Alex','Offline note',remote[key]['revision'],True);memory.deliver(client,session,peer)
    check(remote[key]['note']=='Offline note' and not memory.page()['pending'],'explicit keep-local resolution uses current revision')
    baseline=remote[key]['revision'];stage(key,'Newer edit','Newer local note',baseline)
    race=lambda:stage(key,'An edit during delivery','Preserve me',baseline)
    memory.deliver(client,session,peer)
    check(memory.page()['pending'][0]['change']['note']=='Preserve me','late receipt cannot remove a newer queued edit')
    memory.deliver(client,session,peer);pending=memory.page()['pending'][0]
    rejects(lambda:memory.discard({'peer_id':peer,'key':key,'change_id':'0'*32}),'stale use-Cloud resolution requires a fresh review')
    check(len(memory.page()['pending'])==1,'stale use-Cloud action cannot discard a newer local edit')
    memory.discard({'peer_id':peer,'key':key,'change_id':pending['change']['change_id']})
    check(not memory.page()['pending'],'use-Cloud discards exactly the reviewed edit')
    stage(key,'','',remote[key]['revision'],deleted=True);memory.deliver(client,session,peer)
    check(remote[key]['deleted'] and remote[key]['note']=='','deletion carries an empty text tombstone')
    rejects(lambda:memory.stage({'peer_id':'https://other.invalid|2','changes':[]}),'foreign account queue submission rejected')
    rejects(lambda:memory.stage({'peer_id':peer,'changes':[{'key':key,'embeddings':[[1,2]]}]}),'sample-bearing submission rejected')
    other='b'*32;stage(other,'Pending')
    swap=True;memory.deliver(client,session,peer);swap=False
    with db() as connection:count=connection.execute('SELECT COUNT(*) FROM participant_memory_outbox WHERE profile_key=?',(other,)).fetchone()[0]
    check(count==1,'pairing change during network delivery prevents receipt commit')
    rejects(memory.page,'unverified replacement pairing cannot read previous participant memory')
    save_https_session(session['endpoint'],'a'*64)
    from app.services import app_scopes
    app_scopes.save_scope(app,{'cloud_allowed':True,'memory_key_prefixes':['unrelated:']})
    rejects(memory.page,'restricted memory namespace cannot export participant notes')
    app_scopes.save_scope(app,{'cloud_allowed':True})
    with db() as connection:connection.execute("UPDATE app_permissions SET allowed=0 WHERE permission='memory.read'")
    rejects(memory.page,'revoked memory permission blocks participant reads')
    with db() as connection:connection.execute("UPDATE app_permissions SET allowed=1 WHERE permission='memory.read'")
    for i in range(105):stage(f'{i:032x}','Same name',local=f'local-{i+100}')
    first=memory.page();second=memory.page(first['next_after'])
    check(first['next_after'] and not second['next_after'] and len(first['pending'])<=100 and len(second['pending'])<=100 and len(first['pending'])+len(second['pending'])==106,'offline pending edits use bounded cursor pages without losing entries')
    check(all(set(change)<={'key','name','note','deleted','expected_revision','change_id'} for request in requests for change in request['changes']),'paired transport never sends local IDs, images, audio or enrollment')
    client.close();print(f'PARTICIPANT_MEMORY_SYNC=PASS ({checks} cases)')
