"""Signed, immutable comments. The post owner controls public thread visibility."""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid

from . import vrss as v, vrss_social as s, vrss_interactions as r

CONTRACT = 'vp3.vrss.comments.v1'
TYPES = {'Comment', 'DeleteComment'}
SCHEMA = """
CREATE TABLE IF NOT EXISTS vrss_comments (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, digest TEXT UNIQUE NOT NULL, actor TEXT NOT NULL,
 object TEXT NOT NULL, follow_id TEXT NOT NULL, parent TEXT NOT NULL, body TEXT NOT NULL,
 state TEXT NOT NULL, depth INTEGER NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL, notice_delivered INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS vrss_comments_post ON vrss_comments(object,seq);
CREATE TABLE IF NOT EXISTS vrss_comment_out (
 id TEXT PRIMARY KEY, object TEXT NOT NULL, peer TEXT NOT NULL, follow_id TEXT NOT NULL,
 parent TEXT NOT NULL, body TEXT NOT NULL, operation_id TEXT NOT NULL,
 state TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS vrss_comment_requests (
 id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL);
"""


def setup(c):
    for statement in SCHEMA.split(';'):
        if statement.strip():
            c.execute(statement)
    if 'comments_enabled' not in {row[1] for row in c.execute('PRAGMA table_info(vrss_profile)')}:
        c.execute('ALTER TABLE vrss_profile ADD COLUMN comments_enabled INTEGER NOT NULL DEFAULT 0')


def comment_address(value):
    if not isinstance(value, str):
        raise v.VRSSError('A canonical VRSS comment ID is required.', 422)
    try:
        base, key = value.rsplit('/vrss/v1/comments/', 1)
        if str(uuid.UUID(key)) != key:
            raise ValueError()
        s.actor_address(base + '/vrss/v1/actor')
        if len(value) > 400:
            raise ValueError()
    except (ValueError, TypeError, AttributeError) as exc:
        raise v.VRSSError('A canonical VRSS comment ID is required.', 422) from exc
    return value, base


def body_text(value):
    value = v.text(value, 2000, 'Comment', True)
    if len(value.encode('utf-8')) > 2000:
        raise v.VRSSError('Comment is too long. Shorten it before posting.', 422)
    return value


def cursor(value):
    if not isinstance(value, str) or not re.fullmatch(r'0|[1-9][0-9]{0,15}', value) or int(value) >= 2**53:
        raise v.VRSSError('A valid comment cursor is required.', 422)
    return int(value)


def validate(a):
    _, base, _ = r.address(a['object'])
    _, origin = comment_address(a['comment_id'])
    if origin + '/vrss/v1/actor' != a['actor'] or a['recipient'] != base + '/vrss/v1/actor':
        raise ValueError('Comment ownership does not match.')
    if a['type'] == 'Comment':
        if body_text(a['body']) != a['body']:
            raise ValueError('Comment text is not canonical.')
        if a['parent'] != '':
            comment_address(a['parent'])


def active(c, p, row):
    if row['actor'] == s.actor_url(p):
        return True
    rel = r.relation(c, row['actor'], 'in')
    return bool(rel and rel['follow_id'] == row['follow_id'])


def visible(c, p, row):
    for _ in range(5):
        if row is None or row['state'] != 'approved' or not active(c, p, row):
            return False
        if not row['parent']:
            return True
        row = c.execute('SELECT * FROM vrss_comments WHERE id=?', (row['parent'],)).fetchone()
    return False


def value(c, p, row, owner=False):
    peer = c.execute('SELECT name,identity FROM vrss_social_peers WHERE actor=?', (row['actor'],)).fetchone()
    local = row['actor'] == s.actor_url(p)
    result = {'id': row['id'], 'actor': row['actor'], 'name': p['display_name'] if local else peer['name'],
              'identity': p['actor_id'] if local else peer['identity'], 'parent': row['parent'],
              'body': row['body'], 'created_at': row['created_at'], 'depth': row['depth']}
    if owner:
        result.update(state=row['state'], revision=row['revision'], can_delete=local,
                      can_moderate=not local and active(c, p, row) and row['state'] != 'deleted',
                      published=bool(p['enabled'] and p['social_enabled'] and p['comments_enabled'] and visible(c, p, row)))
    return result


def page(c, p, obj, after='0', owner=False):
    if not r.public_post(c, p, obj):
        raise v.VRSSError('Public post not found.', 404)
    if not owner and (not p['enabled'] or not p['social_enabled'] or not p['comments_enabled']):
        raise v.VRSSError('This post does not accept VRSS comments.', 404)
    after = cursor(after)
    # Scan at most five candidates; pages remain below the signed transport's 32 KiB ceiling.
    rows = c.execute("SELECT * FROM vrss_comments WHERE object=? AND seq>? AND state IN "
                     + ("('pending','approved','hidden')" if owner else "('approved')")
                     + ' ORDER BY seq LIMIT 6', (obj, after)).fetchall()
    items = [value(c, p, row, owner) for row in rows[:5] if owner or visible(c, p, row)]
    return {'contract': CONTRACT, 'object': obj, 'actor': s.actor_url(p), 'identity': p['actor_id'],
            'items': items, 'next_cursor': str(rows[4]['seq']) if len(rows) > 5 else '',
            'owner': owner, 'can_comment': bool(p['enabled'] and p['social_enabled'] and p['comments_enabled'])}


def parent_depth(c, p, obj, parent):
    if parent == '':
        return 0
    comment_address(parent)
    row = c.execute('SELECT * FROM vrss_comments WHERE id=? AND object=?', (parent, obj)).fetchone()
    if not row or not visible(c, p, row) or row['depth'] >= 4:
        raise v.VRSSError('Reply to a published comment in this post, up to four reply levels.', 409)
    return row['depth'] + 1


def receive(c, p, peer, a):
    obj, key = a['object'], a['comment_id']
    old = c.execute('SELECT * FROM vrss_comments WHERE id=?', (key,)).fetchone()
    if old and (old['actor'] != peer['actor'] or old['object'] != obj or old['follow_id'] != a['follow_id']):
        raise v.VRSSError('Comment ID belongs to another post or follow.', 409)
    rel = r.relation(c, peer['actor'], 'in')
    current = bool(rel and rel['follow_id'] == a['follow_id'])
    if a['type'] == 'Comment':
        if not p['comments_enabled'] or not r.public_post(c, p, obj):
            raise v.VRSSError('This public post does not accept VRSS comments.', 404)
        if not current:
            raise v.VRSSError('An accepted current follow is required to comment.', 403)
        if old:
            if old['state'] == 'deleted' or old['body'] != a['body'] or old['parent'] != a['parent']:
                raise v.VRSSError('Comments are immutable; this ID cannot be reused.', 409)
        else:
            depth = parent_depth(c, p, obj, a['parent'])
            if c.execute('SELECT count(*) FROM vrss_comments').fetchone()[0] >= 10000:
                raise v.VRSSError('VRSS comment history is full.', 409)
            c.execute("INSERT INTO vrss_comments(id,digest,actor,object,follow_id,parent,body,state,depth,created_at) VALUES(?,?,?,?,?,?,?,'pending',?,?)",
                      (key, hashlib.sha256(key.encode()).hexdigest(), peer['actor'], obj, a['follow_id'], a['parent'], a['body'], depth, v.now()))
    else:
        if not old and not current and not c.execute('SELECT 1 FROM vrss_social_revoked WHERE actor=? AND follow_id=?', (peer['actor'], a['follow_id'])).fetchone():
            raise v.VRSSError('A known follow is required to retract a comment.', 403)
        if old:
            c.execute("UPDATE vrss_comments SET state='deleted',body='',revision=revision+1,notice_delivered=0 WHERE id=? AND state!='deleted'", (key,))
        else:
            if c.execute('SELECT count(*) FROM vrss_comments').fetchone()[0] >= 10000:
                raise v.VRSSError('VRSS comment history is full.', 409)
            c.execute("INSERT INTO vrss_comments(id,digest,actor,object,follow_id,parent,body,state,depth,created_at) VALUES(?,?,?,?,?,'','','deleted',0,?)", (key, hashlib.sha256(key.encode()).hexdigest(), peer['actor'], obj, a['follow_id'], v.now()))
    row = c.execute('SELECT * FROM vrss_comments WHERE id=?', (key,)).fetchone()
    return {'object': obj, 'id': key, 'state': row['state']}


def receipt(c, request_id, payload, apply):
    if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{16,80}', request_id):
        raise v.VRSSError('Include a stable comment request ID.', 422)
    digest = hashlib.sha256(s.canonical(payload)).hexdigest()
    old = c.execute('SELECT * FROM vrss_comment_requests WHERE id=?', (request_id,)).fetchone()
    if old:
        if old['fingerprint'] != digest:
            raise v.VRSSError('This request ID belongs to another comment operation.', 409)
        return {**json.loads(old['result']), 'replayed': True}
    if c.execute('SELECT count(*) FROM vrss_comment_requests').fetchone()[0] >= 10000:
        raise v.VRSSError('VRSS comment request history is full.', 409)
    result = apply()
    c.execute('INSERT INTO vrss_comment_requests VALUES(?,?,?)', (request_id, digest, json.dumps(result)))
    return {**result, 'replayed': False}


def discovery(obj, loader=None):
    obj, base, _ = r.address(obj)
    target = base + '/vrss/v1/actor'
    with v.database() as c:
        p = s.ready(c)
        rel = r.relation(c, target)
        if not rel:
            raise v.VRSSError('Follow this VRSS profile and wait for acceptance before commenting.', 409)
        fid = rel['follow_id']
    doc = s.peer_document(target, loader)
    cap = doc.get('comments')
    if not isinstance(cap, dict) or cap.get('contract') != CONTRACT or cap.get('enabled') is not True:
        raise v.VRSSError('This profile has not enabled VRSS comments.', 409)
    return obj, target, fid, doc


def read(obj, after='0', loader=None, parent=''):
    obj, base, _ = r.address(obj)
    cursor(after)
    with v.database() as c:
        p = v.profile(c)
        if base == p['public_base']:
            return page(c, p, obj, after, owner=True)
    obj, target, fid, doc = discovery(obj, loader)
    suffix = '/id-' + hashlib.sha256(parent.encode()).hexdigest() if parent else ('/' + after if after != '0' else '')
    result = (loader or s.exchange)(obj + '/comments' + suffix)
    if (not isinstance(result, dict) or result.get('contract') != CONTRACT or result.get('object') != obj
            or result.get('actor') != target or result.get('identity') != doc['identity']
            or not isinstance(result.get('items'), list) or len(result['items']) > 5
            or not isinstance(result.get('next_cursor'), str)):
        raise v.VRSSError('Remote comment thread does not match its post author.', 422)
    if result['next_cursor']:
        if cursor(result['next_cursor']) <= cursor(after):
            raise v.VRSSError('Remote comment cursor did not advance.', 422)
    items = []
    for item in result['items']:
        try:
            _, origin = comment_address(item['id'])
            if item['actor'] != origin + '/vrss/v1/actor' or body_text(item['body']) != item['body']:
                raise ValueError()
            v.text(item['name'], 120, 'Comment author', True)
            v.text(item['created_at'], 80, 'Comment time', True)
            if item['parent']:
                comment_address(item['parent'])
            if type(item['depth']) is not int or not 0 <= item['depth'] <= 4:
                raise ValueError()
            if 'urn:uuid:' + str(uuid.UUID(item['identity'].removeprefix('urn:uuid:'))) != item['identity']:
                raise ValueError()
            items.append({k: item[k] for k in ('id','actor','identity','name','parent','body','created_at','depth')})
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise v.VRSSError('Remote comment thread is invalid.', 422) from exc
    if parent and (len(items) != 1 or items[0]['id'] != parent or items[0]['depth'] >= 4):
        raise v.VRSSError('Reply to a published comment in this post, up to four reply levels.', 409)
    with v.database() as c:
        p = r.verified(c, target, fid, doc)
        for item in items:
            own = c.execute('SELECT state,operation_id FROM vrss_comment_out WHERE id=?', (item['id'],)).fetchone() if item['actor'] == s.actor_url(p) else None
            item['can_delete'] = bool(own and own['state'] != 'deleted')
            if own and own['state'] == 'deleted':
                job = c.execute('SELECT state FROM vrss_social_outbox WHERE id=?', (own['operation_id'],)).fetchone()
                if job and job['state'] == 'pending':
                    item['state'] = 'retraction_pending'
        pending = [dict(row) for row in c.execute("SELECT id,parent,body,state,created_at FROM vrss_comment_out WHERE object=? AND follow_id=? AND state!='deleted' ORDER BY created_at DESC LIMIT 5", (obj, fid))]
    return {'contract': CONTRACT, 'object': obj, 'actor': target, 'identity': doc['identity'],
            'items': items, 'next_cursor': result['next_cursor'], 'owner': False, 'can_comment': True, 'outgoing': pending}


def create(obj, body, parent, request_id, loader=None):
    obj, base, _ = r.address(obj)
    body = body_text(body)
    if parent != '':
        comment_address(parent)
    payload = ['create', obj, body, parent]
    if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{16,80}', request_id):
        raise v.VRSSError('Include a stable comment request ID.', 422)
    # Exact retries do not require a now-unavailable destination, and never enqueue twice.
    with v.database() as c:
        if c.execute('SELECT 1 FROM vrss_comment_requests WHERE id=?', (request_id,)).fetchone():
            return receipt(c, request_id, payload, lambda: None)
        local = base == v.profile(c)['public_base']
    if not local:
        read(obj, loader=loader, parent=parent)
    discovered = None if local else discovery(obj, loader)
    with v.database() as c:
        def apply():
            p = s.ready(c)
            if not p['comments_enabled'] and local:
                raise v.VRSSError('Enable comments in Profile first.', 409)
            key = p['public_base'] + '/vrss/v1/comments/' + str(uuid.uuid4())
            if local:
                if not r.public_post(c, p, obj):
                    raise v.VRSSError('Public post not found.', 404)
                depth = parent_depth(c, p, obj, parent)
                if c.execute('SELECT count(*) FROM vrss_comments').fetchone()[0] >= 10000:
                    raise v.VRSSError('VRSS comment history is full.', 409)
                c.execute("INSERT INTO vrss_comments(id,digest,actor,object,follow_id,parent,body,state,depth,created_at,notice_delivered) VALUES(?,?,?,?,'',?,?,'approved',?,?,1)", (key, hashlib.sha256(key.encode()).hexdigest(), s.actor_url(p), obj, parent, body, depth, v.now()))
                return {'comment': {'id': key, 'object': obj, 'state': 'approved'}}
            _, target, fid, doc = discovered
            p = r.verified(c, target, fid, doc)
            if c.execute('SELECT count(*) FROM vrss_comment_out').fetchone()[0] >= 10000:
                raise v.VRSSError('VRSS outgoing comment history is full.', 409)
            job = s.enqueue(c, p, target, 'Comment', fid, {'contract': CONTRACT, 'object': obj, 'comment_id': key, 'body': body, 'parent': parent})
            c.execute("INSERT INTO vrss_comment_out VALUES(?,?,?,?,?,?,?,'pending_delivery',?)", (key, obj, target, fid, parent, body, job['id'], v.now()))
            return {'comment': {'id': key, 'object': obj, 'state': 'pending_delivery'}}
        return receipt(c, request_id, payload, apply)


def remove(obj, key, request_id):
    obj, base, _ = r.address(obj)
    comment_address(key)
    with v.database() as c:
        def apply():
            p = v.profile(c)
            if key.rsplit('/vrss/v1/comments/', 1)[0] != p['public_base']:
                raise v.VRSSError('Only the comment author can retract it. Post owners can hide comments.', 403)
            if base == p['public_base']:
                row = c.execute('SELECT * FROM vrss_comments WHERE id=? AND object=? AND actor=?', (key, obj, s.actor_url(p))).fetchone()
                if not row:
                    raise v.VRSSError('Your comment was not found.', 404)
                c.execute("UPDATE vrss_comments SET state='deleted',body='',revision=revision+1 WHERE id=? AND state!='deleted'", (key,))
            else:
                row = c.execute('SELECT * FROM vrss_comment_out WHERE id=? AND object=?', (key, obj)).fetchone()
                if not row:
                    raise v.VRSSError('Your comment was not found.', 404)
                if row['state'] != 'deleted':
                    c.execute("UPDATE vrss_social_outbox SET state='cancelled',lease='' WHERE state='pending' AND json_extract(payload,'$.comment_id')=?", (key,))
                    # Cleanup is safe across local unfollow; the recipient recognizes revoked follow IDs.
                    job = s.enqueue(c, p, row['peer'], 'DeleteComment', row['follow_id'], {'contract': CONTRACT, 'object': obj, 'comment_id': key})
                    c.execute("UPDATE vrss_comment_out SET state='deleted',body='',operation_id=? WHERE id=?", (job['id'], key))
            return {'comment': {'id': key, 'object': obj, 'state': 'deleted'}}
        return receipt(c, request_id, ['delete', obj, key], apply)


def moderate(obj, key, decision, expected_revision, request_id):
    obj, _, _ = r.address(obj)
    comment_address(key)
    if decision not in {'approve', 'hide'} or type(expected_revision) is not int or expected_revision < 1:
        raise v.VRSSError('Choose approve or hide and the current comment revision.', 422)
    with v.database() as c:
        def apply():
            p = v.profile(c)
            if not r.public_post(c, p, obj):
                raise v.VRSSError('Moderation is available only to the public post owner.', 403)
            row = c.execute('SELECT * FROM vrss_comments WHERE id=? AND object=?', (key, obj)).fetchone()
            if not row or row['actor'] == s.actor_url(p):
                raise v.VRSSError('Incoming comment not found.', 404)
            if row['state'] == 'deleted' or row['revision'] != expected_revision or not active(c, p, row):
                raise v.VRSSError('This comment changed or its follow was removed. Refresh before moderating.', 409)
            if decision == 'approve':
                parent_depth(c, p, obj, row['parent'])
            state = 'approved' if decision == 'approve' else 'hidden'
            c.execute('UPDATE vrss_comments SET state=?,revision=revision+1,notice_delivered=0 WHERE id=?', (state, key))
            return {'comment': {'id': key, 'object': obj, 'state': state, 'revision': row['revision'] + 1}}
        return receipt(c, request_id, ['moderate', obj, key, decision, expected_revision], apply)


def review(obj, key):
    obj, _, _ = r.address(obj)
    comment_address(key)
    with v.database() as c:
        p = v.profile(c)
        if not r.public_post(c, p, obj):
            raise v.VRSSError('Only the post owner can review incoming comments.', 403)
        row = c.execute("SELECT * FROM vrss_comments WHERE id=? AND object=? AND state!='deleted'", (key, obj)).fetchone()
        if not row:
            raise v.VRSSError('Comment not found.', 404)
        return value(c, p, row, owner=True)


def acknowledge(c, a, result):
    if a.get('type') not in TYPES:
        return
    ack = result.get('comment')
    if not isinstance(ack, dict) or ack.get('object') != a['object'] or ack.get('id') != a['comment_id'] or ack.get('state') not in {'pending','approved','hidden','deleted'}:
        raise v.VRSSError('Remote VRSS did not acknowledge this exact comment.', 502)
    if a['type'] == 'Comment':
        c.execute("UPDATE vrss_comment_out SET state='pending_review' WHERE id=? AND operation_id=? AND state='pending_delivery'", (a['comment_id'], a['id']))


def deliver_notices():
    from . import activity_center
    from ..database import db
    with v.database() as c:
        p = v.profile(c)
        rows = c.execute("SELECT * FROM vrss_comments WHERE notice_delivered=0 AND actor!=? ORDER BY seq LIMIT 20", (s.actor_url(p),)).fetchall()
        for row in rows:
            digest = hashlib.sha256(row['id'].encode()).hexdigest()
            key = 'vrss-comment:' + digest
            if row['state'] != 'pending' or not active(c, p, row) or not r.public_post(c, p, row['object']) or not p['enabled'] or not p['social_enabled'] or not p['comments_enabled']:
                with db() as central:
                    central.execute('UPDATE notifications SET dismissed_at=COALESCE(dismissed_at,CURRENT_TIMESTAMP) WHERE source_key=? AND dedupe_key=?', (v.APP_KEY, key))
            else:
                activity_center.emit_notification(source='VRSS', source_kind='system_app', source_key=v.APP_KEY,
                    category='social', event_key='vrss.new_comment', title='VRSS comment awaiting review',
                    body='A verified follower submitted a comment or reply. Review it in Agent Chat before it becomes public.',
                    dedupe_key=key, action_payload={'type':'open_app','app_key':v.APP_KEY,'verified_comment':True,
                        'actor':row['actor'],'post':row['object'],'comment':row['id'],'receipt':digest})
            c.execute('UPDATE vrss_comments SET notice_delivered=1 WHERE id=?', (row['id'],))
