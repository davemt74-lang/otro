from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from relay.app import ALLOWED_OPERATIONS, PUBLIC_OPERATIONS  # noqa: E402

EXPECTED_REMOTE_OPERATIONS = {
    "capabilities",
    "capability.registry",
    "pair.request",
    "pair.status",
    "agent.chat",
    "chat",
    "conversations.list",
    "conversation.get",
    "contacts.search",
    "knowledge.search",
    "memory.read",
    "memory.write",
    "inference.status",
    "events.emit",
    "events.list",
    "awareness.list",
    "plugins.list",
    "usage.write",
    "usage.cloud",
    "usage.read",
    "tools.list",
    "skills.list",
    "tool.execute",
    "action.status",
    "action.list",
    "action.approve",
    "action.deny",
}

assert EXPECTED_REMOTE_OPERATIONS <= ALLOWED_OPERATIONS, sorted(EXPECTED_REMOTE_OPERATIONS - ALLOWED_OPERATIONS)
assert PUBLIC_OPERATIONS == {"capabilities", "pair.request", "pair.status"}
for protected in EXPECTED_REMOTE_OPERATIONS - PUBLIC_OPERATIONS:
    assert protected in ALLOWED_OPERATIONS
for forbidden in {"http.proxy", "owner.control", "filesystem.read", "shell.execute"}:
    assert forbidden not in ALLOWED_OPERATIONS

print("HomeServer relay operation contract passed")
