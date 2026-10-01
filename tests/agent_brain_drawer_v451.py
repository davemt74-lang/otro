"""Section 31A Agent Brain drawer contract; no new repair execution endpoint."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
page=(ROOT/"ui/index.html").read_text(encoding="utf-8")
script=(ROOT/"ui/agent-brain-drawer.js").read_text(encoding="utf-8")
style=(ROOT/"ui/agent-brain-drawer.css").read_text(encoding="utf-8")
assert '<script src="/assets/agent-brain-drawer.js" defer></script>' in page
assert page.index('/assets/brain.js') < page.index('/assets/agent-brain-drawer.js')
for expected in (
    '/api/v1/control/health',
    "document.querySelector('.nav [data-view=\"chat\"]')",
    "openWorkspace('health')",
    "input.value = existing",
    'input.focus()',
    "credentials:'same-origin'",
    "setAttribute('inert', '')",
    "setAttribute('aria-expanded', String(open))",
    'clearInterval(timer)',
    "if (e.key === 'Escape'",
):
    assert expected in script,expected
assert "chatForm.submit(" not in script
assert "requestSubmit(" not in script
assert "fetch('/api/v1/control/health', {credentials:'same-origin'" in script
assert 'position:fixed' in style
assert 'max-width:100vw' in style
assert 'translateX(105%)' in style
for page in ("tasks.html","system.html","remote.html"):
    text=(ROOT/"ui"/page).read_text(encoding="utf-8")
    assert '<script src="/assets/agent-brain-drawer.js" defer></script>' in text,page
    assert text.index('/assets/universal-shell-v220.js') < text.index('/assets/agent-brain-drawer.js'),page
assert '/assets/agent-brain-drawer.js' not in (ROOT/"ui"/"member.html").read_text(encoding="utf-8")
assert 'CHAT_DRAFT_KEY' in script
assert 'sessionStorage.setItem(CHAT_DRAFT_KEY' in script
assert 'window.location.assign(\'/#chat\')' in script
# Defer global drawer initialization until the universal shell mounts.
assert "document.readyState !== 'complete'" in script
print("Section 31: global owner drawer and safe Chat handoff contract PASS")
