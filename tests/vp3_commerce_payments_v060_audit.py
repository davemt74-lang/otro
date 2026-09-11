from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
remote = (ROOT / "app/services/vp3_commerce_remote.py").read_text(encoding="utf-8")

assert "commerce.payment.checkout_created" in remote
assert "commerce.payment.webhook_verified" in remote
assert "commerce.payment.refund_executed" in remote
assert "resource_type, resource_key, metadata_json" in remote
assert "'commerce_order'" in remote
assert '"payer_email"' not in remote.split("def _audit", 1)[1].split("def install", 1)[0]
assert '"success_url"' not in remote.split("def _audit", 1)[1].split("def install", 1)[0]
assert '"cancel_url"' not in remote.split("def _audit", 1)[1].split("def install", 1)[0]
assert '"idempotency_key"' not in remote.split("def _audit", 1)[1].split("def install", 1)[0]
assert "secret_key" not in remote.split("def _audit", 1)[1].split("def install", 1)[0]
assert "webhook_secret" not in remote.split("def _audit", 1)[1].split("def install", 1)[0]

print("VP3 local commerce payment audit privacy contract passed")
