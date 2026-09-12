from __future__ import annotations

import base64
import json
from typing import Callable

from ..database import db
from . import pairing, remote_bridge, stripe_commerce_payments, stripe_payment_secrets

CONTRACT = "commerce-payment-v1"
CONTRACT_SHA256 = "1bc15e1965846ef32fcdddb758c4827e3a21b1bf3dfae4bc61cb5ea8591463ea"
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


def _audit(operation: str, body: dict, result: dict) -> None:
    action = {
        "vp3.commerce.checkout.create": "commerce.payment.checkout_created",
        "vp3.commerce.webhook.verify": "commerce.payment.webhook_verified",
        "vp3.commerce.refund": "commerce.payment.refund_executed",
    }.get(operation)
    if not action:
        return
    order_id = str(result.get("order_id") or body.get("order_id") or "").strip()[:190]
    metadata = {
        "provider": str(result.get("provider") or "stripe")[:32],
        "authority": "homeserver",
        "amount_minor": int(result.get("amount_minor") or body.get("amount_minor") or 0),
        "fulfillment_type": str(result.get("fulfillment_type") or body.get("fulfillment_type") or "")[:40],
        "contract": CONTRACT,
        "contract_sha256": CONTRACT_SHA256,
    }
    with db() as connection:
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES ('app', 'vp3', ?, 'commerce_order', ?, ?)
            """,
            (action, order_id, json.dumps(metadata, separators=(",", ":"))),
        )


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
                if body:
                    raise stripe_commerce_payments.StripeCommercePaymentError("Commerce payment status does not accept request fields.")
                credential = stripe_payment_secrets.status()
                account = stripe_commerce_payments.account_status() if credential["configured"] else None
                ready = bool(
                    credential["configured"]
                    and credential["webhook_configured"]
                    and isinstance(account, dict)
                    and account.get("charges_enabled")
                )
                result = {
                    "contract": CONTRACT,
                    "contract_sha256": CONTRACT_SHA256,
                    "authority": "homeserver",
                    "providers": {"stripe": {**credential, "account": account, "ready": ready}},
                    "operations": sorted(OPERATIONS),
                    "platform_fees_supported": False,
                }
            elif op == "vp3.commerce.checkout.create":
                allowed = {
                    "order_id", "order_item_id", "fulfillment_type", "amount_minor", "platform_fee_minor",
                    "currency", "success_url", "cancel_url", "payer_email", "description", "idempotency_key",
                }
                required = {"order_id", "fulfillment_type", "amount_minor", "platform_fee_minor", "currency", "success_url", "cancel_url", "idempotency_key"}
                if set(body) - allowed or required - set(body):
                    raise stripe_commerce_payments.StripeCommercePaymentError("Commerce checkout request does not match commerce-payment-v1.")
                result = stripe_commerce_payments.create_checkout(body)
            elif op == "vp3.commerce.checkout.retrieve":
                if set(body) != {"external_session_id"}:
                    raise stripe_commerce_payments.StripeCommercePaymentError("Commerce checkout lookup does not match commerce-payment-v1.")
                result = stripe_commerce_payments.retrieve_checkout(str(body.get("external_session_id") or ""))
            elif op == "vp3.commerce.refund":
                required = {"external_payment_id", "amount_minor", "order_id", "idempotency_key"}
                if set(body) != required:
                    raise stripe_commerce_payments.StripeCommercePaymentError("Commerce refund request does not match commerce-payment-v1.")
                result = stripe_commerce_payments.refund(
                    str(body.get("external_payment_id") or ""),
                    int(body.get("amount_minor") or 0),
                    str(body.get("order_id") or ""),
                    str(body.get("idempotency_key") or ""),
                )
            else:
                required = {"provider", "payload_b64", "provider_signature"}
                if set(body) != required or str(body.get("provider") or "").strip().lower() != "stripe":
                    raise stripe_commerce_payments.StripeCommercePaymentError("Commerce webhook verification does not match commerce-payment-v1.")
                try:
                    raw = base64.b64decode(str(body.get("payload_b64") or ""), validate=True)
                except Exception as exc:
                    raise stripe_commerce_payments.StripeCommercePaymentError("Webhook payload encoding is invalid.") from exc
                result = stripe_commerce_payments.verify_webhook(raw, str(body.get("provider_signature") or ""))
        except (stripe_payment_secrets.StripePaymentSecretError, stripe_commerce_payments.StripeCommercePaymentError) as exc:
            raise remote_bridge.RemoteBridgeError(str(exc)) from exc
        _audit(op, body, result)
        return {"status": 200, "ok": True, "payload": result}

    remote_bridge.dispatch_remote_request = dispatch_remote_request
    remote_bridge._vp3_commerce_v060_installed = True
