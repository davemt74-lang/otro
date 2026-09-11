from __future__ import annotations

import base64
from typing import Callable

from . import pairing, remote_bridge, stripe_commerce_payments, stripe_payment_secrets

VERSION = "v0.60"
CONTRACT = "commerce-payment-v1"
OPERATIONS = {
    "vp3.commerce.payments.status",
    "vp3.commerce.checkout.create",
    "vp3.commerce.checkout.retrieve",
    "vp3.commerce.webhook.verify",
    "vp3.commerce.refund",
}


def _identity(operation: str, bearer_token: str | None) -> dict:
    token = str(bearer_token or "").strip()
    identity = pairing.authenticate(token) if token else None
    if identity is None or str(identity.get("app_key") or "") != "vp3":
        raise remote_bridge.RemoteBridgeError("A paired VP3 identity is required for local commerce payments.")
    permissions = set(identity.get("permissions") or [])
    if operation == "vp3.commerce.refund":
        needed = "payments.refund"
    elif operation in {"vp3.commerce.payments.status", "vp3.commerce.checkout.retrieve"}:
        needed = "payments.read"
    else:
        needed = "payments.write"
    if needed not in permissions:
        raise remote_bridge.RemoteBridgeError(f"Permission required: {needed}")
    return identity


def install() -> None:
    if getattr(remote_bridge, "_vp3_commerce_v060_installed", False):
        return
    original: Callable[[str, dict | None, str | None], dict] = remote_bridge.dispatch_remote_request

    def dispatch_remote_request(operation: str, payload: dict | None, bearer_token: str | None = None) -> dict:
        op = str(operation or "").strip()
        if op not in OPERATIONS:
            return original(operation, payload, bearer_token)
        _identity(op, bearer_token)
        body = payload if isinstance(payload, dict) else {}
        if remote_bridge._payload_size(body) > 1_250_000:
            raise remote_bridge.RemoteBridgeError("VP3 commerce payment payload is too large.")
        try:
            if op == "vp3.commerce.payments.status":
                credential = stripe_payment_secrets.status()
                result = {
                    "version": VERSION,
                    "contract": CONTRACT,
                    "authority": "homeserver",
                    "providers": {"stripe": credential},
                    "operations": sorted(OPERATIONS),
                    "platform_fees_supported": False,
                }
            elif op == "vp3.commerce.checkout.create":
                allowed = {
                    "order_id", "order_item_id", "fulfillment_type", "amount_cents", "platform_fee_cents",
                    "currency", "success_url", "cancel_url", "payer_email", "description", "idempotency_key",
                }
                if set(body) - allowed:
                    raise stripe_commerce_payments.StripeCommercePaymentError("Commerce checkout quote contains unsupported fields.")
                result = stripe_commerce_payments.create_checkout(body)
            elif op == "vp3.commerce.checkout.retrieve":
                if set(body) - {"external_session_id"}:
                    raise stripe_commerce_payments.StripeCommercePaymentError("Commerce checkout lookup contains unsupported fields.")
                result = stripe_commerce_payments.retrieve_checkout(str(body.get("external_session_id") or ""))
            elif op == "vp3.commerce.refund":
                allowed = {"external_payment_id", "amount_cents", "order_id", "idempotency_key"}
                if set(body) - allowed:
                    raise stripe_commerce_payments.StripeCommercePaymentError("Commerce refund request contains unsupported fields.")
                result = stripe_commerce_payments.refund(
                    str(body.get("external_payment_id") or ""),
                    int(body.get("amount_cents") or 0),
                    int(body.get("order_id") or 0),
                    str(body.get("idempotency_key") or ""),
                )
            else:
                allowed = {"payload_b64", "stripe_signature"}
                if set(body) - allowed:
                    raise stripe_commerce_payments.StripeCommercePaymentError("Commerce webhook verification contains unsupported fields.")
                try:
                    raw = base64.b64decode(str(body.get("payload_b64") or ""), validate=True)
                except Exception as exc:
                    raise stripe_commerce_payments.StripeCommercePaymentError("Webhook payload encoding is invalid.") from exc
                result = stripe_commerce_payments.verify_webhook(raw, str(body.get("stripe_signature") or ""))
        except (stripe_payment_secrets.StripePaymentSecretError, stripe_commerce_payments.StripeCommercePaymentError) as exc:
            raise remote_bridge.RemoteBridgeError(str(exc)) from exc
        return {"status": 200, "ok": True, "payload": result}

    remote_bridge.dispatch_remote_request = dispatch_remote_request
    remote_bridge._vp3_commerce_v060_installed = True
