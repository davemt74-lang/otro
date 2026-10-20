"""V2B production SQLite/parser/transport/Agent acceptance with deterministic remote documents."""
import io
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

def rss(*ids):
    return ('<rss version="2.0"><channel><title>Followed site</title><link>https://news.example/</link><description>Updates</description>'+''.join('<item><guid>'+str(i)+'</guid><title>Post '+str(i)+'</title></item>' for i in ids)+'</channel></rss>').encode()

def document(body,url='https://news.example/rss',**extra):
    return {'body':body,'url':url,'status':200,'etag':'"version-1"','last_modified':'Mon, 19 Oct 2026 12:00:00 GMT',**extra}

with tempfile.TemporaryDirectory(prefix='vrss-v2b-') as data:
    os.environ['HOMESERVER_DATA_DIR']=data
    from app.database import initialize_database,db
    from app.services import vrss as v,homeserver_app_prebuilt as packages,homeserver_app_control as control,activity_center,homeserver_apps
    initialize_database();packages.ensure_defaults()
    def reject(fn,status):
        try:fn()
        except Exception as e:
            assert getattr(e,'status_code',None)==status,(e,status)
        else:raise AssertionError('Operation should be rejected')
    html=b'<html><head><link rel="alternate" type="application/rss+xml" href="/rss"></head></html>'
    seen=[]
    def loader(url,**kwargs):
        seen.append(url)
        return document(html,url) if url.endswith('/') else document(rss(1),url)
    assert v.discover('https://news.example/',loader)['url']=='https://news.example/rss'
    assert len(seen)==2
    assert v.discover('https://news.example/rss',loader)['body']==rss(1)
    def json_loader(url,**kwargs):
        return document(json.dumps({'contract':v.CONTRACT,'rss':'/rss'}).encode(),url) if url.endswith('/profile') else document(rss(1),url)
    assert v.discover('https://news.example/profile',json_loader)['url'].endswith('/rss')
    rejects=b'<link rel="alternate" type="application/rss+xml" href="http://127.0.0.1/private"><link rel="alternate" type="application/rss+xml" href="http://news.example/rss">'
    seen=[]
    def unsafe(url,**kwargs):seen.append(url);return document(rejects,url)
    reject(lambda:v.discover('https://news.example/',unsafe),422)
    assert seen==['https://news.example/']
    with patch.object(v,'fetch_document',loader):
        sub=v.subscribe('https://news.example/')['subscription_id']
    assert not v.request({'action':'notices'})['notices'],'Initial archive must be quiet'
    # Upgrade preserves canonical identity; manifests are genuinely executable through governed control.
    spec=control.manifest(v.APP_KEY)
    assert len(spec['actions'])==14 and spec['compatible']
    assert {'vrss.social.like','vrss.social.reactions'} <= {row['key'] for row in spec['actions']}
    assert control.invoke(v.APP_KEY,'vrss.status',{},read_only=True)['result']['new_post_notifications']
    reject(lambda:control.invoke(v.APP_KEY,'vrss.refresh_all',{},read_only=True),409)
    assert control.invoke(v.APP_KEY,'vrss.refresh_all',{},confirmed=True)['result']['queued']==1
    def age():
        with v.database() as c:c.execute("UPDATE vrss_subscriptions SET checked_at='',next_check_at=0,lease_until=0")
    age()
    with patch.object(v,'fetch_document',return_value=document(rss(1,2))) as fetch:
        changed=v.refresh(sub)
    assert changed['new']==1
    assert fetch.call_args.args[1]['etag']=='"version-1"'
    notices=v.request({'action':'notices'})['notices'];assert len(notices)==1
    with db() as c:
        n=dict(c.execute('SELECT * FROM notifications WHERE id=?',(notices[0]['id'],)).fetchone())
    assert n['event_key']=='vrss.new_posts' and n['source_key']==v.APP_KEY
    assert json.loads(n['action_json'])['app_key']==v.APP_KEY
    age();v.refresh(sub,lambda _:document(rss(1,2)))
    assert len(v.request({'action':'notices'})['notices'])==1,'Replay duplicated notice'
    age();v.refresh(sub,lambda _:document(b'',status=304,etag=''))
    assert v.request({'action':'subscriptions'})['subscriptions'][0]['etag']=='"version-1"'
    assert len(v.request({'action':'feed'})['feed']['items'])==2
    v.request({'action':'notice.read','notice_id':notices[0]['id']})
    assert not v.request({'action':'notices'})['notices']
    # A restart/retry of an unsent notification uses the existing durable dedupe key.
    age()
    with patch.object(activity_center,'emit_notification',side_effect=RuntimeError('offline')):
        v.refresh(sub,lambda _:document(rss(1,2,3)))
    with v.database() as c:assert c.execute('SELECT count(*) FROM vrss_notices WHERE delivered=0').fetchone()[0]==1
    v.deliver_notices();v.deliver_notices()
    assert len(v.request({'action':'notices'})['notices'])==1
    # Stable receipts survive cache eviction, so a source replay cannot announce old posts again.
    age();v.refresh(sub,lambda _:document(rss(*range(4,104))))
    with v.database() as c:assert not c.execute('SELECT 1 FROM vrss_items WHERE item_key=?',(v.parse_feed(rss(1),'https://news.example/rss')['items'][0]['key'],)).fetchone()
    age();assert v.refresh(sub,lambda _:document(rss(1)))['new']==0
    # Backoff persists in SQLite, and no due fetch occurs before its deadline.
    age()
    def fail(_):raise v.VRSSError('Remote source unavailable',502)
    reject(lambda:v.refresh(sub,fail),502)
    state=v.request({'action':'subscriptions'})['subscriptions'][0]
    assert state['failures']==1 and state['next_check_at']>time.time()+800 and not state['lease_until']
    with patch.object(v,'fetch_document') as fetch:assert v.refresh_due()==[] and not fetch.called
    # One network lease per source, even if the one-minute manual throttle has expired.
    age();entered=threading.Event();finish=threading.Event();errors=[]
    def blocking(_):entered.set();assert finish.wait(10);return document(rss(104))
    def run():
        try:v.refresh(sub,blocking)
        except Exception as e:errors.append(e)
    thread=threading.Thread(target=run);thread.start();assert entered.wait(10)
    reject(lambda:v.refresh(sub,lambda _:document(rss(105))),429)
    # Unfollow during the network operation fences both imports and deferred notices.
    v.request({'action':'unsubscribe','id':sub});finish.set();thread.join(10)
    assert not thread.is_alive() and len(errors)==1 and errors[0].status_code==409
    assert not v.request({'action':'subscriptions'})['subscriptions'] and not v.request({'action':'notices'})['notices']
    with v.database() as c:
        assert not c.execute('SELECT 1 FROM vrss_seen').fetchone() and not c.execute('SELECT 1 FROM vrss_notices').fetchone()
    # Real transport: conditional headers stay on their exact URL, redirects repin, 304 requires validators.
    responses=[];requests=[]
    class Response:
        def __init__(self,status,headers=None,body=b''):self.status=status;self.headers=headers or {};self.body=io.BytesIO(body)
        def getheader(self,key,default=None):return self.headers.get(key,default)
        def read(self,size):return self.body.read(size)
    class Connection:
        def __init__(self,host,**kw):self.host=host
        def request(self,method,path,headers):requests.append((self.host,headers));self._create_connection((self.host,443),8)
        def getresponse(self):return responses.pop(0)
        def close(self):pass
    with patch.object(v.socket,'getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443))]),patch.object(v.socket,'create_connection',return_value=object()),patch.object(v.http.client,'HTTPSConnection',Connection):
        url='https://news.example/rss';validators={'validator_url':url,'etag':'"x"','last_modified':'Mon, 19 Oct 2026 12:00:00 GMT'}
        responses[:]=[Response(304)]
        assert v.fetch_document(url,validators)['status']==304
        assert requests[-1][1]['If-None-Match']=='"x"'
        responses[:]=[Response(302,{'Location':'https://other.example/rss'}),Response(200,body=rss(1))]
        v.fetch_document(url,validators)
        assert 'If-None-Match' not in requests[-1][1] and 'If-Modified-Since' not in requests[-1][1]
        responses[:]=[Response(304)]
        reject(lambda:v.fetch_document(url),502)
    # Actual paired-app bridge enforces the current Cloud-export scope before control reads/writes.
    from app.services import pairing,remote_bridge,app_scopes
    paired=pairing.create_pairing_request('vp3','VP3',['agent.chat','notifications.read'])
    pairing.approve_pairing_request(paired['request_id'])
    token=paired['claim_token'];principal=pairing.authenticate(token)
    payload={'app_key':'vp3.feed','action':'vrss.status','arguments':{},'confirmed':False}
    assert remote_bridge.dispatch_remote_request('apps.control.invoke',payload,token)['status']==200
    app_scopes.save_scope(principal['id'],{'cloud_allowed':False})
    assert remote_bridge.dispatch_remote_request('apps.control.invoke',payload,token)['status']==403
    payload['app_key']=' vp3.feed '
    assert remote_bridge.dispatch_remote_request('apps.control.invoke',payload,token)['status']==403
    app_scopes.save_scope(principal['id'],{'cloud_allowed':True})
    payload.update(app_key='vp3.feed',action='vrss.refresh_all')
    assert remote_bridge.dispatch_remote_request('apps.control.invoke',payload,token)['status']==409
    payload.update(action='vrss.follow',arguments={'url':'https://revoked.example/'},confirmed=True)
    def revoke_during_discovery(url,**kwargs):
        app_scopes.save_scope(principal['id'],{'cloud_allowed':False})
        return document(rss(1),url)
    with patch.object(v,'fetch_document',revoke_during_discovery):
        assert remote_bridge.dispatch_remote_request('apps.control.invoke',payload,token)['status']==403
    assert not v.request({'action':'subscriptions'})['subscriptions'],'Late Cloud action survived scope revocation'
    app_scopes.save_scope(principal['id'],{'cloud_allowed':True})
    # Crash lease expires without changing IDs; stopped Feed cannot poll.
    sub=v.subscribe('https://news.example/rss',lambda _:document(rss(1)))['subscription_id'];age()
    with v.database() as c:c.execute('UPDATE vrss_subscriptions SET lease_until=?,next_check_at=?',(time.time()-1,time.time()-1))
    assert v.refresh_due(lambda _:document(rss(1,2)))[0]['new']==1
    homeserver_apps.transition(v.APP_KEY,'stopped');reject(lambda:v.refresh_due(),409)
    print('PASS VRSS V2B discovery, guarded conditional HTTP, durable refresh/backoff/leases, stable receipts, quiet initial import, replay-safe Chat notices, real governed app actions, unsubscribe and stopped-app fences')
