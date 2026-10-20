"""Like/unlike over real independent HTTPS apps and the production transport."""
import uuid
from vrss_v2c_tls import main


def reactions(a,b,request,ad,bd):
    p=request(b,'profile')['profile']
    assert p['reactions_enabled'] is False, 'Existing profiles must not silently opt into likes'
    request(b,'profile.update',display_name=p['display_name'],bio=p['bio'],public_base=p['public_base'],
            enabled=True,social_enabled=True,reactions_enabled=True,expected_revision=p['revision'])
    post=request(b,'post.create',body='A real TLS post',media=[],visibility='public',request_id=str(uuid.uuid4()))['post_id']
    obj='https://bob.example.test/vrss/v1/posts/'+post
    key=str(uuid.uuid4())
    pending=request(a,'social.like',url=obj,enabled=True,request_id=key)['reaction']
    assert pending['liked'] and pending['pending'] and pending['likes']==0
    assert a.post('/api/v1/control/social-test-pump').status_code==200
    value=request(a,'social.reactions',url=obj)['reaction']
    assert value['likes']==1 and value['liked'] and not value['pending']
    assert b.get('/vrss/v1/posts/'+post+'/interactions').json()['likes']==1
    assert len([n for n in request(b,'notices')['notices'] if n['title']=='New VRSS like'])==1
    assert request(a,'social.like',url=obj,enabled=True,request_id=key)['replayed']
    request(a,'social.like',url=obj,enabled=False,request_id=str(uuid.uuid4()))
    assert a.post('/api/v1/control/social-test-pump').status_code==200
    assert b.get('/vrss/v1/posts/'+post+'/interactions').json()['likes']==0
    assert not [n for n in request(b,'notices')['notices'] if n['title']=='New VRSS like']
    private=request(b,'post.create',body='Private',media=[],visibility='private',request_id=str(uuid.uuid4()))['post_id']
    assert b.get('/vrss/v1/posts/'+private+'/interactions').status_code==404
    print('PASS V2D real HTTPS: explicit upgrade opt-in, actual public post, signed Like/Unlike, delivery ACK counts, replay, private-post exclusion and Chat like notice')


if __name__=='__main__':main(reactions)
