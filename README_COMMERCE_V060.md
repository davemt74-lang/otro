# HomeServer v0.60 — Local Commerce Payment Authority

HomeServer v0.60 provides an optional local payment authority for VP3 Commerce. VP3 Cloud remains fully usable without HomeServer.

## Contract

- Contract: `commerce-payment-v1`
- Provider implemented initially: Stripe
- Payment authority: `homeserver`
- Canonical commerce ownership remains in VP3 Cloud: Product/Offer → Order/Order Item → Payment → Fulfillment.
- HomeServer receives frozen order/payment instructions and does not calculate product price, deposits, tax, fulfillment, or scheduling rules.
- Appointment is fulfillment metadata only; the same payment contract supports future digital, physical, event, membership and other fulfillment types.

## Security

- Stripe secret key and webhook signing secret are stored only in HomeServer's protected local credential store.
- Raw credentials are never returned through APIs or the VP3 Remote Bridge.
- Card details are collected by Stripe-hosted Checkout and never handled by VP3 or HomeServer.
- Only the paired `vp3` application may invoke the commerce payment bridge.
- Live permissions are required: `payments.read`, `payments.write`, and `payments.refund`.
- Provider mutations use stable idempotency keys.
- Stripe webhooks are HMAC verified locally with a five-minute timestamp tolerance.
- Money-moving actions are recorded in HomeServer activity history without payer email, return URLs, idempotency keys, or provider secrets.

## Routing

Payment attempts are pinned to the selected authority. HomeServer does not silently fall back to cloud payment credentials if it is offline or later unavailable.

HomeServer Stripe currently fails closed for offers with a non-zero VP3 platform fee. Those offers must use a compatible cloud payment authority until local platform-fee routing is explicitly implemented.
