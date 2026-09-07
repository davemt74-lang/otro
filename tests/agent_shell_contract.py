from __future__ import annotations

from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
index = (ROOT_DIR / "ui" / "index.html").read_text(encoding="utf-8")
shell = (ROOT_DIR / "ui" / "shell.js").read_text(encoding="utf-8")
brain = (ROOT_DIR / "ui" / "brain.js").read_text(encoding="utf-8")

# The agent-first shell must be part of the actual owner entry point, not only
# a syntactically valid asset that never gets loaded by the packaged UI.
assert '<script src="/assets/shell.js" data-homeserver-shell="1" defer></script>' in index
assert index.count('/assets/shell.js') == 1
assert 'script[data-homeserver-shell]' in brain

# Canonical chat and conversation nodes are moved/reused by shell.js rather
# than duplicated into a second conversation implementation.
assert 'id="view-chat"' in index
assert 'id="conversationList"' in index
assert "const conversationList = byId('conversationList')" in shell
assert "history.appendChild(conversationList)" in shell

# Required v0.14 navigation and status surfaces are built by the active shell.
for label in ('New Chat', 'Approvals', 'Knowledge', 'Memory', 'Contacts'):
    assert label in shell
assert 'AGENT BRAIN' in shell
assert 'Token Usage History' in shell
assert 'homeServerConnectionButton' in shell
assert 'loadConnectionModal' in shell
assert 'openDefaultChat' in shell
assert "history.replaceState(null, '', '#chat')" in shell

# Chat management remains on the canonical conversation API.
assert 'data-conversation-rename' in brain
assert 'data-conversation-delete' in brain
assert "method: 'PATCH'" in brain
assert "method: 'DELETE'" in brain

print("HomeServer agent-first shell integration contract passed")
