from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

routing = (ROOT / "app" / "services" / "agent_routing.py").read_text(encoding="utf-8")
routing_api = (ROOT / "app" / "agent_routing_api.py").read_text(encoding="utf-8")
context_chat = (ROOT / "app" / "services" / "context_chat.py").read_text(encoding="utf-8")
brain_api = (ROOT / "app" / "brain_api.py").read_text(encoding="utf-8")
delegation = (ROOT / "app" / "services" / "delegation.py").read_text(encoding="utf-8")
delegation_api = (ROOT / "app" / "delegation_api.py").read_text(encoding="utf-8")
bridge = (ROOT / "app" / "bridge.py").read_text(encoding="utf-8")
database = (ROOT / "app" / "database.py").read_text(encoding="utf-8")
schema = (ROOT / "database" / "agent_routing.sql").read_text(encoding="utf-8")
spec = (ROOT / "HomeServer.spec").read_text(encoding="utf-8")
ui = (ROOT / "ui" / "agent-routing.js").read_text(encoding="utf-8")
ui_css = (ROOT / "ui" / "agent-routing.css").read_text(encoding="utf-8")
voice_profile = (ROOT / "ui" / "agent-voice-profile.js").read_text(encoding="utf-8")
voice_chat = (ROOT / "ui" / "chat-enhancements.js").read_text(encoding="utf-8")
brain_ui = (ROOT / "ui" / "brain.js").read_text(encoding="utf-8")

# Persistence is a narrow feature extension; canonical schema version remains unchanged.
assert 'AGENT_ROUTING_VERSION = "v0.47"' in routing
assert "CREATE TABLE IF NOT EXISTS app_agent_grants" in schema
assert "PRIMARY KEY (paired_app_id, agent_id)" in schema
assert "ON DELETE CASCADE" in schema
assert 'ROOT_DIR / "database" / "agent_routing.sql"' in database
assert "('database/agent_routing.sql', 'database')" in spec

# Primary access remains implicit under the existing agent.chat contract. Every
# secondary Agent requires an exact app+Agent grant and active paired app.
assert "def resolve_agent(" in routing
assert "if bool(agent[\"is_primary\"]):" in routing
assert "FROM app_agent_grants" in routing
assert '"Agent is not authorized for this application."' in routing
assert "def validate_conversation_agent(" in routing
assert '"This conversation is bound to another Agent.' in routing
assert "def save_app_agent_grant(" in routing
assert "app.agent_access.updated" in routing
assert "Primary Agent access is provided by agent.chat" in routing

# Existing chat endpoints gain an optional Agent ID rather than creating a
# parallel inference implementation. Stateful and delegated paths both resolve it.
assert "agent_id: int | None = Field(default=None, ge=1)" in brain_api
assert "agent_id=payload.agent_id" in brain_api
assert "agent_routing.resolve_agent(" in context_chat
assert "agent_routing.validate_conversation_agent(" in context_chat
assert "canonical_context.build_authorized_context(" in context_chat
assert '"agent_routing_version": agent_routing.AGENT_ROUTING_VERSION' in context_chat
assert "agent_id: int | None = Field(default=None, ge=1)" in delegation_api
assert "agent_id=payload.agent_id" in delegation_api
assert "agent_routing.resolve_agent(source, agent_id, owner=False)" in delegation
assert "canonical_context.build_authorized_context(" in delegation
assert '"agent": agent_routing.safe_summary(homeserver_agent)' in delegation

# Paired wrappers can discover only primary plus explicitly granted specialists;
# owner controls expose exact per-app grants without adding a broad new permission.
assert '@router.get("/api/v1/agents")' in routing_api
assert '@router.get("/api/v1/control/connected-apps/{app_id}/agents")' in routing_api
assert '@router.put("/api/v1/control/connected-apps/{app_id}/agents/{agent_id}")' in routing_api
assert '"agent.chat"' in routing_api
assert "agent.select" not in bridge
assert '"agent_routing": {' in bridge
assert '"version": "v0.47"' in bridge
assert '"paired_app_scoped": True' in bridge
assert '"conversation_bound": True' in bridge
assert '"agent.routing.v047"' in bridge

# Owner chat gets an explicit selector while preserving the long-standing
# brain.js submit path. The routing adapter injects only the selected agent_id.
assert "chatAgentSelect" in ui
assert "Choose for new chat" in ui
assert "Bound to conversation" in ui
assert "body.agent_id == null" in ui
assert "agent_id: Number(state.selectedAgentId)" in ui
assert "syncConversationBinding" in ui
assert "'/api/v1/control/chat'" in brain_ui

# Conversation-mode Piper speech follows the same selected Agent through the
# established v0.45 per-Agent synthesis endpoint; legacy TTS remains the fallback.
assert "LEGACY_TTS_ENDPOINT = '/api/v1/control/voice/synthesize'" in ui
assert "/api/v1/control/voice/agents/${Number(state.selectedAgentId)}/synthesize" in ui
assert "LOCAL_TTS_ENDPOINT = '/api/v1/control/voice/synthesize'" in voice_chat
assert "HomeServerAgentRouting" in ui

# Connected Apps gets an owner-facing Agent grant surface and clearly states
# that selecting an Agent cannot expand private resource permissions.
assert "Agent access" in ui
assert "Primary · implicit access" in ui
assert "Secondary persona" in ui
assert "Agent selection never expands" in ui
assert "/api/v1/control/connected-apps/${appId}/agents" in ui
assert "/agents/${toggle.dataset.agentAccessId}" in ui
assert ".agent-access-grid" in ui_css

# v0.47 UI is loaded by the existing Voice Catalog -> Agent Voice Profile chain,
# so it is present without replacing the v0.45/v0.46 management surfaces.
assert "'/assets/agent-routing.js'" in voice_profile
assert "script.dataset.agentRouting = 'v0.47'" in voice_profile
assert "'/assets/agent-management.js'" in voice_profile

print("HomeServer v0.47 Agent Routing UI/API/package contract passed")
