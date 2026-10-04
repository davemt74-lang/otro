from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

from . import local_voice, provider_secrets, speaker_attribution

CONTRACT = "vp3.homeserver.speaker-diarization.v1"
MODEL = "scribe_v2"
ENDPOINT = "https://api.elevenlabs.io/v1/speech-to-text"
MAX_TURNS = 64
MAX_WORDS = 12000
REQUEST_TIMEOUT_SECONDS = 90.0
_SPEAKER = re.compile(r"^[A-Za-z0-9_.:@-]{1,190}$")


class SpeakerDiarizationError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def status() -> dict[str, Any]:
    try:
        configured = bool(provider_secrets.get_api_key("elevenlabs"))
    except provider_secrets.ProviderSecretError:
        configured = False
    return {
        "contract": CONTRACT,
        "provider": "elevenlabs",
        "model": MODEL,
        "configured": configured,
        "available": configured,
        "cloud_assisted": True,
        "audio_retained_by_homeserver": False,
        "provider_logging_requested": False,
        "diarization": True,
        "speaker_library_supported": True,
        "speaker_identity_verified": False,
        "authentication_authority": False,
    }


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0 or number > 7200:
        return None
    return number


def _speaker(value: Any) -> str:
    candidate = str(value or "").strip()
    return candidate if _SPEAKER.fullmatch(candidate) else ""


def _label(index: int) -> str:
    return f"Speaker {max(1, int(index))}"


def parse_response(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise SpeakerDiarizationError("Speaker diarization returned an invalid response.", 502)
    words = payload.get("words")
    if not isinstance(words, list):
        raise SpeakerDiarizationError("Speaker diarization returned no word timeline.", 502)
    if len(words) > MAX_WORDS:
        raise SpeakerDiarizationError("Speaker diarization returned too many word events.", 502)

    speaker_indexes: dict[str, int] = {}
    turns: list[dict[str, Any]] = []
    last_word_end = 0.0

    for item in words:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "word").strip().lower()
        if kind not in {"word", "spacing"}:
            continue
        text = str(item.get("text") or "")
        if not text:
            continue
        start = _number(item.get("start"))
        end = _number(item.get("end"))
        if start is None or end is None or end < start:
            raise SpeakerDiarizationError("Speaker diarization returned invalid word timing.", 502)
        # Provider arrays should be time ordered. Small overlap is allowed, but
        # large backwards jumps make deterministic turn attribution unsafe.
        if start + 5.0 < last_word_end:
            raise SpeakerDiarizationError("Speaker diarization returned an invalid word order.", 502)
        last_word_end = max(last_word_end, end)

        speaker_id = _speaker(item.get("speaker_id"))
        if not speaker_id and kind == "spacing" and turns:
            speaker_id = str(turns[-1]["_speaker_id"])
        speaker_id = speaker_id or "unidentified"
        if speaker_id not in speaker_indexes:
            speaker_indexes[speaker_id] = len(speaker_indexes) + 1
        label = _label(speaker_indexes[speaker_id])

        if turns and turns[-1]["_speaker_id"] == speaker_id:
            turns[-1]["text"] += text
            turns[-1]["end_ms"] = max(turns[-1]["end_ms"], round(end * 1000))
            continue

        if len(turns) >= MAX_TURNS:
            raise SpeakerDiarizationError("Speaker diarization returned too many speaker turns.", 502)
        turns.append(
            {
                "_speaker_id": speaker_id,
                "speaker_label": label,
                "text": text,
                "started_ms": round(start * 1000),
                "end_ms": round(end * 1000),
                "overlap": False,
                "overlap_group": "",
            }
        )

    cleaned: list[dict[str, Any]] = []
    for index, turn in enumerate(turns):
        text = " ".join(str(turn["text"]).split())
        if not text:
            continue
        overlap_group = ""
        if index > 0 and turn["started_ms"] < turns[index - 1]["end_ms"]:
            overlap_group = f"overlap-{index}"
            turn["overlap"] = True
            turn["overlap_group"] = overlap_group
            turns[index - 1]["overlap"] = True
            turns[index - 1]["overlap_group"] = overlap_group

    for turn in turns:
        text = " ".join(str(turn["text"]).split())
        if not text:
            continue
        raw = {
            "source": "provider_diarization",
            "speaker_label": turn["speaker_label"],
            # The provider's token log probability is not a speaker-identity
            # confidence, so no fabricated identity confidence is supplied.
            "confidence": 0.0,
            "provider_speaker_id": turn["_speaker_id"],
            "overlap": turn["overlap"],
            "overlap_group": turn["overlap_group"],
        }
        attribution = speaker_attribution.fuse([raw])
        cleaned.append(
            {
                "text": text[:8000],
                "speaker_label": attribution["speaker_label"][:80],
                "started_ms": max(0, int(turn["started_ms"])),
                "ended_ms": max(int(turn["started_ms"]), int(turn["end_ms"])),
                "overlap": bool(attribution["overlap"]),
                "attribution": attribution,
            }
        )

    return {
        "contract": CONTRACT,
        "provider": "elevenlabs",
        "model": str(payload.get("model_id") or MODEL)[:80],
        "language_code": str(payload.get("language_code") or "")[:20],
        "turns": cleaned,
        "speaker_count": len(speaker_indexes),
        "speaker_identity_verified": False,
        "authentication_authority": False,
        "raw_speaker_ids_included": False,
        "audio_retained_by_homeserver": False,
    }


async def transcribe_request(audio: bytes, request: Any, *, use_speaker_library: bool = True) -> dict[str, Any]:
    try:
        local_voice._validate_wav(audio)
    except local_voice.LocalVoiceError as exc:
        raise SpeakerDiarizationError(str(exc), exc.status_code) from exc
    try:
        api_key = provider_secrets.get_api_key("elevenlabs")
    except provider_secrets.ProviderSecretError as exc:
        raise SpeakerDiarizationError("ElevenLabs credentials are unavailable on this HomeServer.", 503) from exc
    if not api_key:
        raise SpeakerDiarizationError("Configure an ElevenLabs API key in Agent Brain to use enhanced speaker separation.", 409)

    data = {
        "model_id": MODEL,
        "diarize": "true",
        "timestamps_granularity": "word",
        "use_speaker_library": "true" if use_speaker_library else "false",
    }
    files = {"file": ("transcription.wav", audio, "audio/wav")}
    timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout, trust_env=True) as client:
        task = asyncio.create_task(
            client.post(
                ENDPOINT,
                params={"enable_logging": "false"},
                headers={"xi-api-key": api_key, "accept": "application/json"},
                data=data,
                files=files,
            )
        )
        try:
            while not task.done():
                if await request.is_disconnected():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass
                    raise SpeakerDiarizationError("Speaker diarization was cancelled.", 499)
                await asyncio.wait({task}, timeout=0.1)
            response = await task
        except SpeakerDiarizationError:
            raise
        except httpx.TimeoutException as exc:
            raise SpeakerDiarizationError("Speaker diarization timed out; local transcription can continue without speaker separation.", 504) from exc
        except httpx.HTTPError as exc:
            raise SpeakerDiarizationError("Speaker diarization provider is unavailable; local transcription can continue without speaker separation.", 502) from exc

    if response.status_code == 401:
        raise SpeakerDiarizationError("The saved ElevenLabs API key was rejected.", 401)
    if response.status_code in {402, 429}:
        raise SpeakerDiarizationError("ElevenLabs speaker diarization is temporarily unavailable for this account.", 429)
    if response.status_code >= 400:
        raise SpeakerDiarizationError("Speaker diarization provider rejected the audio request.", 502)
    try:
        payload = response.json()
    except ValueError as exc:
        raise SpeakerDiarizationError("Speaker diarization returned invalid JSON.", 502) from exc
    return parse_response(payload)
