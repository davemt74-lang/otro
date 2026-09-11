from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


service = text("app/services/agent_workflow_rehydration.py")
api = text("app/workflow_rehydration_api.py")
resume_api = text("app/workflow_resume_api.py")
bridge = text("app/bridge.py")
migration = text("database/migrations/021_agent_workflow_rehydration.sql")
ui = text("ui/agent-workflow-rehydration.js")
css = text("ui/agent-workflow-rehydration.css")
loader = text("ui/agent-workflow-continuation.js")
resume_ui = text("ui/agent-workflow-resume.js")
edges = text("tests/agent_workflow_rehydration_edges_v056.py")
capability = text("tests/agent_workflow_rehydration_capability_v056.py")
workflow = text(".github/workflows/agent-workflow-rehydration-v056.yml")
packaged = text("tests/packaged_workflow_rehydration_v056.py")

assert 'AGENT_WORKFLOW_REHYDRATION_VERSION = "v0.56"' in service
assert "agent_workflow_continuation.conversation_continuation" in service
assert "agent_team_orchestration.get_orchestration" in service
assert "agent_routing.resolve_agent" in service
assert "BEGIN IMMEDIATE" in service
assert "state_fingerprint" in service
assert "revision_token" in service
assert "agent_workflow_rehydrations" in service
assert "state_changed_since_last_rehydration" in service
assert "_live_app_permissions_tx" in service
assert "_agent_access_tx" in service
assert "Application permissions changed during recovery" in service
assert "Specialist Agent access changed during recovery" in service
assert '"status": "planned"' in service
assert '"safe_to_continue"' in service
assert '"canonical_source": True' in service
assert '"auto_executes": False' in service
assert '"actions_executed": 0' in service
assert '"actions_via": agent_team_orchestration.AGENT_TEAM_ORCHESTRATION_VERSION' in service
assert '"result"' not in service
assert "provider_key" not in service
assert '"model"' not in service

assert api.count('@router.post("/api/v1/agent-workflows/rehydrate")') == 1
assert api.count('@router.post("/api/v1/control/agent-workflows/rehydrate")') == 1
assert "Permission required: agent.chat" in api
assert "WorkflowRehydrateRequest" in api
assert "agent_workflow_rehydration.rehydrate_workflow" in api
assert "workflow_rehydration_router" in resume_api
assert "router.include_router(workflow_rehydration_router)" in resume_api

# v0.55 remains a GET-only discovery/navigation contract.
assert resume_api.count('@router.get("/api/v1/agent-workflows/resume")') == 1
assert resume_api.count('@router.get("/api/v1/control/agent-workflows/resume")') == 1
assert '@router.post("/api/v1/agent-workflows/resume")' not in resume_api
assert "const VERSION = 'v0.55'" in resume_ui
assert "method: 'POST'" not in resume_ui
assert "This is navigation only" in resume_ui

assert '"agent_workflow_rehydration": {' in bridge
assert '"version": "v0.56"' in bridge
assert '"persistent_checkpoint": True' in bridge
assert '"idempotent": True' in bridge
assert '"drift_detection": True' in bridge
assert '"canonical_plan_run_state": True' in bridge
assert '"auto_execute": False' in bridge
assert '"explicit_actions_preserved": True' in bridge
assert '"paired_app_scoped": True' in bridge
assert '"requires_resume": "v0.55"' in bridge
assert '"actions_via": "v0.53"' in bridge
assert '"agent.workflows.rehydration.v056"' in bridge
assert '"agent.chat.workflow_checkpoint.v056"' in bridge

assert "CREATE TABLE IF NOT EXISTS agent_workflow_rehydrations" in migration
assert "UNIQUE (source_app_key, conversation_id, plan_id, state_fingerprint)" in migration
assert "FOREIGN KEY (plan_id) REFERENCES agent_team_plans(id) ON DELETE CASCADE" in migration
assert "CHECK (status IN ('ready','conflict'))" in migration

assert "const VERSION = 'v0.56'" in ui
assert "method: 'POST'" in ui
assert "data-workflow-resume-conversation" in ui
assert "user explicitly pressed Resume" in ui
assert "setTimeout(() => recover" in ui
assert "boot/refresh paths never POST recovery" in ui
assert "RECOVERED WORKFLOW" in ui
assert "Checkpoint ready" in ui
assert "Review conflict" in ui
assert "actions_executed" not in ui
assert "provider" not in ui.lower()
assert "result_available" not in ui
assert "PRIVATE RESULT" not in ui
assert "workflow-rehydration" in css
assert "has-conflict" in css
assert "@media" in css

assert "/assets/agent-workflow-rehydration.js" in loader
assert "data-agent-workflow-rehydration-v056" in loader
assert "ensureRehydrationExtension" in loader
assert "method: 'POST'" not in loader

assert "v056-proposed-members" in edges
assert "_live_app_permissions_tx" in edges
assert "_agent_access_tx" in edges
assert "permissions changed during recovery" in edges
assert "specialist agent access changed" in edges
assert "/api/v1/capabilities" in capability
assert 'payload["agent_workflow_rehydration"]' in capability
assert '"agent.workflows.rehydration.v056"' in capability
assert '"agent.chat.workflow_checkpoint.v056"' in capability

assert "workflow-rehydration:" in workflow
assert "packaged-rehydration:" in workflow
assert "app/bridge.py" in workflow
assert "python tests/agent_workflow_rehydration_v056.py" in workflow
assert "python tests/agent_workflow_rehydration_edges_v056.py" in workflow
assert "python tests/agent_workflow_rehydration_capability_v056.py" in workflow
assert "python tests/agent_workflow_rehydration_ui_contract_v056.py" in workflow
assert "python tests/packaged_workflow_rehydration_v056.py" in workflow
assert "python tests/agent_workflow_resume_v055.py" in workflow
assert "python tests/agent_workflow_resume_ui_contract_v055.py" in workflow
assert "pyinstaller HomeServer.spec --clean --noconfirm" in workflow

assert "verify_rehydration" in packaged
assert 'payload["version"] == "v0.56"' in packaged
assert "/api/v1/control/agent-workflows/rehydrate" in packaged
assert "/api/v1/control/system/restart" in packaged
assert "old_session.get(\"/api/v1/control/system\").status_code == 401" in packaged
assert "/prepare" in packaged
assert "agent_result_handoffs" in packaged

print("HomeServer v0.56 Durable Workflow Rehydration & Safe Resume UI/API contract passed")
