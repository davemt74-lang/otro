"""Real persisted pairings; controlled relay faults and replacement races."""
from __future__ import annotations
import ast
import hashlib
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
with tempfile.TemporaryDirectory() as directory:
    os.environ['HOMESERVER_DATA_DIR'] = directory
    from app.database import initialize_database
    from app.config import settings
    from app.services import remote_bridge as bridge, cloud_pairing, pairing, https_bridge_session as sessions
    from app.services.runtime_build import runtime_build_id
    initialize_database()
    endpoint = 'https://vp3.me/api/homeserver-https-poll-v1300.php'
    token = 'S' * 64
    request = pairing.create_pairing_request('vp3', 'VP3', ['agent.chat'])
    pairing.approve_pairing_request(request['request_id'])
    local_token = request['claim_token']
    def configure(value=token):
        sessions.save_https_session(endpoint, value)
        bridge.save_vp3_https_settings(endpoint)
        bridge._RELOAD_EVENT.clear()
    configure()
    worker = bridge.RemoteBridgeWorker()
    calls, receipts = [], []
    class Response:
        status_code = 200
        def __init__(self, messages=None): self.messages = messages or []
        def json(self): return {'ok': True, 'requests': self.messages, 'poll_after_ms': 250}
    class Relay:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def post(self, *args, **kwargs):
            if kwargs['json']['results']:
                receipts.extend(dict(item) for item in kwargs['json']['results'])
                worker._stop.set()
                return Response()
            return Response([{'request_id': name, 'operation': 'system.ping', 'payload': {'nonce': name}, 'bearer_token': local_token} for name in ['first', 'interrupted', 'third']])
    def dispatch(operation, payload, authorization):
        if operation == 'capabilities': return {'ok': True, 'payload': {}}
        calls.append(payload['nonce'])
        if payload['nonce'] == 'interrupted': raise bridge.httpx.ReadTimeout('private-secret-must-not-be-in-error')
        return {'ok': True, 'status': 200, 'payload': {'pong': True}}
    with patch.object(bridge.httpx, 'Client', Relay), patch.object(bridge, 'dispatch_remote_request', dispatch), patch.object(worker, '_sync_features'):
        worker._run_https(bridge.get_bridge_settings())
    assert calls == ['first', 'interrupted', 'third'], calls
    assert len(receipts) == 3 and receipts[0]['ok'] and not receipts[1]['ok'] and receipts[2]['ok'], receipts
    assert 'private-secret' not in str(receipts)
    print('PASS interrupted command produces one failure receipt; completed batch commands never replay')

    for code in (200, 410):
        configure()
        worker = bridge.RemoteBridgeWorker()
        class ReplacementRelay:
            def post(self, *args, **kwargs):
                configure('N' * 64)
                bridge._RELOAD_EVENT.clear() # Test the credential binding itself.
                response = Response([{'request_id': 'stale', 'operation': 'system.ping'}])
                response.status_code = code
                return response
        worker._receive_https_exchange(ReplacementRelay(), endpoint, {}, token, {}, [])
        assert worker._https_pending_exchange is None
        assert sessions.load_https_session()['session_token'] == 'N' * 64
        assert bridge.get_bridge_settings()['enabled']
        assert pairing.authenticate(local_token)
    print('PASS stale success and stale 410 cannot dispatch work or revoke a replacement pairing')

    configure()
    worker = bridge.RemoteBridgeWorker()
    class Gone:
        def post(self, *args, **kwargs):
            response = Response(); response.status_code = 410; return response
    with patch.object(Path, 'unlink', side_effect=OSError('synthetic access failure')):
        try: worker._receive_https_exchange(Gone(), endpoint, {}, token, {}, [])
        except OSError: pass
        else: raise AssertionError('Failed credential removal must be reported')
    assert sessions.load_https_session()['session_token'] == token
    assert bridge.get_bridge_settings()['enabled'] and pairing.authenticate(local_token)
    print('PASS failed credential removal cannot silently disable or revoke the saved authorization')

    worker = bridge.RemoteBridgeWorker()
    class Stop:
        stopped = False
        def is_set(self): return self.stopped
        def wait(self, seconds): assert 1 <= seconds <= 30; return self.stopped
    worker._stop = Stop()
    runs = []
    def run():
        runs.append(True)
        if len(runs) == 1: raise ValueError('private-value')
        worker._stop.stopped = True
    worker._run = run
    worker._run_guarded()
    assert len(runs) == 2 and 'private-value' not in str(bridge._STATE)
    with patch.object(bridge.tracky_physical_context, 'sync_due', side_effect=ValueError('private-value')):
        worker._sync_features()
    assert bridge._STATE['feature_sync_error'] and 'private-value' not in bridge._STATE['feature_sync_error']
    assert sessions.SESSION_LOCK is cloud_pairing._PAIR_LOCK
    print('PASS unexpected worker and optional feature faults recover with safe bounded backoff')

    # Exercise actual launcher predicate without starting the GUI or tray.
    tree = ast.parse((ROOT / 'desktop/launcher.py').read_text())
    predicate = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_same_running_build')
    namespace = {'settings': settings, 'runtime_build_id': runtime_build_id}
    exec(compile(ast.Module(body=[predicate], type_ignores=[]), '<launcher-predicate>', 'exec'), namespace)
    match = namespace['_same_running_build']
    assert not match({'version': settings.version})
    assert not match({'version': settings.version, 'build_id': 'older-build'})
    assert match({'version': settings.version, 'build_id': runtime_build_id()})
    assert not match({'version': '2.1', 'build_id': runtime_build_id()})
    runtime_build_id.cache_clear()
    fixture = Path(directory) / 'fixture.exe'; fixture.write_bytes(b'packaged-test')
    with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(fixture)):
        assert runtime_build_id() == hashlib.sha256(b'packaged-test').hexdigest()
    runtime_build_id.cache_clear()
    print('PASS same product version with a different build is replaced; executable identity is exact SHA256')
