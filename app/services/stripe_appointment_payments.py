from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from . import stripe_payment_secrets

VERSION = "v0.60"
STRIPE_API = "https://api.stripe.com/v1"


class StripeAppointmentPaymentError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _post(path: str, fields: list[tuple[str, str]], *, idempotency_key: str = "") -> dict[str, Any]:
    if not path.startswith("/") or ".." in path or "?" in path:
        raise StripeAppointmentPaymentError("Stripe operation path is not allowlisted.")
    headers = {"Authorization": f"Bearer {stripe_payment_secrets.secret_key()}", "Content-Type": "application/x-www-form-urlencoded"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    try:
        response = httpx.post(STRIPE_API + path, content=urlencode(fields), headers=headers, timeout=30.0, follow_redirects=False)
    except httpx.HTTPError as exc:
        raise StripeAppointmentPaymentError("Stripe is unreachable.", 503) from exc
    try:
        body = response.json()
    except ValueError as exc:
        raise StripeAppointmentPaymentError("Stripe returned an invalid response.", 502) from exc
    if not response.is_success:
        message = str(((body.get("error") or {}).get("message") if isinstance(body, dict) else "") or "Stripe request failed.")[:300]
        raise StripeAppointmentPaymentError(message, 409 if response.status_code < 500 else 502)
    if not isinstance(body, dict):
        raise StripeAppointmentPaymentError("Stripe returned an invalid response.", 502)
    return body


def _get(path: str) -> dict[str, Any]:
    if not path.startswith("/checkout/sessions/") or ".." in path or "?" in path:
        raise StripeAppointmentPaymentError("Stripe retrieval path is not allowlisted.")
    headers = {"Authorization": f"Bearer {stripe_payment_secrets.secret_key()}"}
    try:
        response = httpx.get(STRIPE_API + path, headers=headers, timeout=30.0, follow_redirects=False)
    except httpx.HTTPError as exc:
        raise StripeAppointmentPaymentError("Stripe is unreachable.", 503) from exc
    try:
        body = response.json()
    except ValueError as exc:
        raise StripeAppointmentPaymentError("Stripe returned an invalid response.", 502) from exc
    if not response.is_success or not isinstance(body, dict):
        raise StripeAppointmentPaymentError("Stripe checkout could not be retrieved.", 409 if response.status_code < 500 else 502)
    return body


def account_status() -> dict[str, Any]:
    secret = stripe_payment_secrets.secret_key()
    headers = {"Authorization": f"Bearer {secret}"}
    try:
        response = httpx.get(STRIPE_API + "/account", headers=headers, timeout=20.0, follow_redirects=False)
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise StripeAppointmentPaymentError("Stripe account verification failed.", 503) from exc
    if not response.is_success or not isinstance(body, dict):
        raise StripeAppointmentPaymentError("Stripe account credentials were rejected.", 409)
    return {
        "provider": "stripe",
        "account_id": str(body.get("id") or ""),
        "country": str(body.get("country") or ""),
        "default_currency": str(body.get("default_currency") or "").lower(),
        "charges_enabled": bool(body.get("charges_enabled")),
        "payouts_enabled": bool(body.get("payouts_enabled")),
        "livemode": secret.startswith("sk_live_"),
    }


def create_checkout(quote: dict[str, Any]) -> dict[str, Any]:
    paid_booking_id = int(quote.get("paid_booking_id") or 0)
    amount = int(quote.get("amount_cents") or 0)
    currency = str(quote.get("currency") or "").strip().lower()
    success_url = str(quote.get("success_url") or "").strip()
    cancel_url = str(quote.get("cancel_url") or "").strip()
    payer_email = str(quote.get("payer_email") or "").strip()
    idem = str(quote.get("idempotency_key") or "").strip()
    if paid_booking_id < 1 or amount < 1 or amount > 100_000_000:
        raise StripeAppointmentPaymentError("Paid appointment quote amount is invalid.")
    if len(currency) != 3 or not currency.isalpha():
        raise StripeAppointmentPaymentError("Paid appointment quote currency is invalid.")
    if not (success_url.startswith("https://") and cancel_url.startswith("https://")):
        raise StripeAppointmentPaymentError("Paid appointment return URLs must use HTTPS.")
    if not idem or len(idem) > 160:
        raise StripeAppointmentPaymentError("Paid appointment checkout requires a stable idempotency key.")
    fields = [
        ("mode", "payment"), ("success_url", success_url), ("cancel_url", cancel_url),
        ("line_items[0][quantity]", "1"), ("line_items[0][price_data][currency]", currency),
        ("line_items[0][price_data][unit_amount]", str(amount)),
        ("line_items[0][price_data][product_data][name]", "VP3 appointment payment"),
        ("metadata[vp3_paid_booking_id]", str(paid_booking_id)),
        ("metadata[vp3_payment_authority]", "homeserver"),
        ("payment_intent_data[metadata][vp3_paid_booking_id]", str(paid_booking_id)),
        ("payment_intent_data[metadata][vp3_payment_authority]", "homeserver"),
    ]
    if payer_email:
        fields.append(("customer_email", payer_email[:254]))
    body = _post("/checkout/sessions", fields, idempotency_key=idem)
    session_id = str(body.get("id") or "")
    checkout_url = str(body.get("url") or "")
    if not session_id.startswith("cs_") or not checkout_url.startswith("https://"):
        raise StripeAppointmentPaymentError("Stripe did not return a usable checkout.", 502)
    return {"provider":"stripe","authority":"homeserver","external_session_id":session_id,"checkout_url":checkout_url,"status":str(body.get("status") or "open"),"amount_cents":int(body.get("amount_total") or amount),"currency":str(body.get("currency") or currency).lower()}


def retrieve_checkout(session_id: str) -> dict[str, Any]:
    session = str(session_id or "").strip()
    if not session.startswith("cs_") or len(session) > 255:
        raise StripeAppointmentPaymentError("Stripe checkout session ID is invalid.")
    body = _get("/checkout/sessions/" + session)
    return {"provider":"stripe","authority":"homeserver","external_session_id":session,"external_payment_id":str(body.get("payment_intent") or ""),"status":str(body.get("status") or ""),"payment_status":str(body.get("payment_status") or ""),"amount_cents":int(body.get("amount_total") or 0),"currency":str(body.get("currency") or "").lower(),"paid_booking_id":int(((body.get("metadata") or {}).get("vp3_paid_booking_id")) or 0)}


def refund(payment_intent_id: str, amount_cents: int, paid_booking_id: int, idempotency_key: str) -> dict[str, Any]:
    payment = str(payment_intent_id or "").strip()
    amount = int(amount_cents or 0)
    if not payment.startswith("pi_") or len(payment) > 255 or amount < 1 or paid_booking_id < 1:
        raise StripeAppointmentPaymentError("Stripe refund request is invalid.")
    idem = str(idempotency_key or "").strip()
    if not idem or len(idem) > 160:
        raise StripeAppointmentPaymentError("Stripe refund requires a stable idempotency key.")
    body = _post("/refunds", [("payment_intent",payment),("amount",str(amount)),("metadata[vp3_paid_booking_id]",str(paid_booking_id)),("metadata[vp3_payment_authority]","homeserver")], idempotency_key=idem)
    refund_id = str(body.get("id") or "")
    if not refund_id.startswith("re_"):
        raise StripeAppointmentPaymentError("Stripe did not return a refund ID.", 502)
    return {"provider":"stripe","authority":"homeserver","external_refund_id":refund_id,"status":str(body.get("status") or "pending"),"amount_cents":int(body.get("amount") or amount),"currency":str(body.get("currency") or "").lower(),"payment_intent_id":payment}


def verify_webhook(payload: bytes, signature_header: str) -> dict[str, Any]:
    if len(payload) > 1_000_000:
        raise StripeAppointmentPaymentError("Stripe webhook payload is too large.")
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
        raise StripeAppointmentPaymentError("Stripe webhook signature is invalid.", 401)
    expected = hmac.new(stripe_payment_secrets.webhook_secret().encode(), str(timestamp).encode()+b"."+payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        raise StripeAppointmentPaymentError("Stripe webhook signature verification failed.", 401)
    import json
    try:
        event = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StripeAppointmentPaymentError("Stripe webhook payload is invalid.") from exc
    if not isinstance(event, dict):
        raise StripeAppointmentPaymentError("Stripe webhook payload is invalid.")
    obj = ((event.get("data") or {}).get("object") or {}) if isinstance(event.get("data"), dict) else {}
    metadata = obj.get("metadata") if isinstance(obj, dict) else {}
    return {"verified":True,"provider":"stripe","authority":"homeserver","event_id":str(event.get("id") or ""),"event_type":str(event.get("type") or ""),"object_id":str(obj.get("id") or "") if isinstance(obj,dict) else "","paid_booking_id":int((metadata or {}).get("vp3_paid_booking_id") or 0) if isinstance(metadata,dict) else 0,"payment_status":str(obj.get("payment_status") or "") if isinstance(obj,dict) else "","amount_cents":int(obj.get("amount_total") or 0) if isinstance(obj,dict) else 0,"currency":str(obj.get("currency") or "").lower() if isinstance(obj,dict) else "","external_payment_id":str(obj.get("payment_intent") or "") if isinstance(obj,dict) else ""}
