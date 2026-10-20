"""Origin-bound reactions to public VRSS posts, over the existing signed inbox."""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from urllib.parse import urlsplit

from . import vrss as v, vrss_social as s

CONTRACT = 'vp3.vrss.interactions.v1'
TYPES = {'Like', 'Unlike'}
SCHEMA = """
CREATE TABLE IF NOT EXISTS vrss_likes (
 id TEXT PRIMARY KEY, actor TEXT NOT NULL, object TEXT NOT NULL, follow_id TEXT NOT NULL,
 enabled INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS vrss_likes_object ON vrss_likes(object,actor,enabled);
CREATE TABLE IF NOT EXISTS vrss_like_out (
 object TEXT PRIMARY KEY, peer TEXT NOT NULL, reaction_id TEXT NOT NULL DEFAULT '',
 follow_id TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 0,
 operation_id TEXT NOT NULL DEFAULT '', likes INTEGER, checked_at REAL NOT NULL DEFAULT 0,
 revision INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS vrss_interaction_requests (
 id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS vrss_like_notices (
 id TEXT PRIMARY KEY, actor TEXT NOT NULL, object TEXT NOT NULL,
 created_at TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0);
"""


def setup(c):
    for statement in SCHEMA.split(';'):
        if statement.strip():
            c.execute(statement)
    if 'reactions_enabled' not in {row[1] for row in c.execute('PRAGMA table_info(vrss_profile)')}:
        c.execute('ALTER TABLE vrss_profile ADD COLUMN reactions_enabled INTEGER NOT NULL DEFAULT 0')


def address(value):
    value = v.safe_url(value, https_only=True)
    try:
        base, key = value.rsplit('/vrss/v1/posts/', 1)
        if len(value) > 400 or urlsplit(value).query or str(uuid.UUID(key)) != key:
            raise ValueError()
        s.actor_address(base + '/vrss/v1/actor')
    except (ValueError, TypeError, AttributeError) as exc:
        raise v.VRSSError('Use the canonical public VRSS post URL.', 422) from exc
    return value, base, key


def validate(a):
    obj, base, _ = address(a['object'])
    origin = a['actor'][:-len('/vrss/v1/actor')]
    prefix = origin + '/vrss/v1/reactions/'
    rid = a['reaction_id']
    if (not isinstance(rid, str) or not rid.startswith(prefix)
            or str(uuid.UUID(rid[len(prefix):])) != rid[len(prefix):]
            or a['recipient'] != base + '/vrss/v1/actor'):
        raise ValueError('Reaction ownership does not match.')


def public_post(c, p, obj):
    _, base, key = address(obj)
    if base != p['public_base']:
        return False
    return bool(c.execute("SELECT 1 FROM vrss_posts WHERE id=? AND visibility='public' AND deleted=0", (key,)).fetchone())


def count(c, p, obj):
    if not p['enabled'] or not p['social_enabled'] or not p['reactions_enabled'] or not public_post(c, p, obj):
        return 0
    return c.execute("""SELECT count(DISTINCT l.actor) FROM vrss_likes l
        JOIN vrss_social_peers p ON p.actor=l.actor
        JOIN vrss_social_relations r ON r.actor=l.actor AND r.direction='in'
        WHERE l.object=? AND l.enabled=1 AND p.blocked=0
        AND r.state='accepted' AND r.follow_id=l.follow_id""", (obj,)).fetchone()[0]


def public_view(c, p, obj):
    if not p['social_enabled'] or not p['reactions_enabled'] or not public_post(c, p, obj):
        raise v.VRSSError('This public post does not accept VRSS likes.', 404)
    return {'contract': CONTRACT, 'object': obj, 'actor': s.actor_url(p),
            'identity': p['actor_id'], 'likes': count(c, p, obj)}


def relation(c, peer, direction='out'):
    return c.execute("""SELECT r.* FROM vrss_social_relations r JOIN vrss_social_peers p ON p.actor=r.actor
        WHERE r.actor=? AND r.direction=? AND r.state='accepted' AND p.blocked=0""", (peer, direction)).fetchone()


def summary(c, p, obj):
    _, base, _ = address(obj)
    if base == p['public_base']:
        return {'object': obj, 'likes': count(c, p, obj), 'liked': False, 'can_like': False,
                'pending': False, 'state': 'local', 'checked_at': time.time()}
    peer = base + '/vrss/v1/actor'
    rel = relation(c, peer)
    row = c.execute('SELECT * FROM vrss_like_out WHERE object=?', (obj,)).fetchone()
    job = c.execute('SELECT state FROM vrss_social_outbox WHERE id=?', (row['operation_id'],)).fetchone() if row else None
    return {'object': obj, 'likes': row['likes'] if row else None,
            'liked': bool(row and row['enabled'] and rel and row['follow_id'] == rel['follow_id']),
            'can_like': bool(rel), 'pending': bool(rel and job and job['state'] == 'pending'),
            'state': job['state'] if job else 'idle', 'checked_at': row['checked_at'] if row else 0}


def feed_value(c, p, obj, feed_url=''):
    try:
        _, base, _ = address(obj)
        if base != p['public_base']:
            peer = c.execute('SELECT actor FROM vrss_social_peers WHERE rss=?', (feed_url,)).fetchone()
            if not peer or peer['actor'] != base + '/vrss/v1/actor' or not relation(c, peer['actor']):
                return None
        return summary(c, p, obj)
    except v.VRSSError:
        return None


def discover(obj, loader=None):
    obj, base, _ = address(obj)
    target = base + '/vrss/v1/actor'
    with v.database() as c:
        p = s.ready(c)
        if target == s.actor_url(p):
            raise v.VRSSError('Your own posts show received likes; choose another profile to like.', 422)
        rel = relation(c, target)
        if not rel:
            raise v.VRSSError('Follow this VRSS profile and wait for acceptance before liking its posts.', 409)
        fid = rel['follow_id']
        old = c.execute('SELECT revision FROM vrss_like_out WHERE object=?', (obj,)).fetchone()
        revision = old[0] if old else None
    doc = s.peer_document(target, loader)
    capability = doc.get('interactions')
    if not isinstance(capability, dict) or capability.get('contract') != CONTRACT or capability.get('likes') is not True:
        raise v.VRSSError('This profile has not enabled VRSS likes. Its owner may need to update Feed.', 409)
    value = (loader or s.exchange)(obj + '/interactions')
    if (not isinstance(value, dict) or value.get('contract') != CONTRACT or value.get('object') != obj
            or value.get('actor') != target or value.get('identity') != doc['identity']
            or type(value.get('likes')) is not int or not 0 <= value['likes'] <= 1000):
        raise v.VRSSError('The remote post does not match its VRSS identity.', 422)
    return obj, target, fid, revision, doc, value


def verified(c, target, fid, doc):
    p = s.ready(c)
    rel = relation(c, target)
    if not rel or rel['follow_id'] != fid:
        raise v.VRSSError('This VRSS follow changed during discovery.', 409)
    s.pin(c, doc)
    return p


def ensure_view(c, obj, peer):
    if not c.execute('SELECT 1 FROM vrss_like_out WHERE object=?', (obj,)).fetchone():
        if c.execute('SELECT count(*) FROM vrss_like_out').fetchone()[0] >= 10000:
            raise v.VRSSError('VRSS reaction cache is full.', 409)
        c.execute('INSERT INTO vrss_like_out(object,peer) VALUES(?,?)', (obj, peer))


def read(obj, loader=None):
    obj, base, _ = address(obj)
    with v.database() as c:
        p = v.profile(c)
        if base == p['public_base']:
            if not public_post(c, p, obj):
                raise v.VRSSError('Public post not found.', 404)
            return summary(c, p, obj)
    obj, target, fid, revision, doc, value = discover(obj, loader)
    with v.database() as c:
        p = verified(c, target, fid, doc)
        row = c.execute('SELECT revision FROM vrss_like_out WHERE object=?', (obj,)).fetchone()
        if (row[0] if row else None) == revision:
            ensure_view(c, obj, target)
            c.execute('UPDATE vrss_like_out SET likes=?,checked_at=?,revision=revision+1 WHERE object=?', (value['likes'], time.time(), obj))
        return summary(c, p, obj)


def set_like(obj, enabled, request_id, loader=None):
    obj, base, _ = address(obj)
    if type(enabled) is not bool or not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{16,80}', request_id):
        raise v.VRSSError('Choose like or unlike and include a stable request ID.', 422)
    fingerprint = hashlib.sha256(s.canonical([obj, enabled])).hexdigest()
    def replay(c):
        old = c.execute('SELECT * FROM vrss_interaction_requests WHERE id=?', (request_id,)).fetchone()
        if old:
            if old['fingerprint'] != fingerprint:
                raise v.VRSSError('This request ID belongs to another reaction.', 409)
            return {**json.loads(old['result']), 'reaction': summary(c, v.profile(c), obj), 'replayed': True}
    with v.database() as c:
        old = replay(c)
        if old is not None:
            return old
    discovered = discover(obj, loader) if enabled else None
    with v.database() as c:
        old = replay(c)
        if old is not None:
            return old
        if c.execute('SELECT count(*) FROM vrss_interaction_requests').fetchone()[0] >= 10000:
            raise v.VRSSError('VRSS reaction request history is full.', 409)
        p = v.profile(c)
        if base == p['public_base']:
            raise v.VRSSError('You cannot react to your own post.', 422)
        target = base + '/vrss/v1/actor'
        row = c.execute('SELECT * FROM vrss_like_out WHERE object=?', (obj,)).fetchone()
        rel = relation(c, target)
        if enabled:
            _, target, fid, view_revision, doc, value = discovered
            p = verified(c, target, fid, doc)
            ensure_view(c, obj, target)
            if (row['revision'] if row else None) == view_revision:
                c.execute('UPDATE vrss_like_out SET likes=?,checked_at=?,revision=revision+1 WHERE object=?', (value['likes'], time.time(), obj))
            if not row or not row['enabled'] or row['follow_id'] != fid:
                rid = p['public_base'] + '/vrss/v1/reactions/' + str(uuid.uuid4())
                job = s.enqueue(c, p, target, 'Like', fid, {'contract': CONTRACT, 'object': obj, 'reaction_id': rid})
                c.execute('UPDATE vrss_like_out SET enabled=1,reaction_id=?,follow_id=?,operation_id=?,revision=revision+1 WHERE object=?', (rid, fid, job['id'], obj))
        elif row and row['enabled']:
            c.execute("UPDATE vrss_social_outbox SET state='cancelled',lease='' WHERE state='pending' AND json_extract(payload,'$.reaction_id')=?", (row['reaction_id'],))
            operation = ''
            if rel and rel['follow_id'] == row['follow_id']:
                job = s.enqueue(c, p, target, 'Unlike', row['follow_id'], {'contract': CONTRACT, 'object': obj, 'reaction_id': row['reaction_id']})
                operation = job['id']
            c.execute('UPDATE vrss_like_out SET enabled=0,operation_id=?,revision=revision+1 WHERE object=?', (operation, obj))
        result = {'reaction': summary(c, p, obj), 'replayed': False}
        c.execute('INSERT INTO vrss_interaction_requests VALUES(?,?,?)', (request_id, fingerprint, json.dumps(result)))
        return result


def receive(c, p, peer, a):
    obj = a['object']
    old = c.execute('SELECT * FROM vrss_likes WHERE id=?', (a['reaction_id'],)).fetchone()
    if old and (old['actor'] != peer['actor'] or old['object'] != obj or old['follow_id'] != a['follow_id']):
        raise v.VRSSError('Reaction ID was reused for another post or follow.', 409)
    rel = relation(c, peer['actor'], 'in')
    if a['type'] == 'Unlike' and not old and not (rel and rel['follow_id'] == a['follow_id']):
        if not c.execute('SELECT 1 FROM vrss_social_revoked WHERE actor=? AND follow_id=?', (peer['actor'], a['follow_id'])).fetchone():
            raise v.VRSSError('An existing VRSS follow is required to remove a reaction.', 403)
    if a['type'] == 'Like':
        if not p['reactions_enabled'] or not public_post(c, p, obj):
            raise v.VRSSError('This public post does not accept VRSS likes.', 404)
        if not rel or rel['follow_id'] != a['follow_id']:
            raise v.VRSSError('An accepted current follow is required to like this post.', 403)
        if old and not old['enabled']:
            raise v.VRSSError('This reaction was removed. Use a new reaction ID.', 409)
        active = c.execute('SELECT * FROM vrss_likes WHERE actor=? AND object=? AND enabled=1', (peer['actor'], obj)).fetchall()
        for row in active:
            if row['follow_id'] == a['follow_id'] and row['id'] != a['reaction_id']:
                raise v.VRSSError('This profile already likes the post.', 409)
            if row['follow_id'] != a['follow_id']:
                c.execute('UPDATE vrss_likes SET enabled=0 WHERE id=?', (row['id'],))
    if not old:
        if c.execute('SELECT count(*) FROM vrss_likes').fetchone()[0] >= 10000:
            raise v.VRSSError('VRSS reaction history is full.', 409)
        c.execute('INSERT INTO vrss_likes VALUES(?,?,?,?,?)', (a['reaction_id'], peer['actor'], obj, a['follow_id'], int(a['type'] == 'Like')))
    elif a['type'] == 'Unlike':
        c.execute('UPDATE vrss_likes SET enabled=0 WHERE id=?', (a['reaction_id'],))
    notice = hashlib.sha256(s.canonical([peer['actor'], obj])).hexdigest()
    if a['type'] == 'Like':
        c.execute('INSERT OR IGNORE INTO vrss_like_notices(id,actor,object,created_at) VALUES(?,?,?,?)', (notice, peer['actor'], obj, v.now()))
    else:
        c.execute('UPDATE vrss_like_notices SET delivered=0 WHERE id=?', (notice,))
    return {'object': obj, 'actor': s.actor_url(p), 'likes': count(c, p, obj)}


def acknowledge(c, a, result):
    value = result.get('interaction')
    if (a.get('type') not in TYPES or not isinstance(value, dict) or value.get('object') != a.get('object')
            or value.get('actor') != a['recipient'] or type(value.get('likes')) is not int
            or not 0 <= value['likes'] <= 1000):
        return
    c.execute('UPDATE vrss_like_out SET likes=?,checked_at=?,revision=revision+1 WHERE object=?', (value['likes'], time.time(), a['object']))


def deliver_notices():
    from . import activity_center
    from ..database import db
    with v.database() as c:
        p = v.profile(c)
        for row in c.execute('SELECT * FROM vrss_like_notices WHERE delivered=0 LIMIT 20').fetchall():
            active = c.execute("""SELECT 1 FROM vrss_likes l JOIN vrss_social_relations r ON r.actor=l.actor
                JOIN vrss_social_peers p ON p.actor=l.actor WHERE l.actor=? AND l.object=? AND l.enabled=1
                AND p.blocked=0 AND r.direction='in' AND r.state='accepted' AND r.follow_id=l.follow_id""", (row['actor'], row['object'])).fetchone()
            key = 'vrss-like:' + row['id']
            if not active or not p['enabled'] or not p['social_enabled'] or not p['reactions_enabled'] or not public_post(c, p, row['object']):
                with db() as central:
                    central.execute('UPDATE notifications SET dismissed_at=COALESCE(dismissed_at,CURRENT_TIMESTAMP) WHERE source_key=? AND dedupe_key=?', (v.APP_KEY, key))
                c.execute('UPDATE vrss_like_notices SET delivered=2 WHERE id=?', (row['id'],))
                continue
            activity_center.emit_notification(source='VRSS', source_kind='system_app', source_key=v.APP_KEY,
                category='social', event_key='vrss.new_like', title='New VRSS like',
                body='A verified VRSS follower liked your post. Ask Agent Chat to show its VRSS likes.',
                dedupe_key=key, action_payload={'type': 'open_app', 'app_key': v.APP_KEY,
                    'verified_interaction': True, 'actor': row['actor'], 'post': row['object'], 'receipt': row['id']})
            c.execute('UPDATE vrss_like_notices SET delivered=1 WHERE id=?', (row['id'],))
