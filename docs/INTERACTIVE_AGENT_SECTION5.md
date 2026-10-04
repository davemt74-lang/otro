# Interactive Agent review — Section 5: participant identity integrity

## Audit and integration

Cloud uses the existing studio participant/profile/voice tables and the canonical
Agent surface context. HomeServer retains the bundled Tracky local participant
store, owner-only self-check, governed visual-contact association, canonical
local transcription sessions and room-channel meeting capture. No parallel
identity ledger, camera runtime, voice recognizer, or schema migration.

The audit found stale voice bindings accepted as current identity, ambiguous
provider bindings picking the first profile, caller-entered bindings marked
verified, presence scoped to unchecked conversation/session IDs, eight-hour
presence without departure, late browser responses crossing selections, stale
self-check enrollment after inference, and non-atomic local profile patches.

## Repairs

- Cloud checks authenticated ownership of both conversation and transcript and
  rejects mismatched scopes/completed transcript presence. Presence writes lock
  their existing parent rows. Explicit left-state updates are idempotent and
  never create absent participants. Live reports expire after five minutes.
- Voice presence revalidates current active/consented/verified binding and the
  confidence threshold. Ambiguous duplicate bindings, changed IDs, inactive
  profiles and revocation become unknown. Changing a binding does not inherit
  verification; a caller-entered ID is stored unverified. Recognition and cloning
  remain separate. Voice binding reports remain owner-reported conversational
  attribution, never proof of which person actually spoke or authentication.
- Agent enrichment obtains current participant context from the server ledger;
  browser names/recognized flags cannot bypass current evidence. Provider IDs,
  embeddings, recordings and photos are excluded from model participant context.
- Cloud browser reads are tied to scope and request generation, including A-B-A
  selection. Scope changes, expired cache, errors and uncertain mutation results
  clear identity. Mutations serialize, keep original scope and refresh the current
  selection. In-flight consent changes hide cached identity. Requests have a
  thirty-second deadline. Unknown speakers lose name/account identifiers.
- HomeServer validates complete finite descriptors, valid reference sample counts
  and face geometry; large vectors avoid numeric overflow. Ambiguous/revoked
  profiles do not match. Spatial association clears identity and cannot reclaim
  an expired track. A spatial track is not a person identity.
- The owner self-check rereads the profile and privacy/consent after inference.
  Results stay local, ephemeral and independently unverified. No bystander
  enrollment or remote recognition is added.
- Local profile patches read/write in one IndexedDB transaction. Deletion and
  dialogue writes share a transaction; deleted participants cannot be resurrected
  by a delayed patch/turn. Nearby names resolve from current IDs; deleting one
  person does not erase another person with the same name.
- Local transcripts explicitly identify their speaker label as an unidentified
  single microphone channel without diarization. Physical meeting room capture
  declares a shared room channel, not a voice-profile identification.

## Software acceptance criteria

Acceptance covers scoped identity integrity: owner isolation; session matching;
current binding and ambiguity; revocation; explicit departure and expiry;
server-resolved Agent context; browser scope/race handling; descriptor validity;
transactional local mutation/deletion; post-inference consent; honest attribution.
CI and installed acceptance are required separately; a scoped software score is
not a score for recognition accuracy or completeness of all planned capabilities.

Run `node tests/participants-section5.mjs` in either repository (11 Cloud browser
or 10 HomeServer matcher/lifecycle cases). Cloud also runs
`php tests/participants_integrity_section5.php` (12 canonical service cases with
SQLite SQL-dialect adaptation; not InnoDB concurrency certification).
HomeServer runs `node tests/participants_store_section5.mjs` with Playwright 1.62.1
in its focused workflow (7 cases against real Chromium IndexedDB).
Retain Section 4, owner visual/privacy/contact and established core/recovery gates.

## Installed acceptance and remaining capability work

Check real microphone noise/overlap, anonymous channels, manual speaker correction,
profile consent/revocation/deletion during a live self-check, identical names,
conversation/transcript switching, two-tab concurrent profile changes, offline
requests, departure/re-entry and expiry in installed Cloud/Windows browsers.
No raw biometric transfer or automatic contact creation is enabled by this work.
Actual voice-profile recognition/diarization for the local shared microphone,
continuous multi-person recognition and cross-modal fusion are still capability
work, not features supplied by a speaker label or a face-count detector. These
belong in Section 9's missing-capabilities review; live accuracy and hardware
certification remain Section 10 acceptance. Section 6 reviews video meeting
reliability next, after this section is green, merged and packaged.
