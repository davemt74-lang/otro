from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
SETTINGS_JS = (ROOT / "ui" / "voice-settings.js").read_text(encoding="utf-8")
SETTINGS_CSS = (ROOT / "ui" / "voice-settings.css").read_text(encoding="utf-8")
TALK_JS = (ROOT / "ui" / "chat-enhancements.js").read_text(encoding="utf-8")
DICTATE_JS = (ROOT / "ui" / "chat-dictation.js").read_text(encoding="utf-8")
API = (ROOT / "app" / "local_voice_api.py").read_text(encoding="utf-8")

# Assets are explicit and the preference controller loads before either voice
# mode so helpers are available before Talk/Dictate initialize.
assert '/assets/voice-settings.css' in INDEX
assert '/assets/voice-settings.js' in INDEX
assert INDEX.index('/assets/voice-settings.js') < INDEX.index('/assets/chat-enhancements.js')
assert INDEX.index('/assets/voice-settings.js') < INDEX.index('/assets/chat-dictation.js')

# The settings UI uses the owner-only local API and persists only non-device
# preferences to HomeServer.
assert "'/api/v1/control/voice/settings'" in SETTINGS_JS
assert "method: 'PUT'" in SETTINGS_JS
assert 'input_device_id' not in SETTINGS_JS
assert 'output_device_id' not in SETTINGS_JS
assert 'ConfigDict(extra="forbid")' in API

# Device identifiers remain browser-local. Input selection is an exact
# getUserMedia constraint; output routing is best-effort setSinkId.
assert "homeserver.voice.inputDeviceId" in SETTINGS_JS
assert "homeserver.voice.outputDeviceId" in SETTINGS_JS
assert "localStorage.setItem" in SETTINGS_JS
assert "audio.deviceId = {exact: state.inputDeviceId}" in SETTINGS_JS
assert "typeof target.setSinkId !== 'function'" in SETTINGS_JS
assert "await target.setSinkId(deviceId)" in SETTINGS_JS
assert "device_selection\": \"browser-local\"" in API

# Opening the dialog must not itself prompt for microphone access. Permission
# is requested only from the explicit Allow / refresh devices action.
assert "enumerateDevices(false).catch(() => null)" in SETTINGS_JS
assert "#refreshVoiceDevices" in SETTINGS_JS
assert "enumerateDevices(true)" in SETTINGS_JS
assert "permissionStream?.getTracks?.().forEach(track => track.stop())" in SETTINGS_JS

# Privacy and fallback limitations are visible in the product copy.
assert "not written to the HomeServer database" in SETTINGS_JS
assert "Browser speech-recognition fallback uses the browser/OS default input" in SETTINGS_JS
assert "browser speech-synthesis fallback uses the OS default output" in SETTINGS_JS
assert "Strict Local by default" in SETTINGS_JS

# All persisted controls are represented and constrained in the UI.
for control in (
    'voiceSttModel', 'voiceTtsVoice', 'voiceDefaultMode', 'voiceStrictLocalDefault',
    'voiceSpeakingRate', 'voiceSentenceSilence', 'voiceListenSilence',
    'voiceNoSpeechTimeout', 'voiceMaxSegment', 'voiceInputDevice', 'voiceOutputDevice',
):
    assert control in SETTINGS_JS, control
assert 'min="0.6" max="1.6" step="0.05"' in SETTINGS_JS
assert 'min="400" max="3000" step="100"' in SETTINGS_JS
assert 'min="2000" max="30000" step="500"' in SETTINGS_JS
assert 'min="5000" max="60000" step="1000"' in SETTINGS_JS

# Opening/saving settings safely ends live capture and emits one explicit
# change event that the voice modes consume.
assert "function stopActiveVoiceModes()" in SETTINGS_JS
assert "talk.click()" in SETTINGS_JS and "dictate.click()" in SETTINGS_JS
assert "homeserver:voice-settings-changed" in SETTINGS_JS
assert "homeserver:voice-settings-changed" in TALK_JS
assert "homeserver:voice-settings-changed" in DICTATE_JS

# Both local capture modes use the same saved timing and input-device helpers.
for source in (TALK_JS, DICTATE_JS):
    assert "HomeServerVoiceSettings?.getCaptureTiming?.()" in source
    assert "HomeServerVoiceSettings?.captureConstraints?.(base)" in source
    assert "timing.listenSilenceMs" in source
    assert "timing.noSpeechTimeoutMs" in source
    assert "timing.maxSegmentMs" in source

# Conversation startup is a cancellable engaged state. This closes the race
# where Voice Settings or Dictate could open while Talk was awaiting status.
assert "let conversationStarting = false;" in TALK_JS
assert "const engaged = conversationMode || conversationStarting;" in TALK_JS
assert "if (conversationMode || conversationStarting)" in TALK_JS
assert "if (!conversationStarting) return;" in TALK_JS
assert "conversationStarting = false;\n    conversationMode = true;" in TALK_JS

# Local playback uses the selected output where supported and browser TTS
# fallback honors the same speaking-rate preference as Piper.
assert "HomeServerVoiceSettings?.applyOutputSink?.(context)" in TALK_JS
assert "utterance.rate = Number(window.HomeServerVoiceSettings?.getPreferences?.().speaking_rate || 1);" in TALK_JS

# Dictation remains compose-only: it must not submit a chat message or speak.
assert "requestSubmit" not in DICTATE_JS
assert "speechSynthesis" not in DICTATE_JS
assert "SpeechSynthesisUtterance" not in DICTATE_JS
assert "/api/v1/control/voice/synthesize" not in DICTATE_JS

# Dialog is keyboard-dismissible/responsive and does not force animation.
assert 'role="dialog"' in SETTINGS_JS
assert 'aria-modal="true"' in SETTINGS_JS
assert "event.key === 'Escape'" in SETTINGS_JS
assert '@media (max-width: 700px)' in SETTINGS_CSS
assert '@media (prefers-reduced-motion: reduce)' in SETTINGS_CSS

print("HomeServer v0.42 Voice Settings UI contract passed")
