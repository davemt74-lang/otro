from pathlib import Path

root = Path(__file__).resolve().parents[1]
tools = (root / "app/services/vp3_scheduling_tools.py").read_text(encoding="utf-8")
approvals = (root / "app/services/vp3_scheduling_approvals.py").read_text(encoding="utf-8")
remote = (root / "app/services/vp3_scheduling_remote.py").read_text(encoding="utf-8")
connector = (root / "app/services/vp3_scheduling_connector.py").read_text(encoding="utf-8")
policy = (root / "app/services/action_policy.py").read_text(encoding="utf-8")

assert "vp3.connector.configure" not in tools
assert 'OPERATION = "vp3.connector.configure"' in remote
assert 'identity.get("app_key")' in remote and '!= "vp3"' in remote
assert "hashlib.sha256(token.encode" in remote
assert "pairing_token_hash=pairing_token_hash" in remote
assert '"scheduling.read"' in tools and '"scheduling.write"' in tools
for key in ("vp3.booking.create", "vp3.booking.reschedule", "vp3.booking.cancel"):
    assert key in policy
assert "APPROVAL_ONLY_WRITE_TOOLS" in policy
assert 'payload.pop("idempotency_key", None)' in approvals
assert 'payload["idempotency_key"] = "hs-action-" + uuid.uuid4().hex' in approvals
assert "http://example.com" not in connector
assert 'parsed.scheme != "https"' in connector
assert "parsed.query" in connector
assert "pairing_token_hash" in connector
assert "_pairing_binding_active" in connector
assert "app_key='vp3'" in connector
assert "status='active'" in connector
assert "follow_redirects=False" in connector
assert "Authorization" in connector
assert "access_token" not in connector
assert "refresh_token" not in connector
print("VP3 scheduling v0.59 static safety checks passed")
