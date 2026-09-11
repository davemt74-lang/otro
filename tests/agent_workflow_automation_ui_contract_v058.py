from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


service = text("app/services/agent_workflow_automation.py")
api = text("app/workflow_automation_api.py")
supervision_api = text("app/workflow_supervision_api.py")
migration = text("database/migrations/022_agent_workflow_automation.sql")
ui = text("ui/agent-workflow-automation.js")
css = text("ui/agent-workflow-automation.css")
supervision_ui = text("ui/agent-workflow-supervision.js")
regression = text("tests/agent_workflow_automation_v058.py")
lifespan = text("tests/agent_workflow_automation_lifespan_v058.py")
schema_test = text("tests/agent_workflow_automation_schema_v058.py")

assert 'AGENT_WORKFLOW_AUTOMATION_VERSION = "v0.58"' in service
assert "MIN_INTERVAL_SECONDS = 60" in service
assert 'AUTOMATION_TRIGGER_TYPES = {"once", "interval", "activity"}' in service
assert "SAFE_ACTIVITY_PREFIXES" in service
assert "agent_workflow_supervision.continue_workflow" in service
assert "agent_workflow_rehydration.rehydrate_workflow" in service
assert "agent_team_orchestration.run_plan_team" not in service
assert "agent_team_orchestration.prepare_plan_synthesis" not in service
assert "BEGIN IMMEDIATE" in service
assert "UNIQUE" not in service  # uniqueness is enforced by the schema, not string-built SQL
assert "agent_workflow_automation_runs" in service
assert "trigger_key" in service
assert "watermark" in service
assert "_live_access" in service
assert "agent.chat" in service
assert "REVIEW_BOUNDARIES" in service
assert "WorkflowAutomationScheduler" in service
assert 'name="homeserver-workflow-automation-scheduler"' in service
assert "run_due_automations()" in service
assert ".approve" not in service
assert "retry_plan_member" not in service
assert "parent_chat" not in service

assert api.count('@router.post("/api/v1/agent-workflows/automations")') == 1
assert api.count('@router.post("/api/v1/control/agent-workflows/automations")') == 1
assert api.count('@router.get("/api/v1/agent-workflows/automations")') == 1
assert api.count('@router.get("/api/v1/control/agent-workflows/automations")') == 1
assert "workflow_automation_lifespan" in api
assert "scheduler.start()" in api
assert "scheduler.stop()" in api
assert "Permission required: agent.chat" in api
assert '"requires_explicit_creation": True' in api
assert '"durable_claim": True' in api
assert '"restart_resumable": True' in api
assert '"canonical_revalidation_at_fire": True' in api
assert '"auto_approval": False' in api
assert '"auto_retry": False' in api
assert '"auto_parent_chat": False' in api
assert '"uses_supervision"' in api
assert "workflow_automation_router" in supervision_api
assert "router.include_router(workflow_automation_router)" in supervision_api
assert '"automation_extension": "v0.58"' in supervision_api

assert "CREATE TABLE IF NOT EXISTS agent_workflow_automations" in migration
assert "CREATE TABLE IF NOT EXISTS agent_workflow_automation_runs" in migration
assert "CHECK (trigger_type IN ('once','interval','activity'))" in migration
assert "CHECK (max_steps BETWEEN 1 AND 2)" in migration
assert "UNIQUE (automation_id, trigger_key)" in migration
assert "FOREIGN KEY (supervision_id) REFERENCES agent_workflow_supervisions(id) ON DELETE SET NULL" in migration

assert "const VERSION = 'v0.58'" in ui
assert "Schedule / Trigger" in ui
assert "Run once later" in ui
assert "Repeat on an interval" in ui
assert "Run on an exact activity event" in ui
assert "data-automation-open" in ui
assert "workflowAutomationForm" in ui
assert "method: 'POST'" in ui
assert "explicit owner submit" in ui
assert "Boot, recovery" in ui
assert "no automatic approval" in ui
assert "no automatic retry" in ui
assert "no automatic parent message" in ui
assert "setTimeout(() => createAutomation" not in ui
assert "createAutomation().catch" in ui
boot = ui.split("function boot()", 1)[1]
assert "method: 'POST'" not in boot
assert "createAutomation(" not in boot
assert "workflow-automation" in css
assert "@media" in css
assert "/assets/agent-workflow-automation.js" in supervision_ui
assert "data-agent-workflow-automation-v058" in supervision_ui

assert "sum(item[\"claimed\"] for item in race_results) == 1" in regression
assert "task.reminded" in regression
assert "agent.workflow.supervision.stopped" in regression
assert "allowed=0" in regression
assert "proposed workflow should not be automatable" in regression
assert "schema_migrations WHERE version=22" in regression
assert "scheduler._thread.is_alive()" in lifespan
assert 'payload["version"] == "v0.58"' in lifespan
assert "versions_after == versions_before" in schema_test
assert "versions_after.count(22) == 1" in schema_test

print("HomeServer v0.58 Scheduled / Triggered Workflows UI/API contract passed")