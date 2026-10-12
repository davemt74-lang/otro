"""Read-only installed integration evidence; no model, repair or private record reads."""
from datetime import datetime, timezone
from ..database import db
from . import agent_mission_runtime as mission, agent_tools, app_scopes, tools
from . import workspace_sync, providers, agent_browser_runtime, runtime_build
from . import plugins, capability_registry, remote_bridge

CONTRACT = 'vp3.agent-integration.diagnostics.v1'
SECTION = 'A5C11'


def inspect(source='owner'):
    owner = source == 'owner'
    with db() as conn:
        schema = conn.execute('SELECT max(version) FROM schema_migrations').fetchone()[0]
        if owner:
            permissions = set()
        else:
            app = conn.execute("SELECT id FROM paired_apps WHERE app_key=? AND status='active'", (source.removeprefix('app:'),)).fetchone()
            if not source.startswith('app:') or not app:
                raise mission.MissionError('Diagnostic application is no longer paired.', 403)
            permissions = {r[0] for r in conn.execute('SELECT permission FROM app_permissions WHERE paired_app_id=? AND allowed=1', (app['id'],))}
            if 'agent.chat' not in permissions:
                raise mission.MissionError('Diagnostic permission was revoked.', 403)
    scope = app_scopes.get_scope_for_source(source) if not owner else {}
    inventory = [t for t in tools.list_tools(permissions, owner=owner)
                 if owner or app_scopes.tool_allowed(scope, t['key'])]
    skills = [s for s in tools.list_skills(permissions, owner=owner)
              if owner or all(app_scopes.tool_allowed(scope, k) for k in s['tools'])]
    if owner:
        installed_plugins=[{'key':str(p['plugin_key'])[:80],'name':str(p.get('name') or '')[:160],
                            'version':str(p.get('version') or '')[:80],
                            'tool_keys':[str(t.get('key') or '')[:80] for t in (p.get('manifest') or {}).get('tools', [])][:50]}
                           for p in plugins.list_plugins(active_only=True)][:100]
    else:
        installed_plugins=capability_registry._plugin_inventory(scope, permissions)
    checks = []
    def check(key, ready, detail, recovery, *, unknown=False):
        checks.append({'key':key, 'status':'not_verified' if unknown else 'ready' if ready else 'blocked',
                       'detail':detail, 'next_action':'' if ready and not unknown else recovery})
    check('schema', schema >= 79, 'Installed database schema: '+str(schema), 'Install the matching HomeServer build and complete its database upgrade.')
    policy = agent_tools.get_policy()
    check('agent_tools', policy['enabled'], 'Agent tool policy is '+('enabled.' if policy['enabled'] else 'disabled.'), 'Enable the required Agent tools in HomeServer after reviewing their permissions.')
    check('browser_privacy', owner or bool(scope.get('cloud_allowed')), 'External research requires current app and conversation privacy permission.', 'Review this app scope and the conversation privacy setting.')
    try:
        raw_connection=remote_bridge.cloud_connection_status()['cloud']
        connection={k:raw_connection.get(k) for k in ('state','connected','paired','transport','last_seen_at')}
        check('relay_worker', bool(connection.get('connected')), 'This running process reports relay state: '+str(connection.get('state') or 'unknown'), 'Open HomeServer connection status and check the HTTPS relay worker.')
    except Exception:
        connection={'state':'unavailable','connected':False}
        check('relay_worker', False, 'Relay worker status could not be read.', 'Review the installed HomeServer connection status.')
    try:
        managed = bool(agent_browser_runtime.executable())
        check('managed_browser', managed, 'Managed browser files '+('verified.' if managed else 'are not certified in this source runtime.'), 'Install the certified HomeServer installer; browser launching remains untested by this diagnostic.', unknown=not managed)
    except Exception:
        check('managed_browser', False, 'Managed browser verification failed.', 'Reinstall the complete certified HomeServer package.')
    try:
        sync = workspace_sync.status()
        age=None
        try:
            observed=datetime.fromisoformat(str(sync['last_success_at']).replace('Z','+00:00'))
            if observed.tzinfo is None:observed=observed.replace(tzinfo=timezone.utc)
            age=(datetime.now(timezone.utc)-observed).total_seconds()
        except (KeyError,TypeError,ValueError):pass
        sync_ready = sync['state'] == 'synced' and age is not None and 0 <= age <= 300
        check('workspace_sync', sync_ready, 'Workspace sync state: '+sync['state']+'; last success is '+('fresh within five minutes.' if sync_ready else 'stale, paused or not verified.'), 'Check pairing, sync opt-in, and the last successful synchronization in Agent Brain.')
        synchronization = {'state':sync['state'], 'paired':sync['paired'], 'enabled':sync['enabled'],
                           'last_success_at':sync.get('last_success_at'),
                           'last_success_age_seconds':round(age) if age is not None else None,'freshness_window_seconds':300,
                           'cloud_dataset_count':len(sync.get('cloud_datasets', [])),
                           'homeserver_dataset_count':len(sync.get('homeserver_datasets', [])),
                           'copied_schedules_execute':False}
    except Exception:
        synchronization = {'state':'unavailable'}
        check('workspace_sync', False, 'Workspace sync status could not be read.', 'Review installed schema and pairing; previous copies must remain preserved.')
    inference = providers.inference_status()
    check('inference', bool(inference.get('available')), 'Configured inference readiness was read without spending tokens.', 'Configure a ready model provider in HomeServer.')
    return {'contract':CONTRACT, 'section':SECTION, 'schema':schema, 'build_id':runtime_build.runtime_build_id(),
            'checked_at':datetime.now(timezone.utc).isoformat(), 'read_only':True, 'token_spend':0,
            'write_actions_executed':0, 'browser_pages_opened':0, 'source':'owner' if owner else 'paired_app',
            'checks':checks, 'ready':all(c['status']=='ready' for c in checks), 'sync':synchronization,
            'connection':connection,
            'inference':{k:inference.get(k) for k in ('available','selected_provider','model','compute_source','cloud_fallback_required')},
            'tools':[{k:t.get(k) for k in ('key','name','mode','available','missing_permissions')} for t in inventory],
            'skills':[{k:s.get(k) for k in ('key','name','tools','available')} for s in skills],
            'plugins':installed_plugins,
            'browser_tasks':{'separate_origin_approval':True,'per_worker_isolation':True,'source_citations':True,'max_parallel':4}}
