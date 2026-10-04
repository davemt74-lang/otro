"""Bounded speaker context for meeting intelligence; never authentication."""
from __future__ import annotations
import hashlib
import json
from typing import Any

_SOURCES = {"unknown", "heuristic_acoustic", "provider_diarization", "verified_voice", "livekit_track", "manual_correction", "account_identity"}

def attribution(raw: Any, label: str, *, overlap: bool = False) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    source = str(raw.get("source") or "unknown")
    if source not in _SOURCES:
        source = "unknown"
    overlap = overlap or raw.get("overlap") is True
    verified = raw.get("speaker_identity_verified") is True and source in {"verified_voice", "livekit_track", "manual_correction", "account_identity"}
    if source == "verified_voice" and overlap:
        verified = False
    identity = str(raw.get("participant_identity") or "")[:160] if verified else ""
    participant = raw.get("participant_id")
    participant = participant if type(participant) is int and participant > 0 and verified else 0
    verified = bool(verified and (identity or participant))
    if raw.get("identity_conflict") or raw.get("visual_conflict"):
        identity, participant, verified = "", 0, False
    return {
        "contract": "speaker-attribution-v1-20261004", "source": source,
        "speaker_label": label[:190], "speaker_identity_verified": verified,
        "participant_identity": identity, "participant_id": participant,
        "authentication_authority": False, "overlap": overlap,
        "diarization_source": str(raw.get("diarization_source") or "none")[:40],
    }

def normalize(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = [dict(row) for row in segments]
    for row in result:
        row["speaker_attribution"] = attribution(row.get("speaker_attribution"), row["speaker_name"], overlap=row.get("overlap") is True)
    for index, row in enumerate(result):
        for other in result[index + 1:]:
            if row["start_ms"] < other["end_ms"] and other["start_ms"] < row["end_ms"]:
                a, b = row["speaker_attribution"], other["speaker_attribution"]
                # Same known participant's duplicate/continued track is not a second speaker.
                same = a["speaker_identity_verified"] and b["speaker_identity_verified"] and (a["participant_identity"], a["participant_id"]) == (b["participant_identity"], b["participant_id"])
                if not same:
                    row["overlap"] = other["overlap"] = True
    for row in result:
        row["speaker_attribution"] = attribution(row["speaker_attribution"], row["speaker_name"], overlap=row.get("overlap") is True)
        row["overlap"] = row["speaker_attribution"]["overlap"]
    return result

def digest(segments: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(segments, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()

def transcript_text(segments: list[dict[str, Any]]) -> str:
    lines = []
    for row in segments:
        seconds = int(row["start_ms"]) // 1000
        evidence = row["speaker_attribution"]
        flags = []
        if evidence["overlap"]:
            flags.append("overlapping speech")
        if not evidence["speaker_identity_verified"]:
            flags.append("identity unverified")
        if evidence["source"] == "manual_correction":
            flags.append("owner annotation")
        suffix = " [" + "; ".join(flags) + "]" if flags else ""
        lines.append(f"[{seconds // 60:02d}:{seconds % 60:02d}] {row['speaker_name']}{suffix}: {row['text']}")
    return "\n".join(lines)

PROMPT_RULE = " Preserve overlapping speech and uncertain identities. A speaker label is not proof of identity. Do not infer an action owner or commitment from overlapping or unverified attribution; use explicit words naming the owner, otherwise leave ownership unassigned. Owner annotations are reviewed labels, never authentication."
