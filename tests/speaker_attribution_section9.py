from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import speaker_attribution as attribution


def check(ok: bool, message: str) -> None:
    if not ok:
        raise AssertionError(message)


cases = 0

f = attribution.fuse([{"source": "heuristic_acoustic", "speaker_label": "Speaker 2", "confidence": .93, "participant_id": 99}])
check(f["participant_id"] == 0 and not f["speaker_identity_verified"], "heuristic claimed identity")
cases += 1

f = attribution.fuse([{"source": "provider_diarization", "speaker_label": "Speaker 3", "confidence": .91, "provider_speaker_id": "secret-provider-id"}])
check(f["diarization_source"] == "provider_diarization" and not f["speaker_identity_verified"], "diarization claimed identity")
check("secret-provider-id" not in str(f), "provider ID leaked")
cases += 1

f = attribution.fuse([
    {"source": "verified_voice", "speaker_label": "Owner", "participant_id": 7, "confidence": .91},
    {"source": "visual_corroboration", "participant_id": 7, "confidence": .88},
])
check(f["participant_id"] == 7 and f["visual_corroborated"] and f["speaker_identity_verified"], "voice/visual fusion failed")
check(f["identity_confidence"] > .91 and not f["authentication_authority"], "authority boundary failed")
cases += 1

f = attribution.fuse([{"source": "visual_corroboration", "speaker_label": "Owner", "participant_id": 7, "confidence": .99}])
check(f["participant_id"] == 0 and f["source"] == "unknown" and not f["speaker_identity_verified"], "visual-only speaker identity accepted")
cases += 1

f = attribution.fuse([
    {"source": "verified_voice", "speaker_label": "Speaker 1", "participant_identity": "tracky:owner-1", "confidence": .94},
    {"source": "visual_corroboration", "participant_identity": "tracky:owner-1", "confidence": .91},
])
check(f["participant_identity"] == "tracky:owner-1" and f["visual_corroborated"] and f["speaker_identity_verified"], "opaque local voice/visual fusion failed")
cases += 1

f = attribution.fuse([
    {"source": "verified_voice", "speaker_label": "Speaker 1", "participant_identity": "tracky:owner-1", "confidence": .96},
    {"source": "visual_corroboration", "participant_identity": "tracky:guest-2", "confidence": .92},
])
check(f["visual_conflict"] and not f["speaker_identity_verified"] and f["participant_identity"] == "" and f["identity_confidence"] == 0, "visual conflict did not fail closed")
cases += 1

f = attribution.fuse([
    {"source": "verified_voice", "speaker_label": "Owner", "participant_id": 7, "confidence": .94},
    {"source": "account_identity", "speaker_label": "Guest", "participant_id": 8, "confidence": .99},
])
check(f["identity_conflict"] and f["participant_id"] == 0 and not f["speaker_identity_verified"], "identity conflict did not fail closed")
cases += 1

f = attribution.fuse([{"source": "livekit_track", "speaker_label": "Guest", "participant_identity": "vp3p-abc", "track_id": "TR_x", "confidence": 1, "overlap": True, "overlap_group": "g1"}])
check(f["source"] == "livekit_track" and f["participant_identity"] == "vp3p-abc" and f["overlap"], "LiveKit attribution failed")
check("TR_x" not in str(f), "raw track reference leaked")
cases += 1

f = attribution.fuse([
    {"source": "heuristic_acoustic", "speaker_label": "Speaker 2", "confidence": .8},
    {"source": "manual_correction", "speaker_label": "Jamie", "participant_id": 4, "confidence": 1},
])
check(f["source"] == "manual_correction" and f["participant_id"] == 4, "manual correction priority failed")
cases += 1

f = attribution.fuse([
    {"source": "verified_voice", "speaker_label": "Wrong", "participant_id": 7, "confidence": .97},
    {"source": "manual_correction", "speaker_label": "Jamie", "participant_id": 4, "confidence": 1},
])
check(not f["identity_conflict"] and f["source"] == "manual_correction" and f["participant_id"] == 4, "manual correction conflicted with lower evidence")
cases += 1

print(f"SPEAKER_ATTRIBUTION_SECTION9A=PASS ({cases} canonical HomeServer cases)")
