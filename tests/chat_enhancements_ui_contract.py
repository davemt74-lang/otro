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
    'SpeechRecognition',
    'webkitSpeechRecognition',
    'aria-pressed',
    'voicePrivacyNote',
    'may not be local',
    'HomeServer receives text only when you send the message',
]:
    assert marker in script, f'missing voice contract marker: {marker}'

assert "requestSubmit" not in script, 'voice dictation must not auto-submit a message'
assert "'/api/v1/control/chat'" not in script, 'enhancement layer must not bypass the canonical chat submission path'

for endpoint in [
    '/api/v1/control/inference',
    '/api/v1/control/cognition',
    '/api/v1/control/cognition/events?limit=12',
    '/api/v1/control/tool-runs?limit=12',
    '/api/v1/control/activity?limit=12',
]:
    assert endpoint in script, f'missing inspectable activity endpoint: {endpoint}'

for marker in [
    'chatBrainToggle',
    'chatBrainDrawer',
    'aria-controls',
    'aria-expanded',
    'aria-hidden',
    'hidden model reasoning',
    'chain-of-thought',
    'Promise.allSettled',
]:
    assert marker in script, f'missing Brain activity contract marker: {marker}'

for marker in ['chat-brain-drawer', 'chat-brain-backdrop', 'chat-brain-stats', '@media(max-width:700px)']:
    assert marker in styles, f'missing responsive drawer style marker: {marker}'

print('Agent Chat voice + Brain activity UI contract passed.')
