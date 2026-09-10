from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one anchor, got {count}: {old[:100]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


path = "ui/chat-enhancements.js"
replace_once(path, "  let conversationMode = false;\n  let awaitingAgent = false;", "  let conversationMode = false;\n  let conversationStarting = false;\n  let awaitingAgent = false;")
replace_once(
    path,
    "    button.classList.remove('listening', 'transcribing', 'thinking', 'speaking');\n    button.setAttribute('aria-pressed', conversationMode ? 'true' : 'false');\n    const label = button.querySelector('.chat-control-label');\n    if (!conversationMode) {\n      button.setAttribute('aria-label', 'Start conversation mode');\n      if (label) label.textContent = 'Talk';\n      return;\n    }",
    "    button.classList.remove('listening', 'transcribing', 'thinking', 'speaking');\n    const engaged = conversationMode || conversationStarting;\n    button.setAttribute('aria-pressed', engaged ? 'true' : 'false');\n    const label = button.querySelector('.chat-control-label');\n    if (conversationStarting) {\n      button.setAttribute('aria-label', 'Cancel conversation mode setup');\n      if (label) label.textContent = 'Checking';\n      return;\n    }\n    if (!conversationMode) {\n      button.setAttribute('aria-label', 'Start conversation mode');\n      if (label) label.textContent = 'Talk';\n      return;\n    }",
)
replace_once(path, "  function stopConversationMode(message = 'Conversation mode stopped.') {\n    conversationMode = false;", "  function stopConversationMode(message = 'Conversation mode stopped.') {\n    conversationStarting = false;\n    conversationMode = false;")
replace_once(path, "    utterance.rate = 1;\n    utterance.pitch = 1;", "    utterance.rate = Number(window.HomeServerVoiceSettings?.getPreferences?.().speaking_rate || 1);\n    utterance.pitch = 1;")
replace_once(
    path,
    "  async function toggleConversationMode() {\n    if (conversationMode) {\n      stopConversationMode();\n      return;\n    }\n\n    // Unlock local audio synchronously inside the user's click gesture. This\n    // prevents delayed Piper playback from being rejected by autoplay policy.\n    unlockLocalAudio();\n    const status = await refreshLocalVoiceStatus();\n    const strict = strictLocalEnabled();",
    "  async function toggleConversationMode() {\n    if (conversationMode || conversationStarting) {\n      stopConversationMode(conversationStarting ? 'Conversation mode setup cancelled.' : 'Conversation mode stopped.');\n      return;\n    }\n\n    // Mark setup as engaged before awaiting status so Dictate, Voice Settings,\n    // or a second Talk click can reliably cancel this startup transaction.\n    conversationStarting = true;\n    setVoiceState('checking');\n\n    // Unlock local audio synchronously inside the user's click gesture. This\n    // prevents delayed Piper playback from being rejected by autoplay policy.\n    unlockLocalAudio();\n    const status = await refreshLocalVoiceStatus();\n    if (!conversationStarting) return;\n    const strict = strictLocalEnabled();",
)
replace_once(
    path,
    "    if (strict && (!localStt || !localTts)) {\n      closePlaybackContext();\n      flash('Strict Local Voice requires healthy Whisper STT and Piper TTS Local Apps plus browser microphone capture and audio playback support.', true);\n      return;\n    }\n    if (!localStt && !browserRecognition) {\n      closePlaybackContext();\n      flash('No speech-to-text path is available. Install Whisper STT or use a browser with speech recognition.', true);\n      return;\n    }\n    if (!localTts && !browserTts) {\n      closePlaybackContext();\n      flash('No speech-output path is available. Install Piper TTS or use a browser with speech synthesis.', true);\n      return;\n    }",
    "    if (strict && (!localStt || !localTts)) {\n      conversationStarting = false;\n      closePlaybackContext();\n      setVoiceState('idle');\n      flash('Strict Local Voice requires healthy Whisper STT and Piper TTS Local Apps plus browser microphone capture and audio playback support.', true);\n      return;\n    }\n    if (!localStt && !browserRecognition) {\n      conversationStarting = false;\n      closePlaybackContext();\n      setVoiceState('idle');\n      flash('No speech-to-text path is available. Install Whisper STT or use a browser with speech recognition.', true);\n      return;\n    }\n    if (!localTts && !browserTts) {\n      conversationStarting = false;\n      closePlaybackContext();\n      setVoiceState('idle');\n      flash('No speech-output path is available. Install Piper TTS or use a browser with speech synthesis.', true);\n      return;\n    }",
)
replace_once(path, "    if (voicePath.tts !== 'local') closePlaybackContext();\n    conversationMode = true;", "    if (voicePath.tts !== 'local') closePlaybackContext();\n    conversationStarting = false;\n    conversationMode = true;")
replace_once(path, "    if (conversationMode) stopConversationMode('Conversation mode stopped because Voice Settings changed.');", "    if (conversationMode || conversationStarting) stopConversationMode('Conversation mode stopped because Voice Settings changed.');")
replace_once(path, "    conversationMode = false;\n    captureGeneration += 1;", "    conversationStarting = false;\n    conversationMode = false;\n    captureGeneration += 1;")

Path(".github/voice_settings_final_patch.py").unlink(missing_ok=True)
Path(".github/workflows/voice-settings-final-patch.yml").unlink(missing_ok=True)
