# Interactive Agent review — Section 2: Listening and conversation correctness

This section repairs the existing Cloud and HomeServer controllers. The canonical
chat, transcription, editor conversation, inference and voice providers remain the
request owners. There is no new conversation service or standalone installation.

## Software acceptance

- Separate utterances retain repeated wording. Replayed native final results are
  identified by recognition run and result index, independently of their text.
- Native transcription restarts use a fresh run identity. Recognition from a
  retired native instance cannot change the current capture.
- Native finals append to the transcript. Overlap reconciliation requires explicit
  cumulative-provider metadata and compares Unicode letters, marks and numbers.
- Cloud Chat and shared editor acceptance preserve non-Latin and single-character
  speech. Existing confidence thresholds, filler and output-echo policy remain.
- Interrupt/Stop detach and abort the current request. Late results cannot clear a
  newer request, change its conversation identity, submit during context preparation,
  or start obsolete playback. Reader cancellation also settles a stalled stream.
- Failed, timed-out, empty and rejected submissions have a defined recovery path.
  Request deadlines are 120 seconds; browser playback has a bounded watchdog.
- HomeServer Brain emits request-specific started/completed/failed/cancelled events.
  Conversation voice speaks the returned reply rather than counting DOM messages.
  Equal replies on different requests remain playable; repeated completion is inert.
- HomeServer output is bound to a playback generation. Stop aborts its synthesis
  request, retires browser/native callbacks and cancels its owned chat turn.
  Strict Local continues to block browser speech fallback.
- Sidebar/history failure cannot invalidate a successful reply or disable the
  composer indefinitely. Earlier history refreshes cannot replace a later turn.

The software target is 10/10 when these cases, retained Section 1 ownership checks,
existing relevant contracts and exact-head required CI are green. A test score is
not installed-device hardware certification.

## Validation

`node tests/listening-section2.mjs` runs 20 Cloud / 11 HomeServer behavioral cases,
including real controller functions with deterministic synthetic browser events,
transport failures, cancellation, Unicode and repeated utterances. Section 1's
19 Cloud / 9 HomeServer ownership/Stop cases remain active. The focused Section 2
suite runs in the existing Cloud Recovery Baseline and HomeServer PR Core/Windows
build workflows; historical matrices have not been duplicated.

One older optional `chat-voice-runtime.mjs` fails its already-obsolete v142 build
marker on the Section 1 baseline. It is not a required CI gate. Its relevant
behavior is exercised against the current controllers in the focused suites.

## Installed acceptance after deployment

On the installed Windows HomeServer and Cloud browser, speak identical sentences
in consecutive turns; verify both are retained and both answers play. Exercise a
non-Latin utterance supported by the installed recognition model. Interrupt an
answer, start a fresh turn and confirm no obsolete audio or reply changes its state.
Repeat with provider failure, browser microphone denial/disconnection, empty reply,
and failed history refresh. Verify Talk resumes after completion/error and Stop
releases capture/audio; Strict Local never switches to browser speech services.
These microphone, browser-permission, provider and hardware exercises remain owner
acceptance. Video meeting and participant identity review are later sections.

## Remaining coordinated review plan

1. Capture ownership and Stop — complete, merged and packaged.
2. Listening and conversation correctness — this section.
3. Continuous capture and transcription processing.
4. Transcript document integrity.
5. Participant tracking and identity.
6. Video meeting reliability.
7. Cloud/HomeServer transfer and retention.
8. Agent Chat, Brain and Knowledge integration.
9. Missing transcription and meeting capabilities.
10. Performance and release acceptance.

A subsequent section starts only after this section is green, merged and its
merged-main Cloud/Windows deployment artifacts have been verified.
