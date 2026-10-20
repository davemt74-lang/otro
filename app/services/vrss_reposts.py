"""Signed, author-consented reposts. Publish attributed pointers, never copied content."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid

from . import vrss as v, vrss_social as s, vrss_interactions as r

CONTRACT = 'vp3.vrss.reposts.v1'
TYPES = {'Repost', 'UndoRepost'}
FRESH_SECONDS = 900
_PUBLIC_READ_SLOTS = threading.BoundedSemaphore(4)
SCHEMA = """
CREATE TABLE IF NOT EXISTS vrss_reposts (
 id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL, actor TEXT NOT NULL, object TEXT NOT NULL,
 follow_id TEXT NOT NULL, enabled INTEGER NOT NULL, notice_delivered INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS vrss_reposts_object ON vrss_reposts(object,actor,enabled);
CREATE TABLE IF NOT EXISTS vrss_repost_out (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, object TEXT UNIQUE NOT NULL, peer TEXT NOT NULL,
 repost_id TEXT NOT NULL DEFAULT '', follow_id TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 0,
 operation_id TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 0,
 source_identity TEXT NOT NULL DEFAULT '', source_name TEXT NOT NULL DEFAULT '',
 reposts INTEGER, verified_at REAL NOT NULL DEFAULT 0, checked_at REAL NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS vrss_repost_requests (
 id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL);
"""


def setup(c):
    for statement in SCHEMA.split(';'):
        if statement.strip():
            c.execute(statement)
    if 'reposts_enabled' not in {row[1] for row in c.execute('PRAGMA table_info(vrss_profile)')}:
        c.execute('ALTER TABLE vrss_profile ADD COLUMN reposts_enabled INTEGER NOT NULL DEFAULT 0')


def repost_address(value):
    if not isinstance(value, str):
        raise v.VRSSError('A canonical VRSS repost ID is required.', 422)
    try:
        base, key = value.rsplit('/vrss/v1/reposts/', 1)
        if len(value) > 400 or str(uuid.UUID(key)) != key:
            raise ValueError()
        s.actor_address(base + '/vrss/v1/actor')
    except (ValueError, TypeError, AttributeError) as exc:
        raise v.VRSSError('A canonical VRSS repost ID is required.', 422) from exc
    return value, base


def validate(a):
    _, base, _ = r.address(a['object'])
    _, origin = repost_address(a['repost_id'])
    if a['actor'] != origin + '/vrss/v1/actor' or a['recipient'] != base + '/vrss/v1/actor':
        raise ValueError('Repost ownership does not match.')


def count(c, p, obj):
    if not p['enabled'] or not p['social_enabled'] or not p['reposts_enabled'] or not r.public_post(c, p, obj):
        return 0
    return c.execute("""SELECT count(DISTINCT x.actor) FROM vrss_reposts x
        JOIN vrss_social_relations rel ON rel.actor=x.actor AND rel.direction='in'
        JOIN vrss_social_peers peer ON peer.actor=x.actor
        WHERE x.object=? AND x.enabled=1 AND rel.state='accepted'
        AND rel.follow_id=x.follow_id AND peer.blocked=0""", (obj,)).fetchone()[0]


def source_view(c, p, obj, digest=''):
    if not p['enabled'] or not p['social_enabled'] or not p['reposts_enabled'] or not r.public_post(c, p, obj):
        raise v.VRSSError('This public post does not permit VRSS reposts.', 404)
    result = {'contract': CONTRACT, 'object': obj, 'actor': s.actor_url(p),
              'identity': p['actor_id'], 'name': p['display_name'], 'reposts': count(c, p, obj)}
    if digest:
        row = c.execute('SELECT * FROM vrss_reposts WHERE object=? AND digest=?', (obj, digest)).fetchone()
        if not row:
            raise v.VRSSError('Repost receipt not found.', 404)
        rel = r.relation(c, row['actor'], 'in')
        result['repost'] = {'id': row['id'], 'actor': row['actor'],
                            'active': bool(row['enabled'] and rel and rel['follow_id'] == row['follow_id'])}
    return result


def summary(c, p, obj):
    _, base, _ = r.address(obj)
    if base == p['public_base']:
        return {'object': obj, 'reposts': count(c, p, obj), 'reposted': False,
                'can_repost': False, 'pending': False, 'published': False, 'state': 'local'}
    target = base + '/vrss/v1/actor'
    rel = r.relation(c, target)
    row = c.execute('SELECT * FROM vrss_repost_out WHERE object=?', (obj,)).fetchone()
    job = c.execute('SELECT state FROM vrss_social_outbox WHERE id=?', (row['operation_id'],)).fetchone() if row else None
    current = bool(row and row['enabled'] and rel and row['follow_id'] == rel['follow_id'])
    published = bool(current and p['enabled'] and p['social_enabled'] and job and job['state'] == 'delivered'
                     and row['verified_at'] > time.time() - FRESH_SECONDS)
    return {'object': obj, 'reposts': row['reposts'] if row else None, 'reposted': current,
            'can_repost': bool(rel and p['enabled'] and p['social_enabled']),
            'can_undo': bool(row and row['enabled']), 'pending': bool(job and job['state'] == 'pending'),
            'published': published, 'state': job['state'] if job else 'idle',
            'checked_at': row['checked_at'] if row else 0,
            'url': row['repost_id'] if row else ''}


def discovery(obj, loader=None):
    obj, base, _ = r.address(obj)
    target = base + '/vrss/v1/actor'
    with v.database() as c:
        p = s.ready(c)
        if target == s.actor_url(p):
            raise v.VRSSError('Choose another profile’s original post to repost.', 422)
        rel = r.relation(c, target)
        if not rel:
            raise v.VRSSError('An accepted current VRSS follow is required to repost.', 409)
        fid = rel['follow_id']
        row = c.execute('SELECT * FROM vrss_repost_out WHERE object=?', (obj,)).fetchone()
        revision = row['revision'] if row else None
        job = c.execute('SELECT state FROM vrss_social_outbox WHERE id=?', (row['operation_id'],)).fetchone() if row else None
        key = row['repost_id'] if row and row['enabled'] and row['follow_id'] == fid and job and job['state'] == 'delivered' else ''
    doc = s.peer_document(target, loader)
    cap = doc.get('reposts')
    if not isinstance(cap, dict) or cap.get('contract') != CONTRACT or cap.get('enabled') is not True:
        raise v.VRSSError('The original author has not enabled VRSS reposts.', 409)
    suffix = '/id-' + hashlib.sha256(key.encode()).hexdigest() if key else ''
    value = (loader or s.exchange)(obj + '/reposts' + suffix)
    if (not isinstance(value, dict) or value.get('contract') != CONTRACT or value.get('object') != obj
            or value.get('actor') != target or value.get('identity') != doc['identity']
            or value.get('name') != doc['name'] or type(value.get('reposts')) is not int or not 0 <= value['reposts'] <= 1000):
        raise v.VRSSError('The repost projection does not match its original author.', 422)
    if key:
        item = value.get('repost')
        if (not isinstance(item, dict) or item.get('id') != key or item.get('actor') != s.actor_url(p)
                or type(item.get('active')) is not bool):
            raise v.VRSSError('The author did not confirm this exact repost.', 422)
    return obj, target, fid, revision, doc, value


def read(obj, loader=None):
    obj, base, _ = r.address(obj)
    with v.database() as c:
        p = v.profile(c)
        if base == p['public_base']:
            source_view(c, p, obj)
            return summary(c, p, obj)
        before = c.execute('SELECT revision FROM vrss_repost_out WHERE object=?', (obj,)).fetchone()
        revision = before[0] if before else None
    try:
        _, target, fid, revision, doc, value = discovery(obj, loader)
    except Exception:
        # A failed current check fences older successful reads too.
        with v.database() as c:
            c.execute('UPDATE vrss_repost_out SET verified_at=0,checked_at=?,revision=revision+1 WHERE object=? AND revision=?', (time.time(), obj, revision))
        raise
    with v.database() as c:
        p = r.verified(c, target, fid, doc)
        old = c.execute('SELECT revision FROM vrss_repost_out WHERE object=?', (obj,)).fetchone()
        if old and old[0] == revision:
            active = value.get('repost', {}).get('active') is True
            c.execute('UPDATE vrss_repost_out SET reposts=?,verified_at=?,checked_at=?,source_name=?,revision=revision+1 WHERE object=?',
                      (value['reposts'], time.time() if active else 0, time.time(), doc['name'], obj))
        result = summary(c, p, obj)
        result['reposts'] = value['reposts']
        return result


def set_repost(obj, enabled, request_id, loader=None):
    obj, base, _ = r.address(obj)
    if type(enabled) is not bool or not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{16,80}', request_id):
        raise v.VRSSError('Choose repost or undo and include a stable request ID.', 422)
    fingerprint = hashlib.sha256(s.canonical([obj, enabled])).hexdigest()
    def replay(c):
        old = c.execute('SELECT * FROM vrss_repost_requests WHERE id=?', (request_id,)).fetchone()
        if old:
            if old['fingerprint'] != fingerprint:
                raise v.VRSSError('This request ID belongs to another repost operation.', 409)
            return {**json.loads(old['result']), 'repost': summary(c, v.profile(c), obj), 'replayed': True}
    with v.database() as c:
        old = replay(c)
        if old is not None:
            return old
    discovered = discovery(obj, loader) if enabled else None
    with v.database() as c:
        old = replay(c)
        if old is not None:
            return old
        if c.execute('SELECT count(*) FROM vrss_repost_requests').fetchone()[0] >= 10000:
            raise v.VRSSError('VRSS repost request history is full.', 409)
        p = v.profile(c)
        if base == p['public_base']:
            raise v.VRSSError('Choose another profile’s original post to repost.', 422)
        row = c.execute('SELECT * FROM vrss_repost_out WHERE object=?', (obj,)).fetchone()
        if enabled:
            _, target, fid, _, doc, value = discovered
            p = r.verified(c, target, fid, doc)
            if not row or not row['enabled'] or row['follow_id'] != fid:
                if not row and c.execute('SELECT count(*) FROM vrss_repost_out').fetchone()[0] >= 10000:
                    raise v.VRSSError('VRSS repost history is full.', 409)
                key = p['public_base'] + '/vrss/v1/reposts/' + str(uuid.uuid4())
                job = s.enqueue(c, p, target, 'Repost', fid, {'contract': CONTRACT, 'object': obj, 'repost_id': key})
                c.execute("""INSERT INTO vrss_repost_out(object,peer,repost_id,follow_id,enabled,operation_id,source_identity,source_name,reposts,checked_at,created_at)
                    VALUES(?,?,?,?,1,?,?,?,?,?,?) ON CONFLICT(object) DO UPDATE SET repost_id=excluded.repost_id,follow_id=excluded.follow_id,
                    enabled=1,operation_id=excluded.operation_id,source_identity=excluded.source_identity,source_name=excluded.source_name,
                    reposts=excluded.reposts,verified_at=0,checked_at=excluded.checked_at,created_at=excluded.created_at,revision=revision+1""",
                          (obj, target, key, fid, job['id'], doc['identity'], doc['name'], value['reposts'], time.time(), v.now()))
        elif row and row['enabled']:
            c.execute("UPDATE vrss_social_outbox SET state='cancelled',lease='' WHERE state='pending' AND json_extract(payload,'$.repost_id')=?", (row['repost_id'],))
            job = s.enqueue(c, p, row['peer'], 'UndoRepost', row['follow_id'], {'contract': CONTRACT, 'object': obj, 'repost_id': row['repost_id']})
            c.execute('UPDATE vrss_repost_out SET enabled=0,verified_at=0,operation_id=?,revision=revision+1 WHERE object=?', (job['id'], obj))
        result = {'repost': summary(c, p, obj), 'replayed': False}
        c.execute('INSERT INTO vrss_repost_requests VALUES(?,?,?)', (request_id, fingerprint, json.dumps(result)))
        return result


def receive(c, p, peer, a):
    key, obj = a['repost_id'], a['object']
    row = c.execute('SELECT * FROM vrss_reposts WHERE id=?', (key,)).fetchone()
    if row and (row['actor'] != peer['actor'] or row['object'] != obj or row['follow_id'] != a['follow_id']):
        raise v.VRSSError('Repost ID belongs to another post or follow.', 409)
    rel = r.relation(c, peer['actor'], 'in')
    current = bool(rel and rel['follow_id'] == a['follow_id'])
    if a['type'] == 'Repost':
        source_view(c, p, obj)
        if not current:
            raise v.VRSSError('A current accepted follow is required to repost.', 403)
        if row and not row['enabled']:
            raise v.VRSSError('This repost was undone. Use a new repost ID.', 409)
        active = c.execute('SELECT * FROM vrss_reposts WHERE actor=? AND object=? AND enabled=1', (peer['actor'], obj)).fetchall()
        for other in active:
            if other['follow_id'] == a['follow_id'] and other['id'] != key:
                raise v.VRSSError('This profile already reposts the post.', 409)
            if other['follow_id'] != a['follow_id']:
                c.execute('UPDATE vrss_reposts SET enabled=0,notice_delivered=0 WHERE id=?', (other['id'],))
    elif not row and not current and not c.execute('SELECT 1 FROM vrss_social_revoked WHERE actor=? AND follow_id=?', (peer['actor'], a['follow_id'])).fetchone():
        raise v.VRSSError('A known follow is required to undo a repost.', 403)
    if not row:
        if c.execute('SELECT count(*) FROM vrss_reposts').fetchone()[0] >= 10000:
            raise v.VRSSError('VRSS repost history is full.', 409)
        c.execute('INSERT INTO vrss_reposts(id,digest,actor,object,follow_id,enabled) VALUES(?,?,?,?,?,?)',
                  (key, hashlib.sha256(key.encode()).hexdigest(), peer['actor'], obj, a['follow_id'], int(a['type'] == 'Repost')))
    elif a['type'] == 'UndoRepost':
        c.execute('UPDATE vrss_reposts SET enabled=0,notice_delivered=0 WHERE id=?', (key,))
    return acknowledgement(c, p, a)


def acknowledgement(c, p, a):
    row = c.execute('SELECT * FROM vrss_reposts WHERE id=?', (a['repost_id'],)).fetchone()
    rel = r.relation(c, a['actor'], 'in')
    return {'object': a['object'], 'id': a['repost_id'], 'actor': s.actor_url(p),
            'active': bool(row and row['enabled'] and rel and rel['follow_id'] == row['follow_id']
                           and p['enabled'] and p['social_enabled'] and p['reposts_enabled'] and r.public_post(c, p, a['object'])),
            'reposts': count(c, p, a['object'])}


def acknowledge(c, a, result, started_at=None):
    if a.get('type') not in TYPES:
        return
    ack = result.get('repost')
    if (not isinstance(ack, dict) or ack.get('id') != a['repost_id'] or ack.get('object') != a['object']
            or ack.get('actor') != a['recipient'] or type(ack.get('active')) is not bool
            or type(ack.get('reposts')) is not int or not 0 <= ack['reposts'] <= 1000
            or (a['type'] == 'UndoRepost' and ack['active'])):
        raise v.VRSSError('Remote VRSS did not acknowledge this exact repost.', 502)
    c.execute('UPDATE vrss_repost_out SET verified_at=?,checked_at=?,reposts=?,revision=revision+1 WHERE object=? AND operation_id=? AND checked_at<=?',
              (time.time() if a['type'] == 'Repost' and ack['active'] else 0, time.time(), ack['reposts'], a['object'], a['id'], started_at if started_at is not None else time.time()))


def pointer(c, p, row, owner=False):
    state = summary(c, p, row['object'])
    if not owner and not state['published']:
        raise v.VRSSError('This repost is not currently published.', 404)
    return {'contract': CONTRACT, 'id': row['repost_id'], 'url': row['repost_id'],
            'body': 'Reposted from ' + row['source_name'] + '. Open the original post for its current content.',
            'media': [], 'created_at': row['created_at'], 'visibility': 'public', 'can_delete': False,
            'author': {'display_name': p['display_name'], 'id': p['actor_id'], 'name': p['display_name'], 'url': p['profile_url']},
            'original': {'url': row['object'], 'actor': row['peer'], 'identity': row['source_identity'], 'name': row['source_name']},
            'repost': state if owner else {'object': row['object'], 'reposts': state['reposts'], 'published': True}}


def public_pointer(key, loader=None):
    repost_address(key)
    s.rate_limit('public-repost:' + key)
    if not _PUBLIC_READ_SLOTS.acquire(blocking=False):
        raise v.VRSSError('VRSS repost verification is busy.', 429)
    try:
        return _public_pointer(key, loader)
    finally:
        _PUBLIC_READ_SLOTS.release()


def _public_pointer(key, loader=None):
    with v.database() as c:
        p = s.ready(c)
        row = c.execute('SELECT * FROM vrss_repost_out WHERE repost_id=? AND enabled=1', (key,)).fetchone()
        if not row or not summary(c, p, row['object'])['published']:
            raise v.VRSSError('Published repost not found.', 404)
        obj = row['object']
    read(obj, loader)
    with v.database() as c:
        p = s.ready(c)
        row = c.execute('SELECT * FROM vrss_repost_out WHERE repost_id=?', (key,)).fetchone()
        if not row:
            raise v.VRSSError('Published repost not found.', 404)
        return pointer(c, p, row)


def refresh_due(loader=None, stop_event=None):
    with v.database() as c:
        rows = c.execute("""SELECT x.object FROM vrss_repost_out x
            JOIN vrss_social_relations rel ON rel.actor=x.peer AND rel.direction='out' AND rel.state='accepted' AND rel.follow_id=x.follow_id
            JOIN vrss_social_peers peer ON peer.actor=x.peer AND peer.blocked=0
            JOIN vrss_social_outbox job ON job.id=x.operation_id AND job.state='delivered'
            WHERE x.enabled=1 AND x.checked_at<? ORDER BY x.checked_at LIMIT 2""", (time.time() - 60,)).fetchall()
    for row in rows:
        if stop_event is not None and stop_event.is_set():
            break
        try:
            read(row['object'], loader)
        except Exception:
            pass


def deliver_notices():
    from . import activity_center
    from ..database import db
    with v.database() as c:
        p = v.profile(c)
        rows = c.execute('SELECT * FROM vrss_reposts WHERE notice_delivered=0 LIMIT 20').fetchall()
        for row in rows:
            key = 'vrss-repost:' + row['digest']
            live = acknowledgement(c, p, {'object': row['object'], 'repost_id': row['id'], 'actor': row['actor']})['active']
            if not live:
                with db() as central:
                    central.execute('UPDATE notifications SET dismissed_at=COALESCE(dismissed_at,CURRENT_TIMESTAMP) WHERE source_key=? AND dedupe_key=?', (v.APP_KEY, key))
            else:
                activity_center.emit_notification(source='VRSS', source_kind='system_app', source_key=v.APP_KEY,
                    category='social', event_key='vrss.new_repost', title='New VRSS repost',
                    body='A verified follower reposted your public post. Ask Agent Chat to show its VRSS reposts.',
                    dedupe_key=key, action_payload={'type': 'open_app', 'app_key': v.APP_KEY, 'verified_repost': True,
                        'actor': row['actor'], 'post': row['object'], 'repost': row['id'], 'receipt': row['digest']})
            c.execute('UPDATE vrss_reposts SET notice_delivered=1 WHERE id=?', (row['id'],))
