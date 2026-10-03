# Interactive Agent review — Section 3: Continuous capture and transcription processing

Repairs use the existing Cloud listening controller, HomeServer Dictate capture,
local transcription workspace and managed Whisper service. No database migration.

## Changes and software acceptance

- HomeServer transcription keeps its microphone/context open across segment rotation.
  Whisper processing is serialized while the next segment records. Quiet intervals
  rotate capture without stopping the session. One-shot Dictate and half-duplex Agent
  Conversation retain their existing behavior.
- Audio backlog is bounded to six segments or 8 MiB, including the processing segment;
  individual encoded segments are limited to 2 MiB. Overload or microphone loss stops
  capture with an explicit error. Stop cancels unsent/in-flight audio and generation
  guards prevent late text from entering another run. Previously saved text remains.
- HomeServer browser transcription resumes after normal end/no-speech. Native final
  identity uses run plus result index; repeated utterances survive a new native run.
- Frontend Whisper conversion/request/body processing has a 100-second deadline.
  The managed backend runs outside the async control event loop, permits one Whisper
  child per process, and rejects concurrent processing with 429. Deadline (90 seconds),
  request disconnect or handler cancellation kills/reaps the child and cleans private
  temporary audio. PCM WAV validation checks samples, truncation and 120-second duration.
- HomeServer document saves retain a failed segment with its original idempotency key,
  retry in order every four seconds, cap the unsaved backlog at 200 text segments and
  apply a 30-second request/body deadline. Completion waits until all accepted text
  saves succeed. Failed saves never produce a completed/saved success message.
- Cloud saves preserve batch ordering and keys, apply a 30-second deadline and use
  4/8/16/30-second failure backoff without immediate retry storms. Queue flush scheduling
  cannot be postponed indefinitely by frequent finals. Unsaved backlog caps at 500;
  saturation and backup quota failure stop capture with accepted text retained.
- Cloud optional retained audio automatically closes a clip near 32 MiB while live
  transcription continues. Missing recorder Stop and stalled upload settle after five
  and 120 seconds, release memory/media and report failure without claiming a saved clip.

## Validation

Run `node tests/transcription-section3.mjs` in either repository (six Cloud/eight
HomeServer behavioral cases). HomeServer also runs
`python tests/transcription_processing_section3.py` (five cases using real disposable
synthetic child processes). Retain Sections 1/2 acceptance, existing voice UI/runtime
contracts, owner authorization/privacy tests and exact-final-head required CI.
The new checks run in existing PR/release workflows.

## Installed acceptance and limits

On the Windows HomeServer, verify a long local transcription containing quiet periods
and speech during Whisper processing, Stop during inference, microphone removal,
slow processing, reconnection after a failed text save and normal Conversation/Dictate.
In Cloud, test offline/online save recovery and a retained clip limit. Record observable
missing words/latency and processor utilization using the real microphone/provider.
MediaRecorder rotation and browser recognition restarts are best-effort: software
fixtures cannot certify gapless hardware/browser capture. No physical certification
or synthetic claim of accuracy is made.

Keep HomeServer's page open while unsaved text retries: its current backlog remains
in page memory. Cloud already backs up pending text in browser storage; quota failure
requires keeping the page open. Persistent recovery across HomeServer reloads,
document timeline integrity, participant identity, meeting delivery, cross-system
retention and model throughput optimization remain their scheduled review sections.

Next: Section 4 — Transcript document integrity.
