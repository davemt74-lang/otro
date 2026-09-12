# commerce-payment-v1

This directory is the pinned HomeServer copy of the VP3 Cloud-owned `commerce-payment-v1` payment-authority contract.

VP3 Cloud is canonical for Product/Offer → Order → Order Item → Payment/Refund ledger → Fulfillment linkage. HomeServer may execute an explicitly selected local payment authority, but it does not price products, calculate deposits/tax/discounts, own scheduling, or perform fulfillment.

Wire rules:

- IDs are opaque strings across the bridge.
- Money is an integer in the currency's minor unit and uses `*_minor` field names.
- Currency is lowercase ISO-4217 alpha-3.
- Unknown request fields fail closed.
- Money-moving mutations require stable idempotency keys.
- Payment authority is pinned to the attempt; there is no silent fallback.
- Provider credentials and raw card data never cross the pairing boundary.
- Every bridge response identifies the contract plus the exact contract SHA-256.

The canonical payload is `contract.json`; `SHA256` pins the exact revision. Breaking changes require `commerce-payment-v2`.
