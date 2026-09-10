from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
script = (ROOT / "ui" / "voice-catalog.js").read_text(encoding="utf-8")
index = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")

required_script_markers = [
    "/api/v1/control/voice/catalog",
    "/api/v1/control/local-apps",
    "voicePackStatus",
    "voicePackInstallButton",
    "Install required",
    "runtime_app_key",
    "install_app_key",
    "homeserver:voice-settings-loaded",
    "HomeServerVoiceCatalog",
]
for marker in required_script_markers:
    assert marker in script, f"missing Voice Catalog UI marker: {marker}"

assert "fetch(`${LOCAL_APPS_ENDPOINT}/${encodeURIComponent(voice.runtime_app_key)}/install`" not in script
assert "requestJson(`${LOCAL_APPS_ENDPOINT}/${encodeURIComponent(voice.runtime_app_key)}/install`" in script
assert "requestJson(`${LOCAL_APPS_ENDPOINT}/${encodeURIComponent(voice.install_app_key)}/install`" in script
assert script.index("voice.runtime_app_key") < script.index("voice.install_app_key"), "runtime must be installed before an optional pack"
assert '<script src="/assets/voice-catalog.js" defer></script>' in index

print("HomeServer v0.43 Voice Catalog UI contract passed")
