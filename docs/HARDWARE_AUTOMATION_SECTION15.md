# Section 15 — Home Automation and Hardware

Scope: integrated VP3 Cloud and HomeServer device discovery, safe device commands,
physical hardware inputs, local-owner approvals, receipts and automation suggestions.
Standalone Tracky2 is separate. This audit extends the existing native services.

## Confirmed repairs

- Devices in disabled rooms report the blocking reason and reject proposals and dispatch.
- Device approvals retain a hash of the provider, physical target, category and room.
  Remapping a device key, changing its provider type, or moving it to another room
  requires a new approval. Old pending device requests without a binding also require
  replacement. Normal sensor/state updates do not invalidate an approval.
- Dispatch validates the original source and expiry. Existing local-owner review,
  current application grants, restricted categories and unique action receipts remain.
- Negative or queued-only driver replies cannot produce completed physical actions.
  Provider errors do not expose arbitrary exception content in receipts or audit logs.
- Suggestion conversion creates the approval and links its suggestion in one transaction.
  Concurrent retries return the same request and its current state; dismissal cannot
  overwrite a converted suggestion. Link failures roll back the request and audit.
- Serial controllers emitting no messages for ten seconds disconnect, clear reported
  readiness and return through the existing reconnect/handshake loop. The bundled
  controller emits state every second. String acknowledgements cannot become true.
- Cloud requires a durable, pending, matching local-owner device approval receipt.
  Empty, failed, mismatched or purported automatic replies cannot appear as execution.

## Ten acceptance criteria

1. Current device/provider availability and room enablement.
2. Approval bound to the intended physical target and room.
3. Local owner approval and restricted control categories.
4. Source identity, expiry, current grants and revocation boundaries.
5. Existing exactly-once action receipts and duplicate approval prevention.
6. Atomic, repeatable suggestion request and dismissal lifecycle.
7. Confirmed driver completion and honest failed/unknown outcomes.
8. Sanitized provider errors, audit metadata and Cloud projections.
9. Hardware silence, disconnect, reconnect and fresh-handshake behavior.
10. Green retained regressions and packages verified against merged sources.

Focused behavior runs on Ubuntu and Windows and Cloud PHP 8.1/8.3. Retained suites
cover device registration, approved action dispatch, automation, hardware inputs,
Physical Agent, hardware experience and the prior Section 14 authority safeguards.
No schema migration or additional physical control capability is introduced.

## Owner testing

1. Queue a light command, disable its room, then approve: no driver may run.
2. Queue a command, change its physical provider ID or room, then approve: create a
   replacement request. A fresh allowed request should execute once.
3. Disconnect or disable the provider and inspect its UI and Agent device status.
4. Retry a suggestion request from two tabs: one approval must exist. Dismissal after
   conversion must fail. Reopening a completed request must show its existing result.
5. Use a real allowed device: verify local approval, physical effect and final receipt.
   A timeout or failed provider must not be displayed as a confirmed success.
6. Stop controller state reports: after ten seconds it must show disconnected with
   no stale hardware readiness. Reconnect and confirm a fresh hello/state restores it.
7. Check the Cloud canvas and Agent Brain reflect pending, denied and failed states.

Software acceptance does not certify installed hardware or external device drivers.
A command already dispatched cannot be recalled; physical outcomes after transport
failure require inspection before proposing another action.
