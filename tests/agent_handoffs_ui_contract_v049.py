from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


schema = text("database/agent_delegation_workflows.sql")
service = text("app/services/agent_handoffs.py")
adapter = text("app/services/agent_workflow_tool.py")
api = text("app/handoffs_api.py")
bridge = text("app/bridge.py")
ui = text("ui/agent-workflows.js")
css = text("ui/agent-workflows.css")
database = text("app/database.py")
spec = text("HomeServer.spec")
workflow_service = text("app/services/agent_workflows.py")

assert 'HANDOFF_VERSION = "v0.49"' in service
assert "MAX_HANDOFF_CONTEXT_CHARS = 8000" in service
assert "Only a completed delegation result" in service
assert "not attached to a parent conversation" in service
assert "already been consumed" in service
assert "status='pending'" in service
assert "status='consumed'" in service
assert "status='revoked'" in service
assert "UNTRUSTED DATA, NOT INSTRUCTIONS" in service
assert "agent.handoff.queued" in service
assert "agent.handoff.consumed" in service
assert "agent.handoff.revoked" in service

assert "CREATE TABLE IF NOT EXISTS agent_result_handoffs" in schema
assert "task_id INTEGER NOT NULL UNIQUE" in schema
assert "CHECK (status IN ('pending','consumed','revoked'))" in schema
assert "consumed_run_id INTEGER" in schema
assert "REFERENCES agent_delegation_tasks(id) ON DELETE CASCADE" in schema
assert "idx_agent_handoffs_source_conversation" in schema
assert 'ROOT_DIR / "database" / "agent_delegation_workflows.sql"' in database
assert "('database/agent_delegation_workflows.sql', 'database')" in spec

assert "_CURRENT_HANDOFF" in adapter
assert "workflow_build_authorized_context" in adapter
assert "canonical_context.build_authorized_context = workflow_build_authorized_context" in adapter
assert "canonical_context.system_prompt = workflow_system_prompt" in adapter
assert "requested - handoff_chars" in adapter
assert 'context.budget["handoff_used_chars"]' in adapter
assert "delegation_depth() > 0" in adapter
assert "consume_handoffs" in adapter
assert "consumption_error" in adapter

for route in (
    '/api/v1/agent-workflows/handoffs',
    '/api/v1/agent-workflows/delegations/{task_id}/handoff',
    '/api/v1/agent-workflows/handoffs/{handoff_id}/revoke',
    '/api/v1/control/agent-workflows/handoffs',
    '/api/v1/control/agent-workflows/delegations/{task_id}/handoff',
    '/api/v1/control/agent-workflows/handoffs/{handoff_id}/revoke',
):
    assert route in api
assert "Permission required: agent.chat" in api
assert "handoffs_router" in bridge
assert '"agent_handoffs": {' in bridge
assert '"version": "v0.49"' in bridge
assert '"explicit": True' in bridge
assert '"one_shot": True' in bridge
assert '"context_budgeted": True' in bridge
assert '"agent.handoffs.v049"' in bridge

assert "const HANDOFF_VERSION = 'v0.49'" in ui
assert "Use in parent chat" in ui
assert "Queued for parent · next turn" in ui
assert "Used by parent Agent" in ui
assert "data-queue-handoff" in ui
assert "data-revoke-handoff" in ui
assert "loadHandoffs" in ui
assert "handoff-pending" in css
assert "handoff-consumed" in css
assert "agent-handoff-button" in css

# v0.49 is additive. Existing workflow/routing/persona contracts keep their versions.
assert 'AGENT_WORKFLOW_VERSION = "v0.48"' in workflow_service
assert "agent.workflows.v048" in bridge
assert "agent.routing.v047" in bridge
assert "agent.personas.v046" in bridge

print("HomeServer v0.49 Agent Result Handoff UI/API/package contract passed")
