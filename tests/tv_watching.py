"""Real pairing DB, owner tool registry and API; only Cloud transport is mocked."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix='tv-watching-') as folder:
    os.environ['HOMESERVER_DATA_DIR'] = folder
    import httpx
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.database import initialize_database, db
    from app.services import tv_watching as tv, tools, agent_tools, app_scopes
    from app.services.https_bridge_session import save_https_session
    from app.tv_watching_api import router
    initialize_database()
    with db() as conn:
        conn.execute("INSERT INTO paired_apps(app_key,name,token_hash,status) VALUES('vp3','VP3',?,'active')", ('f'*64,))
        app_id = conn.execute("SELECT id FROM paired_apps WHERE app_key='vp3'").fetchone()[0]
        for permission in ['agent.chat', 'memory.read']:
            conn.execute('INSERT INTO app_permissions(paired_app_id,permission,allowed) VALUES(?,?,1)', (app_id, permission))
    token = 'a'*64
    save_https_session('https://cloud.example/vp3/api/homeserver-https-poll-v1300.php', token)
    checks = 0
    requests = []
    failure = False
    mismatch = False
    oversize = False
    race = None

    def check(ok, name):
        global checks
        assert ok, name
        checks += 1
        print('PASS', name)

    def rejects(fn, code, name):
        try:
            fn()
        except (tv.TVError, tools.ToolError) as exc:
            check(exc.status_code == code, name)
            return
        raise AssertionError(name)

    def serve(request):
        global race
        requests.append(request)
        if race:
            action, race = race, None
            action()
        if failure:
            return httpx.Response(503, json={'ok': False, 'error': 'fixture unavailable'})
        return httpx.Response(200, json={'ok': True, 'contract': 'wrong' if mismatch else tv.CONTRACT, 'items': [{'title': 'Cheers', 'status': 'likely'}], 'padding': 'x'*170000 if oversize else ''})

    original_client = httpx.Client

    def client(**kwargs):
        return original_client(transport=httpx.MockTransport(serve), **kwargs)

    with patch.object(tv.httpx, 'Client', side_effect=client):
        result = tv.history({})
        check(result['items'][0]['title'] == 'Cheers', 'history available without Eyes')
        check(str(requests[-1].url) == 'https://cloud.example/vp3/api/tv-watching-v1.php', 'existing subdirectory-aware pairing selects endpoint')
        check(requests[-1].headers['Authorization'] == 'Bearer '+token, 'existing paired credential used')
        check('X-HomeServer-Device' in requests[-1].headers, 'canonical device header used')
        count = len(requests)
        rejects(lambda: tv.request({'action': 'analyze', 'account_binding': 'obsolete', 'images': ['private']}), 409, 'old binding rejects images before transport')
        check(len(requests) == count, 'rejected crops never leave HomeServer')
        check({'tv.history', 'tv.interests'}.issubset({r['key'] for r in tools.list_tools(owner=True)}), 'owner registry includes TV tools')
        check(not any(r['available'] for r in tools.list_tools({'tools.execute'}, owner=False) if r['key'].startswith('tv.')), 'paired apps cannot inherit owner tools')
        schemas = agent_tools.model_tool_schemas(owner=True)
        check({'homeserver_tv_history', 'homeserver_tv_interests'}.issubset({r['function']['name'] for r in schemas}), 'model registry exposes TV reads')
        executed = tools.execute_tool('owner', 'tv.history', {}, owner=True)
        check('Cheers' in json.dumps(executed), 'canonical owner tool execution returns history')
        check(any(r['key'] == 'tv.watching' for r in tools.SKILL_DEFINITIONS), 'first-class TV skill registered')
        rejects(lambda: tv.history({'images': ['private']}), 422, 'history cannot smuggle crops')
        rejects(lambda: tv.request({'action': 'history', 'owner_user_id': 9}), 422, 'caller cannot pick another Cloud account')
        rejects(lambda: tv.request({'action': 'analyze', 'images': ['x'*900000]}), 422, 'request byte budget enforced')
        mismatch = True
        rejects(lambda: tv.history({}), 409, 'wrong Cloud contract rejected')
        mismatch = False
        failure = True
        rejects(lambda: tv.history({}), 503, 'Cloud errors never invent viewing results')
        failure = False
        oversize = True
        rejects(lambda: tv.history({}), 502, 'response byte budget enforced')
        oversize = False

        def revoke():
            with db() as conn:
                conn.execute("UPDATE app_permissions SET allowed=0 WHERE permission='memory.read'")
        race = revoke
        rejects(lambda: tv.history({}), 403, 'permission rechecked after Cloud reply')
        with db() as conn:
            conn.execute("UPDATE app_permissions SET allowed=1 WHERE permission='memory.read'")
        race = lambda: save_https_session('https://cloud.example/vp3/api/homeserver-https-poll-v1300.php', 'b'*64)
        rejects(lambda: tv.history({}), 409, 'account change fences pending reply')
        save_https_session('https://cloud.example/vp3/api/homeserver-https-poll-v1300.php', token)
        app_scopes.save_scope(app_id, {'cloud_allowed': False})
        rejects(lambda: tv.history({}), 403, 'Cloud scope remains authoritative')
        app_scopes.save_scope(app_id, {'cloud_allowed': True, 'memory_key_prefixes': ['participant:']})
        rejects(lambda: tv.history({}), 403, 'participant scope does not grant TV history')
        app_scopes.save_scope(app_id, {'cloud_allowed': True})
        api = FastAPI();api.include_router(router)
        with TestClient(api) as ui:
            check(ui.post('/api/v1/control/tv-watching', json={'action': 'history'}).status_code == 200, 'owner history API needs no capture')
            check(ui.post('/api/v1/control/tv-watching', json={'action': 'settings'}).status_code == 403, 'capture mutations require owner UI header')
            check(ui.post('/api/v1/control/tv-watching', json={'action': 'settings'}, headers={'X-HomeServer-UI': '1'}).status_code == 200, 'explicit controls use canonical paired service')
    print(f'TV_WATCHING_HOME=PASS ({checks} cases)')
