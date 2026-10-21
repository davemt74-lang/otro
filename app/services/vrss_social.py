"""VRSS authenticated follows. HomeServer-owned, HTTPS origin-bound Ed25519 exchange.

This bounded VRSS protocol uses Follow/Accept/Undo/Reject semantics; it is not a
claim of general ActivityPub interoperability. RSS remains the content transport.
"""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import socket
import ssl
import threading
import time
import uuid
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from . import vrss as v

CONTRACT = 'vp3.vrss.follow.v1'
MAX_BODY = 32768
SCHEMA = """
CREATE TABLE IF NOT EXISTS vrss_social_identity (
 id INTEGER PRIMARY KEY CHECK(id=1), secret TEXT NOT NULL, rotations TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS vrss_social_peers (
 actor TEXT PRIMARY KEY, identity TEXT NOT NULL, public_key TEXT NOT NULL, name TEXT NOT NULL,
 inbox TEXT NOT NULL, rss TEXT NOT NULL, send_seq INTEGER NOT NULL DEFAULT 0,
 receive_seq INTEGER NOT NULL DEFAULT 0, blocked INTEGER NOT NULL DEFAULT 0, blocked_at REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS vrss_social_relations (
 actor TEXT NOT NULL, direction TEXT NOT NULL, follow_id TEXT NOT NULL, state TEXT NOT NULL,
 created_at REAL NOT NULL, PRIMARY KEY(actor,direction));
CREATE TABLE IF NOT EXISTS vrss_social_outbox (
 id TEXT PRIMARY KEY, actor TEXT NOT NULL, seq INTEGER NOT NULL, payload TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 due REAL NOT NULL DEFAULT 0, lease TEXT NOT NULL DEFAULT '', lease_until REAL NOT NULL DEFAULT 0,
 error TEXT NOT NULL DEFAULT '', UNIQUE(actor,seq));
CREATE TABLE IF NOT EXISTS vrss_social_receipts (
 id TEXT PRIMARY KEY, digest TEXT NOT NULL, actor TEXT NOT NULL, received_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS vrss_social_revoked (
 actor TEXT NOT NULL, follow_id TEXT NOT NULL, PRIMARY KEY(actor,follow_id));
CREATE TABLE IF NOT EXISTS vrss_social_notices (
 id TEXT PRIMARY KEY, actor TEXT NOT NULL, created_at TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0);
"""
_SLOTS = threading.BoundedSemaphore(4)
_RATE_LOCK = threading.Lock()
_RATE = {}


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def setup(c):
    # executescript would commit the caller's transaction. Migrations stay atomic.
    for statement in SCHEMA.split(';'):
        if statement.strip():
            c.execute(statement)
    columns = {row[1] for row in c.execute('PRAGMA table_info(vrss_profile)')}
    for name in ('social_enabled', 'social_key_created'):
        if name not in columns:
            c.execute('ALTER TABLE vrss_profile ADD COLUMN ' + name + ' INTEGER NOT NULL DEFAULT 0')
    from . import vrss_interactions
    vrss_interactions.setup(c)
    from . import vrss_comments
    vrss_comments.setup(c)
    from . import vrss_reposts
    vrss_reposts.setup(c)
    from . import vrss_conversations
    vrss_conversations.setup(c)


def ready(c):
    setup(c)
    p = v.profile(c)
    if not p['enabled'] or not p['public_base'] or not p['social_enabled']:
        raise v.VRSSError('Enable public publishing and authenticated follows in Profile first.', 409)
    actor_address(actor_url(p))
    if p['hosting_site_id']:
        v.hosted_address(p['hosting_site_id'], p['public_base'])
    return p


def identity(c):
    row = c.execute('SELECT * FROM vrss_social_identity WHERE id=1').fetchone()
    if not row:
        if c.execute('SELECT social_key_created FROM vrss_profile WHERE id=1').fetchone()[0]:
            raise v.VRSSError('VRSS signing key is missing. Restore the complete Feed data backup to recover this identity.', 409)
        key = Ed25519PrivateKey.generate()
        c.execute('INSERT INTO vrss_social_identity(id,secret) VALUES(1,?)', (key.private_bytes_raw().hex(),))
        c.execute('UPDATE vrss_profile SET social_key_created=1 WHERE id=1')
        row = c.execute('SELECT * FROM vrss_social_identity WHERE id=1').fetchone()
    return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(row['secret'])), json.loads(row['rotations'])


def actor_url(p):
    return p['public_base'] + '/vrss/v1/actor'


def actor_document():
    with v.database() as c:
        p = ready(c)
        key, rotations = identity(c)
        return {'contract': CONTRACT, 'id': actor_url(p), 'identity': p['actor_id'], 'name': p['display_name'],
                'inbox': p['public_base'] + '/vrss/v1/inbox', 'rss': p['feed_url'],
                'public_key': key.public_key().public_bytes_raw().hex(), 'rotations': rotations,
                'interactions': {'contract': 'vp3.vrss.interactions.v1', 'likes': bool(p['reactions_enabled'])},
                'comments': {'contract': 'vp3.vrss.comments.v1', 'enabled': bool(p['comments_enabled'])},
                'reposts': {'contract': 'vp3.vrss.reposts.v1', 'enabled': bool(p['reposts_enabled'])},
                'conversations': {'contract':'vp3.vrss.conversations.v1', 'mentions':bool(p['mentions_enabled'])}}


def rotate():
    with v.database() as c:
        p = ready(c)
        key, chain = identity(c)
        if len(chain) >= 16:
            raise v.VRSSError('Identity rotation history is full. Retain the current key and its backup.', 409)
        new = Ed25519PrivateKey.generate()
        cert = {'contract': CONTRACT, 'actor': actor_url(p), 'from': key.public_key().public_bytes_raw().hex(),
                'to': new.public_key().public_bytes_raw().hex()}
        chain.append({**cert, 'signature': key.sign(canonical(cert)).hex()})
        c.execute('UPDATE vrss_social_identity SET secret=?,rotations=? WHERE id=1',
                  (new.private_bytes_raw().hex(), json.dumps(chain)))
    return {'rotated': True}


def actor_address(url):
    url = v.safe_url(url, https_only=True)
    if len(url) > 400 or urlsplit(url).query or not url.endswith('/vrss/v1/actor'):
        raise v.VRSSError('A canonical VRSS actor address is required.', 422)
    return url


def exchange(url, payload=None):
    """No redirects, proxy credentials, cookies or private IPs; pin IP, verify TLS hostname."""
    url = v.safe_url(url, https_only=True)
    p = urlsplit(url)
    raw = canonical(payload) if payload is not None else None
    if raw is not None and len(raw) > MAX_BODY:
        raise v.VRSSError('VRSS activity is too large.', 413)
    try:
        addresses = {x[4][0] for x in socket.getaddrinfo(p.hostname, 443, type=socket.SOCK_STREAM)}
        if not addresses or any(not ipaddress.ip_address(ip).is_global or ipaddress.ip_address(ip).is_multicast for ip in addresses):
            raise v.VRSSError('VRSS destination resolves to a private address.', 422)
        ip = sorted(addresses)[0]
        conn = http.client.HTTPSConnection(p.hostname, timeout=8, context=ssl.create_default_context())
        conn._create_connection = lambda address, timeout=8, source_address=None: socket.create_connection((ip, 443), timeout, source_address)
        try:
            conn.request('POST' if raw is not None else 'GET', p.path, body=raw,
                         headers={'Accept': 'application/json', 'Content-Type': 'application/json',
                                  'Accept-Encoding': 'identity', 'User-Agent': 'VP3-VRSS/1.4'})
            response = conn.getresponse()
            if response.status not in ({200, 202} if raw is not None else {200}):
                raise v.VRSSError('Remote VRSS returned HTTP ' + str(response.status) + '.', 502)
            if response.getheader('Content-Encoding', 'identity').lower() not in {'', 'identity'}:
                raise v.VRSSError('Compressed VRSS responses are not accepted.', 422)
            if response.getheader('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                raise v.VRSSError('Remote VRSS did not return JSON.', 422)
            # Bound total time as well as per-read socket time.
            data = bytearray()
            deadline = time.monotonic() + 15
            while len(data) <= MAX_BODY:
                if time.monotonic() > deadline:
                    raise v.VRSSError('VRSS response timed out.', 504)
                part = response.read(min(4096, MAX_BODY + 1 - len(data)))
                if not part:
                    result = json.loads(data)
                    if not isinstance(result, dict):
                        raise ValueError()
                    return result
                data.extend(part)
            raise v.VRSSError('VRSS response is too large.', 413)
        finally:
            conn.close()
    except (OSError, http.client.HTTPException, ValueError, UnicodeError, RecursionError) as exc:
        raise v.VRSSError('Remote VRSS is unavailable or returned an invalid response.', 502) from exc


def peer_document(url, loader=None):
    url = actor_address(url)
    doc = (loader or exchange)(url)
    base = url[:-len('/vrss/v1/actor')]
    if (not isinstance(doc, dict) or doc.get('contract') != CONTRACT or doc.get('id') != url
            or doc.get('inbox') != base + '/vrss/v1/inbox' or doc.get('rss') != base + '/vrss/v1/rss'):
        raise v.VRSSError('Remote VRSS identity does not match its HTTPS address.', 422)
    try:
        uuid.UUID(doc['identity'].removeprefix('urn:uuid:'))
        if not doc['identity'].startswith('urn:uuid:'):
            raise ValueError()
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(doc['public_key']))
        if not isinstance(doc.get('rotations'), list) or len(doc['rotations']) > 16:
            raise ValueError()
        doc['name'] = v.text(doc.get('name'), 120, 'Remote name', True)
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise v.VRSSError('Remote VRSS identity is invalid.', 422) from exc
    return doc


def pin(c, doc):
    old = c.execute('SELECT * FROM vrss_social_peers WHERE actor=?', (doc['id'],)).fetchone()
    if old:
        if old['identity'] != doc['identity']:
            raise v.VRSSError('The remote identity changed. This connection is suspended.', 409)
        current = old['public_key']
        if current != doc['public_key']:
            try:
                for step in doc['rotations']:
                    if step['from'] != current:
                        continue
                    cert = {'contract': CONTRACT, 'actor': doc['id'], 'from': current, 'to': step['to']}
                    Ed25519PublicKey.from_public_bytes(bytes.fromhex(current)).verify(bytes.fromhex(step['signature']), canonical(cert))
                    current = step['to']
                if current != doc['public_key']:
                    raise ValueError()
            except (KeyError, ValueError, TypeError, InvalidSignature) as exc:
                raise v.VRSSError('Remote signing key changed without verified continuity.', 409) from exc
        c.execute('UPDATE vrss_social_peers SET public_key=?,name=? WHERE actor=?', (current, doc['name'], doc['id']))
    else:
        if c.execute('SELECT count(*) FROM vrss_social_peers').fetchone()[0] >= 1000:
            raise v.VRSSError('VRSS connection limit reached.', 409)
        c.execute('INSERT INTO vrss_social_peers(actor,identity,public_key,name,inbox,rss) VALUES(?,?,?,?,?,?)',
                  (doc['id'], doc['identity'], doc['public_key'], doc['name'], doc['inbox'], doc['rss']))
    return c.execute('SELECT * FROM vrss_social_peers WHERE actor=?', (doc['id'],)).fetchone()


def enqueue(c, p, peer, kind, follow_id, extra=None):
    if c.execute('SELECT count(*) FROM vrss_social_outbox').fetchone()[0] >= 10000:
        raise v.VRSSError('VRSS delivery history is full.', 409)
    seq = c.execute('SELECT send_seq FROM vrss_social_peers WHERE actor=?', (peer,)).fetchone()[0] + 1
    c.execute('UPDATE vrss_social_peers SET send_seq=? WHERE actor=?', (seq, peer))
    activity = {'contract': CONTRACT, 'id': p['public_base'] + '/vrss/v1/activities/' + str(uuid.uuid4()),
                'actor': actor_url(p), 'identity': p['actor_id'], 'recipient': peer, 'type': kind,
                'follow_id': follow_id, 'seq': seq, 'issued_at': time.time()}
    if kind == 'Mention':
        activity.pop('follow_id')
    if extra:
        activity.update(extra)
    c.execute('INSERT INTO vrss_social_outbox(id,actor,seq,payload) VALUES(?,?,?,?)',
              (activity['id'], peer, seq, canonical(activity).decode()))
    return activity


def follow(url, loader=None):
    # User may paste a canonical profile, root or actor URL. No guessed foreign inboxes.
    url = v.safe_url(url, https_only=True).rstrip('/')
    for suffix in ('/vrss/v1/profile', '/vrss/v1/rss', '/vrss/v1/actor'):
        if url.endswith(suffix):
            url = url[:-len(suffix)]
            break
    target = actor_address(url + '/vrss/v1/actor')
    with v.database() as c:
        ready(c)
    doc = peer_document(target, loader)
    with v.database() as c:
        p = ready(c)
        if target == actor_url(p) or doc['identity'] == p['actor_id']:
            raise v.VRSSError('You cannot follow this HomeServer identity itself.', 422)
        peer = pin(c, doc)
        if peer['blocked']:
            raise v.VRSSError('Unblock this profile before following.', 409)
        old = c.execute("SELECT * FROM vrss_social_relations WHERE actor=? AND direction='out'", (target,)).fetchone()
        if old and old['state'] in {'pending', 'accepted'}:
            return {'state': old['state'], 'actor': target, 'replayed': True}
        fid = p['public_base'] + '/vrss/v1/follows/' + str(uuid.uuid4())
        c.execute("INSERT OR REPLACE INTO vrss_social_relations VALUES(?,'out',?,'pending',?)", (target, fid, time.time()))
        enqueue(c, p, target, 'Follow', fid)
    return {'state': 'pending', 'actor': target}


def change(target, action):
    target = actor_address(target)
    with v.database() as c:
        setup(c)
        p = v.profile(c)
        peer = c.execute('SELECT * FROM vrss_social_peers WHERE actor=?', (target,)).fetchone()
        if not peer:
            raise v.VRSSError('This VRSS connection was not found.', 404)
        if action == 'unblock':
            c.execute('UPDATE vrss_social_peers SET blocked=0 WHERE actor=?', (target,))
            return {'unblocked': True}
        if action not in {'unfollow', 'block', 'remove'}:
            raise v.VRSSError('Unknown VRSS social action.', 422)
        directions = ('out', 'in') if action == 'block' else (('out',) if action == 'unfollow' else ('in',))
        for direction in directions:
            row = c.execute('SELECT * FROM vrss_social_relations WHERE actor=? AND direction=?', (target, direction)).fetchone()
            if row and row['state'] in {'pending', 'accepted'}:
                c.execute('INSERT OR IGNORE INTO vrss_social_revoked VALUES(?,?)',(target,row['follow_id']))
                c.execute("UPDATE vrss_social_outbox SET state='cancelled',lease='' WHERE actor=? AND state='pending' AND json_extract(payload,'$.follow_id')=?", (target, row['follow_id']))
                enqueue(c, p, target, 'Undo' if direction == 'out' else 'Reject', row['follow_id'])
                c.execute("UPDATE vrss_social_relations SET state='removed' WHERE actor=? AND direction=?", (target, direction))
        if action in {'block', 'remove'}:
            c.execute('UPDATE vrss_social_peers SET blocked_at=? WHERE actor=?', (time.time(), target))
        if action == 'block':
            from . import vrss_conversations
            c.execute('UPDATE vrss_conversation_watches SET enabled=0,revision=revision+1 WHERE actor=?', (target,))
            c.execute('UPDATE vrss_conversation_notices SET inactive=1 WHERE actor=?', (target,))
            c.execute("UPDATE vrss_mention_intents SET state='cancelled' WHERE target=?", (target,))
            c.execute("UPDATE vrss_social_outbox SET state='cancelled',lease='' WHERE actor=? AND state='pending' AND json_extract(payload,'$.type')='Mention'", (target,))
            c.execute('UPDATE vrss_social_peers SET blocked=1,blocked_at=? WHERE actor=?', (time.time(), target))
            c.execute('DELETE FROM vrss_social_notices WHERE actor=? AND delivered=0', (target,))
        if action in {'unfollow', 'block'}:
            c.execute('DELETE FROM vrss_subscriptions WHERE url=?', (peer['rss'],))
        c.execute('UPDATE vrss_comments SET notice_delivered=0 WHERE actor=?', (target,))
        c.execute('UPDATE vrss_reposts SET notice_delivered=0 WHERE actor=?', (target,))
    dismiss_notices(target) if action in {'block', 'remove'} else None
    return {'state': 'blocked' if action == 'block' else 'removed'}


def rate_limit(actor):
    minute = int(time.time() // 60)
    with _RATE_LOCK:
        for key in list(_RATE):
            if _RATE[key][0] != minute:
                del _RATE[key]
        if len(_RATE) >= 500 and actor not in _RATE:
            raise v.VRSSError('VRSS inbox is busy.', 429)
        for key, limit in (('*', 120), (actor, 30)):
            count = _RATE.get(key, (minute, 0))[1] + 1
            if count > limit:
                raise v.VRSSError('VRSS inbox rate limit reached.', 429)
            _RATE[key] = (minute, count)


def receive(raw, loader=None):
    from . import vrss_interactions as interactions
    from . import vrss_comments as comments
    from . import vrss_reposts as reposts, vrss_conversations as conversations
    if len(raw) > MAX_BODY:
        raise v.VRSSError('VRSS activity is too large.', 413)
    try:
        packet = json.loads(raw)
        a = packet['activity']
        actor = actor_address(a['actor'])
        base = actor[:-len('/vrss/v1/actor')]
        reaction = a.get('type') in interactions.TYPES
        comment = a.get('type') in comments.TYPES
        repost = a.get('type') in reposts.TYPES
        mention = a.get('type') == 'Mention'
        fields = {'contract','id','actor','identity','recipient','type','follow_id','seq','issued_at'}
        if mention:
            fields.remove('follow_id')
            fields |= {'object','comment_id','mention_id'}
        if reaction:
            fields |= {'object','reaction_id'}
        if repost:
            fields |= {'object','repost_id'}
        if comment:
            fields |= {'object','comment_id'}
            if a['type'] == 'Comment':
                fields |= {'body','parent'}
        if (set(a) != fields
                or a['contract'] != (conversations.CONTRACT if mention else reposts.CONTRACT if repost else comments.CONTRACT if comment else interactions.CONTRACT if reaction else CONTRACT)
                or (not mention and not repost and not reaction and not comment and a['type'] not in {'Follow','Accept','Undo','Reject'})
                or type(a['seq']) is not int or not 0 < a['seq'] < 2**53
                or type(a['issued_at']) not in {int,float} or not 0 < a['issued_at'] <= time.time() + 300
                or type(packet['sent_at']) is not int or abs(time.time() - packet['sent_at']) > 300
                or not a['id'].startswith(base + '/vrss/v1/activities/')):
            raise ValueError()
        uuid.UUID(a['id'].removeprefix(base + '/vrss/v1/activities/'))
        # The Follow ID belongs to the follower, not necessarily the signed sender of Accept/Reject.
        fid, prefix = '', ''
        if mention:
            conversations.validate(a)
        else:
            fid = v.safe_url(a['follow_id'], https_only=True)
            prefix, tail = fid.rsplit('/vrss/v1/follows/', 1)
            uuid.UUID(tail)
        if reaction:
            interactions.validate(a)
        if comment:
            comments.validate(a)
        if repost:
            reposts.validate(a)
        signature = bytes.fromhex(packet['signature'])
    except (KeyError, ValueError, TypeError, AttributeError, UnicodeError, RecursionError) as exc:
        raise v.VRSSError('Invalid VRSS activity.', 422) from exc
    rate_limit(actor)
    if not _SLOTS.acquire(blocking=False):
        raise v.VRSSError('VRSS inbox is busy.', 429)
    try:
        with v.database() as c:
            p = ready(c)
            if a['recipient'] != actor_url(p) or actor == actor_url(p):
                raise v.VRSSError('VRSS recipient does not match.', 403)
            if mention and not p['mentions_enabled']:
                raise v.VRSSError('Incoming VRSS mentions are disabled.', 403)
            old = c.execute('SELECT * FROM vrss_social_peers WHERE actor=?', (actor,)).fetchone()
            if old and old['blocked']:
                raise v.VRSSError('This VRSS connection is blocked.', 403)
        doc = peer_document(actor, loader)
        try:
            signed = {'activity': a, 'sent_at': packet['sent_at']}
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(doc['public_key'])).verify(signature, canonical(signed))
        except (ValueError, InvalidSignature) as exc:
            raise v.VRSSError('VRSS signature did not verify.', 403) from exc
        if mention:
            proof = conversations.verify_source(a['object'], a['comment_id'], a['recipient'], loader)
            if proof['identity'] != doc['identity'] or proof['public_key'] != doc['public_key']:
                raise v.VRSSError('Mention source identity changed.', 409)
        with v.database() as c:
            p = ready(c)
            if mention and not p['mentions_enabled']:
                raise v.VRSSError('Incoming VRSS mentions are disabled.', 403)
            if a['recipient'] != actor_url(p) or a['identity'] != doc['identity']:
                raise v.VRSSError('VRSS identity or audience changed.', 403)
            peer = pin(c, doc)
            if peer['blocked']:
                raise v.VRSSError('This VRSS connection is blocked.', 403)
            if mention and a['issued_at'] <= peer['blocked_at']:
                raise v.VRSSError('This mention predates a block or connection removal.', 409)
            digest = hashlib.sha256(canonical(a)).hexdigest()
            receipt = c.execute('SELECT digest FROM vrss_social_receipts WHERE id=?', (a['id'],)).fetchone()
            if receipt:
                if receipt[0] != digest:
                    raise v.VRSSError('VRSS activity ID was reused with different content.', 409)
                result = {'contract': CONTRACT, 'ok': True, 'replayed': True}
                if mention:
                    result['mention'] = {'id':a['mention_id'],'object':a['object'],'actor':actor_url(p)}
                if reaction:
                    result['interaction'] = {'object': a['object'], 'actor': actor_url(p),
                                             'likes': interactions.count(c, p, a['object'])}
                if repost:
                    result['repost'] = reposts.acknowledgement(c, p, a)
                if comment:
                    row = c.execute('SELECT state FROM vrss_comments WHERE id=?', (a['comment_id'],)).fetchone()
                    result['comment'] = {'object': a['object'], 'id': a['comment_id'], 'state': row['state']}
                return result
            if a['seq'] <= peer['receive_seq']:
                raise v.VRSSError('Stale VRSS activity.', 409)
            if c.execute('SELECT count(*) FROM vrss_social_receipts').fetchone()[0] >= 10000:
                raise v.VRSSError('VRSS inbox history is full.', 409)
            direction = 'in' if reaction or comment or repost or a['type'] in {'Follow','Undo'} else 'out'
            expected = base if direction == 'in' else p['public_base']
            if not mention and prefix != expected:
                raise v.VRSSError('VRSS follow owner does not match.', 403)
            relation = c.execute('SELECT * FROM vrss_social_relations WHERE actor=? AND direction=?', (actor,direction)).fetchone()
            if a['type'] in {'Undo','Reject'}:
                c.execute('INSERT OR IGNORE INTO vrss_social_revoked VALUES(?,?)',(actor,fid))
            interaction = None
            comment_result = None
            repost_result = None
            if mention:
                conversations.receive(c, p, peer, a)
            elif repost:
                repost_result = reposts.receive(c, p, peer, a)
            elif comment:
                comment_result = comments.receive(c, p, peer, a)
            elif reaction:
                interaction = interactions.receive(c, p, peer, a)
            elif a['type'] == 'Follow':
                if c.execute('SELECT 1 FROM vrss_social_revoked WHERE actor=? AND follow_id=?',(actor,fid)).fetchone() or a['issued_at'] <= peer['blocked_at']:
                    raise v.VRSSError('This follow predates connection removal.', 409)
                c.execute("INSERT OR REPLACE INTO vrss_social_relations VALUES(?,'in',?,'accepted',?)", (actor,fid,time.time()))
                enqueue(c,p,actor,'Accept',fid)
                if not relation or relation['state'] != 'accepted':
                    c.execute('INSERT INTO vrss_social_notices(id,actor,created_at) VALUES(?,?,?)', (digest,actor,v.now()))
            elif relation and relation['follow_id'] == fid:
                state = 'accepted' if a['type'] == 'Accept' and relation['state'] in {'pending','accepted'} else 'removed'
                c.execute('UPDATE vrss_social_relations SET state=? WHERE actor=? AND direction=?', (state,actor,direction))
                if state == 'accepted':
                    if not c.execute('SELECT 1 FROM vrss_subscriptions WHERE url=?', (peer['rss'],)).fetchone():
                        if c.execute('SELECT count(*) FROM vrss_subscriptions').fetchone()[0] >= 100:
                            raise v.VRSSError('Feed subscription limit reached.',409)
                        c.execute('INSERT INTO vrss_subscriptions(id,url,title) VALUES(?,?,?)', (str(uuid.uuid4()),peer['rss'],peer['name']))
                elif direction == 'out':
                    c.execute('DELETE FROM vrss_subscriptions WHERE url=?',(peer['rss'],))
                else:
                    c.execute('DELETE FROM vrss_social_notices WHERE actor=? AND delivered=0',(actor,))
                c.execute('UPDATE vrss_comments SET notice_delivered=0 WHERE actor=?', (actor,))
                c.execute('UPDATE vrss_reposts SET notice_delivered=0 WHERE actor=?', (actor,))
            c.execute('INSERT INTO vrss_social_receipts VALUES(?,?,?,?)',(a['id'],digest,actor,time.time()))
            c.execute('UPDATE vrss_social_peers SET receive_seq=? WHERE actor=?',(a['seq'],actor))
        return {'contract':CONTRACT,'ok':True, **({'mention':{'id':a['mention_id'],'object':a['object'],'actor':actor_url(p)}} if mention else {}), **({'interaction': interaction} if interaction is not None else {}),
                **({'comment': comment_result} if comment_result is not None else {}),
                **({'repost': repost_result} if repost_result is not None else {})}
    finally:
        _SLOTS.release()


def signed(c, activity):
    key, _ = identity(c)
    packet = {'activity': activity, 'sent_at': int(time.time())}
    return {**packet, 'signature': key.sign(canonical(packet)).hex()}


def deliver(sender=None, stop_event=None):
    sender = sender or exchange
    for _ in range(4):
        if stop_event is not None and stop_event.is_set():
            break
        lease = str(uuid.uuid4())
        with v.database() as c:
            ready(c)
            job = c.execute("""SELECT o.*,p.inbox,p.blocked FROM vrss_social_outbox o JOIN vrss_social_peers p ON p.actor=o.actor
                WHERE o.state='pending' AND o.due<=? AND o.lease_until<=? AND NOT EXISTS
                (SELECT 1 FROM vrss_social_outbox x WHERE x.actor=o.actor AND x.state='pending' AND x.seq<o.seq)
                ORDER BY o.due,o.seq LIMIT 1""",(time.time(),time.time())).fetchone()
            if not job:
                break
            a=json.loads(job['payload'])
            if job['blocked'] and a['type'] not in {'Undo','Reject'}:
                c.execute("UPDATE vrss_social_outbox SET state='cancelled' WHERE id=?",(job['id'],))
                continue
            from . import vrss_conversations
            if not vrss_conversations.can_send(c, v.profile(c), a):
                c.execute("UPDATE vrss_social_outbox SET state='cancelled',lease='' WHERE id=?", (job['id'],))
                continue
            delivery_started = time.time()
            c.execute('UPDATE vrss_social_outbox SET lease=?,lease_until=?,attempts=attempts+1 WHERE id=?', (lease,delivery_started+120,job['id']))
            packet=signed(c,a)
        try:
            # A block/unfollow in the gap cancels this lease before sending if it wins this check.
            with v.database() as c:
                current = ready(c)
                if not vrss_conversations.can_send(c, current, a):
                    c.execute("UPDATE vrss_social_outbox SET state='cancelled',lease='' WHERE id=?", (job['id'],))
                live=c.execute("SELECT 1 FROM vrss_social_outbox WHERE id=? AND lease=? AND state='pending'",(job['id'],lease)).fetchone()
                if not live:
                    continue
            result=sender(job['inbox'],packet)
            if result.get('contract')!=CONTRACT or result.get('ok') is not True:
                raise v.VRSSError('Remote VRSS did not acknowledge the activity.',502)
            with v.database() as c:
                ready(c)
                updated = c.execute("UPDATE vrss_social_outbox SET state='delivered',lease='',lease_until=0,error='' WHERE id=? AND lease=? AND state='pending'",(job['id'],lease))
                if updated.rowcount:
                    if a['type'] == 'Mention' and result.get('mention') != {'id':a['mention_id'],'object':a['object'],'actor':a['recipient']}:
                        raise v.VRSSError('Remote acknowledgement does not match this mention.', 502)
                    from . import vrss_interactions
                    vrss_interactions.acknowledge(c, a, result)
                    from . import vrss_comments
                    vrss_comments.acknowledge(c, a, result)
                    from . import vrss_reposts
                    vrss_reposts.acknowledge(c, a, result, delivery_started)
        except Exception:
            with v.database() as c:
                c.execute("UPDATE vrss_social_outbox SET error='Delivery unavailable; retrying automatically.',due=?,lease='',lease_until=0 WHERE id=? AND lease=? AND state='pending'",
                          (time.time()+min(86400,30*2**min(job['attempts'],11)),job['id'],lease))
    if stop_event is not None and stop_event.is_set():
        return
    deliver_notices()
    from . import vrss_interactions
    vrss_interactions.deliver_notices()
    from . import vrss_comments
    vrss_comments.deliver_notices()
    from . import vrss_reposts
    vrss_reposts.deliver_notices()
    vrss_reposts.refresh_due(stop_event=stop_event)
    from . import vrss_conversations
    if stop_event is None or not stop_event.is_set():
        vrss_conversations.prepare_mentions()
        vrss_conversations.refresh_due(stop_event=stop_event)


def deliver_notices():
    from . import activity_center
    with v.database() as c:
        setup(c)
        for row in c.execute('SELECT * FROM vrss_social_notices WHERE delivered=0 LIMIT 20').fetchall():
            relation=c.execute("SELECT 1 FROM vrss_social_relations r JOIN vrss_social_peers p ON p.actor=r.actor WHERE r.actor=? AND r.direction='in' AND r.state='accepted' AND p.blocked=0",(row['actor'],)).fetchone()
            if not relation:
                c.execute('DELETE FROM vrss_social_notices WHERE id=?',(row['id'],))
                continue
            activity_center.emit_notification(source='VRSS',source_kind='system_app',source_key=v.APP_KEY,
                category='social',event_key='vrss.new_follower',title='New VRSS follower',
                body='A verified VRSS profile is now following you. Ask Agent Chat to show your VRSS followers.',
                dedupe_key='vrss-follower:'+row['id'],
                action_payload={'type':'open_app','app_key':v.APP_KEY,'verified_follow':True,'actor':row['actor'],'receipt':row['id']})
            c.execute('UPDATE vrss_social_notices SET delivered=1 WHERE id=?',(row['id'],))


def dismiss_notices(actor):
    from ..database import db
    with db() as c:
        rows=c.execute("SELECT id,action_json FROM notifications WHERE source_key=? AND event_key IN ('vrss.new_follower','vrss.new_like','vrss.new_comment','vrss.new_repost','vrss.mention','vrss.thread_reply')",(v.APP_KEY,)).fetchall()
        for row in rows:
            if json.loads(row['action_json']).get('actor')==actor:
                c.execute('UPDATE notifications SET dismissed_at=COALESCE(dismissed_at,CURRENT_TIMESTAMP) WHERE id=?',(row['id'],))


def status(cursor=''):
    if not isinstance(cursor,str):
        raise v.VRSSError('A valid connection cursor is required.',422)
    if cursor:
        actor_address(cursor)
    with v.database() as c:
        setup(c)
        p=v.profile(c)
        pause_reason = ''
        try:
            ready(c)
            if p.get('social_key_created') and not c.execute('SELECT 1 FROM vrss_social_identity WHERE id=1').fetchone():
                raise v.VRSSError('Signing key missing; restore the complete Feed backup.',409)
        except v.VRSSError as exc:
            pause_reason = str(exc)
        # A bounded peer page keeps control responses below the existing 256 KiB bridge limit.
        peers=c.execute('SELECT actor,blocked FROM vrss_social_peers WHERE actor>? ORDER BY actor LIMIT 26',(cursor,)).fetchall()
        page=peers[:25]
        rows=[dict(row) for row in c.execute("""SELECT r.*,p.name,p.blocked FROM vrss_social_relations r
            JOIN vrss_social_peers p ON p.actor=r.actor WHERE r.actor IN
            (SELECT actor FROM vrss_social_peers WHERE actor>? ORDER BY actor LIMIT 25) ORDER BY r.actor,r.direction""",(cursor,))]
        jobs=[dict(row) for row in c.execute("SELECT id,actor,state,attempts,due,error,json_extract(payload,'$.type') AS type FROM vrss_social_outbox WHERE state='pending' ORDER BY seq LIMIT 20")]
        blocked=[row['actor'] for row in page if row['blocked']]
        counts={row['direction']:row['n'] for row in c.execute("SELECT direction,count(*) AS n FROM vrss_social_relations r JOIN vrss_social_peers p ON p.actor=r.actor WHERE r.state='accepted' AND p.blocked=0 GROUP BY direction")}
        pending=c.execute("SELECT count(*) FROM vrss_social_outbox WHERE state='pending'").fetchone()[0]
        reviews=[dict(row) for row in c.execute("SELECT x.object,x.id FROM vrss_comments x JOIN vrss_social_relations r ON r.actor=x.actor AND r.direction='in' JOIN vrss_social_peers p ON p.actor=x.actor JOIN vrss_posts post ON x.object=? || '/vrss/v1/posts/' || post.id WHERE x.state='pending' AND r.state='accepted' AND r.follow_id=x.follow_id AND p.blocked=0 AND post.deleted=0 AND post.visibility='public' ORDER BY x.seq LIMIT 5", (p['public_base'],))]
        from . import vrss_reposts
        repost_states=[{**dict(row), **vrss_reposts.summary(c, p, row['object'])} for row in c.execute("SELECT object,enabled,verified_at,checked_at FROM vrss_repost_out ORDER BY created_at DESC,seq DESC LIMIT 5")]
        review=c.execute("SELECT count(*) FROM vrss_comments x JOIN vrss_social_relations r ON r.actor=x.actor AND r.direction='in' JOIN vrss_social_peers p ON p.actor=x.actor JOIN vrss_posts post ON x.object=? || '/vrss/v1/posts/' || post.id WHERE x.state='pending' AND r.state='accepted' AND r.follow_id=x.follow_id AND p.blocked=0 AND post.deleted=0 AND post.visibility='public'", (p['public_base'],)).fetchone()[0]
    from . import vrss_conversations
    conversations = vrss_conversations.status()
    return {'conversations':conversations,'enabled':bool(p['social_enabled'] and p['enabled']),'likes_enabled':bool(p['reactions_enabled']),'relations':rows,'blocked':blocked,'deliveries':jobs,'pause_reason':pause_reason,
            'followers':counts.get('in',0),'following':counts.get('out',0),'pending_deliveries':pending,'pending_comments':review,'comment_reviews':reviews,'repost_checks':repost_states,
            'next_cursor':page[-1]['actor'] if len(peers)>25 else ''}
