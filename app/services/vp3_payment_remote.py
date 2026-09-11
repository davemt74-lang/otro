from __future__ import annotations

import base64
from typing import Callable

from . import pairing, remote_bridge, stripe_appointment_payments, stripe_payment_secrets

VERSION = "v0.60"
OPERATIONS = {
    "vp3.payments.status",
    "vp3.payments.checkout.create",
    "vp3.payments.checkout.retrieve",
    "vp3.payments.webhook.verify",
}


def _identity(operation: str, bearer_token: str | None) -> dict:
    token = str(bearer_token or "").strip()
    identity = pairing.authenticate(token) if token else None
    if identity is None or str(identity.get("app_key") or "") != "vp3":
        raise remote_bridge.RemoteBridgeError("A paired VP3 identity is required for local payments.")
    permissions = set(identity.get("permissions") or [])
    needed = "payments.read" if operation in {"vp3.payments.status","vp3.payments.checkout.retrieve"} else "payments.write"
    if needed not in permissions:
        raise remote_bridge.RemoteBridgeError(f"Permission required: {needed}")
    return identity


def install() -> None:
    if getattr(remote_bridge, "_vp3_payments_v060_installed", False):
        return
    original: Callable[[str, dict | None, str | None], dict] = remote_bridge.dispatch_remote_request

    def dispatch_remote_request(operation: str, payload: dict | None, bearer_token: str | None = None) -> dict:
        op = str(operation or "").strip()
        if op not in OPERATIONS:
            return original(operation, payload, bearer_token)
        _identity(op, bearer_token)
        body = payload if isinstance(payload, dict) else {}
        if remote_bridge._payload_size(body) > 1_250_000:
            raise remote_bridge.RemoteBridgeError("VP3 payment payload is too large.")
        try:
            if op == "vp3.payments.status":
                credential = stripe_payment_secrets.status()
                account = stripe_appointment_payments.account_status() if credential["configured"] else None
                result = {"version":VERSION,"authority":"homeserver","providers":{"stripe":{**credential,"account":account}}}
            elif op == "vp3.payments.checkout.create":
                allowed={"paid_booking_id","amount_cents","currency","success_url","cancel_url","payer_email","idempotency_key"}
                if set(body)-allowed:
                    raise stripe_appointment_payments.StripeAppointmentPaymentError("Checkout quote contains unsupported fields.")
                result = stripe_appointment_payments.create_checkout(body)
            elif op == "vp3.payments.checkout.retrieve":
                if set(body)-{"external_session_id"}:
                    raise stripe_appointment_payments.StripeAppointmentPaymentError("Checkout lookup contains unsupported fields.")
                result = stripe_appointment_payments.retrieve_checkout(str(body.get("external_session_id") or ""))
            else:
                allowed={"payload_b64","stripe_signature"}
                if set(body)-allowed:
                    raise stripe_appointment_payments.StripeAppointmentPaymentError("Webhook verification contains unsupported fields.")
                try:
                    raw=base64.b64decode(str(body.get("payload_b64") or ""),validate=True)
                except Exception as exc:
                    raise stripe_appointment_payments.StripeAppointmentPaymentError("Webhook payload encoding is invalid.") from exc
                result = stripe_appointment_payments.verify_webhook(raw,str(body.get("stripe_signature") or ""))
        except (stripe_payment_secrets.StripePaymentSecretError, stripe_appointment_payments.StripeAppointmentPaymentError) as exc:
            raise remote_bridge.RemoteBridgeError(str(exc)) from exc
        return {"status":200,"ok":True,"payload":result}

    remote_bridge.dispatch_remote_request=dispatch_remote_request
    remote_bridge._vp3_payments_v060_installed=True
