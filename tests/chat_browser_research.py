"""Real owner context, pairing and proxy boundaries; Cloud transport is a deterministic fixture."""
import hashlib,json,os,sys,tempfile
from pathlib import Path
from unittest.mock import patch
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix='chat-research-') as data:
    os.environ['HOMESERVER_DATA_DIR']=data
    from app.database import db,initialize_database
    from app.services import chat_browser_research as research,agent_routing,context_engine,app_scopes
    from app.services.https_bridge_session import save_https_session
    initialize_database()
    parent=agent_routing.resolve_agent('owner',None,owner=True)
    with db() as c:
        c.execute('INSERT INTO conversations(id,agent_id,source_app_key,title) VALUES(?,?,?,?)',('research-chat',parent['id'],'owner','Research'))
        aid=c.execute('INSERT INTO paired_apps(app_key,name,token_hash) VALUES(?,?,?)',('vp3','VP3',hashlib.sha256(b'pairing-fixture').hexdigest())).lastrowid
        c.execute("INSERT INTO app_permissions(paired_app_id,permission,allowed) VALUES(?,'agent.chat',1)",(aid,))
    token='research-session-fixture-00000000000000000'
    save_https_session('http://127.0.0.1:49000/installed/vp3/api/homeserver-https-poll-v1300.php',token)
    calls=[];revoke=False
    def cloud(request):
        assert str(request.url)=='http://127.0.0.1:49000/installed/vp3/api/chat-browser-research-v1.php'
        assert request.headers['Authorization']=='Bearer '+token
        assert request.headers['X-HomeServer-Device'].startswith('hs-')
        body=json.loads(request.content);calls.append(body)
        assert 'parent_agent_id' not in body and 'csrf_token' not in body and 'private_context' not in body
        if revoke:
            with db() as c:c.execute("UPDATE app_permissions SET allowed=0 WHERE paired_app_id=? AND permission='agent.chat'",(aid,))
        return httpx.Response(200,json={'ok':True,'contract':'vp3.chat-browser-research.v1','request':{'id':'canonical-reference'}})
    original=httpx.Client
    def client(**kwargs):return original(transport=httpx.MockTransport(cloud),**kwargs)
    body={'action':'prepare','local_conversation':'research-chat','parent_agent_id':parent['id'],'question':'Compare https://one.example/about','runtime_id':'runtime','request_id':'request','private_context':'must not export','discovery_confirmed':True,'discovery_domains':['one.example']}
    with patch.object(research.httpx,'Client',client):
        response=research.request(body);assert response['request']['id']=='canonical-reference'
        assert response['cloud_base']=='http://127.0.0.1:49000/installed/vp3'
        assert len(calls)==1
        assert calls[0]['discovery_confirmed'] is True and calls[0]['discovery_domains']==['one.example']
        with db() as c:c.execute("UPDATE conversation_context_settings SET cloud_allowed=0 WHERE conversation_id='research-chat'")
        try:research.request(body);raise AssertionError('private conversation exported')
        except research.ResearchError as e:assert e.status_code==403
        assert len(calls)==1
        with db() as c:c.execute("UPDATE conversation_context_settings SET cloud_allowed=1 WHERE conversation_id='research-chat'")
        revoke=True
        try:research.request(body);raise AssertionError('revoked result exposed')
        except research.ResearchError as e:assert e.status_code==403
        assert len(calls)==2
        try:research.request(body);raise AssertionError('revoked pairing exported')
        except research.ResearchError as e:assert e.status_code==403
        assert len(calls)==2
    assert token not in json.dumps(response)
    for action in ['worker.claim','worker.step','unknown']:
        try:research.request({**body,'action':action});raise AssertionError('worker access through owner proxy')
        except research.ResearchError:pass
    print('PASS: native owner context, public-intent-only export, paired URL subfolder/session, Cloud contract, private conversation blocking, mid-flight revocation, worker-route isolation and credential-free projection.')
