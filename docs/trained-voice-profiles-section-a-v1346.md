# Voice upgrade — Section A: trained local profiles (v1346)

This section replaces the optional matching engine with actual trained WavLM speaker embeddings. It preserves the existing lightweight profiles, microphone owners, Stop controls, participant IDs, account permissions, provider routes and text-only participant sync. It does not implement simultaneous-speaker separation; that remains a later gated section.

## Functional acceptance score

The implementation score measures these ten acceptance criteria, not recognition accuracy or a probability of identity. Each criterion must pass before the section is scored 10/10. PR checks, merge checks and deployment package verification remain release gates after local acceptance.

1. Model revision, six asset sizes and SHA-256 digests are fixed; model and runtime bytes are verified before execution, including the WASM loader.
2. The optional component uses one install action and the existing HomeServer Local Apps installer; the base installer contains no model or new inference runtime binaries.
3. Real native and real Chromium/WASM inference produce valid normalized 512-dimensional speaker embeddings.
4. Trained profiles have explicit enrollment provenance, a model revision and bounded samples, and persist separately from existing acoustic/visual profiles and ordinary participant notes.
5. Enrollment rejects too-short, silent, clipped and steady-noise captures. Caller-reported overlap is rejected. This quality gate is not presented as automatic overlap detection.
6. Three current samples, a conservative similarity threshold and a runner-up margin are required. Unknown, ambiguous, malformed or wrong-model profiles remain unassigned. Similarity is bounded to its valid numerical range and is never described as percentage accuracy.
7. Stop cancels inference promptly; timeouts, page hiding, Disable and interrupted installation fence late results. No cancelled operation can enable matching or write a profile.
8. Existing owner/account boundaries remain in place. No enrollment audio is retained, no second microphone is opened, no audio is posted for remote inference, and biometric features do not enter participant text synchronization.
9. The canonical voice guard uses the trained engine only when selected and preserves one-use participant context. A slow uncertain result remains marked as a voice turn, preventing fallback to a manually selected typed participant.
10. Existing Chat, listening, participant storage/synchronization, voice, Local Apps and speaker-fusion regressions remain green.

Local functional acceptance: **10/10**. The 14-case real-model browser suite, 16-case transcription lifecycle suite, 17 owner/asset route cases, 11 core validation cases, both account adapters, and the existing Cloud/HomeServer regression baselines pass. This score does not replace the remaining PR, main-branch and deployment artifact gates.

## Model and optional runtime

The model is `Xenova/wavlm-base-plus-sv`, revision `e61029603001bd11295c36d878698708bf59190f`, using the quantized ONNX file (101,683,453 bytes). The fixed Transformers.js 3.8.1 runtime and ONNX WASM files bring the optional download to approximately 124 MB. The worker verifies bytes and executes the verified runtime through local blob modules. It disables remote model resolution. No Hugging Face hosted inference API is used.

HomeServer obtains the component through the existing trusted, SHA-pinned Local Apps installer and serves only the six fixed assets through its owner-only control gateway. Cloud installs the same public pinned assets into that browser's local cache. Subsequent worker initialization uses the verified cache; missing or corrupted files require repair rather than silent remote substitution. Account-private participant samples remain in their existing local IndexedDB stores.

The existing voice-profile controls supply the participant and explicit enrollment consent. Trained matching is optional and separately selected. It requires three clean phrases of at least three seconds each; profile clearing removes trained and lightweight voice features while retaining visual samples, names and notes. Existing profiles are never silently converted into trained embeddings. HomeServer transcription uses the same selected engine and canonical profile store; reported overlap remains unassigned, and Stop, profile clearing or a matching-engine change invalidates pending enrollment and speaker attribution.

## Measured validation and limits

The real-browser acceptance test downloads fixed public voice fixtures and runs the actual ONNX model in a worker. It checks held-out recordings from two speakers and an unrelated third speaker, unknown rejection, quality gates, canonical guard routing, three-sample enrollment, cancellation, cached restart with the model server offline, installation UI and no microphone/audio-upload side effects. Test audio and model binaries are not committed to the repositories or deployment ZIPs.

On the development runner, native inference took about 0.2–0.4 seconds for short clips, while Chromium/WASM took roughly 1.5–2 seconds; an eight-second test clip took roughly 3.5 seconds. These are test-runner measurements, not a latency promise for every user computer. The small fixture set is a regression gate, not a population-wide accuracy benchmark. Physical microphones, rooms, accents, similar voices, replayed audio and overlapping speech still require broader evaluation. Recognition remains provisional conversational context and grants no login, identity verification or tool authority.

## Subsequent sections

Section B improves participant tracking and active-speaker highlighting. Section C adds measured overlap detection and two-speaker separation. Neither begins until the prior section is green and merged. No section may claim perfect separation from one microphone or substitute forced single-speaker labels for actual recovered audio.
