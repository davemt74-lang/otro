"""Two independent HomeServer databases: real signatures, owner API and Hosting inbox.

Only the network boundary is a deterministic adapter. Production state, routes,
crypto, retries and notification pipelines are exercised without replacement.
"""
import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import threading
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def child(data):
    os.environ['HOMESERVER_DATA_DIR']=data
    from app.database import initialize_database,db
    from app.services import vrss as v,vrss_social as s,homeserver_app_prebuilt as packages,homeserver_apps
    initialize_database();packages.ensure_defaults()
    for line in sys.stdin:
        req=json.loads(line)
        try:
            cmd=req['cmd']
            if cmd=='configure':
                p=v.request({'action':'profile'})['profile']
                out=v.update_profile({'display_name':req['name'],'bio':'','enabled':True,'social_enabled':True,
                    'public_base':req['base'],'expected_revision':p['revision']})
            elif cmd=='actor':out=s.actor_document()
            elif cmd=='follow':out=s.follow(req['url'],lambda _:req['doc'])
            elif cmd=='receive':out=s.receive(json.dumps(req['packet']).encode(),lambda _:req['doc'])
            elif cmd=='packets':
                with v.database() as c:
                    out=[s.signed(c,json.loads(r[0])) for r in c.execute("SELECT payload FROM vrss_social_outbox WHERE state='pending' ORDER BY seq")]
            elif cmd=='deliver':
                sent=[]
                def sender(url,packet):
                    sent.append(packet)
                    if req.get('fail'):raise OSError('offline')
                    return {'contract':s.CONTRACT,'ok':True}
                s.deliver(sender);out=sent
            elif cmd=='lease_probe':
                entered=threading.Event();finish=threading.Event();errors=[];sent=[]
                def blocked_sender(url,packet):
                    entered.set();assert finish.wait(10);raise OSError('offline')
                def first():
                    try:s.deliver(blocked_sender)
                    except Exception as e:errors.append(e)
                worker=threading.Thread(target=first);worker.start();assert entered.wait(10)
                s.deliver(lambda url,packet:sent.append(packet))
                assert not sent,'Concurrent worker sent another activity for the leased peer'
                finish.set();worker.join(15);assert not worker.is_alive() and not errors
                out=True
            elif cmd=='cancel_inflight':
                blocked=[]
                def sender(url,packet):
                    if packet['activity']['type']=='Follow':
                        blocked.append(packet['activity']['id']);s.change(req['actor'],'block')
                    return {'contract':s.CONTRACT,'ok':True}
                with v.database() as c:c.execute("UPDATE vrss_social_outbox SET due=0,lease_until=0")
                s.deliver(sender)
                assert blocked
                with v.database() as c:
                    for key in blocked:assert c.execute('SELECT state FROM vrss_social_outbox WHERE id=?',(key,)).fetchone()[0]=='cancelled'
                out=True
            elif cmd=='age':
                with v.database() as c:c.execute("UPDATE vrss_social_outbox SET due=0,lease_until=0")
                out=True
            elif cmd=='change':out=s.change(req['actor'],req['action'])
            elif cmd=='status':out=s.status()
            elif cmd=='notices':s.deliver_notices();out=v.request({'action':'notices'})
            elif cmd=='subscriptions':out=v.request({'action':'subscriptions'})
            elif cmd=='rotate':out=s.rotate()
            elif cmd=='disable':
                with v.database() as c:c.execute('UPDATE vrss_profile SET social_enabled=0')
                out=True
            elif cmd=='enable':
                with v.database() as c:c.execute('UPDATE vrss_profile SET social_enabled=1')
                out=True
            elif cmd=='stop':homeserver_apps.transition(v.APP_KEY,'stopped');out=True
            elif cmd=='start':homeserver_apps.transition(v.APP_KEY,'running');out=True
            elif cmd=='read':out=v.request({'action':'notice.read','notice_id':req['id']})
            elif cmd=='forge':
                a=req['activity']
                with v.database() as c:out=s.signed(c,a)
            elif cmd=='missing_key':
                with v.database() as c:
                    row=c.execute('SELECT secret FROM vrss_social_identity WHERE id=1').fetchone()
                    c.execute('DELETE FROM vrss_social_identity WHERE id=1')
                try:
                    try:s.actor_document()
                    except v.VRSSError as exc:assert exc.status_code==409
                    else:raise AssertionError('Missing key silently replaced')
                finally:
                    with v.database() as c:c.execute('INSERT INTO vrss_social_identity(id,secret) VALUES(1,?)',(row[0],))
                out=True
            elif cmd=='revoke_discovery':
                before=s.status()
                def loader(_):
                    with v.database() as c:c.execute('UPDATE vrss_profile SET social_enabled=0')
                    return req['doc']
                try:s.follow(req['url'],loader)
                except v.VRSSError as e:assert e.status_code==409
                else:raise AssertionError('Revocation during discovery committed')
                with v.database() as c:c.execute('UPDATE vrss_profile SET social_enabled=1')
                assert s.status()==before
                out=True
            elif cmd=='cloud_revoke':
                before=s.status();denied=[False]
                def guard():
                    if denied[0]:raise v.VRSSError('Cloud revoked',403)
                def loader(_):denied[0]=True;return req['doc']
                try:
                    with v.authority_guard(guard):s.follow(req['url'],loader)
                except v.VRSSError as e:assert e.status_code==403
                else:raise AssertionError('Late Cloud action committed')
                assert s.status()==before
                out=True
            elif cmd=='api':
                from fastapi.testclient import TestClient
                from app.runtime import app
                from app.security import OWNER_CONTROL_TOKEN
                client=TestClient(app)
                # Public route never substitutes an owner session for a valid signature.
                unauth=client.post('/api/v1/control/homeserver-apps/vrss/request',json={'action':'social.status'},headers={'X-VRSS-Request':'1'})
                assert unauth.status_code==401
                assert client.post('/__owner/session',headers={'X-HomeServer-Owner':OWNER_CONTROL_TOKEN}).status_code==200
                assert client.post('/api/v1/control/homeserver-apps/vrss/request',json={'action':'social.status'},headers={'X-VRSS-Request':'1'}).status_code==200
                assert client.post('/vrss/v1/inbox',content=b'x'*32769,headers={'Content-Type':'application/json'}).status_code==413
                assert client.post('/vrss/v1/inbox',content='{}',headers={'Content-Type':'text/plain'}).status_code==415
                with patch.object(s,'peer_document',return_value=req['doc']):
                    r=client.post('/vrss/v1/inbox',json=req['packet'])
                assert r.status_code==202,r.text
                assert 'secret' not in client.get('/vrss/v1/actor').text
                out=r.json()
            elif cmd=='hosting':
                from vrss_v1_hosting import configure_hosting
                from fastapi.testclient import TestClient
                from app.runtime import app
                _,site,_,route=configure_hosting()
                with v.database() as c:c.execute('UPDATE vrss_profile SET public_base=?,hosting_site_id=?',('https://dave.example.test',site['site_id']))
                client=TestClient(app)
                edge={'X-VP3-Route-Token':route['route_token'],'X-Forwarded-Host':'dave.example.test','X-Forwarded-Proto':'https','Content-Type':'application/json'}
                assert client.get('/__vp3/hosting/vrss/v1/actor',headers=edge).status_code==200
                packet=req['packet'];packet['activity']['recipient']='https://dave.example.test/vrss/v1/actor'
                # Test caller provides a freshly signed envelope for this hosted recipient.
                with patch.object(s,'peer_document',return_value=req['doc']):
                    r=client.post('/__vp3/hosting/vrss/v1/inbox',json=packet,headers=edge)
                assert r.status_code==202,r.text
                assert client.post('/__vp3/hosting/api/v1/control/homeserver-apps/vrss/request',json={},headers=edge).status_code in {404,405}
                assert client.post('/__vp3/hosting/vrss/v1/inbox',content=b'x'*32769,headers=edge).status_code==413
                assert client.post('/__vp3/hosting/vrss/v1/inbox',json=packet,headers={**edge,'X-VP3-Route-Token':'bad'}).status_code==403
                out=r.json()
            else:raise AssertionError(cmd)
            print(json.dumps({'ok':True,'out':out}),flush=True)
        except Exception as e:
            print(json.dumps({'ok':False,'error':str(e),'status':getattr(e,'status_code',None)}),flush=True)


class Server:
    def __init__(self,data):
        self.data=data;self.process=None;self.start()
    def start(self):
        self.process=subprocess.Popen([sys.executable,__file__,'--child',self.data],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    def call(self,cmd,expect=None,**kw):
        self.process.stdin.write(json.dumps({'cmd':cmd,**kw})+'\n');self.process.stdin.flush()
        r=json.loads(self.process.stdout.readline())
        if expect:
            assert not r['ok'] and r['status']==expect,r
            return r
        assert r['ok'],r
        return r['out']
    def close(self):
        self.process.stdin.close();self.process.wait(timeout=15)
    def restart(self):self.close();self.start()


def main():
    with tempfile.TemporaryDirectory(prefix='vrss-two-servers-') as data:
        a=Server(data+'/a');b=Server(data+'/b')
        try:
            a.call('configure',name='Alice',base='https://alice.example.test')
            b.call('configure',name='Bob',base='https://bob.example.test')
            ad=a.call('actor');bd=b.call('actor');assert ad['identity']!=bd['identity'] and ad['public_key']!=bd['public_key']
            a.call('missing_key');a.call('revoke_discovery',url=bd['id'],doc=bd);a.call('cloud_revoke',url=bd['id'],doc=bd)
            assert a.call('follow',url=bd['id'],doc=bd)['state']=='pending'
            assert a.call('follow',url=bd['id'],doc=bd)['replayed']
            follow=a.call('packets')[0]
            assert b.call('receive',packet=follow,doc=ad)['ok']
            assert b.call('receive',packet=follow,doc=ad)['replayed']
            assert b.call('status')['followers']==1 and a.call('status')['following']==0
            accept=b.call('packets')[0]
            a.call('receive',packet=accept,doc=bd)
            assert a.call('status')['following']==1 and len(a.call('subscriptions')['subscriptions'])==1
            notices=b.call('notices')['notices'];assert len(notices)==1 and notices[0]['title']=='New VRSS follower'
            b.call('notices');assert len(b.call('notices')['notices'])==1
            b.call('read',id=notices[0]['id']);assert not b.call('notices')['notices']
            # Stable key/identity, active follows and receipts survive process restart.
            b.restart();assert b.call('actor')==bd and b.call('status')['followers']==1
            assert b.call('receive',packet=follow,doc=ad)['replayed']
            # Exact activity identity cannot be reused with another body, even by its valid signer.
            changed=copy.deepcopy(follow['activity']);changed['seq']+=100
            bad=a.call('forge',activity=changed);b.call('receive',packet=bad,doc=ad,expect=409)
            tampered=copy.deepcopy(follow);tampered['activity']['type']='Undo'
            b.call('receive',packet=tampered,doc=ad,expect=403)
            bad=copy.deepcopy(follow);bad['sent_at']-=1000;b.call('receive',packet=bad,doc=ad,expect=422)
            # A retry leases one durable record, persists its backoff, and expires crash leases.
            a.call('lease_probe');jobs=a.call('status')['deliveries'];assert jobs[0]['attempts']==1 and jobs[0]['due']>time.time()
            assert not a.call('deliver',fail=True)
            a.restart();assert a.call('status')['deliveries'][0]['attempts']==1
            a.call('age');a.call('deliver');assert not a.call('status')['deliveries']
            # Signer rotation chains preserve pinning; unsigned replacement fails closed.
            a.call('rotate');newad=a.call('actor');assert newad['identity']==ad['identity'] and newad['public_key']!=ad['public_key']
            a.call('change',actor=bd['id'],action='unfollow');undo=a.call('packets')[-1]
            b.call('receive',packet=undo,doc=newad);assert b.call('status')['followers']==0
            assert b.call('receive',packet=follow,doc=newad,expect=403) # old-key transport signature is invalid after rotation
            assert not a.call('subscriptions')['subscriptions']
            a.call('follow',url=bd['id'],doc=bd);follow2=a.call('packets')[-1]
            invalid=copy.deepcopy(newad);invalid['rotations']=[];invalid['public_key']=ad['public_key']
            b.call('receive',packet=follow2,doc=invalid,expect=403)
            b.call('receive',packet=follow2,doc=newad);assert b.call('status')['followers']==1
            # Blocking removes both directions, quiets undelivered notices and prevents retries.
            b.call('change',actor=newad['id'],action='block');assert b.call('status')['followers']==0
            b.call('receive',packet=follow2,doc=newad,expect=403)
            b.call('change',actor=newad['id'],action='unblock')
            # A new activity ID cannot revive a Follow issued before block/removal.
            stale=copy.deepcopy(follow2['activity']);stale.update(id=stale['id'].rsplit('/',1)[0]+'/'+str(uuid.uuid4()),seq=100)
            b.call('receive',packet=a.call('forge',activity=stale),doc=newad,expect=409)
            # Revocation during network identity discovery is rechecked before any commit.
            a.call('disable');a.call('receive',packet=accept,doc=bd,expect=409);a.call('enable')
            a.call('stop');a.call('deliver',expect=409);a.call('start')
            # Production owner/public API and canonical-host routing use the same verifier.
            a.call('change',actor=bd['id'],action='unfollow')
            a.call('follow',url=bd['id'],doc=bd);follow3=a.call('packets')[-1]
            b.call('api',packet=follow3,doc=newad)
            hosted=copy.deepcopy(follow3['activity']);hosted.update(recipient='https://dave.example.test/vrss/v1/actor',seq=101,id=hosted['id'].rsplit('/',1)[0]+'/'+str(uuid.uuid4()))
            b.call('hosting',packet=a.call('forge',activity=hosted),doc=newad)
            a.call('cancel_inflight',actor=bd['id'])
            print('PASS V2C two independent servers: Follow/Accept, real Ed25519 signatures, pinning/rotation, exact retries, restart/backoff, Undo/block/unblock/replay fences, owner API, public signed inbox and existing Hosting isolation')
        finally:a.close();b.close()
    # Production HTTPS transport guards, including POST redirect rejection and DNS pinning.
    os.environ['HOMESERVER_DATA_DIR']=tempfile.mkdtemp(prefix='vrss-transport-')
    from app.services import vrss_social as s
    requests=[];responses=[]
    class Response:
        def __init__(self,status=200,body=b'{}'):self.status=status;self.body=io.BytesIO(body)
        def getheader(self,name,default=None):return 'application/json' if name=='Content-Type' else default
        def read(self,n):return self.body.read(n)
    class Connection:
        def __init__(self,host,**kw):self.host=host
        def request(self,method,path,body=None,headers=None):requests.append((method,path,headers));self._create_connection((self.host,443),8)
        def getresponse(self):return responses.pop(0)
        def close(self):pass
    with patch.object(s.socket,'getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443))]),patch.object(s.socket,'create_connection',return_value=object()) as connect,patch.object(s.http.client,'HTTPSConnection',Connection):
        responses.append(Response(body=b'{"ok":true}'));assert s.exchange('https://news.example/vrss/v1/inbox',{'a':1})['ok']
        assert connect.call_args.args[0]==('8.8.8.8',443) and requests[-1][0]=='POST'
        responses.append(Response(302))
        try:s.exchange('https://news.example/vrss/v1/inbox',{})
        except s.v.VRSSError:pass
        else:raise AssertionError('POST redirect was followed')
    with patch.object(s.socket,'getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):
        try:s.exchange('https://news.example/vrss/v1/actor')
        except s.v.VRSSError as e:assert e.status_code==422
        else:raise AssertionError('Private destination accepted')
    print('PASS V2C production pinned HTTPS POST transport, no redirects or private network destinations')

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--child':child(sys.argv[2])
    else:main()
