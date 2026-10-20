"""Two real HTTPS applications using the unchanged production signed transport."""
import uuid
from vrss_v2c_tls import main


def reposts(a,b,request,ad,bd):
    p=request(b,'profile')['profile'];assert p['reposts_enabled'] is False
    request(b,'profile.update',**{**p,'reposts_enabled':True,'expected_revision':p['revision']})
    post=request(b,'post.create',body='Original content is not replicated',media=[],visibility='public',request_id=str(uuid.uuid4()))['post_id']
    obj='https://bob.example.test/vrss/v1/posts/'+post
    key=str(uuid.uuid4());made=request(a,'social.repost',url=obj,enabled=True,request_id=key)
    rid=made['repost']['url'];path=rid.removeprefix('https://alice.example.test')
    assert made['repost']['pending'] and not made['repost']['published']
    assert a.get(path).status_code==404
    assert request(a,'social.repost',url=obj,enabled=True,request_id=key)['replayed']
    assert a.post('/api/v1/control/social-test-pump').status_code==200
    assert b.get('/vrss/v1/posts/'+post+'/reposts').json()['reposts']==1
    pointer=a.get(path).json();assert pointer['original']['url']==obj and pointer['original']['identity']==bd['identity']
    assert 'Original content is not replicated' not in str(pointer)
    html=a.get(path,headers={'Accept':'text/html'});assert html.status_code==200 and obj in html.text and html.headers['cache-control']=='no-store'
    rss=a.get('/vrss/v1/rss');assert rid in rss.text and obj in rss.text and 'Original content is not replicated' not in rss.text
    assert len([x for x in request(b,'notices')['notices'] if x['title']=='New VRSS repost'])==1
    undone=request(a,'social.repost',url=obj,enabled=False,request_id=str(uuid.uuid4()))
    assert not undone['repost']['published'] and a.get(path).status_code==404
    assert rid not in a.get('/vrss/v1/rss').text
    assert a.post('/api/v1/control/social-test-pump').status_code==200
    assert b.get('/vrss/v1/posts/'+post+'/reposts').json()['reposts']==0
    made=request(a,'social.repost',url=obj,enabled=True,request_id=str(uuid.uuid4()));rid=made['repost']['url'];path=rid.removeprefix('https://alice.example.test')
    assert a.post('/api/v1/control/social-test-pump').status_code==200
    assert a.get(path).status_code==200
    request(b,'post.delete',id=post)
    assert a.get(path).status_code!=200
    assert rid not in a.get('/vrss/v1/rss').text
    request(a,'social.repost',url=obj,enabled=False,request_id=str(uuid.uuid4()))
    assert a.post('/api/v1/control/social-test-pump').status_code==200
    private=request(b,'post.create',body='Private original',media=[],visibility='private',request_id=str(uuid.uuid4()))['post_id']
    assert b.get('/vrss/v1/posts/'+private+'/reposts').status_code==404
    assert b.post('/vrss/v1/posts/'+private+'/reposts',json={}).status_code==405
    print('PASS V2F actual HTTPS: signed delivery, source-owned count, original-author attribution, live pointer/source deletion, no copied text, RSS publication/undo, cleanup, private/write boundaries and Chat notices')


if __name__=='__main__':main(reposts)
