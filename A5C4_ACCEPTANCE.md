# A5C4 — Verified results and lead-agent completion

Completing a worker's investigation does not imply its proposed changes were saved. The lead report now separates review, Cloud delivery, receipt readback, changed or removed records, rejected changes and failed workers. Model-generated findings retain their existing unverified designation.

## Acceptance gates

| Gate | Required evidence |
|---|---|
| Native readback | All four native create and update families match an actual source record to the source-scoped mutation receipt, approved payload and successful tool run |
| Changed source | Later record revisions revoke current verification while preserving the original execution history |
| Missing evidence | A missing execution receipt or altered outbox payload cannot verify a write |
| Cloud milestones | Queueing, acknowledgement and a matching synchronized replica are distinct states; only readback verifies |
| Durable identity | Lost acknowledgement sends the same mutation ID and exact fields; concurrent recovery records one intent and wakes once |
| Safe recovery | Explicit consent, exact payload hash, active specialist assignment/policy and current pairing are required; only queued delivery resumes |
| Terminal outcomes | Conflict, blocked, failed, cancelled, rejected and failed-worker results never claim verified execution |
| Durable audit | Outcome/recovery rows persist across database initialization; unchanged polling does not add duplicate outcome events; timestamps identify checks and verification |
| Privacy | Current authority gates results; private Cloud projections export no completion report or action summaries |
| Presentation | Real Chromium tests exercise safe report text, accurate Chat/Brain state and timestamps, consent cancellation, retained recovery IDs and authority hiding |

## Validation and release

HomeServer: `python tests/agent_mission_outcomes_a5c4.py`, existing A5C1–A5C3 and specialist/relay/migration regressions, and `node tests/agent_mission_outcomes_a5c4_browser.mjs`.
Cloud: `node tests/agent-missions-cloud-a5c4-browser.mjs` plus existing PHP, relay, MySQL and browser checks. All repository CI must pass before merge. Deployment packages must identify the merged source tree; the Windows installer must certify its managed browser and schema 73.

Recovery wakes the existing durable Cloud outbox. It does not repeat successful native writes, alter approved fields or reset conflicts. A new edit requires fresh preparation and review. Cloud's paired-app UI displays results; queued workspace delivery recovery belongs to the HomeServer owner, who owns that approved outbox.

Service fixtures control model inference and Cloud HTTP/binding responses while exercising real SQLite writes, approvals, scheduler, delivery state and receipts. Browser fixtures exercise the actual UI in Chromium with controlled transport. These tests do not certify live model quality or a particular installed HomeServer and Cloud account. Verification describes the current readback and can change when a record is edited later.
