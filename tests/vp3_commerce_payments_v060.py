from __future__ import annotations

import base64
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

CONTRACT_SHA256 = "1bc15e1965846ef32fcdddb758c4827e3a21b1bf3dfae4bc61cb5ea8591463ea"

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
        calls.append({"method":"GET","url":url,**kwargs})
        if url.endswith("/account"):
            return FakeResponse({"id":"acct_synthetic","country":"US","default_currency":"usd","charges_enabled":True,"payouts_enabled":True})
        if "/checkout/sessions/" in url:
            session=url.rsplit("/",1)[-1]
            fulfillment="digital" if session.endswith("digital") else "appointment"
            return FakeResponse({"id":session,"payment_intent":"pi_synthetic","status":"complete","payment_status":"paid","amount_total":15000,"currency":"usd","metadata":{"vp3_order_id":"ord_701","vp3_order_item_id":"item_702","vp3_fulfillment_type":fulfillment,"vp3_payment_authority":"homeserver"}})
        raise AssertionError(f"Unexpected provider GET {url}")

    def fake_post(url, **kwargs):
        calls.append({"method":"POST","url":url,**kwargs})
        if url.endswith("/checkout/sessions"):
            encoded=str(kwargs.get("content") or "")
            session="cs_synthetic_digital" if "digital" in encoded else "cs_synthetic_appointment"
            return FakeResponse({"id":session,"url":f"https://checkout.example.test/{session}","status":"open","amount_total":15000,"currency":"usd"})
        if url.endswith("/refunds"):
            return FakeResponse({"id":"re_synthetic","status":"succeeded","amount":5000,"currency":"usd"})
        raise AssertionError(f"Unexpected provider POST {url}")

    original_get=stripe_commerce_payments.httpx.get
    original_post=stripe_commerce_payments.httpx.post
    original_secret_key=stripe_payment_secrets.secret_key
    original_webhook_secret=stripe_payment_secrets.webhook_secret
    original_status=stripe_payment_secrets.status
    stripe_commerce_payments.httpx.get=fake_get
    stripe_commerce_payments.httpx.post=fake_post
    stripe_payment_secrets.secret_key=lambda: "synthetic-provider-key"
    stripe_payment_secrets.webhook_secret=lambda: "synthetic-webhook-key"
    stripe_payment_secrets.status=lambda: {"configured":True,"webhook_configured":True,"secret_key_suffix":"etic","webhook_secret_suffix":"-key"}

    try:
        with TestClient(app) as client:
            assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200
            pair=client.post("/api/v1/pairing/request",json={"app_key":"vp3","app_name":"VP3 Cloud","permissions":["payments.read","payments.write","payments.refund"]})
            assert pair.status_code==200
            pair_json=pair.json()
            assert client.post("/api/v1/pairing/approve",json={"code":pair_json["code"]}).status_code==200
            token=pair_json["claim_token"]

            status=remote_bridge.dispatch_remote_request("vp3.commerce.payments.status",{},token)["payload"]
            assert status["contract"]=="commerce-payment-v1"
            assert status["contract_sha256"]==CONTRACT_SHA256
            assert status["authority"]=="homeserver"
            assert status["platform_fees_supported"] is False

            def checkout(fulfillment_type: str):
                return remote_bridge.dispatch_remote_request("vp3.commerce.checkout.create",{
                    "order_id":"ord_701","order_item_id":"item_702","fulfillment_type":fulfillment_type,
                    "amount_minor":15000,"platform_fee_minor":0,"currency":"usd",
                    "success_url":"https://vp3.example.test/commerce/return","cancel_url":"https://vp3.example.test/commerce/cancel",
                    "payer_email":"buyer@example.test","description":"Synthetic product","idempotency_key":f"order-701-{fulfillment_type}",
                },token)["payload"]

            appointment=checkout("appointment")
            digital=checkout("digital")
            for result,fulfillment in ((appointment,"appointment"),(digital,"digital")):
                assert result["contract_sha256"]==CONTRACT_SHA256
                assert result["order_id"]=="ord_701" and result["order_item_id"]=="item_702"
                assert result["fulfillment_type"]==fulfillment
                assert result["amount_minor"]==15000
                assert result["state"]=="pending" and result["provider_status"]=="open"

            retrieved=remote_bridge.dispatch_remote_request("vp3.commerce.checkout.retrieve",{"external_session_id":"cs_synthetic_digital"},token)["payload"]
            assert retrieved["order_id"]=="ord_701" and retrieved["order_item_id"]=="item_702"
            assert retrieved["state"]=="complete" and retrieved["payment_state"]=="paid"
            assert retrieved["provider_status"]=="complete" and retrieved["provider_payment_status"]=="paid"

            for invalid in (
                {"order_id":"ord_701","fulfillment_type":"appointment","amount_cents":15000,"platform_fee_cents":0,"currency":"usd","success_url":"https://vp3.example.test/a","cancel_url":"https://vp3.example.test/b","idempotency_key":"legacy"},
                {"order_id":"ord_701","fulfillment_type":"appointment","amount_minor":15000,"platform_fee_minor":0,"currency":"usd","success_url":"https://vp3.example.test/a","cancel_url":"https://vp3.example.test/b","idempotency_key":"unknown","arbitrary":"field"},
            ):
                try:
                    remote_bridge.dispatch_remote_request("vp3.commerce.checkout.create",invalid,token)
                    raise AssertionError("Legacy or unknown fields must fail closed")
                except remote_bridge.RemoteBridgeError:
                    pass

            refund=remote_bridge.dispatch_remote_request("vp3.commerce.refund",{"external_payment_id":"pi_synthetic","amount_minor":5000,"order_id":"ord_701","idempotency_key":"refund-order-701-1"},token)["payload"]
            assert refund["order_id"]=="ord_701" and refund["external_refund_id"]=="re_synthetic"
            assert refund["state"]=="refunded" and refund["provider_status"]=="succeeded" and refund["amount_minor"]==5000

            event={"id":"evt_synthetic","type":"checkout.session.completed","data":{"object":{"id":"cs_synthetic_appointment","payment_intent":"pi_synthetic","payment_status":"paid","amount_total":15000,"currency":"usd","metadata":{"vp3_order_id":"ord_701","vp3_order_item_id":"item_702","vp3_fulfillment_type":"appointment","vp3_payment_authority":"homeserver"}}}}
            raw=json.dumps(event,separators=(",",":")).encode()
            timestamp=int(time.time())
            signature=hmac.new(b"synthetic-webhook-key",str(timestamp).encode()+b"."+raw,hashlib.sha256).hexdigest()
            webhook=remote_bridge.dispatch_remote_request("vp3.commerce.webhook.verify",{"provider":"stripe","payload_b64":base64.b64encode(raw).decode(),"provider_signature":f"t={timestamp},v1={signature}"},token)["payload"]
            assert webhook["verified"] is True and webhook["contract_sha256"]==CONTRACT_SHA256
            assert webhook["order_id"]=="ord_701" and webhook["payment_state"]=="paid" and webhook["amount_minor"]==15000

            with db() as connection:
                app_row=connection.execute("SELECT id FROM paired_apps WHERE app_key='vp3'").fetchone()
                connection.execute("UPDATE app_permissions SET allowed=0 WHERE paired_app_id=? AND permission='payments.refund'",(app_row["id"],))
            try:
                remote_bridge.dispatch_remote_request("vp3.commerce.refund",{"external_payment_id":"pi_synthetic","amount_minor":100,"order_id":"ord_701","idempotency_key":"denied-refund"},token)
                raise AssertionError("Refund permission revocation must take effect immediately")
            except remote_bridge.RemoteBridgeError as exc:
                assert "payments.refund" in str(exc)
    finally:
        stripe_commerce_payments.httpx.get=original_get
        stripe_commerce_payments.httpx.post=original_post
        stripe_payment_secrets.secret_key=original_secret_key
        stripe_payment_secrets.webhook_secret=original_webhook_secret
        stripe_payment_secrets.status=original_status

print("VP3 local commerce payments v0.60 regression passed")
