from relay.app import ALLOWED_OPERATIONS, PUBLIC_OPERATIONS

EXPECTED_V017 = {
    "inference.status",
    "events.emit",
    "events.list",
    "awareness.list",
    "plugins.list",
    "usage.cloud",
    "usage.read",
}

assert EXPECTED_V017 <= ALLOWED_OPERATIONS, sorted(EXPECTED_V017 - ALLOWED_OPERATIONS)
assert PUBLIC_OPERATIONS == {"capabilities", "pair.request", "pair.status"}
assert "http.proxy" not in ALLOWED_OPERATIONS
assert "owner.control" not in ALLOWED_OPERATIONS
assert "filesystem.read" not in ALLOWED_OPERATIONS
assert "shell.execute" not in ALLOWED_OPERATIONS

print("HomeServer v0.17 relay operation allowlist regression passed")
