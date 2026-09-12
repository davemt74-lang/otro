from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .services import stripe_commerce_payments, stripe_payment_secrets

router = APIRouter()


class StripeCredentialUpdate(BaseModel):
    secret_key: str | None = Field(default=None, max_length=512)
    webhook_secret: str | None = Field(default=None, max_length=512)


def _status() -> dict:
    return {
        "version": "v0.60",
        "contract": stripe_commerce_payments.CONTRACT,
        "mode": "optional-local-commerce-authority",
        "providers": {"stripe": stripe_payment_secrets.status()},
        "platform_fees_supported": False,
    }


@router.get("/api/v1/control/payments")
def control_payment_status() -> dict:
    return _status()


@router.put("/api/v1/control/payments/stripe")
def control_payment_stripe_update(payload: StripeCredentialUpdate) -> dict:
    try:
        stripe_payment_secrets.save(payload.secret_key, payload.webhook_secret)
        return _status()
    except stripe_payment_secrets.StripePaymentSecretError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/api/v1/control/payments/stripe")
def control_payment_stripe_clear() -> dict:
    try:
        stripe_payment_secrets.save(clear=True)
        return _status()
    except stripe_payment_secrets.StripePaymentSecretError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/v1/control/payments/stripe/verify")
def control_payment_stripe_verify() -> dict:
    try:
        return {"verified": True, "account": stripe_commerce_payments.account_status()}
    except (stripe_payment_secrets.StripePaymentSecretError, stripe_commerce_payments.StripeCommercePaymentError) as exc:
        status = getattr(exc, "status_code", 422)
        raise HTTPException(status_code=status, detail=str(exc)) from exc
