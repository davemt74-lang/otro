# Voice and provider reliability audit — 17 October 2026

Scope: native Agent Chat conversation mode, local Whisper/Piper and permitted browser fallback. Keep the minimal canvas and existing right-side Brain controls.

## Confirmed findings

- Piper preparation shared a text-length playback watchdog. A short reply could be discarded after 15 seconds even though server synthesis permits 60 seconds. Preparation now has its own 65-second deadline; successful audio starts a separate duration-based playback watchdog.
- A stalled local request silently returned to listening. It now aborts preparation and uses browser speech only when permitted. Strict Local stops without fallback. A new output generation makes late fetch/decode completion inert.
- Browser speech errors and stalled audible output silently resumed listening. Conversation now pauses with a short message pointing to the reply in chat. Normal completion still resumes once. No inference request or tool is replayed.

## Acceptance coverage

`node tests/listening-section2.mjs` checks distinct/duplicate completions, late local synthesis, Strict Local, a hung synthesis request that ignores abort, fallback exactly once, failed browser playback, playback deadline, recognition startup/failures, draft preservation, provider timeout, Stop, canonical reply/history ownership and navigation. `node tests/capture-ownership-section1.mjs` covers pending permissions, capture leases, startup cancellation and transcription Stop.

`HOMESERVER_TEST_PYTHON=<python> node tests/chat_workspace_browser.mjs` exercises the complete UI against an isolated real HomeServer, controlled model/speech services and a runtime restart. It covers voice routing, one reply/listening restart, draft/conversation/Agent restoration, binding races, offline behavior, owner expiry, mobile layout and long histories.

Additional provider boundaries: `tests/inference_cancellation_v040.py` verifies provider client cancellation and cancelled action/message cleanup; `tests/haiku55_provider.py` checks configured provider selection and request adaptation; `tests/voice_profile_execution_v235.py` checks profile-safe inference/voice execution; `tests/local_voice_v041.py` checks the local runtime; `tests/agent_routing_v047.py` checks selected Agent and voice routing. These are controlled tests, not live credential verification.

The Cloud companion audit adds canonical cancellation propagation, rejection of streams without a successful completion event, authentication status preservation and bounded ElevenLabs readiness/tickets/playback. Its evidence and release references are in the Cloud repository's document of the same name.

## Installed-machine acceptance remains open

On the installed Windows machine, record OS/browser, configured provider/model, selected Agent/voice and result for:

1. Allow and deny microphone permission; verify a clear stopped/retry state.
2. Ask a question using the configured inference provider and hear the selected Agent voice once.
3. Use installed Whisper and Piper with Strict Local enabled; disconnect/fail a service and verify no browser fallback.
4. Disable Strict Local and verify the permitted fallback is disclosed and occurs once.
5. Stop while transcribing, thinking and speaking; check history/draft before any explicit resend.
6. Complete a normal answer and confirm one listening restart, correct output device and acceptable echo behavior.

Repeat the Cloud voice smoke test after deploying its companion package. Automated fixtures cannot certify real microphone drivers, provider credentials, native speech services, speaker routing or room acoustics. No schema migration, permanent status panel or automatic resend is introduced.
