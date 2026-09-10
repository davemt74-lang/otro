from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

canonical = (ROOT / "app" / "services" / "canonical_context.py").read_text(encoding="utf-8")
context_chat = (ROOT / "app" / "services" / "context_chat.py").read_text(encoding="utf-8")
delegation = (ROOT / "app" / "services" / "delegation.py").read_text(encoding="utf-8")
delegation_api = (ROOT / "app" / "delegation_api.py").read_text(encoding="utf-8")
remote_bridge = (ROOT / "app" / "services" / "remote_bridge.py").read_text(encoding="utf-8")
voice = (ROOT / "ui" / "chat-enhancements.js").read_text(encoding="utf-8")
brain_ui = (ROOT / "ui" / "brain.js").read_text(encoding="utf-8")
brain = (ROOT / "app" / "services" / "brain.py").read_text(encoding="utf-8")

# One production authorization/retrieval boundary owns base HomeServer context,
# collection scope, awareness, collaboration and VP3 surface/workspace context.
assert 'CANONICAL_CONTEXT_VERSION = "v4.30"' in canonical
assert "def build_authorized_context(" in canonical
assert "knowledge_collection_policy.filter_items_for_app(" in canonical
assert "app_collaboration.eligible_grants(" in canonical
assert "awareness_context.collect(" in canonical
assert "VP3 surface/workspace context" in canonical
assert '"surface_used_chars"' in canonical
assert '"used_chars"' in canonical
assert '"remaining_chars"' in canonical
assert "if used > requested_budget:" in canonical
assert "Canonical context budget exceeded." in canonical
assert '"sha256"' in canonical
assert '"layer": "shared_wrapper"' in canonical
assert '"layer": "surface_workspace"' in canonical

# Stateful owner + paired-app text chat and delegated/streamed VP3 chat both
# enter the same builder. The old delegated duplicate collector is gone.
assert "canonical_context.build_authorized_context(" in context_chat
assert "canonical_context.system_prompt(" in context_chat
assert "canonical_context.build_authorized_context(" in delegation
assert "canonical_context.system_prompt(" in delegation
assert "def _collect_context(" not in delegation
assert "def _surface_fragment(" not in delegation
assert "context_engine._memory_candidates(" not in delegation
assert "context_engine._knowledge_candidates(" not in delegation
assert "context_engine._contact_candidates(" not in delegation

# Compatibility routing still sends delegated payloads to delegation.chat and
# non-delegated payloads through client_chat -> context_chat.
assert "return delegation.chat(" in delegation_api
assert "return client_chat(legacy, identity)" in delegation_api

# Remote agent.chat cannot assemble context itself; it authenticates and
# forwards through the same /api/v1/chat boundary.
assert 'if op == "agent.chat":' in remote_bridge
assert 'client.post("/api/v1/chat", json=body, headers=headers)' in remote_bridge

# HomeServer voice/dictation is transport only: transcript -> chatForm submit ->
# the same /api/v1/control/chat endpoint used for typed messages.
assert "function autoSubmitSpeech(transcript)" in voice
assert "form.requestSubmit();" in voice
assert "autoSubmitSpeech(transcript);" in voice
assert "'/api/v1/control/chat'" in brain_ui

# The historical brain.chat implementation remains only as legacy code; no
# active v4.30 production path may call it. Its provider/tool runner is still
# shared by both canonical chat paths.
assert "def chat(" in brain
assert "brain._generate_with_agent_tools(" in context_chat
assert "brain._generate_with_agent_tools(" in delegation

print("HomeServer Canonical Agent Context & Retrieval Boundary v4.30 contract passed")
