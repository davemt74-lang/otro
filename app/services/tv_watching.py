"""Owner TV skill using the paired account's canonical Cloud history.

No second camera or local history cache. Reads do not require active Eyes.
"""
from __future__ import annotations
import hashlib
import json
from urllib.parse import urlparse
import httpx
from ..database import db
from . import app_scopes
from .https_bridge_session import load_https_session, https_session_matches
from .remote_identity import load_or_create_remote_identity

CONTRACT = 'vp3.tv-watching.v1'
READ_ACTIONS = {'history', 'interests', 'status'}
ACTIONS = READ_ACTIONS | {'settings', 'start', 'stop', 'screen_end', 'analyze', 'commit', 'correct', 'forget', 'preference'}


class TVError(RuntimeError):
    def __init__(self, message, status_code=422):
        super().__init__(message)
        self.status_code = status_code


def _binding():
    with db() as conn:
        paired = conn.execute("SELECT id FROM paired_apps WHERE app_key='vp3' AND status='active'").fetchone()
        permissions = {r['permission'] for r in conn.execute('SELECT permission FROM app_permissions WHERE paired_app_id=? AND allowed=1', (paired['id'],)).fetchall()} if paired else set()
    if not paired or not {'agent.chat', 'memory.read'}.issubset(permissions):
        raise TVError('Connect VP3 with Agent Chat and memory-read permissions to use its TV history.', 403)
    scope = app_scopes.get_scope(int(paired['id']))
    if not scope.get('cloud_allowed', False):
        raise TVError('Cloud access is disabled for this pairing. TV history has not been copied locally.', 403)
    prefixes = scope.get('memory_key_prefixes', [])
    if prefixes and not any('tv:'.startswith(prefix) for prefix in prefixes):
        raise TVError('TV history is outside this pairing memory scope.', 403)
    session = load_https_session()
    if not session:
        raise TVError('Connect HomeServer to the matching Cloud account for TV Watching.', 409)
    parsed = urlparse(session['endpoint'])
    suffix = '/api/homeserver-https-poll-v1300.php'
    if parsed.scheme != 'https' or not parsed.path.endswith(suffix) or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise TVError('TV Watching requires the existing HTTPS Cloud pairing.', 409)
    return session, session['endpoint'][:-len(suffix)], int(paired['id'])


def request(body: dict) -> dict:
    if not isinstance(body, dict) or not isinstance(body.get('action', 'history'), str) or body.get('action', 'history') not in ACTIONS:
        raise TVError('Unsupported TV operation.')
    allowed = {'action', 'enabled', 'share_screen', 'run_id', 'screen_id', 'request_id', 'images', 'id', 'revision', 'title', 'episode', 'personal_viewing_confirmed', 'query', 'limit', 'after', 'before', 'preference', 'timezone', 'account_binding', 'expected_epoch'}
    if set(body) - allowed or len(json.dumps(body).encode()) > 890000:
        raise TVError('TV request has unsupported fields or exceeds its size budget.')
    session, base, app_id = _binding()
    token = session['session_token']
    binding = hashlib.sha256(('tv-peer:'+session['endpoint']+':'+token).encode()).hexdigest()
    if body.get('account_binding', binding) != binding:
        raise TVError('Cloud account changed. Refresh TV settings before capture.', 409)
    body = {key: value for key, value in body.items() if key != 'account_binding'}
    try:
        with httpx.Client(timeout=180.0 if body.get('action') == 'analyze' else 15.0, follow_redirects=False, trust_env=False) as client:
            response = client.post(base+'/api/tv-watching-v1.php', json=body, headers={'Authorization': 'Bearer '+token, 'X-HomeServer-Device': load_or_create_remote_identity()['device_id'], 'Accept': 'application/json'})
        if len(response.content) > 160000:
            raise TVError('TV response exceeded its size budget.', 502)
        result = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise TVError('The paired TV history service is unavailable. No result was assumed.', 503) from exc
    fresh, _, current_app = _binding()
    if not https_session_matches(token) or fresh['session_token'] != token or current_app != app_id:
        raise TVError('Cloud account or permissions changed. Previous TV results were discarded.', 409)
    if not isinstance(result, dict) or not response.is_success or not result.get('ok'):
        raise TVError(str(result.get('error', 'TV request failed.'))[:300] if isinstance(result, dict) else 'Invalid TV response.', response.status_code if response.status_code in {401, 403, 404, 409, 410, 413, 419, 429, 502, 503} else 502)
    if result.get('contract') != CONTRACT:
        raise TVError('Update the matching Cloud TV Watching release.', 409)
    return {**result, 'account_binding': binding}


def history(arguments: dict) -> dict:
    if set(arguments)-{'query', 'after', 'before', 'limit'}:
        raise TVError('Unsupported TV history filter.')
    return request({'action': 'history', **arguments})


def interests(arguments: dict) -> dict:
    if arguments:
        raise TVError('TV interests takes no arguments.')
    return request({'action': 'interests'})
