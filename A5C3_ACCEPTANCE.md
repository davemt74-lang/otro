# A5C3 — Governed specialist actions

Specialists can prepare bounded, source-scoped contact, knowledge, task and calendar changes. The HomeServer owner may also prepare an edit to an existing synced Cloud record. Every proposal uses the existing approval executor; completing research alone never saves a change.

## Acceptance gates

| Gate | Required evidence |
|---|---|
| Assignment | Opt-in write policy, current capabilities, distinct read and proposal budgets, no unassigned or destructive action |
| Preparation | Exact immutable payload, server mutation ID, no native write before approval |
| Atomic result | Invalid later proposals roll back the entire worker result and all earlier proposals |
| Review | Per-change explicit confirmation, payload hash, approve or reject, source-qualified target |
| Native execution | Real SQLite contact, knowledge, task and calendar writes through existing approvals |
| Cloud queue | Owner-only workspace proposal becomes one queued edit; replica remains unchanged until verified sync |
| Replay | Concurrent same-intent retries return the durable receipt and produce one write; different intent is rejected |
| Fresh authority | Expiry, revocation, pairing generation, changed policy and payload tampering block generic approval routes too |
| Interruption | Pause/cancel during inference discards the late result and leaves no actionable proposal |
| Presentation | Cloud and native Playwright checks: inert preview text, cancelled confirmation, durable retry, receipt, Brain timestamp |

## Verification

Run `python tests/agent_mission_actions_a5c3.py` and the existing A5C1/A5C2 and migration regressions. Run `node tests/agent_mission_actions_a5c3_browser.mjs` with Playwright Chromium. CI also checks the complete HomeServer Windows installer and managed browser. Schema 72 is required for release packaging.

The service fixture controls model inference and the synced workspace binding/record; scheduler, approvals, SQLite writes, outbox and replay receipts are real. Browser fixtures control transport responses while exercising the actual UI in Chromium. These checks do not certify live model quality, a particular physical device, or a live Cloud account's write delivery.
