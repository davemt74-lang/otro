from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .services import local_voice, voice_settings

router = APIRouter(prefix="/api/v1/control/voice", tags=["local-voice"])


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=local_voice.MAX_TTS_CHARS)


class VoiceSettingsRequest(BaseModel):
    stt_model: Literal["tiny.en-q8_0"] = "tiny.en-q8_0"
    tts_voice: Literal["en_US-lessac-medium"] = "en_US-lessac-medium"
    speaking_rate: float = Field(default=1.0, ge=0.6, le=1.6)
    sentence_silence: float = Field(default=0.2, ge=0.0, le=1.5)
    listen_silence_ms: int = Field(default=900, ge=400, le=3000)
    no_speech_timeout_ms: int = Field(default=8000, ge=2000, le=30000)
    max_segment_ms: int = Field(default=30000, ge=5000, le=60000)
    default_mode: Literal["conversation", "dictation"] = "conversation"
    strict_local_default: bool = False


def _raise(exc: local_voice.LocalVoiceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/status")
def local_voice_status() -> dict:
    return local_voice.status()


@router.get("/settings")
def get_local_voice_settings() -> dict:
    return {
        "preferences": voice_settings.get_preferences(),
        "choices": voice_settings.choices(),
        "storage": {
            "server": "HomeServer system_settings",
            "device_selection": "browser-local",
        },
    }


@router.put("/settings")
def update_local_voice_settings(payload: VoiceSettingsRequest) -> dict:
    preferences = voice_settings.save_preferences(payload.model_dump())
    return {
        "preferences": preferences,
        "choices": voice_settings.choices(),
        "saved": True,
    }


@router.post("/transcribe")
async def transcribe_local_voice(file: UploadFile = File(...)) -> dict:
    try:
        content = await file.read(local_voice.MAX_AUDIO_BYTES + 1)
        if len(content) > local_voice.MAX_AUDIO_BYTES:
            raise local_voice.LocalVoiceError("Recorded audio exceeds the local voice size limit.", 413)
        return local_voice.transcribe(content)
    except local_voice.LocalVoiceError as exc:
        _raise(exc)
    finally:
        await file.close()


@router.post("/synthesize")
def synthesize_local_voice(payload: SpeechRequest) -> Response:
    try:
        audio = local_voice.synthesize(payload.text)
    except local_voice.LocalVoiceError as exc:
        _raise(exc)
    return Response(
        content=audio,
        media_type="audio/wav",
        headers={
            "Cache-Control": "no-store",
            "X-HomeServer-Voice-Provider": "piper",
        },
    )
