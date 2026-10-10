"""Natural-language task preparation; assignment review precedes every tool run."""
from __future__ import annotations

import json
import uuid

from ..database import db
from . import agent_mission_runtime as mission, agent_mission_tool_contracts as contracts
from . import agent_mission_actions as actions, agent_routing, agent_tools


def prepare(source, *, conversation_id, objective, request_id, parent_agent_id=None):
    try:
        if type(request_id) is not str or str(uuid.UUID(request_id)) != request_id:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise mission.MissionError('A canonical task request UUID is required.', 422) from None
    if type(objective) is not str:
        raise mission.MissionError('Task request must be text.', 422)
    objective = mission._clean(objective, 'Task request', 4000)
    conversation_id = mission._clean(conversation_id, 'Conversation', 160)
    try:
        parent = agent_routing.resolve_agent(source, parent_agent_id, owner=source == 'owner')
        agent_routing.validate_conversation_agent(source, conversation_id, int(parent['id']))
    except agent_routing.AgentRoutingError as exc:
        raise mission.MissionError(str(exc), exc.status_code) from exc
    mission._route(source, conversation_id)
    with db() as conn:
        prior = conn.execute('SELECT * FROM agent_missions_v1 WHERE source_app_key=? AND client_request_id=?', (source, request_id)).fetchone()
    if prior:
        mission._assert_same_request(prior, objective, conversation_id, int(parent['id']))
        result = mission.get_mission(source, prior['id'])
        if result['chat_task'] is None:
            raise mission.MissionError('Request belongs to a different or inaccessible task.', 409)
        return result
    snapshot = {'conversation_id': conversation_id, 'tasks': []}
    reads = [k for k in contracts.capabilities_for_snapshot(source, snapshot) if k != 'browser.read']
    writes = actions.capabilities(source, snapshot)
    budget = min(contracts.MAX_CALLS, agent_tools.get_policy()['max_calls'])
    prompt = (
        'You are the lead agent preparing a governed task. Plan only, never claim execution. '
        'Split the request into 1 to 4 specialists with dependencies on earlier zero-based indices. '
        'Assign only the listed capabilities. Reads investigate actual records; actions prepare exact '
        'changes for separate human approval. Never invent missing contact details. Include a read '
        'capability for any record-edit investigation. No browser, messages, deletes or payments. '
        'Stay within the request; use empty capabilities when none are needed. '
        f'Available reads: {json.dumps(reads)}. Available proposals: {json.dumps(writes)}. '
        f'Maximum separate read and proposal budgets: {budget}. '
        'Return JSON only: {"max_parallel":2,"tasks":[{"role":"specialist","title":"title",'
        '"objective":"assignment","instructions":"scope","depends_on":[],"tools":[],"max_calls":0,'
        '"actions":[],"max_actions":0,"output":"analysis"}]}. Output is analysis, sources or document.'
    )
    raw, provider, model = mission._infer(source, conversation_id, [
        {'role': 'system', 'content': prompt}, {'role': 'user', 'content': objective}])
    from .agent_mission_orchestration import _json
    value = _json(raw)
    if not isinstance(value, dict) or set(value) != {'max_parallel', 'tasks'}:
        raise mission.MissionError('Invalid task-plan structure.', 502)
    entries = value['tasks']
    tasks = mission.validate_plan(entries)
    required = {'role', 'title', 'objective', 'instructions', 'depends_on', 'tools', 'max_calls', 'actions', 'max_actions', 'output'}
    if any(set(e) != required for e in entries):
        raise mission.MissionError('Task plan contains unsupported fields.', 502)
    read_for_update = {'contacts.update': 'contacts.search', 'knowledge.update': 'knowledge.search',
                       'tasks.update': 'tasks.list', 'calendar.update': 'calendar.list',
                       'workspace.update': 'workspace.search'}
    for entry in entries:
        if not isinstance(entry['actions'], list) or not isinstance(entry['tools'], list):
            raise mission.MissionError('Task capabilities must be arrays.', 502)
        if any(read_for_update.get(key) not in entry['tools'] for key in entry['actions']
               if type(key) is str and key in read_for_update):
            raise mission.MissionError('Record updates require an assigned investigation read.', 502)
    draft = {'max_parallel': value['max_parallel'], 'assignments': [
        {'task_id': str(i), 'tools': e['tools'], 'max_calls': e['max_calls'], 'actions': e['actions'],
         'max_actions': e['max_actions'], 'output': e['output'], 'browser_url': ''}
        for i, e in enumerate(entries)]}
    snapshot['tasks'] = [{'id': str(i), 'position': i} for i in range(len(tasks))]
    # Recheck permissions after inference; no proposed assignment is approved here.
    reads = [k for k in contracts.capabilities_for_snapshot(source, snapshot) if k != 'browser.read']
    writes = actions.capabilities(source, snapshot)
    draft = contracts.validate(draft, snapshot, reads, writes)
    result = mission.create_mission(source, conversation_id=conversation_id, objective=objective,
        client_request_id=request_id, parent_agent_id=int(parent['id']), owner=source == 'owner',
        tasks=tasks, preparation={'draft': draft, 'provider_key': provider, 'model': model})
    if result['chat_task'] is None:
        raise mission.MissionError('Request already belongs to another task type.', 409)
    return result


def owner_prepare(*, objective, request_id, conversation_id=None, parent_agent_id=None):
    if type(objective) is not str:
        raise mission.MissionError('Task request must be text.', 422)
    objective = mission._clean(objective, 'Task request', 4000)
    try:
        parent = agent_routing.resolve_agent('owner', parent_agent_id, owner=True)
    except agent_routing.AgentRoutingError as exc:
        raise mission.MissionError(str(exc), exc.status_code) from exc
    if not conversation_id:
        # Stable new-chat identity survives lost responses and refreshes.
        try:
            if str(uuid.UUID(request_id)) != request_id: raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise mission.MissionError('A canonical task request UUID is required.', 422) from None
        conversation_id = uuid.uuid5(uuid.NAMESPACE_URL, 'owner:chat-task:' + request_id).hex
        with db() as conn:
            conn.execute('INSERT OR IGNORE INTO conversations(id,agent_id,source_app_key,title) VALUES(?,?,?,?)',
                         (conversation_id, int(parent['id']), 'owner', 'Task: ' + str(objective)[:65]))
    return prepare('owner', conversation_id=conversation_id, objective=objective,
                   request_id=request_id, parent_agent_id=int(parent['id']))
