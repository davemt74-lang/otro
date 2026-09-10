from __future__ import annotations

from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
index = (ROOT_DIR / "ui" / "index.html").read_text(encoding="utf-8")
shell = (ROOT_DIR / "ui" / "shell.js").read_text(encoding="utf-8")
styles = (ROOT_DIR / "ui" / "shell.css").read_text(encoding="utf-8")
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

# Desktop shell layout must remain viewport-sticky while only the chat-history
# region is independently scrollable. The chat composer must not be trapped by
# hidden/auto overflow ancestors or the legacy 520px message cap.
assert '.shell-agent-first .sidebar {\n  position: sticky;' in styles
assert 'height: 100dvh;' in styles
assert 'max-height: 100dvh;' in styles
assert '.shell-agent-first .sidebar-chat-history {' in styles
assert 'overscroll-behavior: contain;' in styles
assert '.shell-agent-first #view-chat .chat-panel {' in styles
assert '.shell-agent-first #view-chat .chat-messages {' in styles
assert 'max-height: none;' in styles
assert 'overflow: visible;' in styles
assert '.shell-agent-first #view-chat .chat-compose {' in styles
assert 'position: sticky;' in styles
assert 'bottom: 14px;' in styles

# The shell's outside-click closer must not immediately close the conversation
# options toggle on the same click that brain.js uses to open it.
assert "!event.target.closest('.conversation-more')" in shell
assert 'data-conversation-more' in brain
assert 'data-conversation-menu' in brain

# Chat management remains on the canonical conversation API.
assert 'data-conversation-rename' in brain
assert 'data-conversation-delete' in brain
assert "method: 'PATCH'" in brain
assert "method: 'DELETE'" in brain

print("HomeServer agent-first shell integration contract passed")
