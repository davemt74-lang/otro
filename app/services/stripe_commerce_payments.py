from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from . import stripe_payment_secrets

VERSION = "v0.60"
CONTRACT = "commerce-payment-v1"
STRIPE_API = "https://api.stripe.com/v1"
MAX_AMOUNT_CENTS = 100_000_000
ALLOWED_FULFILLMENT_TYPES = {
    "appointment", "physical", "local_pickup", "digital", "virtual", "event", "membership", "gift", "other"
}


class StripeCommercePaymentError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _post(path: str, fields: list[tuple[str, str]], *, idempotency_key: str = "") -> dict[str, Any]:
    if not path.startswith("/") or ".." in path or "?" in path:
        raise StripeCommercePaymentError("Stripe operation path is not allowlisted.")
    headers = {
        "Authorization": f"Bearer {stripe_payment_secrets.secret_key()}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    try:
        response = httpx.post(
            STRIPE_API + path,
            content=urlencode(fields),
            headers=headers,
            timeout=30.0,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise StripeCommercePaymentError("Stripe is unreachable.", 503) from exc
    try:
        body = response.json()
    except ValueError as exc:
        raise StripeCommercePaymentError("Stripe returned an invalid response.", 502) from exc
    if not response.is_success:
        message = str(((body.get("error") or {}).get("message") if isinstance(body, dict) else "") or "Stripe request failed.")[:300]
        raise StripeCommercePaymentError(message, 409 if response.status_code < 500 else 502)
    if not isinstance(body, dict):
        raise StripeCommercePaymentError("Stripe returned an invalid response.", 502)
    return body


def _get_checkout(session_id: str) -> dict[str, Any]:
    session = str(session_id or "").strip()
    if not session.startswith("cs_") or len(session) > 255:
        raise StripeCommercePaymentError("Stripe checkout session ID is invalid.")
    try:
        response = httpx.get(
            f"{STRIPE_API}/checkout/sessions/{session}",
            headers={"Authorization": f"Bearer {stripe_payment_secrets.secret_key()}"},
            timeout=30.0,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise StripeCommercePaymentError("Stripe is unreachable.", 503) from exc
    try:
        body = response.json()
    except ValueError as exc:
        raise StripeCommercePaymentError("Stripe returned an invalid response.", 502) from exc
    if not response.is_success or not isinstance(body, dict):
        raise StripeCommercePaymentError("Stripe checkout could not be retrieved.", 409 if response.status_code < 500 else 502)
    return body


def account_status() -> dict[str, Any]:
    secret = stripe_payment_secrets.secret_key()
    try:
        response = httpx.get(
            STRIPE_API + "/account",
            headers={"Authorization": f"Bearer {secret}"},
            timeout=20.0,
            follow_redirects=False,
        )
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise StripeCommercePaymentError("Stripe account verification failed.", 503) from exc
    if not response.is_success or not isinstance(body, dict):
        raise StripeCommercePaymentError("Stripe account credentials were rejected.", 409)
    return {
        "provider": "stripe",
        "account_id": str(body.get("id") or ""),
        "country": str(body.get("country") or ""),
        "default_currency": str(body.get("default_currency") or "").lower(),
        "charges_enabled": bool(body.get("charges_enabled")),
        "payouts_enabled": bool(body.get("payouts_enabled")),
        "livemode": secret.startswith("sk_live_"),
    }


def _commerce_metadata(source: dict[str, Any]) -> dict[str, str]:
    order_id = int(source.get("order_id") or 0)
    order_item_id = max(0, int(source.get("order_item_id") or 0))
    fulfillment_type = str(source.get("fulfillment_type") or "other").strip().lower()
    if order_id < 1:
        raise StripeCommercePaymentError("Commerce order ID is required.")
    if fulfillment_type not in ALLOWED_FULFILLMENT_TYPES:
        raise StripeCommercePaymentError("Commerce fulfillment type is not supported by this contract.")
    return {
        "vp3_order_id": str(order_id),
        "vp3_order_item_id": str(order_item_id),
        "vp3_fulfillment_type": fulfillment_type,
        "vp3_payment_authority": "homeserver",
    }


def create_checkout(quote: dict[str, Any]) -> dict[str, Any]:
    metadata = _commerce_metadata(quote)
    amount = int(quote.get("amount_cents") or 0)
    currency = str(quote.get("currency") or "").strip().lower()
    platform_fee = int(quote.get("platform_fee_cents") or 0)
    success_url = str(quote.get("success_url") or "").strip()
    cancel_url = str(quote.get("cancel_url") or "").strip()
    payer_email = str(quote.get("payer_email") or "").strip()
    description = str(quote.get("description") or "VP3 purchase").strip()[:190] or "VP3 purchase"
    idem = str(quote.get("idempotency_key") or "").strip()
    if amount < 1 or amount > MAX_AMOUNT_CENTS:
        raise StripeCommercePaymentError("Commerce quote amount is invalid.")
    if platform_fee != 0:
        raise StripeCommercePaymentError("HomeServer Stripe does not support VP3 platform fees yet; use a cloud payment authority for this offer.", 409)
    if len(currency) != 3 or not currency.isalpha():
        raise StripeCommercePaymentError("Commerce quote currency is invalid.")
    if not (success_url.startswith("https://") and cancel_url.startswith("https://")):
        raise StripeCommercePaymentError("Commerce return URLs must use HTTPS.")
    if not idem or len(idem) > 160:
        raise StripeCommercePaymentError("Commerce checkout requires a stable idempotency key.")
    fields: list[tuple[str, str]] = [
        ("mode", "payment"),
        ("success_url", success_url),
        ("cancel_url", cancel_url),
        ("line_items[0][quantity]", "1"),
        ("line_items[0][price_data][currency]", currency),
        ("line_items[0][price_data][unit_amount]", str(amount)),
        ("line_items[0][price_data][product_data][name]", description),
    ]
    for key, value in metadata.items():
        fields.append((f"metadata[{key}]", value))
        fields.append((f"payment_intent_data[metadata][{key}]", value))
    if payer_email:
        fields.append(("customer_email", payer_email[:254]))
    body = _post("/checkout/sessions", fields, idempotency_key=idem)
    session_id = str(body.get("id") or "")
    checkout_url = str(body.get("url") or "")
    if not session_id.startswith("cs_") or not checkout_url.startswith("https://"):
        raise StripeCommercePaymentError("Stripe did not return a usable checkout.", 502)
    return {
        "contract": CONTRACT,
        "provider": "stripe",
        "authority": "homeserver",
        "external_session_id": session_id,
        "checkout_url": checkout_url,
        "status": str(body.get("status") or "open"),
        "amount_cents": int(body.get("amount_total") or amount),
        "currency": str(body.get("currency") or currency).lower(),
        "order_id": int(metadata["vp3_order_id"]),
        "order_item_id": int(metadata["vp3_order_item_id"]),
        "fulfillment_type": metadata["vp3_fulfillment_type"],
    }


def retrieve_checkout(session_id: str) -> dict[str, Any]:
    body = _get_checkout(session_id)
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    return {
        "contract": CONTRACT,
        "provider": "stripe",
        "authority": "homeserver",
        "external_session_id": str(body.get("id") or session_id),
        "external_payment_id": str(body.get("payment_intent") or ""),
        "status": str(body.get("status") or ""),
        "payment_status": str(body.get("payment_status") or ""),
        "amount_cents": int(body.get("amount_total") or 0),
        "currency": str(body.get("currency") or "").lower(),
        "order_id": int(metadata.get("vp3_order_id") or 0),
        "order_item_id": int(metadata.get("vp3_order_item_id") or 0),
        "fulfillment_type": str(metadata.get("vp3_fulfillment_type") or "other"),
    }


def refund(payment_intent_id: str, amount_cents: int, order_id: int, idempotency_key: str) -> dict[str, Any]:
    payment = str(payment_intent_id or "").strip()
    amount = int(amount_cents or 0)
    order = int(order_id or 0)
    idem = str(idempotency_key or "").strip()
    if not payment.startswith("pi_") or len(payment) > 255 or amount < 1 or order < 1:
        raise StripeCommercePaymentError("Stripe refund request is invalid.")
    if not idem or len(idem) > 160:
        raise StripeCommercePaymentError("Stripe refund requires a stable idempotency key.")
    body = _post(
        "/refunds",
        [
            ("payment_intent", payment),
            ("amount", str(amount)),
            ("metadata[vp3_order_id]", str(order)),
            ("metadata[vp3_payment_authority]", "homeserver"),
        ],
        idempotency_key=idem,
    )
    refund_id = str(body.get("id") or "")
    if not refund_id.startswith("re_"):
        raise StripeCommercePaymentError("Stripe did not return a refund ID.", 502)
    return {
        "contract": CONTRACT,
        "provider": "stripe",
        "authority": "homeserver",
        "external_refund_id": refund_id,
        "status": str(body.get("status") or "pending"),
        "amount_cents": int(body.get("amount") or amount),
        "currency": str(body.get("currency") or "").lower(),
        "external_payment_id": payment,
        "order_id": order,
    }


def verify_webhook(payload: bytes, signature_header: str) -> dict[str, Any]:
    if len(payload) > 1_000_000:
        raise StripeCommercePaymentError("Stripe webhook payload is too large.")
    signature = str(signature_header or "").strip()
    timestamp = 0
    signatures: list[str] = []
    for part in signature.split(","):
        key, sep, value = part.strip().partition("=")
        if not sep:
            continue
        if key == "t" and value.isdigit():
            timestamp = int(value)
        elif key == "v1" and len(value) == 64:
            signatures.append(value.lower())
    if timestamp < 1 or abs(int(time.time()) - timestamp) > 300 or not signatures:
        raise StripeCommercePaymentError("Stripe webhook signature is invalid.", 401)
    expected = hmac.new(
        stripe_payment_secrets.webhook_secret().encode(),
        str(timestamp).encode() + b"." + payload,
        hashlib.sha256,
    ).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        raise StripeCommercePaymentError("Stripe webhook signature verification failed.", 401)
    try:
        event = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StripeCommercePaymentError("Stripe webhook payload is invalid.") from exc
    if not isinstance(event, dict):
        raise StripeCommercePaymentError("Stripe webhook payload is invalid.")
    obj = ((event.get("data") or {}).get("object") or {}) if isinstance(event.get("data"), dict) else {}
    metadata = obj.get("metadata") if isinstance(obj, dict) and isinstance(obj.get("metadata"), dict) else {}
    if str(metadata.get("vp3_payment_authority") or "") != "homeserver":
        raise StripeCommercePaymentError("Stripe event is not bound to this HomeServer payment authority.", 409)
    return {
        "contract": CONTRACT,
        "verified": True,
        "provider": "stripe",
        "authority": "homeserver",
        "event_id": str(event.get("id") or ""),
        "event_type": str(event.get("type") or ""),
        "object_id": str(obj.get("id") or "") if isinstance(obj, dict) else "",
        "order_id": int(metadata.get("vp3_order_id") or 0),
        "order_item_id": int(metadata.get("vp3_order_item_id") or 0),
        "fulfillment_type": str(metadata.get("vp3_fulfillment_type") or "other"),
        "payment_status": str(obj.get("payment_status") or "") if isinstance(obj, dict) else "",
        "amount_cents": int(obj.get("amount_total") or 0) if isinstance(obj, dict) else 0,
        "currency": str(obj.get("currency") or "").lower() if isinstance(obj, dict) else "",
        "external_payment_id": str(obj.get("payment_intent") or "") if isinstance(obj, dict) else "",
    }
