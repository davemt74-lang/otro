from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "ui" / "chat-experience.js").read_text(encoding="utf-8")
CSS = (ROOT / "ui" / "chat-experience.css").read_text(encoding="utf-8")


def require(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise AssertionError(f"missing {label}: {needle}")


# Assets must be loaded by the owner UI.
require(INDEX, '/assets/chat-experience.css', 'chat experience stylesheet')
require(INDEX, '/assets/chat-experience.js', 'chat experience script')

# Voice is explicit, browser-capability gated and transcript-only until the existing
# chat form is submitted by the owner.
require(JS, 'window.SpeechRecognition || window.webkitSpeechRecognition', 'speech capability detection')
require(JS, "button.addEventListener('click'", 'explicit voice activation')
require(JS, "recognition.start()", 'speech recognition start')
require(JS, "input.value = mergeTranscript(voiceDraft, transcript)", 'transcript insertion')
if "requestSubmit()" in JS or "chatForm')?.submit" in JS or 'chatForm").submit' in JS:
    raise AssertionError('voice module must never auto-submit recognized speech')
require(JS, 'Nothing is sent to HomeServer until you press Send.', 'voice privacy boundary copy')
require(JS, "event.key !== 'Escape'", 'Escape handling')

# Brain / Activity consumes existing safe owner projections only.
for endpoint in (
    '/api/v1/control/tasks?q=',
    '/api/v1/control/activity?limit=8',
    '/api/v1/control/tool-runs?limit=8',
    '/api/v1/control/cognition',
):
    require(JS, endpoint, f'safe data source {endpoint}')

for section in (
    'Goals & active work',
    'Plan / progress',
    'Decision summaries',
    'Recent actions',
    'Tool activity',
):
    require(JS, section, f'drawer section {section}')

require(JS, 'never exposes hidden chain-of-thought or private model reasoning', 'reasoning privacy boundary')
for forbidden in ('/api/v1/control/prompts', '/api/v1/control/reasoning', '/api/v1/control/chain-of-thought'):
    if forbidden in JS:
        raise AssertionError(f'forbidden private reasoning endpoint referenced: {forbidden}')

# Accessibility / responsive interaction contract.
for token in ('aria-controls', 'aria-expanded', 'aria-pressed', 'aria-live', 'aria-modal'):
    require(JS, token, f'accessibility token {token}')
require(CSS, '@media(max-width:560px)', 'mobile layout')
require(CSS, '@media(prefers-reduced-motion:reduce)', 'reduced motion support')
require(CSS, '.chat-voice-button.listening', 'listening state')
require(CSS, '.brain-activity-drawer.open', 'drawer open state')

print('chat experience UI contract: ok')
