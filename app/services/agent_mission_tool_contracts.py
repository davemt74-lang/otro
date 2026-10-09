"""A5C1 source-scoped specialist capability contracts. No tool dispatch here."""
from __future__ import annotations

import hashlib
import json
import uuid

from ..database import atomic_write, db
from . import agent_mission_runtime as mission, app_scopes, tools, agent_tools, action_policy
from .agent_browser_policy import read_navigation

CONTRACT = 'vp3.agent-missions.tools.v1'
READ_TOOLS = ('knowledge.search', 'contacts.search', 'tasks.list')
CAPABILITIES = ('browser.read', *READ_TOOLS)
OUTPUTS = ('analysis', 'sources', 'document')
MAX_CALLS = 3


def authority(source, snapshot):
    """Fresh pairing and model/privacy checks; never promote an app to owner."""
    mission._route(source, str(snapshot['conversation_id']))
    if source == 'owner':
        return set(), True
    with db() as conn:
        app = conn.execute("SELECT id FROM paired_apps WHERE app_key=? AND status='active'", (source.removeprefix('app:'),)).fetchone()
        if not source.startswith('app:') or not app:
            raise mission.MissionError('Mission application is no longer paired.', 403)
        permissions = {r[0] for r in conn.execute('SELECT permission FROM app_permissions WHERE paired_app_id=? AND allowed=1', (app['id'],))}
    if 'agent.chat' not in permissions:
        raise mission.MissionError('Mission permission was revoked.', 403)
    return app_scopes.scoped_tool_permissions(app_scopes.get_scope_for_source(source), permissions), False


def capabilities(source, mid):
    snapshot = mission.get_mission(source, mid)
    return capabilities_for_snapshot(source, snapshot)


def capabilities_for_snapshot(source, snapshot):
    permissions, owner = authority(source, snapshot)
    scope = app_scopes.get_scope_for_source(source) if not owner else {}
    available = {t['key'] for t in tools.list_tools(permissions, owner=owner) if t['available'] and t['mode']=='read' and (owner or app_scopes.tool_allowed(scope,t['key']))}
    enabled=agent_tools.get_policy()['enabled']
    result = [key for key in READ_TOOLS if key in available and enabled and (owner or action_policy.resolve_policy_for_source(source,key)['policy_mode']!=action_policy.SENSITIVE_HIGH_IMPACT)]
    from . import context_engine
    if context_engine.get_settings(snapshot['conversation_id']).get('cloud_allowed',False) and (owner or scope.get('cloud_allowed',False)):
        result.insert(0,'browser.read')
    return result


def validate(value, snapshot, available):
    if not isinstance(value,dict) or set(value) != {'max_parallel','assignments'}:
        raise mission.MissionError('A tool contract requires max_parallel and assignments.',422)
    parallel = value['max_parallel']
    if type(parallel) is not int or not 1 <= parallel <= mission.MAX_PARALLEL:
        raise mission.MissionError('Parallel worker budget must be 1 to 4.',422)
    entries = value['assignments']
    tasks = {t['id']:t for t in snapshot['tasks']}
    if not isinstance(entries,list) or not 1 <= len(entries) <= 12 or len(entries)!=len(tasks):
        raise mission.MissionError('Assign every mission worker exactly once.',422)
    seen = set(); normalized=[]
    for entry in entries:
        if not isinstance(entry,dict) or set(entry) != {'task_id','tools','max_calls','output','browser_url'}:
            raise mission.MissionError('Worker tool assignment has an invalid structure.',422)
        tid = entry['task_id']
        if not isinstance(tid,str) or tid not in tasks or tid in seen:
            raise mission.MissionError('Worker is missing, duplicated or belongs to another mission.',422)
        selected = entry['tools']
        if not isinstance(selected,list) or len(selected)>len(CAPABILITIES) or any(type(x) is not str or x not in CAPABILITIES for x in selected) or len(set(selected))!=len(selected):
            raise mission.MissionError('Only registered read capabilities may be assigned.',422)
        if any(x not in available for x in selected):
            raise mission.MissionError('A selected capability is disabled or outside current permissions.',403)
        calls = entry['max_calls']
        if type(calls) is not int or not 0 <= calls <= MAX_CALLS or bool(selected)!=(calls>0):
            raise mission.MissionError('Tool workers require a 1 to 3 call budget; model-only workers use zero.',422)
        if any(x in READ_TOOLS for x in selected) and calls>agent_tools.get_policy()['max_calls']:
            raise mission.MissionError('Assignment exceeds the owner-defined Agent tool budget.',403)
        if type(entry['output']) is not str or entry['output'] not in OUTPUTS:
            raise mission.MissionError('Unsupported worker output contract.',422)
        url = entry['browser_url']
        if type(url) is not str or ('browser.read' not in selected and url):
            raise mission.MissionError('Browser URL requires the browser.read capability.',422)
        if url:
            url = read_navigation(url)
        seen.add(tid)
        normalized.append({'task_id':tid,'tools':sorted(selected),'max_calls':calls,'output':entry['output'],'browser_url':url})
    normalized.sort(key=lambda e:tasks[e['task_id']]['position'])
    return {'max_parallel':parallel,'assignments':normalized}


def get(source, mid):
    snapshot=mission.get_mission(source,mid)
    authority(source,snapshot)
    with db() as conn:
        row=conn.execute('SELECT *,datetime(expires_at)>datetime(\'now\') AS active FROM agent_mission_tool_contracts_v1 WHERE mission_id=? AND source_app_key=?',(mid,source)).fetchone()
    return {'contract':CONTRACT,'mission_id':mid,'configured':bool(row),'execution_enabled':bool(snapshot.get('tools_enabled')),'orchestrator_available':True,
            'capabilities':capabilities(source,mid),'max_calls_per_worker':MAX_CALLS,
            'revision':row['revision'] if row else 0,'expires_at':row['expires_at'] if row else None,
            'active':bool(row and row['active']),'assignments':json.loads(row['contract_json']) if row else {}}


@atomic_write
def configure(source, mid, value, *, request_id, expected_revision, confirmed):
    if confirmed is not True:
        raise mission.MissionError('Review and confirm specialist capabilities first.',422)
    try:
        if type(request_id) is not str or str(uuid.UUID(request_id))!=request_id:raise ValueError()
    except (ValueError,TypeError,AttributeError):
        raise mission.MissionError('A canonical request UUID is required.',422) from None
    if type(expected_revision) is not int or expected_revision<0:
        raise mission.MissionError('Expected contract revision is required.',422)
    snapshot=mission.get_mission(source,mid)
    if snapshot['status']!='planned' or any(t['status']!='queued' for t in snapshot['tasks']):
        raise mission.MissionError('Capabilities can only be assigned before mission execution.',409)
    normalized=validate(value,snapshot,capabilities(source,mid))
    encoded=json.dumps(normalized,sort_keys=True,separators=(',',':'))
    payload_hash=hashlib.sha256(encoded.encode()).hexdigest()
    with db() as conn:
        prior=conn.execute('SELECT * FROM agent_mission_contract_requests_v1 WHERE source_app_key=? AND request_id=?',(source,request_id)).fetchone()
        if prior:
            if prior['mission_id']!=mid or prior['payload_hash']!=payload_hash:
                raise mission.MissionError('Request ID already belongs to another assignment.',409)
            return get(source,mid)
        previous=conn.execute('SELECT revision FROM agent_mission_tool_contracts_v1 WHERE mission_id=?',(mid,)).fetchone()
        revision=int(previous['revision']) if previous else 0
        if revision!=expected_revision:
            raise mission.MissionError('Specialist assignments changed; review them again.',409)
        conn.execute("INSERT INTO agent_mission_tool_contracts_v1(mission_id,source_app_key,request_id,payload_hash,contract_json,expires_at) VALUES(?,?,?,?,?,datetime('now','+15 minutes')) ON CONFLICT(mission_id) DO UPDATE SET request_id=excluded.request_id,payload_hash=excluded.payload_hash,contract_json=excluded.contract_json,revision=revision+1,expires_at=excluded.expires_at",(mid,source,request_id,payload_hash,encoded))
        conn.execute('INSERT INTO agent_mission_contract_requests_v1(source_app_key,request_id,mission_id,payload_hash,revision) VALUES(?,?,?,?,?)',(source,request_id,mid,payload_hash,revision+1))
        mission._event(conn,mid,'orchestration.contract_approved',detail={'workers':len(normalized['assignments']),'max_parallel':normalized['max_parallel'],'revision':revision+1})
    return get(source,mid)
