# Section 14 — Permissions, Security and Agent Action Boundaries

Scope: Cloud and integrated HomeServer. Standalone Tracky2 is excluded. This section audits existing account/team/device authority, native tool execution, deferred approvals, provider credentials, webhook verification and Agent Chat/Brain projections.

## Repairs

- Cloud resolves a persisted user's current primary role and active state instead of trusting a copied principal. Explicit secondary account types still contribute their grants. Deleted, disabled or unreadable identities grant no roles.
- A failed read of existing permission storage cannot restore compatibility defaults. Pre-storage compatibility helpers remain available only without a database connection; persisted principals require readable identity storage.
- HomeServer authorization failures propagate to Cloud and never invoke read-safe fallback. Outage fallback remains available for eligible read/compute operations; write and physical operations never fall back.
- Disconnecting or re-pairing cancels pending app proposals in the same transaction. New app approvals bind to their pairing generation so re-pairing cannot revive older executing work.
- Every native deferred approval extension shares a write-locked claim that rechecks pending state, current expiry, owner tool enablement, app status, grants, tool scope and execution policy.
- Canonical and file/scheduling/commerce extension executors recheck current app authority at invocation. Passed permission snapshots can only narrow current grants. Direct Tasks API proposals retain their established tasks.write requirement; they do not acquire a new tools.execute requirement, while tool-origin proposals retain both requirements. Approved calls bind source, tool and arguments to the executing persisted request.
- Remote approvals retain reviewer identity across nested calls, so loss of approvals.review is checked again at claim and invocation.
- Owner approval retains the originating application's resource scope. Scoped memory creation, update and deletion cannot gain unrestricted owner resource access.
- Concurrent local provider key updates serialize their read/merge/atomic-write sequence. Windows DPAPI and restricted local-file protection remain in use. Failed edits leave the existing store intact.

## Reviewed retained boundaries

Team/resource ownership continues through the canonical workspace helpers. HomeServer member sessions resolve current status and role. Device commands and file, booking and commerce mutations retain their established local owner approval restrictions. Webhooks retain HMAC verification, timestamp checks when supplied, durable event identity and payload sanitization. Cloud BYOK uses the existing encrypted credential store and never forwards those keys to HomeServer. Existing Agent Chat and Agent Brain controls call these native services and receive their current denial/error states.

## Acceptance rubric

Completion requires evidence for all ten criteria: current principal, live grants, resource scope, owner approval, deferred expiry, revocation, concurrency, no authority-expanding fallback, secret protection and release provenance. Behavioral authority and credential tests plus retained approval, policy, scope, privacy, member and tool contracts run in CI. Windows package verification must match the changed compiled modules to merged source. Live providers and installed devices remain owner acceptance.

## Owner checklist

1. Remove an account role or deactivate a test account while a second tab is open; confirm its next protected operation is denied.
2. Propose a HomeServer memory/task action, then remove the app's permission, narrow its scope, or disconnect it before approval; confirm no mutation occurs.
3. Change an app tool to sensitive/high-impact and confirm remote and queued execution are blocked; review the current control state before creating replacement work.
4. Approve an allowed scoped memory edit and confirm it succeeds once. Repeat approval and confirm the existing result is retained.
5. Confirm expired requests and owner-disabled tools do not execute; test normal local owner proposals.
6. Test HomeServer offline read-safe fallback, then a real revoked/permission-denied connection; denial must be shown without fallback.
7. Save two different provider keys from two tabs and verify both remain configured. Status, audit and Agent context must not expose the full keys.
8. Exercise one real device or booking action through the existing local approval flow; restart and inspect the final receipt.

No new schema, permission grant or physical capability is introduced. Software checks do not certify external provider behavior or installed hardware.

