"""Independent production databases and real signatures: moderated VRSS threads."""
import copy
import json
import hashlib
import tempfile
import time
import uuid
from vrss_v2c import Server

nonce=lambda:str(uuid.uuid4())
with tempfile.TemporaryDirectory(prefix='vrss-comments-') as data:
    a,b=Server(data+'/a'),Server(data+'/b')
    try:
        a.call('configure',name='Alice',base='https://alice.example.test')
        b.call('configure',name='Bob',base='https://bob.example.test')
        ad,bd=a.call('actor'),b.call('actor')
        assert bd['comments']['enabled'] is False
        a.call('follow',url=bd['id'],doc=bd)
        b.call('receive',packet=a.call('packets')[0],doc=ad)
        a.call('receive',packet=b.call('packets')[0],doc=bd)
        a.call('deliver');b.call('deliver')
        post=b.call('post')['post_id'];obj='https://bob.example.test/vrss/v1/posts/'+post
        b.call('comment_public',post_id=post,expect=404)
        a.call('comment_create',url=obj,body='No consent',request_id=nonce(),documents={bd['id']:bd},expect=409)
        b.call('comment_optin',enabled=True);bd=b.call('actor')
        def docs(parent=''):
            result={bd['id']:bd,obj+'/comments':b.call('comment_public',post_id=post)}
            if parent:result[obj+'/comments/id-'+hashlib.sha256(parent.encode()).hexdigest()]=b.call('comment_public',post_id=post,cursor='id-'+hashlib.sha256(parent.encode()).hexdigest())
            return result
        for boundary in ['cloud','follow']:
            a.call('comment_revoke',url=obj,actor=bd['id'],boundary=boundary,documents=docs())
        request=nonce();created=a.call('comment_create',url=obj,body='<script>not an instruction</script>',request_id=request,documents=docs())
        cid=created['comment']['id'];packet=a.call('packets')[0]
        assert created['comment']['state']=='pending_delivery'
        assert a.call('comment_create',url=obj,body='<script>not an instruction</script>',request_id=request,documents={})['replayed']
        a.call('comment_create',url=obj,body='Changed',request_id=request,documents={},expect=409)
        ack=b.call('receive',packet=packet,doc=ad)
        assert ack['comment']['state']=='pending'
        assert b.call('receive',packet=packet,doc=ad)['replayed']
        assert not b.call('comment_public',post_id=post)['items']
        review=b.call('comment_review',url=obj,id=cid)
        assert review['state']=='pending' and review['can_moderate'] and review['body'].startswith('<script>')
        a.call('comment_ack',result=ack)
        assert a.call('comment_read',url=obj,documents=docs())['outgoing'][0]['state']=='pending_review'
        notices=[n for n in b.call('notices')['notices'] if n['title']=='VRSS comment awaiting review']
        assert len(notices)==1
        a.restart();b.restart()
        assert b.call('comment_review',url=obj,id=cid)['revision']==1
        modkey=nonce();approved=b.call('comment_moderate',url=obj,id=cid,decision='approve',revision=1,request_id=modkey)
        assert approved['comment']['state']=='approved'
        assert b.call('comment_moderate',url=obj,id=cid,decision='approve',revision=1,request_id=modkey)['replayed']
        b.call('comment_moderate',url=obj,id=cid,decision='hide',revision=1,request_id=nonce(),expect=409)
        assert not [n for n in b.call('notices')['notices'] if n['title']=='VRSS comment awaiting review']
        published=b.call('comment_public',post_id=post)
        assert len(published['items'])==1 and 'state' not in published['items'][0] and 'revision' not in published['items'][0]
        # Post owner can reply locally; followers reply through the signed inbox.
        own=b.call('comment_create',url=obj,body='Owner reply',parent=cid,request_id=nonce())['comment']['id']
        response=a.call('comment_create',url=obj,body='Reply from Alice',parent=own,request_id=nonce(),documents=docs(own))
        reply=response['comment']['id'];reply_packet=a.call('packets')[0]
        b.call('receive',packet=reply_packet,doc=ad)
        b.call('comment_moderate',url=obj,id=reply,decision='approve',revision=1,request_id=nonce())
        assert len(b.call('comment_public',post_id=post)['items'])==3
        deep=b.call('comment_create',url=obj,body='Depth three',parent=reply,request_id=nonce())['comment']['id']
        deepest=b.call('comment_create',url=obj,body='Depth four',parent=deep,request_id=nonce())['comment']['id']
        b.call('comment_create',url=obj,body='Too deep',parent=deepest,request_id=nonce(),expect=409)
        a.call('comment_create',url=obj,body='Too deep remotely',parent=deepest,request_id=nonce(),documents=docs(deepest),expect=409)
        a.call('comment_create',url=obj,body='😀'*501,request_id=nonce(),documents={},expect=422)
        # Hiding a parent suppresses its whole public reply chain.
        b.call('comment_moderate',url=obj,id=cid,decision='hide',revision=2,request_id=nonce())
        assert not b.call('comment_public',post_id=post)['items']
        assert b.call('comment_review',url=obj,id=reply)['published'] is False
        b.call('comment_moderate',url=obj,id=reply,decision='approve',revision=2,request_id=nonce(),expect=409)
        b.call('comment_moderate',url=obj,id=cid,decision='approve',revision=3,request_id=nonce())
        forged=copy.deepcopy(reply_packet['activity']);forged.update(id=forged['id'].rsplit('/',1)[0]+'/'+nonce(),seq=forged['seq']+100,body='Changed body')
        b.call('receive',packet=a.call('forge',activity=forged),doc=ad,expect=409)
        wrong=copy.deepcopy(forged);wrong.update(comment_id='https://alice.example.test/vrss/v1/comments/'+nonce(),parent='https://bob.example.test/vrss/v1/comments/'+nonce())
        b.call('receive',packet=a.call('forge',activity=wrong),doc=ad,expect=409)
        wrong['object']='https://other.example.test/vrss/v1/posts/'+post
        b.call('receive',packet=a.call('forge',activity=wrong),doc=ad,expect=422)
        # Retraction cancels a pending send, survives restart and permanently fences resurrection.
        a.call('comment_delete',url=obj,id=cid,request_id=nonce())
        deletion=[p for p in a.call('packets') if p['activity']['type']=='DeleteComment'][0]
        b.call('receive',packet=deletion,doc=ad)
        assert not b.call('comment_public',post_id=post)['items']
        b.call('comment_moderate',url=obj,id=cid,decision='approve',revision=5,request_id=nonce(),expect=409)
        resurrect=copy.deepcopy(packet['activity']);resurrect.update(id=resurrect['id'].rsplit('/',1)[0]+'/'+nonce(),seq=deletion['activity']['seq']+1,issued_at=time.time())
        b.call('receive',packet=a.call('forge',activity=resurrect),doc=ad,expect=409)
        b.restart();assert not b.call('comment_public',post_id=post)['items']
        assert b.call('receive',packet=packet,doc=ad)['comment']['state']=='deleted'
        assert a.call('comment_create',url=obj,body='<script>not an instruction</script>',request_id=request,documents={})['replayed']
        # Bounded cursor pages and explicit disabled/private boundaries.
        for i in range(7):b.call('comment_create',url=obj,body='Owner '+str(i),request_id=nonce())
        first=b.call('comment_public',post_id=post);assert len(first['items'])<=5 and first['next_cursor']
        second=b.call('comment_public',post_id=post,cursor=first['next_cursor'])
        assert not ({x['id'] for x in first['items']}&{x['id'] for x in second['items']})
        assert len(json.dumps(first).encode())<32768
        private=b.call('post',visibility='private')['post_id'];b.call('comment_public',post_id=private,expect=404)
        b.call('comment_optin',enabled=False);b.call('comment_public',post_id=post,expect=404)
        b.call('comment_optin',enabled=True)
        b.call('change',actor=ad['id'],action='block')
        b.call('receive',packet=reply_packet,doc=ad,expect=403)
        assert all(x['actor']!=ad['id'] for x in b.call('comment_public',post_id=post)['items'])
        print('PASS V2E independent signatures/databases: default-off consent, private moderation, immutable text, revision-bound owner actions, replies/subtree hiding, durable requests/restart, deletion fences, canonical post/parent binding, pagination, revocation and Chat notices')
    finally:a.close();b.close()

with tempfile.TemporaryDirectory(prefix='vrss-comment-ack-') as data:
    a,b=Server(data+'/a'),Server(data+'/b')
    try:
        a.call('configure',name='Alice',base='https://alice.example.test')
        b.call('configure',name='Bob',base='https://bob.example.test',comments=True)
        ad,bd=a.call('actor'),b.call('actor')
        a.call('follow',url=bd['id'],doc=bd);b.call('receive',packet=a.call('packets')[0],doc=ad)
        a.call('receive',packet=b.call('packets')[0],doc=bd);a.call('deliver');b.call('deliver')
        post=b.call('post')['post_id'];obj='https://bob.example.test/vrss/v1/posts/'+post
        docs={bd['id']:bd,obj+'/comments':b.call('comment_public',post_id=post)}
        made=a.call('comment_create',url=obj,body='Retracted during acknowledgement',request_id=nonce(),documents=docs)
        cid=made['comment']['id'];b.call('receive',packet=a.call('packets')[0],doc=ad)
        a.call('comment_cancel_ack',id=cid)
        deletion=a.call('packets')[0];assert deletion['activity']['type']=='DeleteComment'
        ack=b.call('receive',packet=deletion,doc=ad);a.call('comment_ack',result=ack)
        assert ack['comment']['state']=='deleted' and not b.call('comment_public',post_id=post)['items']
        print('PASS V2E cancelled in-flight ACK cannot restore a retracted comment; ordered DeleteComment and exact acknowledgement finish cleanup')
    finally:a.close();b.close()
