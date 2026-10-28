# Voice upgrade — Section B: participant tracking and speaker highlighting (v1347)

Section A is merged in Cloud (`f86d776`) and HomeServer (`ccb1190`). This section improves the existing local camera tracker and connects its boxes to the canonical voice-attribution path. It uses the existing camera, microphone and selected voice model. There is no new model, runtime download, database migration or recognition enrollment.

## Behavior

The tracker makes one frame-wide, one-to-one assignment before changing any tracks. A detection and an existing track must each be the other's unique best candidate. Bounded motion prediction, appearance checks and box-size checks support normal movement and short detection gaps. Reordered detections cannot steal another track. Ambiguous crossings start unassigned tracks instead of transferring an introduced name. A gap of 4.5 seconds starts a new track; the existing eight-second departure window remains. Reacquisition before an arrival resets the arrival dwell after a gap longer than two seconds.

Camera boxes retain their DOM elements while a track moves. Blue means tracked. Green identifies the most recently matched voice only when exactly one current visible participant agrees with that voice. The label explicitly says **recent voice match**, expires after at most three seconds, and does not claim continuous real-time speech detection. A turn-based matcher cannot establish who is speaking before it finishes processing a phrase. Unknown faces are not assigned an off-camera voice identity. Reduced-motion settings disable box movement transitions.

Reordering the same group does not change the canonical presence revision or discard a pending voice turn. Real participant changes still invalidate it. Popup speaker results include the camera snapshot and original match time; the parent checks its current snapshot, correction state and bounded age before highlighting anything. An older concurrent voice check cannot overwrite a newer turn. Stop, a new unmatched turn, correction, forgetting, a thread change, hiding the page or losing camera evidence clear the highlight. Speaker context is published only after the participant-note lookup finishes and cancellation is rechecked.

Arrival, departure, correction and matched voice-turn summaries continue through the existing private Brain timeline. Geometry, frames, audio, visual embeddings and trained voice embeddings do not enter that event stream. Track IDs and provisional recognition grant no login or tool authority. The approximately 124 MB Section A component remains optional and separate from the base installer.

## Functional acceptance

These criteria measure implemented behavior, not biometric accuracy. Release gates remain separate from local acceptance.

1. Reordered detections preserve established track IDs, names and pending voice context.
2. Movement uses bounded prediction and preserves the existing box element.
3. Brief gaps reacquire a visit; long gaps start a new track without inheriting an introduced identity.
4. Contested observations, ambiguous crossings and incompatible appearances/sizes cannot steal a track.
5. One verified current voice highlights one uniquely agreeing visible participant; unnamed, conflicting and duplicate identities remain unhighlighted.
6. The UI labels recent evidence accurately and expires it within three seconds without needing another camera frame.
7. Popup results require the exact current camera snapshot, original timestamp and existing origin/window checks.
8. Stop, corrections, forgetting, hidden pages, thread changes and newer turns fence pending attribution and clear highlights.
9. Existing capture owners, recognition consent, one-use participant context and summary-only event boundaries remain intact.
10. Core, browser, trained-model, listening, participant and complete Chat regressions pass on both corresponding surfaces; applicable PR/main checks and verified packages are required before release.

Tests include 39 tracker/core cases and 18 new Chromium integration cases using controlled camera/voice fixtures. The existing real-model suite exercises actual WavLM/ONNX inference. Camera fixtures verify lifecycle and assignment rules; they do not measure real-room tracking, face recognition or noisy overlapping speakers. Physical camera/microphone evaluation is still required for those accuracy claims.

## Subsequent section

Section C adds measured overlap detection and two-speaker separation after Section B passes and merges. This build does not add overlap detection, lip-reading or simultaneous-speaker separation.
