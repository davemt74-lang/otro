from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


service = text("app/services/agent_team_runs.py")
api = text("app/team_runs_api.py")
bridge = text("app/bridge.py")
schema = text("database/agent_delegation_workflows.sql")
routing_ui = text("ui/agent-routing.js")
team_ui = text("ui/agent-team-runs.js")
team_css = text("ui/agent-team-runs.css")
workflow_service = text("app/services/agent_workflows.py")
timeline_service = text("app/services/agent_workflow_timeline.py")
handoff_service = text("app/services/agent_handoffs.py")

assert 'AGENT_TEAM_RUN_VERSION = "v0.51"' in service
assert "MIN_TEAM_MEMBERS = 2" in service
assert "MAX_TEAM_MEMBERS = 4" in service
assert "agent_team_runs" in service
assert "agent_team_run_members" in service
assert "Each Team Run specialist must be a different Agent." in service
assert '"nested_delegation": False' in service
assert "agent_workflow_tool.worker_scope()" in service
assert "retry_failed_member" in service
assert "Only failed Team Run members can be retried." in service
assert "prepare_synthesis(" in service
assert "All Team Run specialists must complete successfully" in service
assert "agent.team_run.created" in service
assert "agent.team_run.executed" in service
assert "agent.team_run.member_retried" in service
assert "agent.team_run.prepared" in service
assert "result_redacted" in service
assert "Current application permissions" not in service or "result_authorized" in service

for route in (
    '/api/v1/agent-workflows/team-runs',
    '/api/v1/control/agent-workflows/team-runs',
    '/team-runs/{team_run_id}/run',
    '/team-runs/{team_run_id}/members/{task_id}/retry',
    '/team-runs/{team_run_id}/prepare',
    '/team-runs/{team_run_id}/cancel',
):
    assert route in api
assert "ConfigDict(extra=\"forbid\")" in api
assert "Permission required: agent.chat" in api
assert "min_length=agent_team_runs.MIN_TEAM_MEMBERS" in api
assert "max_length=agent_team_runs.MAX_TEAM_MEMBERS" in api

assert "team_runs_router" in bridge
assert '"agent_team_runs": {' in bridge
assert '"version": "v0.51"' in bridge
assert '"min_members": 2' in bridge
assert '"max_members": 4' in bridge
assert '"distinct_specialists": True' in bridge
assert '"partial_failure_recovery": True' in bridge
assert '"nested_delegation": False' in bridge
assert '"agent.workflows.team_runs.v051"' in bridge
assert '"agent.workflows.team_retry.v051"' in bridge

assert "CREATE TABLE IF NOT EXISTS agent_team_runs" in schema
assert "CREATE TABLE IF NOT EXISTS agent_team_run_members" in schema
assert "UNIQUE (team_run_id, worker_agent_id)" in schema
assert "REFERENCES agent_delegation_tasks(id) ON DELETE CASCADE" in schema
assert "REFERENCES conversations(id) ON DELETE CASCADE" in schema

assert "agent-team-runs.js" in routing_ui
assert "data-agent-team-runs-v051" in routing_ui
assert "const VERSION = 'v0.51'" in team_ui
assert "Fan out to a specialist team" in team_ui
assert "Start Team Run" in team_ui
assert "Retry failed specialist" in team_ui
assert "Prepare parent synthesis" in team_ui
assert "parent synthesis stays explicit" in team_ui
assert "/team-runs" in team_ui
assert "/prepare" in team_ui
assert "/retry" in team_ui
assert "One-hop" not in team_ui or "one hop only" in team_ui
assert "agent-team-member-grid" in team_css
assert "team-synthesized" in team_css

# v0.51 must remain additive over the verified v0.48-v0.50 trust stack.
assert 'AGENT_WORKFLOW_VERSION = "v0.48"' in workflow_service
assert 'AGENT_WORKFLOW_TIMELINE_VERSION = "v0.50"' in timeline_service
assert 'HANDOFF_VERSION = "v0.49"' in handoff_service
assert "agent.workflows.v048" in bridge
assert "agent.handoffs.v049" in bridge
assert "agent.workflows.timeline.v050" in bridge
assert "agent.workflows.synthesis.v050" in bridge
assert "agent.routing.v047" in bridge
assert "agent.personas.v046" in bridge

print("HomeServer v0.51 bounded Agent Team Runs UI/API package contract passed")
