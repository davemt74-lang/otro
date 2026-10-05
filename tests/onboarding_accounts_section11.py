"""Execute production pairing/member services with SQLite and controlled HTTP.
App boot and model providers are excluded; credentials, crypto, sessions and SQL are real.
"""
from __future__ import annotations
import contextlib
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import threading
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]

def package(name):
    result=types.ModuleType(name);result.__path__=[];sys.modules[name]=result;return result

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module
    parent,_,key=name.rpartition(".")
    if parent in sys.modules: setattr(sys.modules[parent],key,module)
    spec.loader.exec_module(module);return module

def reject(fn):
    try: fn()
    except RuntimeError: return
    raise AssertionError('Expected rejection')

with tempfile.TemporaryDirectory(prefix='section11-services-') as work:
    namespace='_section11_services'
    package(namespace);package(namespace+'.services')
    config=types.ModuleType(namespace+'.config')
    config.settings=types.SimpleNamespace(data_dir=Path(work),version='2.4',remote_https_session_path=Path(work)/'https-session.json')
    sys.modules[config.__name__]=config
    database=types.ModuleType(namespace+'.database')
    @contextlib.contextmanager
    def db():
        conn=sqlite3.connect(Path(work)/'db.sqlite',timeout=30,check_same_thread=False)
        conn.row_factory=sqlite3.Row;conn.execute('PRAGMA foreign_keys=ON')
        try: yield conn;conn.commit()
        except Exception: conn.rollback();raise
        finally: conn.close()
    database.db=db;sys.modules[database.__name__]=database
    with db() as conn:
        conn.executescript((ROOT/'database/schema.sql').read_text())
        conn.executescript((ROOT/'database/migrations/003_pairing_claim.sql').read_text())
        conn.executescript((ROOT/'database/migrations/064_multi_user.sql').read_text())
        conn.executescript('CREATE TABLE IF NOT EXISTS app_capability_scopes(paired_app_id INTEGER PRIMARY KEY); CREATE TABLE homeserver_apps(app_key TEXT PRIMARY KEY,name TEXT,installed_version TEXT,lifecycle_state TEXT);')
    scopes=types.ModuleType(namespace+'.services.app_scopes');scopes.get_scope=lambda _:{};sys.modules[scopes.__name__]=scopes
    pairing=load(namespace+'.services.pairing','app/services/pairing.py')
    owner_secret=load(namespace+'.services.owner_secret','app/services/owner_secret.py')
    https=load(namespace+'.services.https_bridge_session','app/services/https_bridge_session.py')
    remote=types.ModuleType(namespace+'.services.remote_bridge')
    remote.dispatch_remote_request=lambda *args:{'ok':True,'payload':{'version':'2.4'}}
    saves=[]
    remote.save_vp3_https_settings=lambda endpoint,enabled:saves.append((endpoint,enabled))
    remote.disable_vp3_https_settings=lambda:saves.append(('disabled',False))
    sys.modules[remote.__name__]=remote
    identity=types.ModuleType(namespace+'.services.remote_identity');identity.load_or_create_remote_identity=lambda:{'device_id':'hs-'+'a'*24};sys.modules[identity.__name__]=identity
    # Controlled transport is the only stand-in for Cloud; service logic is real.
    httpx=types.ModuleType('httpx')
    class HttpError(Exception): pass
    httpx.HTTPError=HttpError
    sent=[];mode=['lost'];fail_settings=[False]
    class Response:
        status_code=200
        def __init__(self,body): self.body=body
        def json(self): return self.body
    class Client:
        def __init__(self,**kwargs): assert kwargs.get('trust_env') is False and kwargs.get('follow_redirects') is False
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def post(self,endpoint,**kwargs):
            body=kwargs['json'];sent.append(body.copy())
            if mode[0]=='lost': raise HttpError('lost after Cloud commit')
            return Response({'ok':True,'device_id':body['device_id'],'transport':'vp3_https',
                             'session_token':body['recovery_session_token'],'poll_url':'https://vp3.me/api/homeserver-https-poll-v1300.php'})
    httpx.Client=Client
    with patch.dict(sys.modules,{'httpx':httpx}):
        cloud=load(namespace+'.services.cloud_pairing','app/services/cloud_pairing.py')
    token='VP3-'+'-'.join(['A1B2C3D4']*8)
    reject(lambda:cloud.redeem_vp3_pairing_token(token))
    pending=cloud._load_pending()
    assert token not in json.dumps(pending)
    assert pairing.authenticate(pending['local_token']) is not None
    # Simulated restart: reload module, then recover the same approved secrets.
    with patch.dict(sys.modules,{'httpx':httpx}):
        cloud=load(namespace+'.services.cloud_pairing','app/services/cloud_pairing.py')
    mode[0]='ok'
    with patch.object(cloud,'save_vp3_https_settings',side_effect=OSError('disk fixture')):
        reject(lambda:cloud.redeem_vp3_pairing_token(token))
    assert sent[0]['homeserver_token']==sent[1]['homeserver_token']
    assert sent[0]['recovery_session_token']==sent[1]['recovery_session_token']
    assert https.load_https_session() is not None and cloud._load_pending() is not None
    requests=len(sent)
    assert cloud.redeem_vp3_pairing_token(token)['accepted']
    assert len(sent)==requests and cloud._load_pending() is None
    print('PASS lost response/restart reuses approval; interrupted settings save finishes without HTTP')
    active=pairing.authenticate(sent[0]['homeserver_token'])
    reject(lambda:cloud.redeem_vp3_pairing_token(token))
    assert pairing.authenticate(sent[0]['homeserver_token'])==active
    cloud.clear_pending_pairing()
    assert https.load_https_session() is not None
    print('PASS existing saved connection survives new pairing and setup reset')
    cloud.disconnect_vp3_pairing()
    assert https.load_https_session() is None and pairing.authenticate(sent[0]['homeserver_token']) is None
    mode[0]='lost';reject(lambda:cloud.redeem_vp3_pairing_token(token))
    original=cloud._load_pending()
    reject(lambda:cloud.redeem_vp3_pairing_token('VP3-'+'-'.join(['B1B2C3D4']*8)))
    assert cloud._load_pending()==original
    pairing.revoke_paired_app('vp3');mode[0]='ok'
    reject(lambda:cloud.redeem_vp3_pairing_token(token))
    assert cloud._load_pending()==original
    cloud.clear_pending_pairing();assert cloud._load_pending() is None
    print('PASS pending token binding, explicit revocation and cancellation stay enforced')
    for poll in ('https://vp3.me:444/poll','http://localhost/poll','https://other.example/poll'):
        reject(lambda poll=poll:cloud._resolved_poll_endpoint('https://vp3.me/api/pair',poll))
    assert cloud._resolved_poll_endpoint('https://vp3.me/api/pair','/api/poll')=='https://vp3.me/api/poll'
    print('PASS relay endpoint requires original scheme, host and port')

    # Exercise the production poll/disconnect ordering with an HTTP request held
    # in flight, using only unrelated perception/provision adapters as stand-ins.
    deps=('local_apps','system_state','onboarding_visual','tracky_native_camera','tracky_native_diagnosis',
          'tracky_native_certification','tracky_native_managed_session','tracky_visual_contact_link',
          'tracky_physical_context','tracky_agent_eyes')
    for name in deps:
        module=types.ModuleType(namespace+'.services.'+name);sys.modules[module.__name__]=module
        setattr(sys.modules[namespace+'.services'],name,module)
    setattr(sys.modules[namespace+'.services'],'remote_bridge',remote)
    remote.cloud_connection_status=lambda:{'cloud':{'state':'not_connected','paired':False,'connected':False}}
    with patch.dict(sys.modules,{'httpx':httpx}):
        onboard=load(namespace+'.services.onboarding_chat','app/services/onboarding_chat.py')
    entered=threading.Event();release=threading.Event();order=[]
    def controlled_cloud(action,state):
        if action=='poll':
            entered.set();assert release.wait(10)
            return {'state':'claimed','pairing_token':token}
        return {'state':'pending' if action=='start' else 'complete'}
    def redeemed(_): order.append('redeem');return {'accepted':True}
    disconnect=cloud.disconnect_vp3_pairing
    def disconnected(): disconnect();order.append('disconnect')
    with patch.object(onboard,'_cloud',controlled_cloud),patch.object(cloud,'redeem_vp3_pairing_token',redeemed),patch.object(cloud,'disconnect_vp3_pairing',disconnected):
        onboard.new_device_code()
        with ThreadPoolExecutor(max_workers=2) as pool:
            poll=pool.submit(onboard.poll_device_code);assert entered.wait(10)
            stop=pool.submit(onboard.disconnect_cloud)
            assert not stop.done()
            release.set();poll.result(10);stop.result(10)
        assert order==['redeem','disconnect'] and onboard._read_device() is None
        onboard.poll_device_code();assert order==['redeem','disconnect']
    print('PASS owner disconnect waits for active polling and prevents stale proof reuse')

    members=load(namespace+'.services.members','app/services/members.py')
    alice=members.create_member('alice','Alice','Alice-old-password')
    member_id=alice['member_id'];password_hash=members._password_hash
    def reset_during_hash(password,salt):
        digest=password_hash(password,salt)
        with patch.object(members,'_password_hash',password_hash): members.set_password(member_id,'Alice-new-password')
        return digest
    with patch.object(members,'_password_hash',reset_during_hash): reject(lambda:members.authenticate('alice','Alice-old-password'))
    with db() as conn: assert conn.execute('SELECT COUNT(*) FROM homeserver_member_sessions').fetchone()[0]==0
    good=members.authenticate('alice','Alice-new-password')
    assert members.session_identity(good['session_token'])['member_id']==member_id
    def disable_during_hash(password,salt):
        digest=password_hash(password,salt);members.update_member(member_id,{'status':'disabled'});return digest
    with patch.object(members,'_password_hash',disable_during_hash): reject(lambda:members.authenticate('alice','Alice-new-password'))
    reject(lambda:members.session_identity(good['session_token']))
    print('PASS reset and disable during hashing cannot mint a stale member session')
    members.update_member(member_id,{'status':'active'})
    barrier=threading.Barrier(5)
    def synchronize_hash(password,salt):
        digest=password_hash(password,salt);barrier.wait(timeout=10);return digest
    def bad_login(_):
        try: members.authenticate('alice','wrong-password')
        except members.MemberError as exc: return exc.status_code
        raise AssertionError('wrong password accepted')
    with patch.object(members,'_password_hash',synchronize_hash),ThreadPoolExecutor(max_workers=5) as pool:
        assert list(pool.map(bad_login,range(5)))==[401]*5
    try: members.authenticate('alice','Alice-new-password')
    except members.MemberError as exc: assert exc.status_code==429
    else: raise AssertionError('concurrent lockout was bypassed')
    members.set_password(member_id,'Alice-new-password')
    newest=None
    for _ in range(12): newest=members.authenticate('alice','Alice-new-password')['session_token']
    assert members.session_identity(newest)['member_id']==member_id
    with db() as conn: assert conn.execute('SELECT COUNT(*) FROM homeserver_member_sessions').fetchone()[0]==10
    print('PASS concurrent failures count atomically and newest session survives ten-session cap')
    for i in range(63): members.set_context(member_id,f'preferences.{i}',i)
    def add_context(key):
        try: members.set_context(member_id,key,1);return True
        except members.MemberError as exc: assert exc.status_code==409;return False
    with ThreadPoolExecutor(max_workers=2) as pool: assert sorted(pool.map(add_context,['preferences.a','preferences.b']))==[False,True]
    assert members.context(member_id)['count']==64
    members.update_member(member_id,{'role':'guest'})
    reject(lambda:members.set_context(member_id,'preferences.0','changed'))
    print('PASS concurrent context capacity and current guest authority stay enforced')
print('ONBOARDING_ACCOUNTS_SECTION11=PASS')
