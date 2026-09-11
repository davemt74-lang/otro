from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


service = text("app/services/agent_workflow_resume.py")
api = text("app/workflow_resume_api.py")
bridge = text("app/bridge.py")
ui = text("ui/agent-workflow-resume.js")
css = text("ui/agent-workflow-resume.css")
loader = text("ui/agent-workflow-continuation.js")
packaged = text("tests/packaged_workflow_resume_v055.py")
scan_regression = text("tests/agent_workflow_resume_scan_v055.py")
workflow = text(".github/workflows/agent-workflow-resume-v055.yml")

assert 'AGENT_WORKFLOW_RESUME_VERSION = "v0.55"' in service
assert "MAX_RESUME_CONVERSATIONS = 50" in service
assert "MAX_SCAN_CONVERSATIONS = 250" in service
assert "agent_workflow_continuation.conversation_continuation" in service
assert "c.status='active'" in service
assert "JOIN agent_team_plans p" in service
assert "p.status IN ('proposed','approved')" in service
assert "MAX(p.id) AS latest_plan_id" in service
assert "ORDER BY latest_plan_id DESC" in service
assert "LIMIT ?" in service
assert "MAX_SCAN_CONVERSATIONS + 1" in service
assert "truncated = len(rows) > MAX_SCAN_CONVERSATIONS" in service
assert "rows[:MAX_SCAN_CONVERSATIONS]" in service
assert "result_limit = _bounded_limit(limit)" in service
assert "visible_items = items[:result_limit]" in service
assert "resumable_count = len(items)" in service
assert '"scan_limit": MAX_SCAN_CONVERSATIONS' in service
assert '"scan_truncated": scan_truncated' in service
assert "if exc.status_code in {403, 404}" in service
assert "raise AgentWorkflowResumeError" in service
assert "def _retryable_ids" in service
assert "items.sort(" in service
assert 'int(item.get("plan_id") or 0)' in service
assert '"read_only": True' in service
assert '"auto_executes": False' in service
assert '"navigation_only": True' in service
assert '"explicit_actions_preserved": True' in service
assert "provider_key" not in service
assert '"model"' not in service
assert '"result"' not in service

assert api.count('@router.get("/api/v1/agent-workflows/resume")') == 1
assert api.count('@router.get("/api/v1/control/agent-workflows/resume")') == 1
assert "@router.post" not in api
assert "@router.put" not in api
assert "@router.patch" not in api
assert "@router.delete" not in api
assert "Permission required: agent.chat" in api

assert "workflow_resume_router" in bridge
assert '"agent_workflow_resume": {' in bridge
assert '"version": "v0.55"' in bridge
assert '"cross_conversation_index": True' in bridge
assert '"explicit_navigation": True' in bridge
assert '"restart_resumable": True' in bridge
assert '"auto_execute": False' in bridge
assert '"explicit_actions_preserved": True' in bridge
assert '"requires_continuation": "v0.54"' in bridge
assert '"agent.workflows.resume.v055"' in bridge
assert '"agent.chat.workflow_recovery.v055"' in bridge

assert "const VERSION = 'v0.55'" in ui
assert "method: 'GET'" in ui
assert "method: 'POST'" not in ui
assert "method: 'PUT'" not in ui
assert "method: 'PATCH'" not in ui
assert "method: 'DELETE'" not in ui
assert "requestSubmit" not in ui
assert ".submit(" not in ui
assert "data-workflow-resume-conversation" in ui
assert "data-brain-conversation" in ui
assert ui.count("target.click()") == 1
assert "This is navigation only" in ui
assert "scrollIntoView" in ui
assert "target.focus" in ui
assert "provider" not in ui.lower()
assert "result" not in ui.lower()
assert "workflow-resume-card" in css
assert "workflow-resume-badge" in css
assert "@media" in css

assert "/assets/agent-workflow-resume.js" in loader
assert "data-agent-workflow-resume-v055" in loader
assert "ensureResumeExtension" in loader

assert "verify_resume" in packaged
assert 'payload["version"] == "v0.55"' in packaged
assert "/api/v1/control/agent-workflows/resume?limit=50" in packaged
assert "/api/v1/control/system/restart" in packaged
assert "old_session.get(\"/api/v1/control/system\").status_code == 401" in packaged
assert "Newer plain chat" in packaged

assert "for index in range(60)" in scan_regression
assert 'resume?limit=1' in scan_regression
assert 'payload["scan_limit"] == 250' in scan_regression
assert 'payload["scan_truncated"] is False' in scan_regression
assert 'payload["suggested"]["conversation_id"] == resumable_conversation' in scan_regression
assert 'payload["suggested"]["retryable_task_ids"] == [901]' in scan_regression

assert "workflow-resume:" in workflow
assert "packaged-resume:" in workflow
assert "python tests/agent_workflow_resume_v055.py" in workflow
assert "python tests/agent_workflow_resume_scan_v055.py" in workflow
assert "python tests/agent_workflow_resume_ui_contract_v055.py" in workflow
assert "python tests/packaged_workflow_resume_v055.py" in workflow
assert "pyinstaller HomeServer.spec --clean --noconfirm" in workflow

print("HomeServer v0.55 Workflow Resume & Recovery UI/API contract passed")
