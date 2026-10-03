# Agent Eyes 1G5 — Installed acceptance

This section adds a checklist over the existing camera review, Stop/privacy/presence evidence, local Agent context and acknowledged Cloud scene ledger. It does not add another camera runtime. Owners inspect each exercise and explicitly record a report after its software prerequisites pass.

| Criterion | Implementation and verification |
| --- | --- |
| Local chat | Current reviewed detector and fresh canonical observation; existing owner conversation with Agent Eyes opted in and Cloud inference disabled; transcript never read or exported |
| Optional scene/Cloud | Separate scene review and delivery/expiry/revocation/reconnect checks; sharing stays explicitly opt-in |
| Original expiry | Same completed observation, original 60-second ceiling, later revision and successful acknowledgment; Stop cannot substitute for expiry |
| Restart | Idle checkpoint before restart; different process afterward, review reset and sharing off; record before accepting another camera review |
| Reconnect | Existing sync failure checkpoint followed by a later successful sync and no pending scene delivery; no automatic connectivity operation |
| Owner control | Authenticated control router, strict payload, UI request header, explicit inspection, current prerequisite token and observation fingerprint |
| Durable receipts | Existing certification ledger, bounded reads, model/device/process scopes, integrity digest and idempotent evidence identity; status remains `not_verified` |
| Browser boundaries | Safe text, bounded timeout, prerequisite expiry, hidden-view cancellation, late response isolation and selected owner conversation checks |
| Reports | Local and authenticated Cloud JSON exports omit scene meaning, model/device/conversation identifiers and raw error text; Cloud never claims current local consent or installed acceptance |
| Regression/package | Existing PR Core, Cloud Recovery and InnoDB suites; existing packaging workflows carry 1G5 metadata and required modules/UI |

Review target is 10/10 for the software section. Final acceptance requires green exact-head CI, merge and inspection of both merged-main deployment archives. Synthetic tests and recorded owner reports do not independently certify a physical camera.

## Installed run order

1. Install the companion Cloud/HomeServer builds, run the standard Cloud upgrade path and complete current camera/model review.
2. Complete existing Stop, camera privacy and owner presence timeout exercises. Inspect camera release on the Windows installation.
3. Select an owner chat, enable local Agent Eyes context and keep Cloud inference off. Obtain a recent observation, inspect the reply and record Local Agent Chat.
4. If using scene understanding, complete the local scene/model review. If using Cloud sharing, record acknowledged delivery, original expiry and acknowledged sharing-off separately. Use a completed one-observation session for expiry, and another acknowledged delivery before testing revocation if needed.
5. Save an idle restart checkpoint. Restart HomeServer manually; record review/sharing reset before reviewing the camera again. Then repeat current-process review, Stop/privacy/presence and local chat checks.
6. If using Cloud, record a normal sync failure after manually interrupting connectivity, restore connectivity and record the later successful reconnect.
7. Download the redacted HomeServer and Cloud reports. Inspect any pending checks and repair actual installation failures before accepting the deployment.

No new database schema, scene protocol, automatic recovery, unattended capture or identity recognition is introduced. The release remains checksum-verified; publisher Authenticode signing is not added by this section. Real installed-device acceptance and any publisher signing required by the earlier hardware-release roadmap remain outstanding until independently performed.
