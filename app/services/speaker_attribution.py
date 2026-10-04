from __future__ import annotations

import hashlib
from typing import Any

CONTRACT = "speaker-attribution-v1-20261004"
SOURCES = {
    "unknown", "heuristic_acoustic", "provider_diarization", "verified_voice",
    "livekit_track", "manual_correction", "visual_corroboration", "account_identity",
}


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[: max(0, int(limit))]


def _confidence(value: Any) -> float:
    try:
        return round(max(0.0, min(1.0, float(value))), 4)
    except (TypeError, ValueError):
        return 0.0


def _hash_ref(value: Any) -> str:
    text = _text(value, 190)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24] if text else ""


def _identity_key(item: dict[str, Any]) -> str:
    participant_id = max(0, int(item.get("participant_id") or 0))
    if participant_id:
        return f"participant:{participant_id}"
    identity = _text(item.get("participant_identity"), 160)
    return f"identity:{identity}" if identity else ""


def evidence(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    source = str(raw.get("source") or "unknown").strip().lower()
    if source not in SOURCES:
        source = "unknown"
    participant_id = max(0, int(raw.get("participant_id") or 0))
    participant_identity = _text(raw.get("participant_identity"), 160)
    label = _text(raw.get("speaker_label"), 190)
    identity_capable = source in {"verified_voice", "livekit_track", "manual_correction", "account_identity"}
    if not identity_capable and source != "visual_corroboration":
        participant_id = 0
        participant_identity = ""
    # Visual evidence may carry the same opaque participant reference as a
    # voice/manual source so fusion can corroborate or conflict. It is never
    # identity-capable by itself.
    identity_verified = bool(identity_capable and (participant_id or participant_identity))
    return {
        "source": source,
        "speaker_label": label,
        "confidence": _confidence(raw.get("confidence")),
        "participant_id": participant_id,
        "participant_identity": participant_identity,
        "identity_verified": identity_verified,
        "authentication_authority": False,
        "overlap": bool(raw.get("overlap")),
        "overlap_group": _text(raw.get("overlap_group"), 80),
        "provider_ref_hash": _hash_ref(raw.get("provider_speaker_id")),
        "track_ref_hash": _hash_ref(raw.get("track_id")),
        "observed_at": _text(raw.get("observed_at"), 40),
    }


def _rank(source: str) -> int:
    return {
        "manual_correction": 700,
        "account_identity": 650,
        "verified_voice": 600,
        "livekit_track": 550,
        "provider_diarization": 350,
        "heuristic_acoustic": 150,
        "unknown": 0,
    }.get(source, -1)


def fuse(raw_evidence: list[dict[str, Any]] | None) -> dict[str, Any]:
    primary_evidence: list[dict[str, Any]] = []
    visual: list[dict[str, Any]] = []
    for row in list(raw_evidence or [])[:16]:
        if not isinstance(row, dict):
            continue
        item = evidence(row)
        (visual if item["source"] == "visual_corroboration" else primary_evidence).append(item)

    primary_evidence.sort(key=lambda item: (_rank(item["source"]), item["confidence"]), reverse=True)
    manual = [
        item for item in primary_evidence
        if item["source"] == "manual_correction" and item["identity_verified"] and item["confidence"] >= 0.72
    ]
    conflict_pool = manual or primary_evidence
    strong = {
        _identity_key(item)
        for item in conflict_pool
        if item["identity_verified"] and item["confidence"] >= 0.72 and _identity_key(item)
    }
    identity_conflict = len(strong) > 1
    primary = primary_evidence[0] if primary_evidence else evidence()
    if identity_conflict:
        primary = evidence(
            {
                "source": "unknown",
                "speaker_label": primary["speaker_label"],
                "overlap": primary["overlap"],
                "overlap_group": primary["overlap_group"],
            }
        )

    key = _identity_key(primary)
    visual_corroborated = False
    visual_conflict = False
    for item in visual:
        visual_key = _identity_key(item)
        if key and visual_key == key and item["confidence"] >= 0.58:
            visual_corroborated = True
        elif key and visual_key and visual_key != key and item["confidence"] >= 0.78:
            visual_conflict = True

    identity_confidence = primary["confidence"] if primary["identity_verified"] else 0.0
    if visual_corroborated:
        identity_confidence = min(1.0, identity_confidence + 0.05)
    if visual_conflict:
        # A strong camera mismatch never proves the camera is right, but it is
        # enough to fail a voice identity closed until another turn/review.
        identity_confidence = 0.0

    overlap = bool(primary["overlap"])
    overlap_group = str(primary["overlap_group"])
    for item in primary_evidence:
        if item["overlap"]:
            overlap = True
            if not overlap_group and item["overlap_group"]:
                overlap_group = str(item["overlap_group"])

    source = str(primary["source"])
    evidence_sources = {str(item["source"]) for item in primary_evidence}
    if "livekit_track" in evidence_sources:
        diarization_source = "livekit_track"
    elif "provider_diarization" in evidence_sources:
        diarization_source = "provider_diarization"
    elif "heuristic_acoustic" in evidence_sources:
        diarization_source = "heuristic_acoustic"
    else:
        diarization_source = "none"

    return {
        "contract": CONTRACT,
        "speaker_label": primary["speaker_label"] or "Speaker",
        "source": source,
        "confidence": primary["confidence"],
        "participant_id": 0 if (identity_conflict or visual_conflict) else primary["participant_id"],
        "participant_identity": "" if (identity_conflict or visual_conflict) else primary["participant_identity"],
        "speaker_identity_verified": bool(not identity_conflict and not visual_conflict and primary["identity_verified"]),
        "identity_confidence": round(identity_confidence, 4),
        "authentication_authority": False,
        "visual_corroborated": visual_corroborated,
        "visual_conflict": visual_conflict,
        "identity_conflict": identity_conflict,
        "overlap": overlap,
        "overlap_group": overlap_group,
        "diarization_source": diarization_source,
        "evidence": [*primary_evidence, *visual],
    }
