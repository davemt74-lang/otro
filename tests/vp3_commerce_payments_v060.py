from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-commerce-v060-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db, initialize_database  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import remote_bridge, stripe_commerce_payments, stripe_payment_secrets  # noqa: E402

    initialize_database()
    calls: list[dict] = []

    class FakeResponse:
        def __init__(self, payload: dict, status_code: int = 200):
            self._payload = payload
            self.status_code = status_code
            self.is_success = 200 <= status_code < 300

        def json(self):
            return self._payload

    def fake_get(url, **kwargs):
        calls.append({"method": "GET", "url": url, **kwargs})
        if url.endswith("/account"):
            return FakeResponse({"id":"acct_local_v060","country":"US","default_currency":"usd","charges_enabled":True,"payouts_enabled":True})
        if "/checkout/sessions/" in url:
            session = url.rsplit("/", 1)[-1]
            fulfillment = "digital" if session.endswith("digital") else "appointment"
            return FakeResponse({
                "id": session,
                "payment_intent": "pi_local_v060",
                "status": "complete",
                "payment_status": "paid",
                "amount_total": 15000,
                "currency": "usd",
                "metadata": {
                    "vp3_order_id": "701",
                    "vp3_order_item_id": "702",
                    "vp3_fulfillment_type": fulfillment,
                    "vp3_payment_authority": "homeserver",
                },
            })
        raise AssertionError(f"Unexpected Stripe GET {url}")

    def fake_post(url, **kwargs):
        calls.append({"method": "POST", "url": url, **kwargs})
        if url.endswith("/checkout/sessions"):
            body = str(kwargs.get("content") or "")
            session = "cs_test_digital" if "digital" in body else "cs_test_appointment"
            return FakeResponse({"id":session,"url":f"https://checkout.stripe.test/{session}","status":"open","amount_total":15000,"currency":"usd"})
        if url.endswith("/refunds"):
            return FakeResponse({"id":"re_local_v060","status":"succeeded","amount":5000,"currency":"usd"})
        raise AssertionError(f"Unexpected Stripe POST {url}")

    original_get = stripe_commerce_payments.httpx.get
    original_post = stripe_commerce_payments.httpx.post
    stripe_commerce_payments.httpx.get = fake_get
    stripe_commerce_payments.httpx.post = fake_post
    try:
        with TestClient(app) as client:
            assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200

            empty = client.get("/api/v1/control/payments")
            assert empty.status_code == 200
            assert empty.json()["contract"] == "commerce-payment-v1"
            assert empty.json()["providers"]["stripe"]["configured"] is False
            assert not calls, "Reading local payment settings must not contact Stripe"

            saved = client.put(
                "/api/v1/control/payments/stripe",
                json={"secret_key":"sk_test_local_v060_secret","webhook_secret":"whsec_local_v060_secret"},
            )
            assert saved.status_code == 200
            serialized = json.dumps(saved.json())
            assert "sk_test_local_v060_secret" not in serialized
            assert "whsec_local_v060_secret" not in serialized
            assert saved.json()["providers"]["stripe"]["secret_key_suffix"] == "cret"
            assert saved.json()["providers"]["stripe"]["webhook_secret_suffix"] == "cret"
            assert not calls, "Saving credentials must not make a provider network call"

            verified = client.post("/api/v1/control/payments/stripe/verify")
            assert verified.status_code == 200
            assert verified.json()["account"]["account_id"] == "acct_local_v060"
            assert verified.json()["account"]["charges_enabled"] is True

            pair = client.post(
                "/api/v1/pairing/request",
                json={
                    "app_key":"vp3",
                    "app_name":"VP3 Cloud",
                    "permissions":["payments.read","payments.write","payments.refund"],
                },
            )
            assert pair.status_code == 200
            pair_json = pair.json()
            assert client.post("/api/v1/pairing/approve", json={"code":pair_json["code"]}).status_code == 200
            token = pair_json["claim_token"]

            status = remote_bridge.dispatch_remote_request("vp3.commerce.payments.status", {}, token)
            assert status["payload"]["contract"] == "commerce-payment-v1"
            assert status["payload"]["authority"] == "homeserver"
            assert status["payload"]["platform_fees_supported"] is False
            assert "secret_key" not in json.dumps(status)

            def checkout(fulfillment_type: str):
                return remote_bridge.dispatch_remote_request(
                    "vp3.commerce.checkout.create",
                    {
                        "order_id":701,
                        "order_item_id":702,
                        "fulfillment_type":fulfillment_type,
                        "amount_cents":15000,
                        "platform_fee_cents":0,
                        "currency":"usd",
                        "success_url":"https://vp3.example.test/commerce/return",
                        "cancel_url":"https://vp3.example.test/commerce/cancel",
                        "payer_email":"buyer@example.test",
                        "description":"Synthetic product",
                        "idempotency_key":f"order-701-{fulfillment_type}",
                    },
                    token,
                )["payload"]

            appointment = checkout("appointment")
            digital = checkout("digital")
            assert appointment["order_id"] == digital["order_id"] == 701
            assert appointment["fulfillment_type"] == "appointment"
            assert digital["fulfillment_type"] == "digital"
            checkout_calls = [entry for entry in calls if entry["method"] == "POST" and entry["url"].endswith("/checkout/sessions")]
            assert len(checkout_calls) == 2
            assert "Idempotency-Key" in checkout_calls[0]["headers"]
            assert "vp3_order_id" in checkout_calls[0]["content"]
            assert "vp3_paid_booking_id" not in checkout_calls[0]["content"]

            retrieved = remote_bridge.dispatch_remote_request(
                "vp3.commerce.checkout.retrieve", {"external_session_id":"cs_test_digital"}, token
            )["payload"]
            assert retrieved["order_id"] == 701
            assert retrieved["order_item_id"] == 702
            assert retrieved["fulfillment_type"] == "digital"

            try:
                checkout_payload = {
                    "order_id":701,"order_item_id":702,"fulfillment_type":"appointment","amount_cents":15000,
                    "platform_fee_cents":500,"currency":"usd","success_url":"https://vp3.example.test/return",
                    "cancel_url":"https://vp3.example.test/cancel","idempotency_key":"fee-blocked",
                }
                remote_bridge.dispatch_remote_request("vp3.commerce.checkout.create", checkout_payload, token)
                raise AssertionError("HomeServer commerce must fail closed when a VP3 platform fee is required")
            except remote_bridge.RemoteBridgeError as exc:
                assert "platform fee" in str(exc).lower()

            try:
                remote_bridge.dispatch_remote_request(
                    "vp3.commerce.checkout.create",
                    {"order_id":701,"amount_cents":100,"currency":"usd","success_url":"https://vp3.example.test/a","cancel_url":"https://vp3.example.test/b","idempotency_key":"bad","arbitrary":"field"},
                    token,
                )
                raise AssertionError("Unknown commerce fields must be rejected")
            except remote_bridge.RemoteBridgeError:
                pass

            refund = remote_bridge.dispatch_remote_request(
                "vp3.commerce.refund",
                {"external_payment_id":"pi_local_v060","amount_cents":5000,"order_id":701,"idempotency_key":"refund-order-701-1"},
                token,
            )["payload"]
            assert refund["order_id"] == 701
            assert refund["external_refund_id"] == "re_local_v060"

            event = {
                "id":"evt_local_v060",
                "type":"checkout.session.completed",
                "data":{"object":{"id":"cs_test_appointment","payment_intent":"pi_local_v060","payment_status":"paid","amount_total":15000,"currency":"usd","metadata":{"vp3_order_id":"701","vp3_order_item_id":"702","vp3_fulfillment_type":"appointment","vp3_payment_authority":"homeserver"}}},
            }
            raw = json.dumps(event, separators=(",", ":")).encode()
            timestamp = int(time.time())
            signature = hmac.new(b"whsec_local_v060_secret", str(timestamp).encode()+b"."+raw, hashlib.sha256).hexdigest()
            webhook = remote_bridge.dispatch_remote_request(
                "vp3.commerce.webhook.verify",
                {"payload_b64":__import__("base64").b64encode(raw).decode(),"stripe_signature":f"t={timestamp},v1={signature}"},
                token,
            )["payload"]
            assert webhook["verified"] is True
            assert webhook["order_id"] == 701
            assert webhook["fulfillment_type"] == "appointment"

            try:
                remote_bridge.dispatch_remote_request(
                    "vp3.commerce.webhook.verify",
                    {"payload_b64":__import__("base64").b64encode(raw).decode(),"stripe_signature":f"t={timestamp},v1={'0'*64}"},
                    token,
                )
                raise AssertionError("Invalid Stripe signatures must be rejected")
            except remote_bridge.RemoteBridgeError:
                pass

            with db() as connection:
                app_row = connection.execute("SELECT id FROM paired_apps WHERE app_key='vp3'").fetchone()
                connection.execute("UPDATE app_permissions SET allowed=0 WHERE paired_app_id=? AND permission='payments.refund'", (app_row["id"],))
            try:
                remote_bridge.dispatch_remote_request(
                    "vp3.commerce.refund",
                    {"external_payment_id":"pi_local_v060","amount_cents":100,"order_id":701,"idempotency_key":"denied-refund"},
                    token,
                )
                raise AssertionError("Refund permission revocation must take effect immediately")
            except remote_bridge.RemoteBridgeError as exc:
                assert "payments.refund" in str(exc)

            other = client.post(
                "/api/v1/pairing/request",
                json={"app_key":"other-commerce-app","app_name":"Other Commerce","permissions":["payments.read","payments.write"]},
            ).json()
            assert client.post("/api/v1/pairing/approve", json={"code":other["code"]}).status_code == 200
            try:
                remote_bridge.dispatch_remote_request("vp3.commerce.payments.status", {}, other["claim_token"])
                raise AssertionError("Non-VP3 paired apps must not access the VP3 commerce authority")
            except remote_bridge.RemoteBridgeError:
                pass

            cleared = client.delete("/api/v1/control/payments/stripe")
            assert cleared.status_code == 200
            assert cleared.json()["providers"]["stripe"]["configured"] is False
            assert not stripe_payment_secrets._path().exists()
    finally:
        stripe_commerce_payments.httpx.get = original_get
        stripe_commerce_payments.httpx.post = original_post

print("VP3 local commerce payments v0.60 regression passed")
