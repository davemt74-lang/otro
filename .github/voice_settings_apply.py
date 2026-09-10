from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one integration anchor, found {count}: {old[:80]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Load the settings controller before the two voice-mode scripts so its
# preference/device helpers are available when Talk and Dictate initialize.
replace_once(
    "ui/index.html",
    '  <link rel="stylesheet" href="/assets/chat-enhancements.css">\n',
    '  <link rel="stylesheet" href="/assets/chat-enhancements.css">\n  <link rel="stylesheet" href="/assets/voice-settings.css">\n',
)
replace_once(
    "ui/index.html",
    '  <script src="/assets/brain.js" defer></script>\n  <script src="/assets/chat-enhancements.js" defer></script>\n',
    '  <script src="/assets/brain.js" defer></script>\n  <script src="/assets/voice-settings.js" defer></script>\n  <script src="/assets/chat-enhancements.js" defer></script>\n',
)

# Conversation mode: consume saved timing and browser-local microphone/output
# selections while preserving the current defaults when settings are not ready.
replace_once(
    "ui/chat-enhancements.js",
    "  function strictLocalEnabled() {\n    return Boolean(byId('strictLocalVoice')?.checked);\n  }\n\n  async function refreshLocalVoiceStatus() {",
    "  function strictLocalEnabled() {\n    return Boolean(byId('strictLocalVoice')?.checked);\n  }\n\n  function captureTiming() {\n    return window.HomeServerVoiceSettings?.getCaptureTiming?.() || {\n      listenSilenceMs: SILENCE_MS,\n      noSpeechTimeoutMs: NO_SPEECH_MS,\n      maxSegmentMs: MAX_SEGMENT_MS,\n    };\n  }\n\n  function localCaptureConstraints() {\n    const base = {channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true};\n    return window.HomeServerVoiceSettings?.captureConstraints?.(base) || {audio: base};\n  }\n\n  async function refreshLocalVoiceStatus() {",
)
replace_once(
    "ui/chat-enhancements.js",
    "      mediaStream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true}});",
    "      mediaStream = await navigator.mediaDevices.getUserMedia(localCaptureConstraints());",
)
replace_once(
    "ui/chat-enhancements.js",
    "      mediaRecorder = new MediaRecorder(mediaStream, recorderOptions());\n      const startedAt = performance.now();\n      let lastVoiceAt = startedAt;",
    "      mediaRecorder = new MediaRecorder(mediaStream, recorderOptions());\n      const timing = captureTiming();\n      const startedAt = performance.now();\n      let lastVoiceAt = startedAt;",
)
replace_once(
    "ui/chat-enhancements.js",
    "        const silenceDone = captureVoiceStarted && now - lastVoiceAt >= SILENCE_MS;\n        const noSpeechDone = !captureVoiceStarted && now - startedAt >= NO_SPEECH_MS;\n        const maxDone = now - startedAt >= MAX_SEGMENT_MS;",
    "        const silenceDone = captureVoiceStarted && now - lastVoiceAt >= timing.listenSilenceMs;\n        const noSpeechDone = !captureVoiceStarted && now - startedAt >= timing.noSpeechTimeoutMs;\n        const maxDone = now - startedAt >= timing.maxSegmentMs;",
)
replace_once(
    "ui/chat-enhancements.js",
    "    await context.resume();\n    const decoded = await context.decodeAudioData(audioBytes.slice(0));",
    "    await context.resume();\n    await window.HomeServerVoiceSettings?.applyOutputSink?.(context);\n    const decoded = await context.decodeAudioData(audioBytes.slice(0));",
)
replace_once(
    "ui/chat-enhancements.js",
    "  document.addEventListener('keydown', event => {\n    if (event.key === 'Escape' && byId('chatBrainDrawer')?.getAttribute('aria-hidden') === 'false') setDrawer(false);",
    "  window.addEventListener('homeserver:voice-settings-changed', () => {\n    localVoiceStatus = null;\n    refreshLocalVoiceStatus().catch(() => null);\n    if (conversationMode) stopConversationMode('Conversation mode stopped because Voice Settings changed.');\n  });\n\n  document.addEventListener('keydown', event => {\n    if (event.key === 'Escape' && byId('chatBrainDrawer')?.getAttribute('aria-hidden') === 'false') setDrawer(false);",
)

# Dictation mode uses the same input device and timing policy but still never
# auto-submits or speaks the transcript.
replace_once(
    "ui/chat-dictation.js",
    "  function strictLocalEnabled() {\n    return Boolean(byId('strictLocalVoice')?.checked);\n  }\n\n  function localCaptureSupported() {",
    "  function strictLocalEnabled() {\n    return Boolean(byId('strictLocalVoice')?.checked);\n  }\n\n  function captureTiming() {\n    return window.HomeServerVoiceSettings?.getCaptureTiming?.() || {\n      listenSilenceMs: SILENCE_MS,\n      noSpeechTimeoutMs: NO_SPEECH_MS,\n      maxSegmentMs: MAX_SEGMENT_MS,\n    };\n  }\n\n  function localCaptureConstraints() {\n    const base = {channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true};\n    return window.HomeServerVoiceSettings?.captureConstraints?.(base) || {audio: base};\n  }\n\n  function localCaptureSupported() {",
)
replace_once(
    "ui/chat-dictation.js",
    "      stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true}});",
    "      stream = await navigator.mediaDevices.getUserMedia(localCaptureConstraints());",
)
replace_once(
    "ui/chat-dictation.js",
    "      recorder = new MediaRecorder(stream, recorderOptions());\n      const startedAt = performance.now();\n      let lastVoiceAt = startedAt;",
    "      recorder = new MediaRecorder(stream, recorderOptions());\n      const timing = captureTiming();\n      const startedAt = performance.now();\n      let lastVoiceAt = startedAt;",
)
replace_once(
    "ui/chat-dictation.js",
    "        const silenceDone = voiceStarted && now - lastVoiceAt >= SILENCE_MS;\n        const noSpeechDone = !voiceStarted && now - startedAt >= NO_SPEECH_MS;\n        const maxDone = now - startedAt >= MAX_SEGMENT_MS;",
    "        const silenceDone = voiceStarted && now - lastVoiceAt >= timing.listenSilenceMs;\n        const noSpeechDone = !voiceStarted && now - startedAt >= timing.noSpeechTimeoutMs;\n        const maxDone = now - startedAt >= timing.maxSegmentMs;",
)
replace_once(
    "ui/chat-dictation.js",
    "  window.addEventListener('beforeunload', () => stopDictation(''));",
    "  window.addEventListener('homeserver:voice-settings-changed', () => {\n    cachedStatus = null;\n    if (active || starting) stopDictation('Dictation stopped because Voice Settings changed.');\n  });\n\n  window.addEventListener('beforeunload', () => stopDictation(''));",
)

# v0.42 intentionally changes the local voice status contract. Preserve all
# v0.41 behavioral/security assertions while pinning the new runtime version
# and verifying Piper receives the default speed/pause arguments.
replace_once(
    "tests/local_voice_v041.py",
    '            assert status.json()["version"] == "v0.41"',
    '            assert status.json()["version"] == "v0.42"',
)
replace_once(
    "tests/local_voice_v041.py",
    '            assert piper_call["kwargs"]["input"] == private_text\n            assert private_text not in " ".join(piper_call["command"])',
    '            assert piper_call["kwargs"]["input"] == private_text\n            assert "--length_scale" in piper_call["command"]\n            assert "--sentence_silence" in piper_call["command"]\n            assert private_text not in " ".join(piper_call["command"])',
)
replace_once(
    "tests/local_voice_v041.py",
    'print("HomeServer v0.41 local Whisper/Piper voice runtime regression passed")',
    'print("HomeServer v0.42 local Whisper/Piper voice runtime regression passed")',
)

# The apply helper/workflow are intentionally absent from the resulting tree.
Path(".github/voice_settings_apply.py").unlink(missing_ok=True)
Path(".github/workflows/voice-settings-apply.yml").unlink(missing_ok=True)
