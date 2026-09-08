from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from relay.app import ALLOWED_OPERATIONS, PUBLIC_OPERATIONS  # noqa: E402

EXPECTED_V018 = {
    "agent.chat",
    "usage.write",
    "chat",
    "usage.cloud",
    "usage.read",
    "inference.status",
}

assert EXPECTED_V018 <= ALLOWED_OPERATIONS, sorted(EXPECTED_V018 - ALLOWED_OPERATIONS)
assert PUBLIC_OPERATIONS == {"capabilities", "pair.request", "pair.status"}
for forbidden in {"http.proxy", "owner.control", "filesystem.read", "shell.execute"}:
    assert forbidden not in ALLOWED_OPERATIONS

print("HomeServer v0.18 relay operation contract passed")
