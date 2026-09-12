from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
read = lambda path: (ROOT / path).read_text(encoding="utf-8")

stripe = read("app/services/stripe_commerce_payments.py")
secrets = read("app/services/stripe_payment_secrets.py")
remote = read("app/services/vp3_commerce_remote.py")
api = read("app/payments_api.py")
pairing = read("app/services/pairing.py")
system_api = read("app/system_api.py")
html = read("ui/system.html")
js = read("ui/system.js")
contract_text = read("contracts/commerce/commerce-payment-v1/contract.json")
contract = json.loads(contract_text)
expected_digest = read("contracts/commerce/commerce-payment-v1/SHA256").strip().split()[0]
actual_digest = hashlib.sha256(contract_text.encode()).hexdigest()

assert actual_digest == expected_digest
assert contract["contract"] == "commerce-payment-v1"
assert contract["canonical_owner"] == "vp3-cloud"
assert contract["wire"]["id_type"] == "opaque-string"
assert contract["wire"]["money_type"] == "integer-minor-unit"
assert contract["wire"]["unknown_fields"] == "reject"
assert contract["authority_rules"]["silent_fallback"] is False
assert contract["compatibility"]["breaking_change_requires"] == "commerce-payment-v2"

assert 'CONTRACT = "commerce-payment-v1"' in stripe
assert expected_digest in stripe and expected_digest in remote
assert "vp3_order_id" in stripe and "vp3_order_item_id" in stripe
assert "vp3_fulfillment_type" in stripe
assert "vp3_paid_booking_id" not in stripe
assert "appointment payment" not in stripe.lower()
assert "agent_paid" not in stripe
assert "platform_fee != 0" in stripe
assert "does not support VP3 platform fees yet" in stripe
assert "STRIPE_API" in stripe and "https://api.stripe.com/v1" in stripe
assert "/payment_methods" not in stripe and "/tokens" not in stripe and "/customers" not in stripe
assert "Idempotency-Key" in stripe
assert "hmac.compare_digest" in stripe
assert "abs(int(time.time()) - timestamp) > 300" in stripe
assert 'quote.get("amount_minor")' in stripe
assert 'quote.get("platform_fee_minor")' in stripe
assert '"amount_cents"' not in remote
assert '"platform_fee_cents"' not in remote
assert '"stripe_signature"' not in remote
assert '"provider_signature"' in remote
assert '"provider"' in remote
assert '"contract_sha256"' in remote
assert '"payment_state"' in stripe and '"provider_payment_status"' in stripe
assert '"state"' in stripe and '"provider_status"' in stripe
assert "int(metadata.get(\"vp3_order_id\")" not in stripe

assert 'STORE_NAME = "commerce-payment-stripe.dat"' in secrets
assert "_protect_windows" in secrets and "windows-dpapi" in secrets
assert "secret_key_suffix" in secrets and "webhook_secret_suffix" in secrets
assert "sk_live_" in secrets and "sk_test_" in secrets and "whsec_" in secrets

for operation in (
    "vp3.commerce.payments.status",
    "vp3.commerce.checkout.create",
    "vp3.commerce.checkout.retrieve",
    "vp3.commerce.webhook.verify",
    "vp3.commerce.refund",
):
    assert operation in remote
    assert operation in contract["operations"]
assert "vp3.payments." not in remote
assert 'app_key") or "") != "vp3"' in remote
assert "payments.read" in remote and "payments.write" in remote and "payments.refund" in remote
assert "platform_fees_supported" in remote
assert "stripe_payment_secrets.secret_key()" not in remote

for permission in ("payments.read", "payments.write", "payments.refund"):
    assert f'"{permission}"' in pairing

assert "stripe_commerce_payments.account_status()" not in api.split("def _status()", 1)[1].split("@router.get", 1)[0]
assert "/api/v1/control/payments/stripe/verify" in api
assert "stripe_payment_secrets.status()" in api
assert "install_vp3_commerce_remote" in system_api

assert 'type="password"' in html
assert 'autocomplete="off"' in html
assert "VP3 Cloud can process commerce without HomeServer" in html
assert "never silently switches authority" in html
assert "platform fee" in html.lower()
assert "/api/v1/control/payments" in js
assert "secret_key" in js and "webhook_secret" in js
assert "sk_live_" not in js and "sk_test_" not in js and "whsec_" not in js
assert 'value="sk_live_' not in html and 'value="sk_test_' not in html and 'value="whsec_' not in html
assert 'placeholder="sk_live_… or sk_test_…"' in html and 'placeholder="whsec_…"' in html

for old_path in ("app/services/stripe_appointment_payments.py", "app/services/vp3_payment_remote.py"):
    assert not (ROOT / old_path).exists(), f"Appointment-specific payment module must not remain: {old_path}"

print("VP3 local commerce payments v0.60 static contract passed")
