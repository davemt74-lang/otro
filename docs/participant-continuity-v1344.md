# Participant-aware conversation continuity — v1344

This round extends the v1343 Agent Eyes, floating orb and voice guard. It uses the existing participant records, Chat owners, conversation databases, Brain history and provider routes. It adds no trained model, runtime download or database migration.

## Current participant and corrections

The Agent Eyes panel has a Conversation participant section. Live camera and voice labels are compared before a speaker hint enters Chat. A conflicting named face, duplicate visual identity or stale active camera leaves the speaker unassigned. Ordinary conversation can continue without an identity; known-voices-only mode rejects uncertain speech. Multiple people can be in view when exactly one visible identity agrees with the current voice match. Camera-off conversation can still use a local voice match. These are provisional conversational labels, never authentication or tool permissions.

“That's not me” is available as a button and as an exact spoken/typed correction. It clears pending attribution, cancels voice learning, prevents immediate visual reacquisition on the current track and dismisses that voice identity for two minutes. Saved profiles remain intact. A single current camera participant can introduce themselves again. A later camera visit can recognize saved visual samples normally. Stop remains available independently of recognition.

## Names and explicit memory

Choose a participant before editing their name or a conversation note. No note is saved merely by typing it. Saving updates that existing participant; it does not create a duplicate or replace linked accounts, notes from other tools, visual samples, voice samples, recognition consent or cloning consent. Forget conversation note clears the saved note. Forgetting visual or voice recognition remains separate.

Cloud notes use a dedicated participant-note entry in the existing owner-private Brain store. This profile note is shared with the owner's agents when explicitly selected or associated with a current voice turn. The helper restores the caller's agent memory scope after accessing this profile-level entry. Name and memory updates commit together; unavailable memory storage rolls back the rename. Cloud re-resolves participant names and notes from current owned records and rejects foreign/inactive records.

HomeServer stores the explicit note on the existing Tracky participant, preserving ordinary Tracky notes and other fields. Its Chat endpoint accepts the bounded label/note only on the owner canvas. Paired applications cannot supply owner speaker context. Notes are quoted background data, not system instructions, permission or identity evidence.

Use in typed Chat explicitly selects the saved participant details for typed turns, including when voice learning is not enabled. Unsaved edits are not used. The choice expires after thirty minutes and clears on a participant/camera change, conversation change, correction, hidden page or sample removal. A matched voice uses its own current context; uncertain voice input cannot silently fall back to the typed selection. The popup receives and clears selections through the existing origin/window-checked bridge.

## Existing conversation handoffs

The popup's Full Chat link tracks the actual selected conversation and Cloud agent. A same-tab page change retains a bounded conversation reference without copying transcripts or drafts. HomeServer on the main workspace restores the same existing Chat form and its draft instead of reloading it. Standalone HomeServer pages can open the exact saved conversation through a validated query reference. Backend conversation ownership and agent binding remain authoritative.

After a requested conversation loads, its query reference is consumed. Later refreshes respect a new explicit selection or new conversation. No handoff auto-starts a microphone or camera, submits a draft, creates a parallel conversation store, synchronizes biometric samples or merges independent Cloud/HomeServer transcript databases. Existing pairing, reconciliation and compute routing remain in place.

## Brain history

Corrections, participant-detail updates and forgotten recognition samples enter the existing private Agent Eyes event timeline. Cloud resolves canonical owned names. Events contain bounded participant references and summaries, not notes, frames, audio or feature vectors. Camera frames and silence do not create new per-frame conversation events.

## Verification and hardware acceptance

Tests exercise current camera/voice agreement and disagreement, group ambiguity, stale camera context, corrections, explicit note storage/clearing, owner isolation, transactional rollback, typed-versus-voice payloads, popup origin/window checks, selection invalidation, exact conversation/agent links, consumed query references, real IndexedDB preservation and existing Chat/voice recovery. Full Cloud recovery and HomeServer owner Chat suites also run.

Camera and acoustic inputs in automated browser tests are controlled fixtures. Physical webcam and noisy-room accuracy still require installed hardware testing. The existing lightweight voice matcher is provisional and does not separate overlapping speakers reliably. Test v1343 independently while this round is reviewed; its source and deployment packages are unchanged by this branch.
