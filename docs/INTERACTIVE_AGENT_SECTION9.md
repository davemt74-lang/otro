# Interactive Agent — Section 9: missing conversation, transcription and meeting capabilities

## 9A — canonical attribution

9A is merged. HomeServer and Cloud share the same speaker-evidence vocabulary.
Acoustic clustering and provider diarization may separate turns but cannot name
a person. LiveKit tracks are already isolated media channels and retain their
canonical participant-track attribution. Conflicting identity evidence fails
closed; no speaker evidence is an authentication factor.

## 9B — shared-microphone diarization

Persistent HomeServer transcription keeps local Whisper as the reliability
baseline. The transcription workspace now offers an explicit Enhanced speaker
separation option. When enabled, each closed VAD chunk is converted to the
existing 16 kHz PCM WAV form and sent transiently to ElevenLabs Scribe v2 with
batch diarization, word timestamps, speaker-library matching and provider
logging/history disabled.

The provider response is reduced to bounded turns. Raw speaker IDs are hashed
inside canonical evidence and are not persisted or relayed. Speaker-library
results remain unverified speaker separation until a later trusted participant
mapping re-verifies them. If Scribe fails, the same chunk is transcribed with
local Whisper and the UI reports that speaker separation degraded; text capture
continues.

Per-segment speaker label, end time, overlap state and sanitized canonical
attribution are stored atomically beside the existing transcript segment. The
legacy transcript schema stays compatible. Retries preserve the first committed
text/attribution, and deletion cascades the side metadata.

Strict Local mode blocks the cloud-assisted diarization path. HomeServer-to-Cloud
sharing remains completed-session, explicit, revocable and text-only.

## Acceptance

Focused tests cover provider parsing, grouping, overlap, malformed timing, no raw
speaker-ID leakage, immutable persistence/retry behavior, sharing/revocation,
cascade deletion, opt-in disclosure, Strict Local behavior and local fallback.
The retained transcription, transfer, meeting and canonical attribution gates
must remain green before merge.

## Remaining Section 9 work

9C: continuous camera/voice fusion and trusted local participant matching.
9D: meeting/recording integration, overlap-aware intelligence and correction
propagation. Hardware accuracy and installed-device certification stay in
Section 10.
