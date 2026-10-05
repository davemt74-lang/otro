# VP3 Section 13: Events, Notifications and Automation

Scope: canonical Cloud event inbox, notification carriers and drawer; HomeServer local rules, workflow scheduling/recovery, and integrated federated event/run receipts. Agent Chat and Agent Brain retain the existing native ownership and approval boundaries. Standalone Tracky2 is outside this section.

## Repairs

- Cloud dispatch completion is fenced by the durable replay generation. A stale worker cannot overwrite a recovered event result or its Brain projection. Oversized external event identities are rejected instead of silently colliding.
- Cloud notification creation serializes canonical owner writes and normalizes carrier identities before deduplication. Carrier writes survive optional projection failure. Brain SQL selection agrees with the PHP classifier for HomeServer attention types.
- Drawer state requests discard obsolete responses and serialize state mutations.
- HomeServer local rule evaluation, trigger advancement, approval creation and execution receipts share a transaction. Failed multi-step routines leave no partial requests or consumed trigger. SQLite timestamp normalization restores rate limiting.
- Workflow checkpoints and completion honor the current lease. Completion, activity and notification commit together. Owner disable invalidates unfinished claims. Older runs cannot overwrite the latest automation summary.
- Stopping a busy scheduler retains its worker reference until it actually exits.
- Federated definitions, trigger decisions, runs, dispatch ledgers, state transitions and applied receipts commit atomically. Identical applied receipts replay without duplicate events; changed terminal receipts and receipts without a matching dispatch attempt are rejected. Cancellation cannot be undone by a late receipt.

## Verification

Production-service tests force approval, trigger receipt, execution event and notification failures; exercise concurrent trigger and suggestion decisions; simulate worker takeover and owner disable; and verify terminal receipt immutability. Retained local automation, workflow recovery and federated contract suites remain mandatory. Cloud tests exercise recovery generation, notification transactions and drawer response ordering.

The software review rubric has ten criteria: ownership, approval retention, durable identity, transaction rollback, concurrency, lease recovery, cancellation, notification consistency, UI ordering and release provenance. Each criterion requires passing behavioral or existing integration checks before this section is considered complete; a numerical target is not a substitute for evidence.

## Owner testing

On the deployed builds, exercise one daily rule, an owner-approved routine, pause/resume, a workflow continuation, notification/read state from two tabs, restart during queued work, and a fresh federated event. Confirm a cancelled run stays cancelled. Test real provider or device playback/actions using the existing approval flow. Automated checks do not certify every external provider or physical device.

No schema migration or new hardware permission is introduced by this section. Existing authentication, webhook signatures, authority epoch, reconciliation and device approval gates remain required.
