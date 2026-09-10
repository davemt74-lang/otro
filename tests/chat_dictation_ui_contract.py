from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
index = (ROOT / 'ui' / 'index.html').read_text(encoding='utf-8')
script = (ROOT / 'ui' / 'chat-dictation.js').read_text(encoding='utf-8')
styles = (ROOT / 'ui' / 'chat-dictation.css').read_text(encoding='utf-8')

# Dictation is a separate layer loaded after the existing conversation runtime.
assert '/assets/chat-dictation.js' in index, 'dictation script is not loaded'
assert index.index('/assets/chat-enhancements.js') < index.index('/assets/chat-dictation.js'), 'dictation must load after conversation enhancements'
assert "href = '/assets/chat-dictation.css'" in script, 'dictation stylesheet must be loaded by the dictation layer'

# Keep unrelated existing UI intact.
assert '<th>Time</th><th>Tool</th><th>Invoker</th><th>Status</th><th>Duration</th>' in index, 'dictation must not alter the Skills & Tools run table'

for marker in [
    'dictateInputButton',
    'Start dictation',
    'Stop dictation',
    'Cancel dictation setup',
    'without sending',
    'MediaRecorder',
    'getUserMedia',
    'AudioContext',
    'SpeechRecognition',
    'webkitSpeechRecognition',
    'strictLocalVoice',
    'selectionStart',
    'selectionEnd',
    'setRangeText',
    "new Event('input', {bubbles: true})",
    'maxLength',
    'recordingToWav',
    'encodePcm16Wav',
    'credentials: \'same-origin\'',
    'No speech detected. Nothing was added.',
    'Review or edit it, then send when ready.',
    'let starting = false',
    'const startGeneration = ++generation',
    'startGeneration !== generation',
    'active || starting',
    'MAX_BOOT_ATTEMPTS = 100',
    'setTimeout(boot, 50)',
]:
    assert marker in script, f'missing dictation contract marker: {marker}'

# Dictation uses STT only. It must never submit the chat or synthesize a reply.
assert '/api/v1/control/voice/status' in script, 'dictation must inspect local STT readiness'
assert '/api/v1/control/voice/transcribe' in script, 'dictation must use the local Whisper endpoint'
assert '/api/v1/control/voice/synthesize' not in script, 'dictation must not call TTS'
assert 'speechSynthesis' not in script, 'dictation must not speak Agent replies'
assert 'requestSubmit' not in script, 'dictation must never auto-submit the chat form'
assert '.submit(' not in script, 'dictation must never submit a form directly'

# Local-first and strict-local behavior.
assert "status?.stt?.available" in script, 'healthy local Whisper must be detected explicitly'
assert "mode = localReady ? 'local' : 'browser'" in script, 'local Whisper must be preferred over browser STT'
assert 'Strict Local Dictation requires healthy Whisper STT' in script, 'Strict Local must fail closed without local Whisper'
assert 'Local Whisper failed; browser dictation fallback is ready' in script, 'browser fallback must be explicit when Strict Local is off'

# Talk and Dictate must remain mutually exclusive even during the async readiness check.
assert "conversationButton?.getAttribute('aria-pressed') === 'true'" in script, 'starting Dictate must stop active Talk mode'
assert "(active || starting) && event.target.closest('#voiceInputButton')" in script, 'starting Talk must cancel active/starting Dictate mode'
assert "(active || starting) && event.target.closest('#strictLocalVoice')" in script, 'privacy-setting changes must stop active/starting Dictate mode'

# One-shot lifecycle: browser recognition is non-continuous and both paths stop after insertion.
assert 'recognition.continuous = false' in script, 'browser dictation must be one-shot'
assert "stopDictation(inserted ? 'Dictation added. Review or edit it, then send when ready.'" in script, 'successful transcription must stop dictation after insertion'
assert 'stopCapture();' in script and 'stopBrowserRecognition();' in script, 'dictation cancellation must clean up local and browser capture'
assert 'stream.getTracks().forEach(track => track.stop())' in script, 'microphone tracks must be released'
assert "window.addEventListener('beforeunload', () => stopDictation(''))" in script, 'navigation must clean up microphone state'

for marker in [
    '.chat-dictate-button',
    '.chat-dictate-button.listening',
    '.chat-dictate-button.transcribing',
    '@media(max-width:700px)',
    '@media(prefers-reduced-motion:reduce)',
]:
    assert marker in styles, f'missing dictation style/accessibility marker: {marker}'

print('Agent Chat one-shot dictation UI contract passed.')
