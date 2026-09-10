from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .services import local_voice

router = APIRouter(prefix="/api/v1/control/voice", tags=["local-voice"])


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=local_voice.MAX_TTS_CHARS)


def _raise(exc: local_voice.LocalVoiceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/status")
def local_voice_status() -> dict:
    return local_voice.status()


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
