"""Two actual HTTPS apps exercising production mention and public-thread transport."""
import uuid
from vrss_v2c_tls import main
nonce=lambda:str(uuid.uuid4())
def conversations(a,b,request,ad,bd):
    for client in [a,b]:
        p=request(client,'profile')['profile']
        request(client,'profile.update',**{**p,'comments_enabled':True,'mentions_enabled':True,'expected_revision':p['revision']})
        for n in request(client,'notices')['notices']:request(client,'notice.read',notice_id=n['id'])
    request(a,'social.unfollow',actor=bd['id']);assert a.post('/api/v1/control/social-test-pump').status_code==200
    assert request(a,'social.status')['social']['following']==0
    post=request(b,'post.create',body='Public discussion @'+ad['id'],media=[],visibility='public',request_id=nonce())['post_id']
    obj='https://bob.example.test/vrss/v1/posts/'+post
    old=request(b,'social.comment',url=obj,body='Baseline reply',parent='',request_id=nonce())['comment']['id']
    made=request(a,'social.conversation',url=obj,enabled=True,muted=False,expected_revision=0,request_id=nonce())
    assert made['conversation']['enabled'] and request(a,'social.status')['social']['following']==0
    for _ in range(2):assert b.post('/api/v1/control/social-test-pump').status_code==200
    assert a.post('/api/v1/control/social-test-pump').status_code==200
    notices=request(a,'notices')['notices'];assert len(notices)==1 and notices[0]['title']=='VRSS mention'
    request(a,'notice.read',notice_id=notices[0]['id'])
    new=request(b,'social.comment',url=obj,body='Public reply @'+ad['id'],parent='',request_id=nonce())['comment']['id']
    for _ in range(2):assert b.post('/api/v1/control/social-test-pump').status_code==200
    assert a.post('/api/v1/control/social-test-pump').status_code==200
    notices=request(a,'notices')['notices'];assert len(notices)==1,'Mention and thread reply must coalesce'
    assert a.post('/api/v1/control/social-test-pump').status_code==200
    assert len(request(a,'notices')['notices'])==1
    watch=request(a,'social.conversations')['conversations']['items'][0]
    request(a,'social.conversation',url=obj,enabled=True,muted=True,expected_revision=watch['revision'],request_id=nonce())
    assert not request(a,'notices')['notices']
    request(b,'social.comment',url=obj,body='Muted @'+ad['id'],parent='',request_id=nonce())
    for _ in range(2):assert b.post('/api/v1/control/social-test-pump').status_code==200
    assert a.post('/api/v1/control/social-test-pump').status_code==200
    assert not request(a,'notices')['notices']
    request(b,'post.delete',id=post)
    assert a.post('/api/v1/control/social-test-pump').status_code==200
    assert request(a,'social.conversations')['conversations']['items'][0]['error']
    assert a.post('/vrss/v1/posts/'+post+'/comments',json={}).status_code==405
    assert b.get('/vrss/v1/posts/'+post+'/comments').status_code==404
    print('PASS V2G actual HTTPS: signed source-publisher mention, live public proof, no-follow thread subscription, baseline, coalesced notices, mute, source deletion and private owner/write boundaries')
if __name__=='__main__':main(conversations)
