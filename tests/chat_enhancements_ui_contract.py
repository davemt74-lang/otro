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
    'SpeechRecognition',
    'webkitSpeechRecognition',
    'speechSynthesis',
    'SpeechSynthesisUtterance',
    'aria-pressed',
    'voicePrivacyNote',
    'auto-sends final speech',
    'requestSubmit',
    'speakAgentReply',
    'startListening',
    'awaitingAgent',
]:
    assert marker in script, f'missing conversation-mode contract marker: {marker}'

assert "'/api/v1/control/chat'" not in script, 'conversation mode must reuse the canonical chat form instead of bypassing it'
assert 'form.requestSubmit()' in script, 'final recognized speech must auto-submit through the canonical chat form'
assert "setTimeout(startListening, 250)" in script, 'conversation mode must resume listening after the spoken reply'

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
    '.chat-icon-button.thinking',
    '.chat-icon-button.speaking',
    '@media(max-width:700px)',
]:
    assert marker in styles, f'missing responsive conversation/drawer style marker: {marker}'

print('Agent Chat conversation mode + Brain activity UI contract passed.')
