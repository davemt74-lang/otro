from __future__ import annotations

from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
shell = (ROOT_DIR / "ui" / "shell.js").read_text(encoding="utf-8")
script = (ROOT_DIR / "ui" / "context-engine.js").read_text(encoding="utf-8")
style = (ROOT_DIR / "ui" / "context-engine.css").read_text(encoding="utf-8")
brain_api = (ROOT_DIR / "app" / "brain_api.py").read_text(encoding="utf-8")

assert "/assets/context-engine.js" in shell
assert "data-homeserver-context-engine" in shell
assert "chatContextControls" in script
assert "contextUseMemory" in script
assert "contextUseKnowledge" in script
assert "contextUseContacts" in script
assert "contextCloudAllowed" in script
assert "contextBudget" in script
assert "/context`" in script or "/context'" in script
assert "contextSources" in script
assert "context-source" in style
assert "ContextSettingsUpdate" in brain_api
assert "/api/v1/control/conversations/{conversation_id}/context" in brain_api
assert "include_contacts=\"contacts.read\" in permissions" in brain_api

print("HomeServer Agent Brain context UI contract passed")
