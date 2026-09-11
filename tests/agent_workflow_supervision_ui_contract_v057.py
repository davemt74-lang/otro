from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


service = text("app/services/agent_workflow_supervision.py")
api = text("app/workflow_supervision_api.py")
rehydration_api = text("app/workflow_rehydration_api.py")
bridge = text("app/bridge.py")
database = text("app/database.py")
schema = text("database/agent_workflow_supervision.sql")
spec = text("HomeServer.spec")
ui = text("ui/agent-workflow-supervision.js")
css = text("ui/agent-workflow-supervision.css")
rehydration_ui = text("ui/agent-workflow-rehydration.js")
regression = text("tests/agent_workflow_supervision_v057.py")
edges = text("tests/agent_workflow_supervision_edges_v057.py")
schema_test = text("tests/agent_workflow_supervision_schema_v057.py")
packaged = text("tests/packaged_workflow_supervision_v057.py")
workflow = text(".github/workflows/agent-workflow-supervision-v057.yml")

assert 'AGENT_WORKFLOW_SUPERVISION_VERSION = "v0.57"' in service
assert "MAX_SUPERVISED_STEPS = 2" in service
assert 'SAFE_ACTIONS = {"run", "prepare"}' in service
assert "agent_workflow_rehydration.rehydrate_workflow" in service
assert "agent_team_orchestration.run_plan_team" in service
assert "agent_team_orchestration.prepare_plan_synthesis" in service
assert "agent_team_orchestration.retry_plan_member" not in service
assert ".approve" not in service
assert "parent_chat" not in service
assert "BEGIN IMMEDIATE" in service
assert "agent_workflow_supervisions" in service
assert "rehydration_id" in service
assert "requires_explicit_start" in service
assert '"auto_approval": False' in service
assert '"auto_retry": False' in service
assert '"auto_parent_chat": False' in service
assert '"nested_delegation": False' in service
assert "provider_key" not in service
assert '"model"' not in service
assert '"result"' not in service

assert api.count('@router.post("/api/v1/agent-workflows/supervise")') == 1
assert api.count('@router.post("/api/v1/control/agent-workflows/supervise")') == 1
assert "Permission required: agent.chat" in api
assert "WorkflowSuperviseRequest" in api
assert "state_fingerprint" in api
assert "max_steps" in api
assert "agent_workflow_supervision.continue_workflow" in api
assert "workflow_supervision_router" in rehydration_api
assert "router.include_router(workflow_supervision_router)" in rehydration_api
assert rehydration_api.count('@router.post("/api/v1/agent-workflows/rehydrate")') == 1
assert rehydration_api.count('@router.post("/api/v1/control/agent-workflows/rehydrate")') == 1

assert '"agent_workflow_supervision": {' in bridge
assert '"version": "v0.57"' in bridge
assert '"requires_explicit_start": True' in bridge
assert '"bounded": True' in bridge
assert '"max_steps": 2' in bridge
assert '"safe_actions": ["run", "prepare"]' in bridge
assert '"canonical_revalidation_each_step": True' in bridge
assert '"idempotent_checkpoint_claim": True' in bridge
assert '"auto_approval": False' in bridge
assert '"auto_retry": False' in bridge
assert '"auto_parent_chat": False' in bridge
assert '"requires_rehydration": "v0.56"' in bridge
assert '"actions_via": "v0.53"' in bridge
assert '"agent.workflows.supervision.v057"' in bridge
assert '"agent.chat.workflow_continue.v057"' in bridge

assert 'ROOT_DIR / "database" / "agent_workflow_supervision.sql"' in database
assert "CREATE TABLE IF NOT EXISTS agent_workflow_supervisions" in schema
assert "rehydration_id INTEGER NOT NULL UNIQUE" in schema
assert "CHECK (max_steps BETWEEN 1 AND 2)" in schema
assert "CHECK (step_count BETWEEN 0 AND 2)" in schema
assert "FOREIGN KEY (rehydration_id) REFERENCES agent_workflow_rehydrations(id) ON DELETE CASCADE" in schema
assert "('database/agent_workflow_supervision.sql', 'database')" in spec

assert "const VERSION = 'v0.57'" in ui
assert "Continue safely" in ui
assert "data-workflow-supervise" in ui
assert "method: 'POST'" in ui
assert "event.target.closest('[data-workflow-supervise]')" in ui
assert "Boot, refresh" in ui
assert "No automatic approval" in ui
assert "no automatic retry" in ui
assert "no automatic parent message" in ui
assert "setTimeout(() => continueSafely" not in ui
assert "fetch(API" not in ui.split("function boot()", 1)[1]
assert "workflow-supervision" in css
assert "@media" in css
assert "/assets/agent-workflow-supervision.js" in rehydration_ui
assert "data-agent-workflow-supervision-v057" in rehydration_ui
assert "getPayload" in rehydration_ui
assert "user explicitly pressed Resume" in rehydration_ui

assert "[\"run\", \"prepare\"]" in regression
assert '"retry_required"' in regression
assert '"plan_approval_required"' in regression
assert '"step_budget_exhausted"' in regression
assert 'duplicate_payload["reused"] is True' in regression
assert "knowledge.search" in edges
assert "app_agent_grants" in edges
assert "fresh checkpoint" in edges
assert "agent_workflow_supervisions" in edges
assert "DROP TABLE agent_workflow_supervisions" in schema_test
assert "versions_after == versions_before" in schema_test
assert "initialize_database()" in schema_test

assert "workflow-supervision:" in workflow
assert "packaged-supervision:" in workflow
assert "HomeServer.spec" in workflow
assert "python tests/agent_workflow_supervision_v057.py" in workflow
assert "python tests/agent_workflow_supervision_edges_v057.py" in workflow
assert "python tests/agent_workflow_supervision_schema_v057.py" in workflow
assert "python tests/agent_workflow_supervision_ui_contract_v057.py" in workflow
assert "python tests/packaged_workflow_supervision_v057.py" in workflow
assert "python tests/agent_workflow_rehydration_v056.py" in workflow
assert "python tests/agent_workflow_rehydration_edges_v056.py" in workflow
assert "python tests/agent_workflow_rehydration_ui_contract_v056.py" in workflow
assert "pyinstaller HomeServer.spec --clean --noconfirm" in workflow

assert "/api/v1/control/agent-workflows/supervise" in packaged
assert "/api/v1/control/agent-workflows/rehydrate" in packaged
assert "/api/v1/control/system/restart" in packaged
assert 'duplicate_payload["reused"] is True' in packaged
assert "agent_workflow_supervisions" in packaged
assert "agent.synthesis.prepared" in packaged

print("HomeServer v0.57 Supervised Workflow Continuation UI/API contract passed")
