from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
script = (ROOT / "ui" / "voice-catalog.js").read_text(encoding="utf-8")
api = (ROOT / "app" / "local_voice_api.py").read_text(encoding="utf-8")
service = (ROOT / "app" / "services" / "voice_settings.py").read_text(encoding="utf-8")

assert "POST" in script
assert "DELETE" in script
assert "Preview voice" in script
assert "Repair voice" in script
assert "Install voice" in script
assert "Uninstall pack" in script
assert "window.confirm" in script
assert "voice.active" in script
assert "can_uninstall" in script
assert "can_preview" in script
assert 'body: JSON.stringify({' in script
assert "voice: voice.key" in script
assert "speaking_rate: speakingRate" in script
assert "sentence_silence: sentenceSilence" in script

assert '@router.post("/preview")' in api
assert '@router.post("/catalog/{voice_key}/install")' in api
assert '@router.post("/catalog/{voice_key}/repair")' in api
assert '@router.delete("/catalog/{voice_key}")' in api
assert "VoicePreviewRequest" in api

assert 'VOICE_CATALOG_VERSION = "v0.44"' in service
assert 'management_state = "repair"' in service
assert 'management_state = "install"' in service
assert '"can_uninstall"' in service
assert '"active"' in service
assert "Select and save another speaking voice before uninstalling" in service
assert "cannot be removed separately" in service

print("HomeServer v0.44 Voice Catalog management UI/API contract passed")
