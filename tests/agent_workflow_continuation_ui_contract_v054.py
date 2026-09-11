from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


service = text("app/services/agent_workflow_continuation.py")
api = text("app/workflow_continuation_api.py")
bridge = text("app/bridge.py")
ui = text("ui/agent-workflow-continuation.js")
css = text("ui/agent-workflow-continuation.css")
loader = text("ui/agent-team-orchestration.js")

assert 'AGENT_WORKFLOW_CONTINUATION_VERSION = "v0.54"' in service
assert 'TERMINAL_STATUSES = {"rejected", "synthesized", "cancelled"}' in service
assert "conversation_continuation" in service
assert "agent_routing.conversation_binding" in service
assert "agent_team_orchestration.list_orchestrations" in service
assert '"read_only": True' in service
assert '"auto_executes": False' in service
assert '"explicit_actions_preserved": True' in service
assert '"actions_via": agent_team_orchestration.AGENT_TEAM_ORCHESTRATION_VERSION' in service
assert "provider_key" not in service
assert '"model"' not in service
assert '"result"' not in service

assert api.count('@router.get("/api/v1/agent-workflows/continuation")') == 1
assert api.count('@router.get("/api/v1/control/agent-workflows/continuation")') == 1
assert "@router.post" not in api
assert "@router.put" not in api
assert "@router.patch" not in api
assert "@router.delete" not in api
assert "Permission required: agent.chat" in api

assert "workflow_continuation_router" in bridge
assert '"agent_workflow_continuation": {' in bridge
assert '"version": "v0.54"' in bridge
assert '"read_only": True' in bridge
assert '"conversation_bound": True' in bridge
assert '"derived_from": "v0.53"' in bridge
assert '"auto_execute": False' in bridge
assert '"explicit_actions_preserved": True' in bridge
assert '"agent.workflows.continuation.v054"' in bridge
assert '"agent.chat.workflow_awareness.v054"' in bridge

assert "const VERSION = 'v0.54'" in ui
assert "method: 'GET'" in ui
assert "method: 'POST'" not in ui
assert "method: 'PUT'" not in ui
assert "method: 'PATCH'" not in ui
assert "method: 'DELETE'" not in ui
assert ".click(" not in ui
assert "requestSubmit" not in ui
assert ".submit(" not in ui
assert "scrollIntoView" in ui
assert ".focus(" in ui
assert "Review plan" in ui
assert "Open Team Run" in ui
assert "Review retry" in ui
assert "Prepare synthesis" in ui
assert "Continue in parent chat" in ui
assert "data-workflow-continuation-action" in ui
assert "provider" not in ui.lower()
assert "agent-workflow-continuation" in css
assert "@media" in css

assert "/assets/agent-workflow-continuation.js" in loader
assert "data-agent-workflow-continuation-v054" in loader
assert "ensureContinuationExtension" in loader

print("HomeServer v0.54 Workflow Continuation Awareness UI/API contract passed")
