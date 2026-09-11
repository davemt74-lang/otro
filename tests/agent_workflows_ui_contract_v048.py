from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


schema = text("database/agent_delegation_workflows.sql")
database = text("app/database.py")
spec = text("HomeServer.spec")
service = text("app/services/agent_workflows.py")
adapter = text("app/services/agent_workflow_tool.py")
api = text("app/delegation_api.py")
bridge = text("app/bridge.py")
routing_ui = text("ui/agent-routing.js")
workflow_ui = text("ui/agent-workflows.js")
workflow_css = text("ui/agent-workflows.css")
legacy = text("app/services/delegation.py")

assert 'AGENT_WORKFLOW_VERSION = "v0.48"' in service
assert 'MODEL_DELEGATE_TOOL_NAME = "homeserver_agent_delegate"' in service
assert 'MODEL_DELEGATE_TOOL_KEY = "agent.delegate"' in service
assert "A delegation worker must be a different Agent" in service
assert "The parent Agent no longer exists." in service
assert "The delegated worker Agent no longer exists." in service
assert "stored_permissions.intersection" in service
assert "status='working'" in service
assert "status='completed'" in service
assert "status='failed'" in service
assert "agent.delegation.queued" in service
assert "agent.delegation.completed" in service
assert "nested_delegation" in service
assert "invoked_by_model" in service

assert "CREATE TABLE IF NOT EXISTS agent_delegation_policy" in schema
assert "CREATE TABLE IF NOT EXISTS agent_delegation_tasks" in schema
assert "CHECK (status IN ('queued','working','completed','failed','cancelled'))" in schema
assert "parent_agent_id INTEGER" in schema and "parent_agent_id INTEGER NOT NULL" not in schema
assert "worker_agent_id INTEGER" in schema
assert schema.count("REFERENCES agents(id) ON DELETE SET NULL") >= 2
assert 'ROOT_DIR / "database" / "agent_delegation_workflows.sql"' in database
assert "('database/agent_delegation_workflows.sql', 'database')" in spec

assert "ContextVar" in adapter
assert "worker_scope" in adapter
assert "delegation_depth() > 0" in adapter
assert "agent_tools.model_tool_schemas = workflow_schemas" in adapter
assert "agent_tools.execute_model_tool = workflow_execute" in adapter
assert "context_chat.chat = scoped_chat" in adapter

for route in (
    '/api/v1/agent-workflows/workers',
    '/api/v1/agent-workflows/delegations',
    '/api/v1/control/agent-workflows/policy',
    '/api/v1/control/agent-workflows/workers',
    '/api/v1/control/agent-workflows/delegations',
):
    assert route in api
assert "ConfigDict(extra=\"forbid\")" in api
assert "agent_workflow_tool.install()" in api
assert "Permission required: agent.chat" in api

assert '"agent_workflows": {' in bridge
assert '"version": "v0.48"' in bridge
assert '"persistent_tasks": True' in bridge
assert '"model_delegation_tool": True' in bridge
assert '"nested_delegation": False' in bridge
assert '"agent.workflows.v048"' in bridge
assert '"agent.workflows.model_delegate"' in bridge

assert "agent-workflows.js" in routing_ui
assert "data-agent-workflows-v048" in routing_ui
assert "getActiveConversationId" in routing_ui
assert "getAgents" in routing_ui
assert "const VERSION = 'v0.48'" in workflow_ui
assert "Run specialist" in workflow_ui
assert "Let Agents delegate tasks automatically" in workflow_ui
assert "One-hop only in v0.48" in workflow_ui
assert "agentWorkflowTasks" in workflow_ui
assert "conversation_id: activeConversationId()" in workflow_ui
assert "@media(max-width:900px)" in workflow_css
assert "@media(max-width:640px)" in workflow_css

# v0.48 is additive: the original stateless VP3 delegation contract remains v0.25.
assert 'DELEGATION_VERSION = "v0.25"' in legacy
assert '"stateless": True' in legacy

print("HomeServer v0.48 Agent Delegation & Multi-Agent Workflows UI/API/package contract passed")
