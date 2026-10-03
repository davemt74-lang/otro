# Interactive Agent Section 1: capture ownership and Stop

This section repairs capture lifecycle in the existing Cloud and HomeServer owners. It does not create a second conversation engine, transcription store or native device authority.

## Behavior

- Chat, transcription, Stem/Video editor voice and meetings participate in the existing same-origin browser voice lease. HomeServer Talk and Dictate/transcription load the same contract for local capture, including browsers without SpeechRecognition. A configured lease that cannot access storage fails closed.
- Capture tickets bind a pending acquisition and its streams to its owner. Stop, page exit or an observed ownership transfer disposes late streams; an older response cannot overwrite a newer capture. Shared preference changes no longer enable another tab's listener automatically.
- Explicit Cloud capture takeover asks the user before switching. HomeServer reports a busy capture domain and requires the existing surface to stop; the contract also exposes explicit yield/takeover operations.
- Cloud Stop discards the unfinished speech preview and saves accepted segments. Cloud Finish explicitly includes that preview before stopping. Optional retained recording was already explicitly started by its owner; stopping capture releases recording tracks before upload completes.
- The existing Transcription Editor registry exposes separate Stop and Finish commands, each verified against the canonical capture state. Capture state reports its owner and browser-default recognition input.
- Cloud's microphone selection controls retained recording and input tests. Browser SpeechRecognition and its level meter use the browser default microphone; the UI states this boundary. HomeServer's local capture continues to use Voice Settings constraints and existing strict-local policy.
- Meeting authorization, connection, microphone/camera toggles and device switches are tied to their join generation. Leave cancels pending startup and releases late local tracks. This does not repair server room termination or final transcript delivery; those are later sections.
- HomeServer Stop aborts an in-flight browser request to local STT and rejects late results. HTTP cancellation does not certify immediate termination of an already running server inference process; shared server scheduling/cancellation is Section 3 work.

## Scope and evidence

The lease is cooperative coordination within the same browser origin and configured account/capture scope. It does not arbitrate unrelated origins, another browser, native processes, or all physical devices. Existing native device authority remains independent. Real browser scheduling, hardware release latency, permission UI and LiveKit behavior require installed acceptance; synthetic tests do not certify them.

Run `node tests/capture-ownership-section1.mjs` from either repository. The same test source covers the shared contract plus 18 Cloud or nine HomeServer behavioral cases. It executes original controllers with synthetic browser/media/network boundaries; meeting rendering is substituted to isolate the lifecycle. These are positive acceptance assertions, not defect assertions from the earlier audit.

Existing local checks cover current transcription capture/workspace contracts, v2.44 voice hardening, conversation consolidation, the Transcription Editor registry, and HomeServer dictation/conversation UI contracts. Two exploratory historical contracts (`voice-debug-single-controller-v134.mjs` and `voice-three-of-three-v157.mjs`) also fail on the untouched reviewed baseline; their obsolete build/upstream-readiness assertions are not treated as regressions introduced here. Required CI remains authoritative before merge.

The focused behavioral test runs in Cloud Recovery Baseline, HomeServer PR Core, and the merged-main Windows HomeServer CI. Cloud additionally lints changed PHP entry points. Do not start Section 2 before exact-head checks, merge, merged-main checks and final deployment artifacts are complete.

## Installed acceptance

1. On Cloud Chat, begin microphone permission, press Stop, then allow the pending request. Confirm no voice mode or monitor remains active. Repeat with recognition permission blocked and with the compatibility fallback.
2. On Cloud Transcriptions, begin retained-recording permission, press Stop, then allow. No recording may begin. With recording already running, Stop must release its tracks while a deliberately slow upload is pending.
3. Speak a partial sentence: Stop discards the unfinished preview; Finish includes it and stops. Repeat through the Agent's Stop/Finish commands. Previously accepted text remains saved under the existing draft policy.
4. In two Cloud tabs, acquire capture in one and explicitly switch to the other. The first releases its owned streams and does not restart through shared preferences. Repeat navigation/closing and expiry recovery. Test Stem and Video voice too.
5. In HomeServer tabs, verify Talk versus Dictate/transcription exclusion, pending permission cancellation, a new capture completing before an older cancelled request, strict-local failure and Voice Settings changes. A cancelled STT response must not populate the composer, transcript or Agent Chat.
6. Join a meeting with delayed authorization or microphone permission, then Leave. Any late local track must stop. Repeat camera enable, microphone toggle, screen-share startup and device switching during Leave/page exit.
7. Select two distinct microphones for Cloud recording/tests and browser recognition. Verify recording/testing uses the selection and the UI accurately describes browser-default recognition. Verify HomeServer local capture uses its configured selected device.

Record browser/OS and installed build revisions, observed active tracks and device indicators, Stop-to-release time, errors and recovery. Do not label missing installed evidence as a hardware pass.
