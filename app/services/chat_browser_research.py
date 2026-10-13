"""Owner Chat proxy to canonical Cloud Chrome research; no local evidence copies."""
from __future__ import annotations
import json
from urllib.parse import urlparse, parse_qs
from uuid import UUID
import httpx
from ..database import db
from . import agent_routing, app_scopes, context_engine
from .https_bridge_session import load_https_session, https_session_matches
from .remote_identity import load_or_create_remote_identity

class ResearchError(RuntimeError):
    def __init__(self, message, status_code=422):
        super().__init__(message);self.status_code=status_code

def request(body: dict) -> dict:
    action=body.get('action')
    if action not in {'list','prepare','get','start','cancel','save','recall','compare'}:
        raise ResearchError('Unsupported Chat research operation.')
    conversation=body.get('local_conversation')
    if not isinstance(conversation,str) or not conversation or len(conversation)>100:
        raise ResearchError('Open a saved Chat conversation before using Chrome research.')
    try:
        parent=agent_routing.resolve_agent('owner',body.get('parent_agent_id'),owner=True)
        agent_routing.validate_conversation_agent('owner',conversation,int(parent['id']))
    except agent_routing.AgentRoutingError as exc:
        raise ResearchError(str(exc),exc.status_code) from exc
    if not context_engine.ensure_settings(conversation).get('cloud_allowed',False):
        raise ResearchError('This conversation is private. Chrome research needs Cloud permission.',403)
    with db() as conn:
        paired=conn.execute("SELECT id FROM paired_apps WHERE app_key='vp3' AND status='active'").fetchone()
        granted=conn.execute("SELECT 1 FROM app_permissions WHERE paired_app_id=? AND permission='agent.chat' AND allowed=1",(paired['id'],)).fetchone() if paired else None
    if not paired or not granted or not app_scopes.get_scope(int(paired['id'])).get('cloud_allowed',False):
        raise ResearchError('The VP3 pairing or Cloud Chat permission is unavailable.',403)
    session=load_https_session()
    if not session:raise ResearchError('Connect HomeServer to Cloud before Chrome research.',409)
    parsed=urlparse(session['endpoint']);suffix='/api/homeserver-https-poll-v1300.php'
    if not parsed.path.endswith(suffix):raise ResearchError('The paired Cloud endpoint is unsupported.',409)
    base=session['endpoint'][:-len(suffix)]
    allowed={'action','local_conversation','id','question','runtime_id','request_id','expected_hash','confirmed','project_id','discovery_confirmed','discovery_domains','before_id','after_id'}
    payload={key:value for key,value in body.items() if key in allowed}
    if len(json.dumps(payload).encode())>12000:raise ResearchError('Research request is too large.')
    token=session['session_token'];device=load_or_create_remote_identity()['device_id']
    try:
        with httpx.Client(timeout=30.0,follow_redirects=False) as client:
            response=client.post(base+'/api/chat-browser-research-v1.php',json=payload,
                headers={'Authorization':'Bearer '+token,'X-HomeServer-Device':device,'Accept':'application/json'})
        result=response.json()
    except (httpx.HTTPError,ValueError) as exc:
        raise ResearchError('Cloud research is unreachable or returned an invalid response.',503) from exc
    if not https_session_matches(token):raise ResearchError('Cloud pairing changed during research. Refresh authorized status.',409)
    try:agent_routing.validate_conversation_agent('owner',conversation,int(parent['id']))
    except agent_routing.AgentRoutingError as exc:raise ResearchError('The research conversation or lead changed.',403) from exc
    if not context_engine.ensure_settings(conversation).get('cloud_allowed',False) or not app_scopes.get_scope(int(paired['id'])).get('cloud_allowed',False):
        raise ResearchError('Cloud research permission changed during the request.',403)
    with db() as conn:
        active=conn.execute("SELECT 1 FROM paired_apps a JOIN app_permissions p ON p.paired_app_id=a.id WHERE a.id=? AND a.status='active' AND p.permission='agent.chat' AND p.allowed=1",(paired['id'],)).fetchone()
    if not active:raise ResearchError('The VP3 pairing or Chat grant was revoked.',403)
    if not isinstance(result,dict) or not response.is_success or not result.get('ok'):
        raise ResearchError(str(result.get('error','Cloud research failed.'))[:300] if isinstance(result,dict) else 'Cloud research failed.',response.status_code if response.status_code in {401,403,404,409,422} else 502)
    if result.get('contract')!='vp3.chat-browser-research.v1':raise ResearchError('Update the matching Cloud research release.',409)
    return {**result,'cloud_base':base}


def saved_report_links(metadata_json: str | None) -> list[dict]:
    """Historical citation links; opening a report uses current Cloud authorization."""
    try:
        metadata = json.loads(metadata_json or '{}')
        raw = metadata.get('research_reports', []) if isinstance(metadata, dict) else []
    except (ValueError, TypeError):
        return []
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw[:5]:
        if not isinstance(item, dict):
            continue
        identifier = str(item.get('id') or '')
        try:
            if str(UUID(identifier)) != identifier:
                continue
            parsed = urlparse(str(item.get('url') or ''))
            if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password or parsed.fragment or not parsed.path.endswith('/research-report.php') or parse_qs(parsed.query) != {'report': [identifier]}:
                continue
        except ValueError:
            continue
        result.append({'id': identifier, 'label': 'R' + str(len(result) + 1), 'url': parsed.geturl(), 'title': str(item.get('title') or 'Saved research')[:300]})
    return result
