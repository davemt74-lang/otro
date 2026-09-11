from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


service = text("app/services/agent_team_planning.py")
api = text("app/team_planning_api.py")
bridge = text("app/bridge.py")
schema = text("database/agent_delegation_workflows.sql")
routing_ui = text("ui/agent-routing.js")
ui = text("ui/agent-team-planning.js")
css = text("ui/agent-team-planning.css")
team_service = text("app/services/agent_team_runs.py")

assert 'AGENT_TEAM_PLANNING_VERSION = "v0.52"' in service
assert "MIN_PLAN_MEMBERS = 2" in service
assert "MAX_PLAN_MEMBERS = 4" in service
assert "_parse_model_plan" in service
assert "set(payload) != {\"members\"}" in service
assert "Each planned specialist must be a different Agent" in service
assert "Nothing was created" in service
assert "context_chat._private_inference_route" in service
assert "providers.generate_ollama" in service
assert "BEGIN IMMEDIATE" in service
assert "status='approved', team_run_id=?" in service
assert '"auto_executes": False' in service
assert "team_plan_id" in service
assert "created_via\": \"team_plan_approval_v052\"" in service
assert "agent.team_plan.proposed" in service
assert "agent.team_plan.edited" in service
assert "agent.team_plan.approved" in service
assert "agent.team_plan.rejected" in service

for route in (
    '/api/v1/agent-workflows/team-plans',
    '/api/v1/control/agent-workflows/team-plans',
    '/approve',
    '/reject',
):
    assert route in api
assert "ConfigDict(extra=\"forbid\")" in api
assert "Permission required: agent.chat" in api
assert "TeamPlanEditRequest" in api

assert "CREATE TABLE IF NOT EXISTS agent_team_plans" in schema
assert "status IN ('proposed','approved','rejected')" in schema
assert "team_run_id INTEGER UNIQUE" in schema
assert "permission_snapshot_json" in schema

assert "team_planning_router" in bridge
assert '"agent_team_planning": {' in bridge
assert '"version": "v0.52"' in bridge
assert '"proposal_only": True' in bridge
assert '"explicit_approval": True' in bridge
assert '"atomic_approval": True' in bridge
assert '"auto_execute": False' in bridge
assert '"privacy_routed": True' in bridge
assert '"agent.workflows.team_planning.v052"' in bridge
assert '"agent.workflows.team_approval.v052"' in bridge

assert "/assets/agent-team-planning.js" in routing_ui
assert "data-agent-team-planning-v052" in routing_ui
assert "const VERSION = 'v0.52'" in ui
assert "Propose team plan" in ui
assert "Approve & create queued Team Run" in ui
assert "Execution still requires a separate Run action" in ui
assert "No Team Run was created" in ui
assert "providers" not in ui.lower(), "UI must not expose provider credentials or direct provider calls."
assert "team-plans" in ui
assert "HomeServerAgentTeamRuns?.refresh" in ui
assert "agent-team-planning" in css
assert "@media" in css

# v0.52 is additive; execution remains the already-verified v0.51 engine.
assert 'AGENT_TEAM_RUN_VERSION = "v0.51"' in team_service
assert '"agent.workflows.team_runs.v051"' in bridge
assert '"agent.workflows.team_retry.v051"' in bridge
assert '"agent.workflows.timeline.v050"' in bridge
assert '"agent.handoffs.v049"' in bridge
assert '"agent.workflows.v048"' in bridge
assert '"agent.routing.v047"' in bridge

print("HomeServer v0.52 Bounded Agent Team Planning UI/API contract passed")
