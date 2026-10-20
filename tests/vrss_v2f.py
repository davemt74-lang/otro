"""Independent production stores and real signatures for attributed signed reposts."""
import copy
import hashlib
import json
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from vrss_v2c import Server

nonce=lambda:str(uuid.uuid4())
with tempfile.TemporaryDirectory(prefix='vrss-reposts-') as data:
    a,b=Server(data+'/a'),Server(data+'/b')
    try:
        a.call('configure',name='Alice',base='https://alice.example.test')
        b.call('configure',name='Bob <script>untrusted</script>',base='https://bob.example.test')
        ad,bd=a.call('actor'),b.call('actor')
        assert bd['reposts']['enabled'] is False
        a.call('follow',url=bd['id'],doc=bd)
        b.call('receive',packet=a.call('packets')[0],doc=ad)
        a.call('receive',packet=b.call('packets')[0],doc=bd)
        a.call('deliver');b.call('deliver')
        post=b.call('post',body='ORIGINAL CONTENT MUST STAY WITH BOB')['post_id']
        obj='https://bob.example.test/vrss/v1/posts/'+post
        b.call('repost_public',post_id=post,expect=404)
        a.call('repost_set',url=obj,enabled=True,request_id=nonce(),documents={bd['id']:bd},expect=409)
        b.call('repost_optin',enabled=True);bd=b.call('actor')
        def docs(key=''):
            d={bd['id']:bd,obj+'/reposts':b.call('repost_public',post_id=post)}
            if key:d[obj+'/reposts/id-'+hashlib.sha256(key.encode()).hexdigest()]=b.call('repost_public',post_id=post,selector='id-'+hashlib.sha256(key.encode()).hexdigest())
            return d
        for boundary in ['cloud','follow']:
            a.call('repost_revoke',url=obj,actor=bd['id'],boundary=boundary,documents=docs())
        request=nonce();made=a.call('repost_set',url=obj,enabled=True,request_id=request,documents=docs())
        key=made['repost']['url'];packet=a.call('packets')[0]
        assert made['repost']['pending'] and not made['repost']['published']
        assert key not in a.call('repost_rss')
        assert a.call('repost_set',url=obj,enabled=True,request_id=request,documents={})['replayed']
        a.call('repost_set',url=obj,enabled=False,request_id=request,expect=409)
        wrong=copy.deepcopy(packet);wrong['signature']='00'*64
        b.call('receive',packet=wrong,doc=ad,expect=403)
        wrong=copy.deepcopy(packet['activity']);wrong.update(id=wrong['id'].rsplit('/',1)[0]+'/'+nonce(),repost_id='https://other.example.test/vrss/v1/reposts/'+nonce())
        b.call('receive',packet=a.call('forge',activity=wrong),doc=ad,expect=422)
        ack=b.call('receive',packet=packet,doc=ad)
        assert ack['repost']['active'] and ack['repost']['reposts']==1
        assert b.call('receive',packet=packet,doc=ad)['replayed']
        a.call('repost_ack',result=ack)
        assert a.call('repost_summary',url=obj)['published']
        pointer=a.call('repost_pointer',id=key,documents=docs(key))
        assert pointer['original']=={'url':obj,'actor':bd['id'],'identity':bd['identity'],'name':bd['name']}
        assert 'ORIGINAL CONTENT MUST STAY WITH BOB' not in json.dumps(pointer)
        assert pointer['media']==[] and set(pointer['repost'])=={'object','reposts','published'}
        rss=a.call('repost_rss');tree=ET.fromstring(rss)
        entry=next(x for x in tree.findall('channel/item') if x.findtext('guid')==key)
        assert entry.findtext('link')==key and obj in entry.findtext('description')
        assert entry.findtext('source')==bd['name'] and 'ORIGINAL CONTENT' not in rss
        assert len([x for x in a.call('repost_feed',mode='profile')['feed']['items'] if x.get('original')])==1
        notices=[n for n in b.call('notices')['notices'] if n['title']=='New VRSS repost']
        assert len(notices)==1
        meta=json.loads(notices[0]['action_json']);assert meta['verified_repost'] and meta['repost']==key and meta['receipt']==hashlib.sha256(key.encode()).hexdigest()
        a.restart();b.restart()
        assert a.call('repost_set',url=obj,enabled=True,request_id=request,documents={})['replayed']
        assert a.call('repost_pointer',id=key,documents=docs(key))['original']['url']==obj
        assert not a.call('repost_failcheck',url=obj)['published']
        a.call('repost_read',url=obj,documents=docs(key))
        assert a.call('repost_summary',url=obj)['published']
        malformed=docs(key);malformed[obj+'/reposts/id-'+hashlib.sha256(key.encode()).hexdigest()]['identity']=ad['identity']
        a.call('repost_read',url=obj,documents=malformed,expect=422)
        assert not a.call('repost_summary',url=obj)['published']
        a.call('repost_read',url=obj,documents=docs(key))
        assert not a.call('repost_failure_race',url=obj,documents=docs(key))['published']
        a.call('repost_read',url=obj,documents=docs(key))
        assert not a.call('repost_read_race',url=obj,documents=docs(key))['published']
        assert key not in a.call('repost_rss')
        undo=next(p for p in a.call('packets') if p['activity']['type']=='UndoRepost')
        cleanup=b.call('receive',packet=undo,doc=ad);assert cleanup['repost']['active'] is False
        a.call('repost_ack',result=cleanup)
        assert b.call('repost_public',post_id=post)['reposts']==0
        assert not [n for n in b.call('notices')['notices'] if n['title']=='New VRSS repost']
        resurrect=copy.deepcopy(packet['activity']);resurrect.update(id=resurrect['id'].rsplit('/',1)[0]+'/'+nonce(),seq=undo['activity']['seq']+1,issued_at=time.time())
        b.call('receive',packet=a.call('forge',activity=resurrect),doc=ad,expect=409)
        assert b.call('receive',packet=packet,doc=ad)['repost']['active'] is False
        made=a.call('repost_set',url=obj,enabled=True,request_id=nonce(),documents=docs());key2=made['repost']['url'];assert key2!=key
        packet2=next(p for p in a.call('packets') if p['activity']['type']=='Repost')
        ack2=b.call('receive',packet=packet2,doc=ad);a.call('repost_ack',result=ack2)
        assert a.call('repost_pointer',id=key2,documents=docs(key2))['repost']['published']
        assert not a.call('repost_set',url=obj,enabled=True,request_id=request,documents={})['repost']['pending']
        extra=b.call('post')['post_id'];extra_obj='https://bob.example.test/vrss/v1/posts/'+extra
        extra_docs={bd['id']:bd,extra_obj+'/reposts':b.call('repost_public',post_id=extra)}
        a.call('repost_set',url=extra_obj,enabled=True,request_id=nonce(),documents=extra_docs)
        extra_packet=next(p for p in a.call('packets') if p['activity']['type']=='Repost')
        b.call('repost_fill_history',kind='inbox')
        b.call('receive',packet=extra_packet,doc=ad,expect=409)
        assert b.call('repost_public',post_id=extra)['reposts']==0
        a.call('repost_set',url=extra_obj,enabled=False,request_id=nonce())
        b.call('change',actor=ad['id'],action='remove')
        assert b.call('repost_public',post_id=post)['reposts']==0
        assert not a.call('repost_read',url=obj,documents=docs(key2))['published']
        a.call('repost_pointer',id=key2,documents=docs(key2),expect=404)
        removed=a.call('repost_set',url=obj,enabled=False,request_id=nonce(),documents={})
        assert not removed['repost']['reposted']
        undo2=next(p for p in a.call('packets') if p['activity']['type']=='UndoRepost' and p['activity']['object']==obj)
        b.call('receive',packet=undo2,doc=ad)
        b.call('repost_delete_post',post_id=post)
        b.call('repost_public',post_id=post,expect=404)
        private=b.call('post',visibility='private')['post_id'];b.call('repost_public',post_id=private,expect=404)
        own=a.call('post')['post_id'];a.call('repost_set',url='https://alice.example.test/vrss/v1/posts/'+own,enabled=True,request_id=nonce(),expect=422)
        a.call('repost_set',url='https://bob.example.test/vrss/v1/reposts/'+nonce(),enabled=True,request_id=nonce(),expect=422)
        print('PASS V2F real signatures/stores: consent, attribution-only RSS, identity/follow binding, replay fences, exact ACK, restart, failed-source/stale-success races, removal, private/deleted posts, history bounds and verified notices')
    finally:a.close();b.close()

with tempfile.TemporaryDirectory(prefix='vrss-repost-ack-') as data:
    a,b=Server(data+'/a'),Server(data+'/b')
    try:
        a.call('configure',name='Alice',base='https://alice.example.test');b.call('configure',name='Bob',base='https://bob.example.test')
        b.call('repost_optin',enabled=True);ad,bd=a.call('actor'),b.call('actor')
        a.call('follow',url=bd['id'],doc=bd);b.call('receive',packet=a.call('packets')[0],doc=ad);a.call('receive',packet=b.call('packets')[0],doc=bd);a.call('deliver');b.call('deliver')
        post=b.call('post')['post_id'];obj='https://bob.example.test/vrss/v1/posts/'+post
        docs={bd['id']:bd,obj+'/reposts':b.call('repost_public',post_id=post)}
        a.call('repost_set',url=obj,enabled=True,request_id=nonce(),documents=docs)
        result=a.call('repost_cancel_ack',url=obj,documents=docs)
        assert not result['published'] and not result['reposted'] and result['reposts']!=999
        assert not a.call('repost_feed',mode='profile')['feed']['items']
        a.call('repost_fill_history',kind='requests')
        a.call('repost_set',url=obj,enabled=True,request_id=nonce(),documents=docs,expect=409)
        assert not a.call('repost_summary',url=obj)['reposted']
        print('PASS V2F cancelled in-flight ACK cannot restore an undone repost or corrupt counts')
    finally:a.close();b.close()
