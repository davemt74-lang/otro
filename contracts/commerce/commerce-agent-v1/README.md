# commerce-agent-v1

`commerce-agent-v1` is the frozen VP3 Cloud ↔ HomeServer Agent Commerce contract.

VP3 Cloud remains canonical for catalog, price/terms, orders, money, customer-facing checkout, refunds, fulfillment linkage and the public profile. HomeServer may read the owner's scoped Commerce state, recommend products using private local context, prepare a public Cloud checkout handoff, and request a bounded fulfillment update.

## Permissions

- `commerce.read`: status, catalog/product reads, order reads.
- `commerce.order`: prepare a public Cloud checkout handoff. This does **not** create an order, accept buyer terms, authorize payment, or move money.
- `commerce.fulfill`: request a `processing` or `fulfilled` transition on an eligible fully-paid generic Profile Commerce order. HomeServer must require local owner approval before executing it.

`payments.*` remains separate under `commerce-payment-v1` and is not expanded by this contract.

## Buyer boundary

HomeServer never accepts seller terms for a buyer and never creates a provider checkout through this Agent contract. `vp3.commerce.checkout.handoff` returns the canonical public Profile Commerce URL and terms digest; the buyer completes acceptance and checkout in VP3 Cloud.

## Fulfillment boundary

Appointment fulfillment remains Scheduling-authoritative. Shipped physical fulfillment remains unavailable until a shipping-address adapter exists. The only v1 fulfillment mutation is the existing generic Profile Commerce `processing|fulfilled` transition, reusing Cloud's canonical Phase 9 operation.

## Compatibility

`contract.json` is canonical and the exact digest is pinned in `SHA256`. IDs are opaque strings, money is integer currency-minor-unit, unknown request fields are rejected, and breaking changes require `commerce-agent-v2`.
