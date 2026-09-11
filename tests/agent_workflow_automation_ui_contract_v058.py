from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


service = text("app/services/agent_workflow_automation.py")
runtime = text("app/services/agent_workflow_automation_runtime.py")
api = text("app/workflow_automation_api.py")
supervision_api = text("app/workflow_supervision_api.py")
database = text("app/database.py")
schema = text("database/agent_workflow_automation.sql")
spec = text("HomeServer.spec")
ui = text("ui/agent-workflow-automation.js")
css = text("ui/agent-workflow-automation.css")
supervision_ui = text("ui/agent-workflow-supervision.js")
regression = text("tests/agent_workflow_automation_v058.py")
recovery = text("tests/agent_workflow_automation_recovery_v058.py")
lifespan = text("tests/agent_workflow_automation_lifespan_v058.py")
schema_test = text("tests/agent_workflow_automation_schema_v058.py")
packaged = text("tests/packaged_workflow_automation_v058.py")
workflow = text(".github/workflows/agent-workflow-automation-v058.yml")

assert 'AGENT_WORKFLOW_AUTOMATION_VERSION = "v0.58"' in service
assert "MIN_INTERVAL_SECONDS = 60" in service
assert 'AUTOMATION_TRIGGER_TYPES = {"once", "interval", "activity"}' in service
assert "SAFE_ACTIVITY_PREFIXES" in service
assert "agent_workflow_rehydration.rehydrate_workflow" in service
assert "BEGIN IMMEDIATE" in service
assert "agent_workflow_automation_runs" in service
assert "trigger_key" in service
assert "watermark" in service
assert "_live_access" in service
assert "agent.chat" in service
assert "REVIEW_BOUNDARIES" in service
assert "agent_team_orchestration.run_plan_team" not in service
assert "agent_team_orchestration.prepare_plan_synthesis" not in service
assert ".approve" not in service
assert "retry_plan_member" not in service
assert "parent_chat" not in service

assert "RUN_LEASE_SECONDS = 60" in runtime
assert "_reserve_run" in runtime
assert "_persist_checkpoint" in runtime
assert "_recover_inflight" in runtime
assert "rehydration_id" in runtime
assert "state_fingerprint" in runtime
assert "agent_workflow_supervision.continue_workflow" in runtime
assert "allow_stale_running" in runtime
assert "status='running'" in runtime
assert 'name="homeserver-workflow-automation-scheduler"' in runtime
assert "run_due_automations()" in runtime
assert "agent_team_orchestration.run_plan_team" not in runtime
assert "retry_plan_member" not in runtime
assert ".approve" not in runtime
assert "parent_chat" not in runtime

assert api.count('@router.post("/api/v1/agent-workflows/automations")') == 1
assert api.count('@router.post("/api/v1/control/agent-workflows/automations")') == 1
assert api.count('@router.get("/api/v1/agent-workflows/automations")') == 1
assert api.count('@router.get("/api/v1/control/agent-workflows/automations")') == 1
assert "workflow_automation_lifespan" in api
assert "agent_workflow_automation_runtime.scheduler.start()" in api
assert "agent_workflow_automation_runtime.scheduler.stop()" in api
assert "Permission required: agent.chat" in api
assert '"requires_explicit_creation": True' in api
assert '"durable_claim": True' in api
assert '"durable_checkpoint_before_supervision": True' in api
assert '"restart_resumable": True' in api
assert '"stale_run_lease_seconds": agent_workflow_automation_runtime.RUN_LEASE_SECONDS' in api
assert '"canonical_revalidation_at_fire": True' in api
assert '"auto_approval": False' in api
assert '"auto_retry": False' in api
assert '"auto_parent_chat": False' in api
assert '"uses_supervision"' in api
assert "workflow_automation_router" in supervision_api
assert "router.include_router(workflow_automation_router)" in supervision_api
assert '"automation_extension": "v0.58"' in supervision_api

assert 'ROOT_DIR / "database" / "agent_workflow_automation.sql"' in database
assert "CREATE TABLE IF NOT EXISTS agent_workflow_automations" in schema
assert "CREATE TABLE IF NOT EXISTS agent_workflow_automation_runs" in schema
assert "CHECK (trigger_type IN ('once','interval','activity'))" in schema
assert "CHECK (max_steps BETWEEN 1 AND 2)" in schema
assert "UNIQUE (automation_id, trigger_key)" in schema
assert "FOREIGN KEY (supervision_id) REFERENCES agent_workflow_supervisions(id) ON DELETE SET NULL" in schema
assert "('database/agent_workflow_automation.sql', 'database')" in spec

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

assert 'sum(item["claimed"] + item["recovered"] for item in race_results) == 1' in regression
assert "task.reminded" in regression
assert "agent.workflow.supervision.stopped" in regression
assert "allowed=0" in regression
assert "proposed workflow should not be automatable" in regression
assert "runtime.run_due_automations" in regression
assert "Crash after durable trigger claim" in recovery
assert "must not rehydrate to a newer" in recovery
assert "in progress" in recovery
assert "RUN_LEASE_SECONDS" in recovery or "timedelta(minutes=23)" in recovery
assert "scheduler._thread.is_alive()" in lifespan
assert 'payload["version"] == "v0.58"' in lifespan
assert 'payload["stale_run_lease_seconds"] == 60' in lifespan
assert "versions_after == versions_before" in schema_test
assert "DROP TABLE agent_workflow_automation_runs" in schema_test
assert "DROP TABLE agent_workflow_automations" in schema_test
assert "initialize_database()" in schema_test

assert "/api/v1/control/agent-workflows/automations" in packaged
assert "/api/v1/control/system/restart" in packaged
assert "agent_workflow_automation_runs" in packaged
assert "agent_workflow_supervisions" in packaged
assert "agent.synthesis.prepared" in packaged

assert "workflow-automation:" in workflow
assert "packaged-automation:" in workflow
assert "agent_workflow_automation_runtime.py" in workflow
assert "agent_workflow_automation.sql" in workflow
assert "agent_workflow_automation_recovery_v058.py" in workflow
assert "python tests/agent_workflow_automation_v058.py" in workflow
assert "python tests/agent_workflow_automation_recovery_v058.py" in workflow
assert "python tests/agent_workflow_automation_lifespan_v058.py" in workflow
assert "python tests/agent_workflow_automation_schema_v058.py" in workflow
assert "python tests/agent_workflow_automation_ui_contract_v058.py" in workflow
assert "python tests/agent_workflow_supervision_v057.py" in workflow
assert "pyinstaller HomeServer.spec --clean --noconfirm" in workflow
assert "python tests/packaged_workflow_automation_v058.py" in workflow

print("HomeServer v0.58 Scheduled / Triggered Workflows UI/API contract passed")