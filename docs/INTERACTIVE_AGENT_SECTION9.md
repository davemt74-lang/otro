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

## 9C — local voice profiles and camera/voice fusion

HomeServer now reuses the canonical Tracky participant database for local speaker
identity. Voice recognition is explicit opt-in: enrollment is armed for one
selected local participant and requires at least three clean speech samples.
Each transient PCM turn is reduced in the browser to a bounded normalized
acoustic feature vector. Only those numeric features and sample metadata are
saved to the existing participant record; raw enrollment audio is never stored.
Legacy profiles do not match unless voice recognition was explicitly enabled.

During transcription, a non-overlapping turn may match an enabled local voice
profile only when the best score meets the software threshold and separates
from the second-best profile by the required margin. The result enters the
canonical Section 9 evidence contract as verified_voice, but remains explicitly
non-authenticating. Overlapping speech cannot establish voice identity.

Camera corroboration is separately owner-started. It uses the existing pinned
Tracky Human model and existing local visual embeddings. Camera frames and face
descriptors stay in the browser. A visual observation contributes evidence only
when exactly one face is visible and that face has one unambiguous local match.
Visual evidence alone never establishes the speaker. A matching visual
observation raises corroboration; a strong conflicting visual match clears the
voice identity and leaves the transcript text intact.

Local transcript persistence stores the canonical fused identity for owner-local
use. Direct identity-capable attribution is rejected unless it arrives through
the bounded evidence-fusion API. Retries cannot mutate the first committed
identity. Completed explicit HomeServer-to-Cloud sharing strips Tracky
participant references, verified-identity flags and camera evidence before the
paired relay, while preserving generic speaker labels, diarization timing and
overlap. Cloud therefore cannot inherit a browser-local biometric claim.

Camera and voice-profile state stop on the existing privacy/lifecycle
boundaries: camera disconnect, drawer close, page hide, transcription finish
and navigation. Voice-profile clearing deletes the stored local feature vectors.

## 9C acceptance

Focused tests cover feature determinism, three-sample readiness, explicit
recognition consent, top-two ambiguity rejection, canonical API validation,
voice/camera corroboration, strong cross-modal conflict, visual-only
non-identity, overlap identity rejection, immutable retries, owner-local
identity persistence and paired-relay identity stripping. Installed microphone,
room-noise, speaker-distance, spoofing and camera accuracy remain Section 10
device certification rather than a software-only claim.

## Remaining Section 9 work

9D: meeting/recording integration, overlap-aware intelligence and correction
propagation. Hardware accuracy and installed-device certification stay in
Section 10.
