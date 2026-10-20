"""Real signatures and independent production databases for VRSS likes/unlikes."""
import copy
import tempfile
import time
import uuid

from vrss_v2c import Server


def nonce():
    return str(uuid.uuid4())


with tempfile.TemporaryDirectory(prefix='vrss-reactions-') as data:
    a, b = Server(data + '/a'), Server(data + '/b')
    try:
        a.call('configure', name='Alice', base='https://alice.example.test', reactions=True)
        b.call('configure', name='Bob', base='https://bob.example.test', reactions=True)
        ad, bd = a.call('actor'), b.call('actor')
        assert bd['interactions']['likes'] is True
        a.call('follow', url=bd['id'], doc=bd)
        follow = a.call('packets')[0]
        b.call('receive', packet=follow, doc=ad)
        a.call('receive', packet=b.call('packets')[0], doc=bd)
        a.call('deliver'); b.call('deliver')
        post = b.call('post')['post_id']
        obj = 'https://bob.example.test/vrss/v1/posts/' + post
        def documents():
            return {bd['id']: b.call('actor'), obj + '/interactions': b.call('public', id=post)}
        docs = documents()
        assert docs[obj + '/interactions']['likes'] == 0
        for boundary in ('cloud','follow'):
            a.call('late_reaction_revoke',url=obj,actor=bd['id'],boundary=boundary,documents=docs)
        b.call('reaction_api',post_id=post,likes=0)
        key = nonce()
        result = a.call('reaction', url=obj, enabled=True, request_id=key, documents=docs)
        assert result['reaction']['liked'] and result['reaction']['pending'] and result['reaction']['likes'] == 0
        assert a.call('reaction', url=obj, enabled=True, request_id=key, documents={})['replayed']
        a.call('reaction', url=obj, enabled=False, request_id=key, expect=409)
        like = a.call('packets')[0]
        assert like['activity']['type'] == 'Like'
        ack = b.call('receive', packet=like, doc=ad)
        assert ack['interaction']['likes'] == 1
        assert b.call('receive', packet=like, doc=ad)['replayed']
        assert b.call('public', id=post)['likes'] == 1
        assert len([n for n in b.call('notices')['notices'] if n['title'] == 'New VRSS like']) == 1
        a.call('deliver')
        a.restart(); b.restart()
        assert b.call('public', id=post)['likes'] == 1
        assert a.call('reaction_read', url=obj, documents=documents())['likes'] == 1
        # Exact ID cannot be reused for another body, even by the real signer.
        changed = copy.deepcopy(like['activity']); changed['seq'] += 50
        b.call('receive', packet=a.call('forge', activity=changed), doc=ad, expect=409)
        # Unlike is durable, and the old reaction ID stays revoked after a fresh signature/sequence.
        a.call('reaction', url=obj, enabled=False, request_id=nonce())
        unlike = a.call('packets')[0]
        b.call('receive', packet=unlike, doc=ad)
        assert b.call('public', id=post)['likes'] == 0
        assert not [n for n in b.call('notices')['notices'] if n['title'] == 'New VRSS like']
        stale = copy.deepcopy(like['activity']); stale.update(id=stale['id'].rsplit('/',1)[0]+'/'+nonce(), seq=unlike['activity']['seq']+1, issued_at=time.time())
        b.call('receive', packet=a.call('forge', activity=stale), doc=ad, expect=409)
        a.call('deliver')
        # Replaying the original owner request after unlike cannot reactivate the like.
        replay = a.call('reaction', url=obj, enabled=True, request_id=key, documents={})
        assert replay['replayed'] and not replay['reaction']['liked']
        # An older read cannot overwrite the count/state from a newer committed mutation.
        raced = a.call('read_race', url=obj, documents=documents())
        assert raced['liked'] and raced['pending'] and raced['likes'] == 0
        # A cancelled in-flight acknowledgement cannot poison the count with its old response.
        cancelled = a.call('cancel_like_ack', url=obj, documents=documents())
        assert not cancelled['liked'] and cancelled['likes'] == 0
        for packet in a.call('packets'):
            b.call('receive', packet=packet, doc=ad)
        a.call('deliver')
        # Disabled and private posts cannot be discovered or receive new likes.
        b.call('likes_enabled', enabled=False)
        b.call('public', id=post, expect=404)
        disabled = {bd['id']: b.call('actor'), obj+'/interactions': docs[obj+'/interactions']}
        a.call('reaction', url=obj, enabled=True, request_id=nonce(), documents=disabled, expect=409)
        b.call('likes_enabled', enabled=True)
        private = b.call('post', visibility='private')['post_id']
        b.call('public', id=private, expect=404)
        # Binding a count or a reaction to a different actor/post is rejected.
        wrong = documents(); wrong[obj+'/interactions']['actor'] = ad['id']
        a.call('reaction', url=obj, enabled=True, request_id=nonce(), documents=wrong, expect=422)
        old_peer = documents(); old_peer[bd['id']].pop('interactions')
        a.call('reaction', url=obj, enabled=True, request_id=nonce(), documents=old_peer, expect=409)
        # Current follow/blocks are authoritative for counts and new interactions.
        a.call('reaction', url=obj, enabled=True, request_id=nonce(), documents=documents())
        new_like = a.call('packets')[0]
        invalid=copy.deepcopy(new_like['activity']);invalid.update(id=invalid['id'].rsplit('/',1)[0]+'/'+nonce(),object='https://bob.example.test/vrss/v1/posts/'+private,reaction_id=ad['id'].replace('/actor','/reactions/')+nonce())
        b.call('receive',packet=a.call('forge',activity=invalid),doc=ad,expect=404)
        invalid['object']='https://other.example.test/vrss/v1/posts/'+post
        b.call('receive',packet=a.call('forge',activity=invalid),doc=ad,expect=422)
        b.call('receive', packet=new_like, doc=ad)
        assert b.call('public', id=post)['likes'] == 1
        b.call('change', actor=ad['id'], action='block')
        assert b.call('public', id=post)['likes'] == 0
        b.call('receive', packet=new_like, doc=ad, expect=403)
        b.call('change', actor=ad['id'], action='unblock')
        assert b.call('public', id=post)['likes'] == 0
        print('PASS V2D: independent databases/signatures, count/dedupe, stable owner retries, unlike tombstones, restart, stale read and cancelled ACK races, opt-in/private/origin/capability/blocked boundaries')
    finally:
        a.close(); b.close()
