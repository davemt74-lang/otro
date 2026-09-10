from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
script = (ROOT / "ui" / "voice-catalog.js").read_text(encoding="utf-8")
styles = (ROOT / "ui" / "voice-settings.css").read_text(encoding="utf-8")
index = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")

required_script_markers = [
    "/api/v1/control/voice/catalog",
    "/api/v1/control/voice/preview",
    "voicePackStatus",
    "voicePackPreviewButton",
    "voicePackManageButton",
    "voicePackUninstallButton",
    "management_state",
    "can_uninstall",
    "can_preview",
    "Preview voice",
    "Repair voice",
    "Install voice",
    "Uninstall pack",
    "window.confirm",
    "homeserver:voice-settings-loaded",
    "homeserver:voice-catalog-changed",
    "HomeServerVoiceCatalog",
]
for marker in required_script_markers:
    assert marker in script, f"missing Voice Catalog UI marker: {marker}"

required_style_markers = [
    ".voice-pack-card",
    ".voice-pack-badge.ready",
    ".voice-pack-badge.repair",
    ".voice-pack-badge.missing",
    ".voice-pack-active",
    ".voice-pack-error",
]
for marker in required_style_markers:
    assert marker in styles, f"missing Voice Catalog style marker: {marker}"

assert "/api/v1/control/local-apps" not in script, "Voice Catalog UI must use policy-aware voice management endpoints"
assert "Select and save another voice before uninstalling this pack." in script
assert "voice: voice.key" in script
assert "speaking_rate: speakingRate" in script
assert "sentence_silence: sentenceSilence" in script
assert "without changing your saved voice" in script
assert '<script src="/assets/voice-catalog.js" defer></script>' in index

print("HomeServer v0.44 Voice Catalog UI contract passed")
