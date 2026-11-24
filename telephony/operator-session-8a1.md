# Operator 8A.1 — durable HomeServer session simulation

## Scope and deployment boundary

The Cloud Operator is the future source of authority. This HomeServer slice
models an independent worker's local lifecycle using **only synthetic**
opaque identities and fake transport receipts. There is no Cloud claim
endpoint, worker registration credential, model request, real media transport,
outbound call adapter, recording, listener, or scheduled runtime launch.
The existing inbound PH3G/PH3I worker, standalone gateway, public phone
permissions, and HomeServer Chat adapter remain unchanged.

The simulation uses a dedicated scratch SQLite database, with an explicit schema
marker and private file permissions on POSIX. On Windows use a private account
directory protected by its normal ACL. It rejects an existing unrelated
database. The CLI requires `--simulate`, uses a temporary directory that it
deletes at exit, and reads no live provider configuration. Internal persistent
test instances use a caller-supplied dedicated scratch path. Do not place real
customer data, phone numbers, credentials or conversation content in this
simulation ledger. There is no automatic installation or service launch.

## Contract

Every session is bound to the exact owner, Agent, private conversation,
contact reference, persistent conversation-workspace reference, source
(manual, scheduled, proactive), and unique occurrence reference. Those
are opaque fixture identifiers, not independently validated Cloud authority.
The same owner/occurrence is idempotent; attempting to reuse it with another
binding or lifetime is refused. Each recurring occurrence receives a distinct
session while retaining the same workspace reference. No scheduled definition
or conversation memory is created yet.

The database transaction and a partial unique owner index admit at most one
leased or unresolved simulated session for an owner, across that owner's
Agents. Each lease binds a worker identifier, boot identifier, generation,
exact session, and random one-use lease credential (only its hash persists).
Heartbeats renew a 30-second lease but never extend the session's absolute
deadline. A pending session lifetime is 60–900 seconds; at most 20
nonterminal fixture sessions can exist per owner. These are conservative
simulation bounds, not the future production calling allowance.

## Lifecycle

```
prepared -> leased -> dispatch_reserved -> ringing -> active -> completed
                |            |                |         |
                |            +----------------+---------+--> needs_review
                |                                              |
                +--> prepared (expired, unspent)               |
                                                               v
                                                 canceled (fake leg reconciled)
```

- Commit a single-use dispatch reservation before the fake transport effect.
- A successful start requires an exact stored `sim-<session id>` receipt;
  an answered state and completed state require matching fake-leg evidence.
  Busy, unanswered and failed outcomes are not treated as successful calls.
- Lost response before or after effect spends the dispatch. The session
  becomes needs_review; no new dispatch is allowed for that occurrence.
- Cancel before dispatch closes the session immediately. After reservation,
  cancel fences the worker; reconciliation inspects and closes the exact fake
  leg. Until then the unresolved session blocks another claim for that owner.
- A restarted worker can reclaim an expired **unspent** lease after maintenance.
  Expired spent sessions instead require reconciliation. Stale generations,
  forged tokens, changed bindings, expired deadlines and backwards clocks fail
  closed. A future real worker must additionally fence media continuously.
- Only the local fake transport can be reconciled here. Reconciliation never
  reports a successful conversation and cannot certify a real carrier call.
  A real uncertain dispatch must stay blocked until its authoritative carrier
  and room outcomes are known; absence from local storage is not sufficient.
- Metadata-only, ordered events and counts survive scratch DB reopening.
  Local health is not a worker connection, media readiness or consent proof.
  Every public report from the demonstration says execution_enabled=false
  and live_certified=false.

## Next integration (not implemented)

1. Cloud-signed short-lived claim/renew/revoke protocol and replay-protected
   worker registration. Bind the exact assigned line, recipient agreement,
   workspace, occurrence, worker, route revision and model/spend reservation.
   Authenticate Cloud independently; a local fixture lease grants nothing.
2. Separately reviewed outbound LiveKit/Twilio transport with real terminal
   callbacks, exact call/room receipts, and read-only uncertain-delivery
   reconciliation. Never retry an unconfirmed carrier dispatch.
3. Server-side owner join/takeover, media cancellation, route/privacy
   authority, recording/transcription consent, and per-turn spend control.
   Keep existing inbound worker permissions separate.
4. Integrate durable recurring schedule definitions and scoped conversation
   workspaces through the Cloud Agent/Calendar and existing report modules.
   No missed-call backlog, no duplicate occurrences, and recheck consent
   and budget before each run.
5. Independent physical provider tests for outbound call setup, audio,
   disclosure, affirmative consent, interruption, owner takeover, carrier
   cancellation, terminal receipts and failure/revocation recovery.
   A dedicated consenting staging contact and actual worker deployment are
   required before any real execution switch is enabled.

Tests use only standard-library SQLite, threads, deterministic clocks and
synthetic transport; the existing Phone Gateway workflow also retains all
prior inbound/SIP/Chat regressions. No calls or model/speech costs are incurred.
