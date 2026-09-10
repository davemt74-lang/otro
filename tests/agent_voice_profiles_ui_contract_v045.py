from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

api = (ROOT / "app" / "local_voice_api.py").read_text(encoding="utf-8")
service = (ROOT / "app" / "services" / "agent_voice_profiles.py").read_text(encoding="utf-8")
catalog_service = (ROOT / "app" / "services" / "voice_settings.py").read_text(encoding="utf-8")
database = (ROOT / "app" / "database.py").read_text(encoding="utf-8")
ui = (ROOT / "ui" / "agent-voice-profile.js").read_text(encoding="utf-8")
catalog_ui = (ROOT / "ui" / "voice-catalog.js").read_text(encoding="utf-8")
css = (ROOT / "ui" / "agent-voice-profile.css").read_text(encoding="utf-8")
feature_schema = (ROOT / "database" / "agent_voice_profiles.sql").read_text(encoding="utf-8")

assert 'VOICE_AGENT_PROFILE_VERSION = "v0.45"' in service
assert 'VOICE_CATALOG_VERSION = "v0.43"' in catalog_service
assert 'VOICE_CATALOG_MANAGEMENT_VERSION = "v0.44"' in catalog_service
assert "agent_voice_profiles.sql" in database
assert "FEATURE_SCHEMA_PATHS" in database
assert "CREATE TABLE IF NOT EXISTS agent_voice_profiles" in feature_schema
assert "FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE" in feature_schema
assert "speaking_rate >= 0.6" in feature_schema and "speaking_rate <= 1.6" in feature_schema
assert "sentence_silence >= 0.0" in feature_schema and "sentence_silence <= 1.5" in feature_schema

for route in (
    '@router.get("/agents/{agent_id}/profile")',
    '@router.put("/agents/{agent_id}/profile")',
    '@router.post("/agents/{agent_id}/preview")',
    '@router.post("/agents/{agent_id}/synthesize")',
):
    assert route in api
assert "primary_agent_id()" in api
assert "not any(value is not None for value in resolved[\"overrides\"].values())" in api
assert "_global_audio(payload.text)" in api
assert '"X-HomeServer-Voice-Source"' in api

for token in (
    "Agent Voice Profile",
    "Use global voice",
    "Custom speaking speed",
    "Custom sentence pause",
    "Preview effective voice",
    "homeserver:agent-voice-profile-changed",
    "fallback_global",
    "fallback_default",
    "agent-voice-profile.css",
):
    assert token in ui
assert "/api/v1/control/voice/agents/${state.agentId}/profile" in ui
assert "/api/v1/control/voice/agents/${state.agentId}/preview" in ui
assert "window.HomeServerVoiceSettings?.applyOutputSink" in ui
assert "agent-voice-profile.js" in catalog_ui
assert "data-agent-voice-profile" in catalog_ui
assert "agent_reference_count" in catalog_ui
assert "Assigned voice cannot be removed" in catalog_ui
assert "agent_voice_references" in catalog_service
assert "Set those Agents to Use global voice" in catalog_service
assert ".agent-voice-profile" in css
assert "@media (max-width: 760px)" in css

print("HomeServer v0.45 Agent Voice Profiles UI/API contract passed")
