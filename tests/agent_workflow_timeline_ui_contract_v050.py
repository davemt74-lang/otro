from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


service = text("app/services/agent_workflow_timeline.py")
api = text("app/workflow_timeline_api.py")
bridge = text("app/bridge.py")
ui = text("ui/agent-workflows.js")
css = text("ui/agent-workflows.css")
handoffs = text("app/services/agent_handoffs.py")
workflow_service = text("app/services/agent_workflows.py")

assert 'AGENT_WORKFLOW_TIMELINE_VERSION = "v0.50"' in service
assert "MAX_SYNTHESIS_TASKS = 4" in service
assert "MIN_SYNTHESIS_TASKS = 2" in service
assert "agent_delegation_tasks" in service
assert "agent_result_handoffs" in service
assert "agent.synthesis.prepared" in service
assert "This conversation already has another pending specialist handoff" in service
assert "All synthesis tasks must belong to the active parent conversation" in service
assert "All synthesis tasks must share the active parent Agent" in service
assert "Current application permissions or Agent access no longer authorize" in service
assert '"parent_synthesis"' in service
assert '"synthesis_prepared"' in service
assert '"result_redacted"' in service

for route in (
    '/api/v1/agent-workflows/timeline',
    '/api/v1/agent-workflows/synthesis',
    '/api/v1/control/agent-workflows/timeline',
    '/api/v1/control/agent-workflows/synthesis',
):
    assert route in api
assert "ConfigDict(extra=\"forbid\")" in api
assert "Permission required: agent.chat" in api
assert "min_length=2" in api

assert "workflow_timeline_router" in bridge
assert '"agent_workflow_timeline": {' in bridge
assert '"version": "v0.50"' in bridge
assert '"batch_synthesis": True' in bridge
assert '"atomic_prepare": True' in bridge
assert '"agent.workflows.timeline.v050"' in bridge
assert '"agent.workflows.synthesis.v050"' in bridge

assert "const VERSION = 'v0.48'" in ui
assert "const HANDOFF_VERSION = 'v0.49'" in ui
assert "const TIMELINE_VERSION = 'v0.50'" in ui
assert "One-hop only in v0.48" in ui
assert "Synthesize selected" in ui
assert "data-synthesis-task" in ui
assert "prepareSynthesis" in ui
assert "loadTimeline" in ui
assert "renderTimeline" in ui
assert "agent-workflow-inline-event" in ui
assert "/api/v1/control/conversations/" in ui
assert "2–4-result synthesis set" in ui
assert "A parent synthesis can include at most four specialist results." in ui
assert "agent-workflow-inline-event" in css
assert "agent-synthesis-select" in css
assert "agent-workflow-history-actions" in css

# v0.50 is additive. Existing trust/version contracts stay unchanged.
assert 'HANDOFF_VERSION = "v0.49"' in handoffs
assert 'AGENT_WORKFLOW_VERSION = "v0.48"' in workflow_service
assert "agent.handoffs.v049" in bridge
assert "agent.workflows.v048" in bridge
assert "agent.routing.v047" in bridge
assert "agent.personas.v046" in bridge

print("HomeServer v0.50 Multi-Agent Workflow Timeline UI/API contract passed")
