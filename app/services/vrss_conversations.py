"""Opt-in signed mentions and local subscriptions to public moderated threads.

No global directory or copied post body: a mention is a source-publisher-signed
pointer, verified against its current public post/comment before presentation.
Subscriptions poll the existing bounded public comment pages, independently of
profile follows. Revisions fence all network results against local changes.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid

from . import vrss as v, vrss_social as s, vrss_comments as comments, vrss_interactions as r

CONTRACT = 'vp3.vrss.conversations.v1'
SCHEMA = """
CREATE TABLE IF NOT EXISTS vrss_conversation_watches (
 object TEXT PRIMARY KEY, actor TEXT NOT NULL, enabled INTEGER NOT NULL,
 muted INTEGER NOT NULL, revision INTEGER NOT NULL, cursor TEXT NOT NULL DEFAULT '0',
 priming INTEGER NOT NULL DEFAULT 1, check_revision INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL DEFAULT 0,
 checked_at REAL NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS vrss_conversation_seen (
 object TEXT NOT NULL, comment TEXT NOT NULL, PRIMARY KEY(object,comment));
CREATE TABLE IF NOT EXISTS vrss_conversation_notices (
 id TEXT PRIMARY KEY, actor TEXT NOT NULL, object TEXT NOT NULL, comment TEXT NOT NULL,
 kind TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0, inactive INTEGER NOT NULL DEFAULT 0, withdrawn INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS vrss_mention_intents (
 id TEXT PRIMARY KEY, object TEXT NOT NULL, comment TEXT NOT NULL, target TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending', due REAL NOT NULL DEFAULT 0,
 UNIQUE(object,comment,target));
"""


def setup(c):
    for statement in SCHEMA.split(';'):
        if statement.strip():
            c.execute(statement)
    columns = {row[1] for row in c.execute('PRAGMA table_info(vrss_profile)')}
    for key, default in [('mentions_enabled', 0), ('thread_notifications_enabled', 1)]:
        if key not in columns:
            c.execute('ALTER TABLE vrss_profile ADD COLUMN '+key+' INTEGER NOT NULL DEFAULT '+str(default))


def targets(body):
    result = set()
    # Canonical, explicit addresses only. No handle guessing or passive RSS discovery.
    for token in re.findall(r'(?<!\S)@(https://[^\s<>"\x27]+)', body):
        try:
            result.add(s.actor_address(token))
        except v.VRSSError:
            continue
    if len(result) > 5:
        raise v.VRSSError('Mention at most five canonical VRSS actor addresses.', 422)
    return sorted(result)


def source_body(c, p, obj, key=''):
    if not p['enabled'] or not p['social_enabled'] or not r.public_post(c, p, obj):
        return None
    if key:
        row = c.execute('SELECT * FROM vrss_comments WHERE object=? AND id=?', (obj, key)).fetchone()
        return row['body'] if p['comments_enabled'] and row and comments.visible(c, p, row) else None
    return c.execute('SELECT body FROM vrss_posts WHERE id=?', (r.address(obj)[2],)).fetchone()[0]


def schedule(c, p, obj, key=''):
    body = source_body(c, p, obj, key)
    if body is None:
        return
    for target in targets(body):
        if target == s.actor_url(p) or c.execute('SELECT 1 FROM vrss_mention_intents WHERE object=? AND comment=? AND target=?', (obj, key, target)).fetchone():
            continue
        bounded(c, 'vrss_mention_intents')
        c.execute('INSERT INTO vrss_mention_intents(id,object,comment,target) VALUES(?,?,?,?)',
                  (p['public_base']+'/vrss/v1/mentions/'+str(uuid.uuid4()), obj, key, target))


def bounded(c, table):
    if c.execute('SELECT count(*) FROM '+table).fetchone()[0] >= 10000:
        raise v.VRSSError('VRSS conversation history is full. Retain its replay history.', 409)


def prepare_mentions(loader=None):
    with v.database() as c:
        s.ready(c)
        jobs = [dict(x) for x in c.execute("SELECT * FROM vrss_mention_intents WHERE state='pending' AND due<=? ORDER BY due,id LIMIT 2", (time.time(),))]
    for job in jobs:
        try:
            with v.database() as c:
                p = s.ready(c)
                body = source_body(c, p, job['object'], job['comment'])
                if body is None or job['target'] not in targets(body):
                    c.execute("UPDATE vrss_mention_intents SET state='cancelled' WHERE id=?", (job['id'],))
                    continue
            doc = s.peer_document(job['target'], loader)
            cap = doc.get('conversations')
            if not isinstance(cap, dict) or cap.get('contract') != CONTRACT or cap.get('mentions') is not True:
                raise v.VRSSError('The recipient has not enabled mentions.', 409)
            with v.database() as c:
                p = s.ready(c)
                live = c.execute("SELECT 1 FROM vrss_mention_intents WHERE id=? AND state='pending'", (job['id'],)).fetchone()
                body = source_body(c, p, job['object'], job['comment'])
                if not live or body is None or job['target'] not in targets(body):
                    c.execute("UPDATE vrss_mention_intents SET state='cancelled' WHERE id=?", (job['id'],))
                    continue
                peer = s.pin(c, doc)
                if peer['blocked']:
                    raise v.VRSSError('This profile is blocked.', 403)
                s.enqueue(c, p, job['target'], 'Mention', '', {'contract': CONTRACT, 'object': job['object'],
                          'comment_id': job['comment'], 'mention_id': job['id']})
                c.execute("UPDATE vrss_mention_intents SET state='queued' WHERE id=?", (job['id'],))
        except v.VRSSError:
            with v.database() as c:
                c.execute('UPDATE vrss_mention_intents SET due=? WHERE id=?', (time.time()+900, job['id']))


def can_send(c, p, a):
    if a['type'] != 'Mention':
        return True
    body = source_body(c, p, a['object'], a['comment_id'])
    return body is not None and a['recipient'] in targets(body)


def validate(a):
    _, base, _ = r.address(a['object'])
    if base+'/vrss/v1/actor' != a['actor']:
        raise ValueError('Only the original thread publisher sends mention pointers.')
    prefix, key = a['mention_id'].rsplit('/vrss/v1/mentions/', 1)
    if prefix != base or str(uuid.UUID(key)) != key:
        raise ValueError('Invalid mention identity.')
    if a['comment_id']:
        comments.comment_address(a['comment_id'])


def verify_source(obj, key='', recipient='', loader=None):
    obj, base, _ = r.address(obj)
    target = base+'/vrss/v1/actor'
    with v.database() as c:
        local = base == v.profile(c)['public_base']
    doc = s.actor_document() if local else s.peer_document(target, loader)
    if key:
        result = comments.read(obj, loader=loader, parent=key, follow_required=False, allow_leaf=True)
        if result['identity'] != doc['identity'] or result['actor'] != target:
            raise v.VRSSError('Conversation source identity changed.', 409)
        body = result['items'][0]['body']
    else:
        result = v.public_document('post', r.address(obj)[2]) if local else (loader or s.exchange)(obj)
        if (not isinstance(result, dict) or result.get('url') != obj or result.get('visibility') != 'public'
                or not isinstance(result.get('author'), dict) or result['author'].get('id') != doc['identity']
                or not isinstance(result.get('body'), str) or len(result['body']) > 10000):
            raise v.VRSSError('The public mention source does not match its publisher.', 422)
        body = result['body']
    if recipient and recipient not in targets(body):
        raise v.VRSSError('This public source does not mention the recipient.', 403)
    return doc


def receive(c, p, peer, a):
    if not p['mentions_enabled']:
        raise v.VRSSError('Incoming VRSS mentions are disabled.', 403)
    key = hashlib.sha256(s.canonical([a['object'],a['comment_id']])).hexdigest()
    notice(c, key, peer['actor'], a['object'], a['comment_id'], 'mention')


def thread(obj, after='0', loader=None):
    """A public post mention remains readable even when its author closed replies."""
    comments.cursor(after)
    obj, _, _ = r.address(obj)
    doc = verify_source(obj, loader=loader)
    with v.database() as c:
        p = s.ready(c)
        if doc['id'] != s.actor_url(p) and s.pin(c, doc)['blocked']:
            raise v.VRSSError('This profile is blocked.', 403)
    cap = doc.get('comments')
    if not isinstance(cap, dict) or cap.get('enabled') is not True:
        return {'contract':comments.CONTRACT,'object':obj,'actor':doc['id'],'identity':doc['identity'],
                'items':[],'next_cursor':'','owner':False,'can_comment':False}
    return comments.read(obj, after, loader=loader, follow_required=False)


def notice(c, key, actor, obj, comment, kind):
    if c.execute('SELECT 1 FROM vrss_conversation_notices WHERE id=?', (key,)).fetchone():
        return
    bounded(c, 'vrss_conversation_notices')
    watch = c.execute('SELECT * FROM vrss_conversation_watches WHERE object=?', (obj,)).fetchone()
    p = v.profile(c)
    suppressed = bool(watch and watch['muted']) or (kind == 'mention' and not p['mentions_enabled']) or (kind == 'thread' and not p['thread_notifications_enabled'])
    c.execute('INSERT INTO vrss_conversation_notices(id,actor,object,comment,kind,inactive) VALUES(?,?,?,?,?,?)',
              (key, actor, obj, comment, kind, int(suppressed)))


def set_watch(obj, enabled, muted, expected_revision, request_id, loader=None):
    obj, base, _ = r.address(obj)
    if type(enabled) is not bool or type(muted) is not bool or type(expected_revision) is not int or expected_revision < 0:
        raise v.VRSSError('Include conversation choices and the current revision.', 422)
    if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{16,80}',request_id):
        raise v.VRSSError('Include a stable conversation request ID.', 422)
    payload = ['conversation', obj, enabled, muted, expected_revision]
    with v.database() as c:
        p = s.ready(c)
        owner = p['actor_id']
        if c.execute('SELECT 1 FROM vrss_comment_requests WHERE id=?', (request_id,)).fetchone():
            return comments.receipt(c, request_id, payload, lambda: None)
        old = c.execute('SELECT * FROM vrss_conversation_watches WHERE object=?', (obj,)).fetchone()
        if (old['revision'] if old else 0) != expected_revision:
            raise v.VRSSError('This conversation changed. Refresh its controls.', 409)
        initialize = enabled and (not old or not old['enabled'] or (old['muted'] and not muted))
    page = comments.read(obj, loader=loader, follow_required=False) if initialize else None
    with v.database() as c:
        def apply():
            p = s.ready(c)
            old = c.execute('SELECT * FROM vrss_conversation_watches WHERE object=?', (obj,)).fetchone()
            if p['actor_id'] != owner or (old['revision'] if old else 0) != expected_revision:
                raise v.VRSSError('Conversation authority changed during verification.', 409)
            if not old and c.execute('SELECT count(*) FROM vrss_conversation_watches').fetchone()[0] >= 50:
                raise v.VRSSError('Follow at most 50 conversations.', 409)
            if not old:
                c.execute('INSERT INTO vrss_conversation_watches(object,actor,enabled,muted,revision) VALUES(?,?,?,?,?)', (obj, base+'/vrss/v1/actor', enabled, muted, 1))
            else:
                c.execute('UPDATE vrss_conversation_watches SET enabled=?,muted=?,revision=revision+1,error=? WHERE object=?', (enabled, muted, '', obj))
            if initialize:
                remember(c, obj, page['items'])
                c.execute('UPDATE vrss_conversation_watches SET cursor=?,priming=?,due=? WHERE object=?', (page['next_cursor'] or '0', bool(page['next_cursor']), time.time()+60, obj))
            if muted or not enabled:
                c.execute('UPDATE vrss_conversation_notices SET inactive=1 WHERE object=?', (obj,))
            return {'conversation': dict(c.execute('SELECT * FROM vrss_conversation_watches WHERE object=?', (obj,)).fetchone())}
        result = comments.receipt(c, request_id, payload, apply)
    deliver_notices(loader, verify=False)
    return result


def remember(c, obj, items):
    for item in items:
        if c.execute('SELECT 1 FROM vrss_conversation_seen WHERE object=? AND comment=?', (obj, item['id'])).fetchone():
            continue
        bounded(c, 'vrss_conversation_seen')
        c.execute('INSERT INTO vrss_conversation_seen VALUES(?,?)', (obj, item['id']))


def refresh_due(loader=None, stop_event=None):
    with v.database() as c:
        p = s.ready(c)
        owner = p['actor_id']
        jobs = [dict(x) for x in c.execute('SELECT * FROM vrss_conversation_watches WHERE enabled=1 AND due<=? ORDER BY due,object LIMIT 2', (time.time(),))]
        for job in jobs:
            c.execute('UPDATE vrss_conversation_watches SET due=?,check_revision=check_revision+1 WHERE object=?', (time.time()+60, job['object']))
            job['check_revision'] += 1
    for job in jobs:
        if stop_event is not None and stop_event.is_set():
            break
        try:
            page = comments.read(job['object'], after=job['cursor'], loader=loader, follow_required=False)
            with v.database() as c:
                p = s.ready(c)
                live = c.execute('SELECT * FROM vrss_conversation_watches WHERE object=?', (job['object'],)).fetchone()
                if p['actor_id'] != owner or not live['enabled'] or live['revision'] != job['revision'] or live['check_revision'] != job['check_revision'] or live['cursor'] != job['cursor']:
                    continue
                for item in page['items']:
                    seen = c.execute('SELECT 1 FROM vrss_conversation_seen WHERE object=? AND comment=?', (job['object'], item['id'])).fetchone()
                    if not seen and not job['priming'] and item['actor'] != s.actor_url(p):
                        key = hashlib.sha256(s.canonical([job['object'], item['id']])).hexdigest()
                        notice(c, key, job['actor'], job['object'], item['id'], 'thread')
                remember(c, job['object'], page['items'])
                c.execute('UPDATE vrss_conversation_watches SET cursor=?,priming=?,checked_at=?,error=?,check_revision=check_revision+1 WHERE object=?', (page['next_cursor'] or '0', bool(job['priming'] and page['next_cursor']), time.time(), '', job['object']))
        except v.VRSSError:
            with v.database() as c:
                c.execute('UPDATE vrss_conversation_watches SET error=?,check_revision=check_revision+1 WHERE object=? AND revision=? AND check_revision=?', ('Source unavailable; retrying without exposing private content.', job['object'], job['revision'], job['check_revision']))
    deliver_notices(loader)


def permitted(c, p, row):
    peer = c.execute('SELECT blocked FROM vrss_social_peers WHERE actor=?', (row['actor'],)).fetchone()
    watch = c.execute('SELECT * FROM vrss_conversation_watches WHERE object=?', (row['object'],)).fetchone()
    return bool(p['enabled'] and p['social_enabled'] and not row['inactive'] and not (peer and peer['blocked'])
                and not (watch and watch['muted']) and (p['mentions_enabled'] if row['kind'] == 'mention' else p['thread_notifications_enabled'] and watch and watch['enabled']))


def deliver_notices(loader=None, verify=True):
    from . import activity_center
    from ..database import db
    with v.database() as c:
        rows = [dict(x) for x in c.execute('SELECT * FROM vrss_conversation_notices WHERE (delivered=0 AND due<=?) OR (inactive=1 AND withdrawn=0) ORDER BY inactive DESC,rowid LIMIT 20', (time.time(),))]
    verified = 0
    for row in rows:
        receipt = hashlib.sha256(row['id'].encode()).hexdigest()
        dedupe = 'vrss-'+row['kind']+':'+receipt
        with v.database() as c:
            allowed = permitted(c, v.profile(c), row)
        if not allowed:
            with db() as central:
                central.execute('UPDATE notifications SET dismissed_at=COALESCE(dismissed_at,CURRENT_TIMESTAMP) WHERE source_key=? AND dedupe_key=?', (v.APP_KEY, dedupe))
            with v.database() as c:
                c.execute('UPDATE vrss_conversation_notices SET inactive=1,delivered=1,withdrawn=1 WHERE id=?', (row['id'],))
            continue
        if not verify or verified >= 2:
            continue
        verified += 1
        with v.database() as c:
            c.execute('UPDATE vrss_conversation_notices SET due=? WHERE id=?', (time.time()+60, row['id']))
        try:
            with v.database() as c:
                recipient = s.actor_url(v.profile(c)) if row['kind'] == 'mention' else ''
            doc = verify_source(row['object'], row['comment'], recipient, loader)
            with v.database() as c:
                p = s.ready(c)
                live = c.execute('SELECT * FROM vrss_conversation_notices WHERE id=?', (row['id'],)).fetchone()
                if not permitted(c, p, live) or (doc['id'] != s.actor_url(p) and s.pin(c, doc)['blocked']):
                    continue
                activity_center.emit_notification(source='VRSS', source_kind='system_app', source_key=v.APP_KEY,
                    category='social', event_key='vrss.'+('mention' if row['kind']=='mention' else 'thread_reply'),
                    title='VRSS mention' if row['kind']=='mention' else 'New reply in a followed conversation',
                    body='A verified public VRSS conversation has an update. Open its current published thread in Agent Chat.',
                    dedupe_key=dedupe, action_payload={'type':'open_app','app_key':v.APP_KEY,'verified_conversation':True,
                    'actor':row['actor'],'post':row['object'],'comment':row['comment'],'receipt':receipt})
                c.execute('UPDATE vrss_conversation_notices SET delivered=1 WHERE id=?', (row['id'],))
        except v.VRSSError:
            continue


def status():
    with v.database() as c:
        p = v.profile(c)
        return {'mentions_enabled':p['mentions_enabled'],'thread_notifications_enabled':p['thread_notifications_enabled'],
                'items':[dict(x) for x in c.execute('SELECT * FROM vrss_conversation_watches ORDER BY object')],
                'pending_mentions':c.execute("SELECT count(*) FROM vrss_mention_intents WHERE state='pending'").fetchone()[0]}
