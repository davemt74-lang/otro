from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


service = text("app/services/agent_team_orchestration.py")
api = text("app/team_orchestration_api.py")
bridge = text("app/bridge.py")
planning_service = text("app/services/agent_team_planning.py")
team_service = text("app/services/agent_team_runs.py")
planning_ui = text("ui/agent-team-planning.js")
ui = text("ui/agent-team-orchestration.js")
css = text("ui/agent-team-orchestration.css")

assert 'AGENT_TEAM_ORCHESTRATION_VERSION = "v0.53"' in service
assert "agent_team_planning.get_plan" in service
assert "agent_team_runs.get_team_run" in service
assert "agent_team_runs.run_team" in service
assert "agent_team_runs.retry_member" in service
assert "agent_team_runs.prepare_team_synthesis" in service
assert '"auto_executes": False' in service
assert '"synthesis_via_parent_chat": True' in service
assert '"one_hop_only": True' in service
assert '"parent_chat"' in service
assert '"run_specialists"' in service
assert '"retry_failed"' in service
assert '"prepare_synthesis"' in service
assert "CREATE TABLE" not in service
assert "INSERT INTO" not in service
assert "from ..database import db" not in service, "v0.53 lifecycle must remain derived instead of creating duplicate persistent state."

for route in (
    '/api/v1/agent-workflows/team-orchestrations',
    '/api/v1/control/agent-workflows/team-orchestrations',
    '/orchestration',
    '/run',
    '/members/{task_id}/retry',
    '/prepare',
):
    assert route in api
assert "/synthesize" not in api, "Parent synthesis must remain an explicit parent chat action."
assert "Permission required: agent.chat" in api
assert "current_permissions=set(identity[\"permissions\"])" in api

assert "team_orchestration_router" in bridge
assert '"agent_team_orchestration": {' in bridge
assert '"version": "v0.53"' in bridge
assert '"derived_state": True' in bridge
assert '"linked_plan_run_lifecycle": True' in bridge
assert '"explicit_run": True' in bridge
assert '"explicit_retry": True' in bridge
assert '"explicit_prepare": True' in bridge
assert '"synthesis_via_parent_chat": True' in bridge
assert '"auto_execute": False' in bridge
assert '"agent.workflows.team_orchestration.v053"' in bridge
assert '"agent.workflows.team_lifecycle.v053"' in bridge

assert "const VERSION = 'v0.52'" in planning_ui
assert "Propose team plan" in planning_ui
assert "Approve & create queued Team Run" in planning_ui
assert "Execution still requires a separate Run action" in planning_ui
assert "/assets/agent-team-orchestration.js" in planning_ui
assert "data-agent-team-orchestration-v053" in planning_ui
assert "HomeServerAgentTeamOrchestration" in planning_ui

assert "const VERSION = 'v0.53'" in ui
assert "team-orchestrations" in ui
assert "data-orch-run" in ui
assert "data-orch-retry" in ui
assert "data-orch-prepare" in ui
assert "data-orch-parent-chat" in ui
assert "Run specialists" in ui
assert "Prepare parent synthesis" in ui
assert "Continue with parent Agent" in ui
assert "synthesize" in ui.lower()
assert "/synthesize" not in ui
assert "HomeServerAgentTeamOrchestration" in ui
assert "agent-orchestration" in css
assert "@media" in css

# v0.53 composes the verified v0.52 and v0.51 state machines instead of replacing them.
assert 'AGENT_TEAM_PLANNING_VERSION = "v0.52"' in planning_service
assert 'AGENT_TEAM_RUN_VERSION = "v0.51"' in team_service
assert '"agent.workflows.team_planning.v052"' in bridge
assert '"agent.workflows.team_approval.v052"' in bridge
assert '"agent.workflows.team_runs.v051"' in bridge
assert '"agent.workflows.team_retry.v051"' in bridge
assert '"agent.workflows.timeline.v050"' in bridge
assert '"agent.handoffs.v049"' in bridge
assert '"agent.workflows.v048"' in bridge
assert '"agent.routing.v047"' in bridge

print("HomeServer v0.53 Team Orchestration UI/API contract passed")