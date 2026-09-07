from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
context_js = (ROOT / "ui" / "context-engine.js").read_text(encoding="utf-8")
cognition_js = (ROOT / "ui" / "cognition.js").read_text(encoding="utf-8")
cognition_css = (ROOT / "ui" / "cognition.css").read_text(encoding="utf-8")

assert "/assets/cognition.js" in context_js
assert "data-homeserver-cognition" in context_js
assert "view-cognition" in cognition_js
assert "Cognition & Plugins" in cognition_js
assert "data-view = 'cognition'" not in cognition_js  # avoid malformed spaced dataset assignment
assert "button.dataset.view = 'cognition'" in cognition_js
assert "/api/v1/control/cognition" in cognition_js
assert "/api/v1/control/cognition/events?limit=100" in cognition_js
assert "include_payload=true" not in cognition_js
assert "/api/v1/control/cognition/memory-candidates/" in cognition_js
assert "/api/v1/control/cognition/awareness/" in cognition_js
assert "/api/v1/control/plugins" in cognition_js
assert "Registering a manifest never executes arbitrary downloaded code" in cognition_js
assert "cognition-plugin-grid" in cognition_css

print("HomeServer Cognition workspace UI integration contract passed")
