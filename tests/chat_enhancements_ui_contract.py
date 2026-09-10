from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
index = (ROOT / 'ui' / 'index.html').read_text(encoding='utf-8')
script = (ROOT / 'ui' / 'chat-enhancements.js').read_text(encoding='utf-8')
styles = (ROOT / 'ui' / 'chat-enhancements.css').read_text(encoding='utf-8')

assert '/assets/chat-enhancements.css' in index, 'chat enhancement stylesheet is not loaded'
assert '/assets/chat-enhancements.js' in index, 'chat enhancement script is not loaded'
assert index.index('/assets/brain.js') < index.index('/assets/chat-enhancements.js'), 'enhancements must load after Agent Brain'

for marker in [
    'voiceInputButton',
    'Start conversation mode',
    'MediaRecorder',
    'getUserMedia',
    'AudioContext',
    'SpeechRecognition',
    'webkitSpeechRecognition',
    'speechSynthesis',
    'SpeechSynthesisUtterance',
    'strictLocalVoice',
    'Strict local voice',
    'aria-pressed',
    'voicePrivacyNote',
    'requestSubmit',
    'speakAgentReply',
    'resumeListening',
    'startLocalListening',
    'startBrowserListening',
    'awaitingAgent',
    'Transcribing',
    'unlockLocalAudio',
    'playbackContext',
    'createBufferSource',
    'decodeAudioData',
]:
    assert marker in script, f'missing conversation-mode contract marker: {marker}'

for endpoint in [
    '/api/v1/control/voice/status',
    '/api/v1/control/voice/transcribe',
    '/api/v1/control/voice/synthesize',
]:
    assert endpoint in script, f'missing local voice endpoint: {endpoint}'

assert "'/api/v1/control/chat'" not in script, 'conversation mode must reuse the canonical chat form instead of bypassing it'
assert 'form.requestSubmit()' in script, 'recognized speech must auto-submit through the canonical chat form'
assert "voicePath.stt === 'local'" in script, 'installed local Whisper must be preferred for STT'
assert "voicePath.tts === 'local'" in script, 'installed local Piper must be preferred for TTS'
assert 'recordingToWav' in script and 'encodePcm16Wav' in script, 'browser microphone audio must be converted locally to PCM WAV'
assert 'credentials: \'same-origin\'' in script, 'local voice requests must stay on the owner-session same-origin route'
assert "if (strictLocalEnabled())" in script, 'strict local mode must block browser service fallback'
assert 'resumeListening()' in script, 'conversation mode must resume listening after spoken replies and empty segments'
assert "new Audio(" not in script, 'delayed Piper playback must use the user-gesture-unlocked AudioContext rather than autoplay-sensitive HTMLAudioElement.play()'
assert 'configured Agent inference route' in script, 'privacy disclosure must distinguish local voice processing from the configured Agent inference route'

for endpoint in [
    '/api/v1/control/inference',
    '/api/v1/control/cognition',
    '/api/v1/control/cognition/events?limit=12',
    '/api/v1/control/tool-runs?limit=12',
    '/api/v1/control/activity?limit=12',
    '/api/v1/control/tasks?q=',
]:
    assert endpoint in script, f'missing inspectable activity endpoint: {endpoint}'

for marker in [
    'chatBrainToggle',
    'chatBrainDrawer',
    'AGENT BRAIN & HISTORY',
    'Active goals & tasks',
    'CURRENT PLAN / DECISION SUMMARY',
    'aria-controls',
    'aria-expanded',
    'aria-hidden',
    'hidden model reasoning',
    'chain-of-thought',
    'Promise.allSettled',
]:
    assert marker in script, f'missing Brain activity contract marker: {marker}'

for marker in [
    'chat-brain-drawer',
    'chat-brain-backdrop',
    'chat-brain-stats',
    '.chat-icon-button.listening',
    '.chat-icon-button.transcribing',
    '.chat-icon-button.thinking',
    '.chat-icon-button.speaking',
    '.chat-voice-options',
    '.chat-local-voice-badge.ready',
    '@media(max-width:700px)',
]:
    assert marker in styles, f'missing responsive conversation/drawer style marker: {marker}'

print('Agent Chat local conversation mode + Brain activity UI contract passed.')
