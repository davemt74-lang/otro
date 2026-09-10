from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

service = (ROOT / "app" / "services" / "agent_management.py").read_text(encoding="utf-8")
api = (ROOT / "app" / "agents_api.py").read_text(encoding="utf-8")
bridge = (ROOT / "app" / "bridge.py").read_text(encoding="utf-8")
ui = (ROOT / "ui" / "agent-management.js").read_text(encoding="utf-8")
voice_ui = (ROOT / "ui" / "agent-voice-profile.js").read_text(encoding="utf-8")
css = (ROOT / "ui" / "agent-management.css").read_text(encoding="utf-8")

assert 'AGENT_PERSONA_MANAGEMENT_VERSION = "v0.46"' in service
for marker in (
    "def list_agents()",
    "def get_agent(agent_id: int)",
    "def create_agent(value:",
    "def update_agent(agent_id: int",
    "def delete_agent(agent_id: int)",
    "def duplicate_agent(agent_id: int)",
    '"The primary Agent cannot be deleted."',
    "SELECT COUNT(*) FROM agent_memory WHERE agent_id=?",
    '"detached_memory_items"',
    "INSERT INTO agent_voice_profiles(agent_id, voice_key, speaking_rate, sentence_silence)",
    "SELECT ?, voice_key, speaking_rate, sentence_silence",
    "_log(connection,",
):
    assert marker in service, f"missing v0.46 service contract marker: {marker}"
assert "UPDATE agents\n            SET name=?, instructions=?, model=?" in service
assert "SET is_primary" not in service

assert 'APIRouter(prefix="/api/v1/control/agents"' in api
for route in (
    '@router.get("")',
    '@router.post("")',
    '@router.get("/{agent_id}")',
    '@router.put("/{agent_id}")',
    '@router.delete("/{agent_id}")',
    '@router.post("/{agent_id}/duplicate")',
):
    assert route in api, f"missing v0.46 route: {route}"
assert "extra=\"forbid\"" in api

assert "from .agents_api import router as agents_router" in bridge
assert "app.include_router(agents_router)" in bridge
assert '"agent_personas": {' in bridge
assert '"version": "v0.46"' in bridge
assert '"agent.personas.v046"' in bridge

for marker in (
    "ADDITIONAL AGENTS · v0.46",
    "Multi-Agent Personas",
    "Duplicate primary",
    "Add Agent",
    "Edit persona",
    "Inherits global voice",
    "Custom speaking speed",
    "Custom sentence pause",
    "Preview voice",
    "memory was not copied",
    "memories remain stored locally and become unassigned",
    "window.HomeServerAgentManagement",
):
    assert marker in ui, f"missing v0.46 UI marker: {marker}"
assert "const API = '/api/v1/control/agents'" in ui
assert "const VOICE_API = '/api/v1/control/voice/agents'" in ui
assert "`${API}/${Number(agentId)}/duplicate`" in ui
assert "`${VOICE_API}/${id}/profile`" in ui
assert "homeserver:agent-voice-profile-changed" in ui
assert "HomeServerVoiceSettings?.applyOutputSink" in ui
assert "data-agent-management" in voice_ui
assert "agent-management.js" in voice_ui
assert ".agent-manager" in css
assert "@media (max-width: 760px)" in css

print("HomeServer v0.46 Multi-Agent Persona UI/API contract passed")
