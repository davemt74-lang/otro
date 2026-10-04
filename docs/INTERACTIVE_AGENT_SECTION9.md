# Interactive Agent Section 9 — Speaker Attribution, Diarization and Cross-Modal Fusion

## Objective

Section 9 closes the deferred speaker-understanding gaps from Sections 5 and 6 without allowing probabilistic audio or camera evidence to become authentication authority.

The work is divided into four independently validated gates:

- **9A — Canonical speaker attribution evidence**
- **9B — Shared-microphone diarization and voice recognition**
- **9C — Camera + voice identity fusion**
- **9D — Meeting overlap, recording and session integration**

Each gate must be green before the next gate is merged.

## 9A — Canonical speaker attribution evidence

A speaker label is no longer treated as equivalent to an identity. The common attribution contract distinguishes:

- `unknown` — no trustworthy speaker separation or identity evidence;
- `heuristic_acoustic` — low-authority acoustic clustering used only as a fallback label;
- `provider_diarization` — a provider separated the audio into speakers, but no person is implied;
- `verified_voice` — a current, consented voice binding resolved to a participant;
- `livekit_track` — a separated meeting audio track bound to a canonical meeting participant identity;
- `manual_correction` — explicit human correction;
- `account_identity` — a canonical account/meeting identity;
- `visual_corroboration` — recent visual evidence that may corroborate another identity source but can never create speaker identity by itself.

Every attribution carries bounded confidence, overlap metadata, sanitized evidence provenance and `authentication_authority=false`.

### Fail-closed rules

1. Acoustic heuristics may assign `Speaker 1..N` but cannot assign a person.
2. Provider diarization separates speakers but cannot assign a person unless a separate verified voice/account binding resolves that speaker.
3. Camera/visual evidence cannot identify the speaker by itself.
4. Conflicting strong identity evidence downgrades identity to unknown.
5. Visual evidence may only corroborate an already resolved identity.
6. Raw provider speaker IDs and raw LiveKit track IDs are hashed before entering attribution provenance.
7. Manual correction overrides inference but remains explicit human evidence, not hidden biometric authentication.
8. LiveKit remote participant tracks are already separated channels and therefore use track identity rather than re-diarizing mixed audio.
9. HomeServer shared-microphone transcription remains explicitly unidentified until 9B provides actual diarization evidence.

## 9B — Shared-microphone diarization and voice recognition

Build a bounded shared-room audio pipeline that can replace heuristic labels with real diarization output. Reconciliation must be source-hash/timestamp bound and idempotent. Known-speaker recognition may resolve a diarized speaker only through current consented voice bindings. Heuristic labels remain a fallback and must be visibly lower authority.

## 9C — Camera + voice fusion

Fuse short-lived visual-presence evidence with voice/account attribution. Voice/account identity remains primary. Visual evidence may increase confidence only when it agrees with the same participant. Ambiguity, stale evidence, revoked consent or conflicting candidates fail closed. Camera evidence never authenticates a user and never names a speaker alone.

## 9D — Meeting overlap, recording and session integration

Preserve simultaneous LiveKit participant tracks as overlapping transcript turns, attach attribution provenance to durable meeting/transcript segments, close recording-state gaps, and make Agent Chat/Brain consume the same canonical attribution contract. Deletion, consent revocation and participant corrections must invalidate future attribution immediately.

## 9A acceptance

- canonical Cloud and HomeServer implementations use the same source hierarchy;
- acoustic-only and visual-only evidence cannot identify a person;
- conflicting strong identities fail closed;
- LiveKit meeting callbacks carry typed track attribution;
- Cloud independently re-resolves the meeting participant rather than trusting the HomeServer name;
- shared-mic local transcription truthfully reports unknown/single-channel attribution;
- provider/track identifiers are not exposed raw in evidence;
- focused tests are in retained CI gates.
