"""Actual independent HTTPS apps and unchanged production comments transport."""
import uuid
from vrss_v2c_tls import main


def comments(a,b,request,ad,bd):
    p=request(b,'profile')['profile'];assert p['comments_enabled'] is False
    request(b,'profile.update',display_name=p['display_name'],bio=p['bio'],public_base=p['public_base'],enabled=True,social_enabled=True,comments_enabled=True,expected_revision=p['revision'])
    post=request(b,'post.create',body='TLS comments',media=[],visibility='public',request_id=str(uuid.uuid4()))['post_id']
    obj='https://bob.example.test/vrss/v1/posts/'+post
    key=str(uuid.uuid4());made=request(a,'social.comment',url=obj,body='A real signed comment',parent='',request_id=key)
    cid=made['comment']['id']
    assert request(a,'social.comment',url=obj,body='A real signed comment',parent='',request_id=key)['replayed']
    assert a.post('/api/v1/control/social-test-pump').status_code==200
    public='/vrss/v1/posts/'+post+'/comments'
    assert b.get(public).json()['items']==[]
    review=request(b,'social.comment_review',url=obj,comment_id=cid)['comment']
    assert review['state']=='pending' and review['body']=='A real signed comment'
    assert len([n for n in request(b,'notices')['notices'] if n['title']=='VRSS comment awaiting review'])==1
    request(b,'social.moderate',url=obj,comment_id=cid,decision='approve',expected_revision=review['revision'],request_id=str(uuid.uuid4()))
    thread=request(a,'social.comments',url=obj,cursor='0')['thread']
    assert len(thread['items'])==1 and thread['items'][0]['can_delete']
    assert 'revision' not in b.get(public).json()['items'][0]
    reply=request(b,'social.comment',url=obj,body='An owner reply',parent=cid,request_id=str(uuid.uuid4()))['comment']
    assert reply['state']=='approved' and len(b.get(public).json()['items'])==2
    denied=a.post('/api/v1/control/homeserver-apps/vrss/request',headers={'X-VRSS-Request':'1'},json={'action':'social.moderate','url':obj,'comment_id':cid,'decision':'hide','expected_revision':2,'request_id':str(uuid.uuid4())})
    assert denied.status_code==403
    request(a,'social.comment_delete',url=obj,comment_id=cid,request_id=str(uuid.uuid4()))
    assert a.post('/api/v1/control/social-test-pump').status_code==200
    assert not b.get(public).json()['items']
    assert 'A real signed comment' not in b.get(public).text
    private=request(b,'post.create',body='Private',media=[],visibility='private',request_id=str(uuid.uuid4()))['post_id']
    assert b.get('/vrss/v1/posts/'+private+'/comments').status_code==404
    print('PASS V2E real HTTPS: signed delivery/ACK, exact nonce replay, default-off opt-in, owner-only private review, approval, local reply, author retraction, subtree removal and private-post exclusion')


if __name__=='__main__':main(comments)
