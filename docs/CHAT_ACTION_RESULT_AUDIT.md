# Chat action result ownership audit

The shared Cloud, HomeServer and Browser Companion action review now binds every request, approval button and cleanup handler to its conversation, Agent and generation. Delayed successes and failures cannot erase a newer review, unlock its pending write, populate its Brain receipts, or continue an old destination-to-prepare chain. Unobserved Agent changes are checked before dispatch. Composer drafts are preserved.

Exact before/after approval, current permissions, record revisions, idempotent request IDs and durable receipts already exist in the action services. This change reuses them without a new schema or protocol. A server write completed before a conversation change remains committed. Returning to its original context retrieves its receipt without applying again.

## Verification

- Browser regression reproduces the original late-options cleanup bug before the fix.
- Shipped scripts run in Cloud, native and extension modes with deferred options, suggestions, destination checks, prepare, apply and status requests, including failures and conversational drafts.
- A newer pending apply retains its fields, source selection and disabled controls; stale review buttons dispatch nothing after a conversation or Agent change.
- Completed old actions recover by receipt with one write; Brain receipts remain scoped and composer text survives.
- Existing R8-R11 browser suites and native durable workspace action, Knowledge action and A5C3 exact-action service tests pass locally.
- Cloud PHP/MySQL acceptance and native Windows certification are required release gates. The merged native tree must match its certified candidate before installer reuse.

The chat canvas keeps its existing minimal layout. Detailed evidence and receipts stay in the existing review and Brain surfaces.
