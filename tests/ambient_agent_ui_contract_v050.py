from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ambient_py = (ROOT / "app" / "services" / "ambient_agent.py").read_text(encoding="utf-8")
ambient_js = (ROOT / "ui" / "ambient-agent.js").read_text(encoding="utf-8")
ambient_css = (ROOT / "ui" / "ambient-agent.css").read_text(encoding="utf-8")
index_html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")

# Ambient coordination must never become a hidden microphone implementation.
for forbidden in (
    "start_capture(",
    "start_stream_capture(",
    "local_voice.transcribe(",
):
    assert forbidden not in ambient_py, forbidden

for required in (
    '"ambient_microphone_capture": False',
    '"ambient_transcription": False',
    '"ambient_memory": False',
    'memory_candidate=False',
):
    assert required in ambient_py, required

# Presence is ephemeral runtime state, not a Cognitive Runtime event.
assert '"ambient.presence"' not in ambient_py

for required in (
    'data-view="ambient"',
    'id="view-ambient"',
    'id="ambientEnabled"',
    'id="ambientPresencePolicy"',
    'id="ambientTestVoice"',
    '/assets/ambient-agent.css',
    '/assets/ambient-agent.js',
):
    assert required in index_html, required

for required in (
    "/api/v1/control/vp3-os/ambient",
    "/api/v1/control/vp3-os/ambient/settings",
    "/api/v1/control/vp3-os/ambient/announce-test",
    "announcement_levels",
    "presence_policy",
):
    assert required in ambient_js, required

for required in (
    ".ambient-grid",
    ".ambient-toggle",
    ".ambient-status-list",
    ".ambient-privacy-contract",
):
    assert required in ambient_css, required

print("VP3 OS v0.50 Ambient Agent privacy/UI contract passed")
