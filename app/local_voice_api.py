from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .services import agent_voice_profiles, local_apps, local_voice, voice_settings

router = APIRouter(prefix="/api/v1/control/voice", tags=["local-voice"])


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=local_voice.MAX_TTS_CHARS)


class VoicePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice: str
    text: str = Field(min_length=1, max_length=local_voice.MAX_TTS_CHARS)
    speaking_rate: float = Field(default=1.0, ge=0.6, le=1.6)
    sentence_silence: float = Field(default=0.2, ge=0.0, le=1.5)

    @field_validator("voice")
    @classmethod
    def validate_voice(cls, value: str) -> str:
        if value not in voice_settings.TTS_VOICES:
            raise ValueError("Unknown local Piper voice.")
        return value


class AgentVoiceProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice: str | None = None
    speaking_rate: float | None = Field(default=None, ge=0.6, le=1.6)
    sentence_silence: float | None = Field(default=None, ge=0.0, le=1.5)

    @field_validator("voice")
    @classmethod
    def validate_voice(cls, value: str | None) -> str | None:
        if value in (None, "", "inherit"):
            return None
        if value not in voice_settings.TTS_VOICES:
            raise ValueError("Unknown local Piper voice.")
        return value


class AgentVoicePreviewRequest(AgentVoiceProfileRequest):
    text: str = Field(min_length=1, max_length=local_voice.MAX_TTS_CHARS)


class VoiceSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stt_model: str = "tiny.en-q8_0"
    tts_voice: str = "en_US-lessac-medium"
    speaking_rate: float = Field(default=1.0, ge=0.6, le=1.6)
    sentence_silence: float = Field(default=0.2, ge=0.0, le=1.5)
    listen_silence_ms: int = Field(default=900, ge=400, le=3000)
    no_speech_timeout_ms: int = Field(default=8000, ge=2000, le=30000)
    max_segment_ms: int = Field(default=30000, ge=5000, le=60000)
    default_mode: Literal["conversation", "dictation"] = "conversation"
    strict_local_default: bool = False

    @field_validator("stt_model")
    @classmethod
    def validate_stt_model(cls, value: str) -> str:
        if value not in voice_settings.STT_MODELS:
            raise ValueError("Unknown local transcription model.")
        return value

    @field_validator("tts_voice")
    @classmethod
    def validate_tts_voice(cls, value: str) -> str:
        if value not in voice_settings.TTS_VOICES:
            raise ValueError("Unknown local Piper voice.")
        return value


def _raise(exc: local_voice.LocalVoiceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _raise_app(exc: local_apps.LocalAppError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _raise_profile(exc: agent_voice_profiles.AgentVoiceProfileError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _profile_audio(text: str, resolved: dict) -> Response:
    effective = resolved["effective"]
    if not effective["ready"]:
        raise HTTPException(status_code=503, detail=effective["warning"] or "Agent local voice is not ready.")
    try:
        audio = local_voice.synthesize(
            text,
            voice_key=effective["voice"],
            speaking_rate=effective["speaking_rate"],
            sentence_silence=effective["sentence_silence"],
        )
    except local_voice.LocalVoiceError as exc:
        _raise(exc)
    return Response(
        content=audio,
        media_type="audio/wav",
        headers={
            "Cache-Control": "no-store",
            "X-HomeServer-Voice-Provider": "piper",
            "X-HomeServer-Voice": str(effective["voice"]),
            "X-HomeServer-Voice-Source": str(effective["voice_source"]),
            "X-HomeServer-Agent": str(resolved["agent"]["id"]),
        },
    )


@router.get("/status")
def local_voice_status() -> dict:
    return local_voice.status()


@router.get("/catalog")
def get_local_voice_catalog() -> dict:
    return voice_settings.voice_catalog()


@router.post("/catalog/{voice_key}/install")
def install_local_voice(voice_key: str) -> dict:
    try:
        return voice_settings.install_voice(voice_key, repair=False)
    except local_apps.LocalAppError as exc:
        _raise_app(exc)


@router.post("/catalog/{voice_key}/repair")
def repair_local_voice(voice_key: str) -> dict:
    try:
        return voice_settings.install_voice(voice_key, repair=True)
    except local_apps.LocalAppError as exc:
        _raise_app(exc)


@router.delete("/catalog/{voice_key}")
def uninstall_local_voice(voice_key: str) -> dict:
    try:
        return voice_settings.uninstall_voice(voice_key)
    except local_apps.LocalAppError as exc:
        _raise_app(exc)


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


@router.get("/agents/{agent_id}/profile")
def get_agent_voice_profile(agent_id: int) -> dict:
    try:
        return agent_voice_profiles.get_profile(agent_id)
    except agent_voice_profiles.AgentVoiceProfileError as exc:
        _raise_profile(exc)


@router.put("/agents/{agent_id}/profile")
def update_agent_voice_profile(agent_id: int, payload: AgentVoiceProfileRequest) -> dict:
    try:
        return agent_voice_profiles.save_profile(agent_id, payload.model_dump())
    except agent_voice_profiles.AgentVoiceProfileError as exc:
        _raise_profile(exc)


@router.post("/agents/{agent_id}/preview")
def preview_agent_voice_profile(agent_id: int, payload: AgentVoicePreviewRequest) -> Response:
    try:
        resolved = agent_voice_profiles.resolve_effective(
            agent_id,
            payload.model_dump(exclude={"text"}),
        )
    except agent_voice_profiles.AgentVoiceProfileError as exc:
        _raise_profile(exc)
    return _profile_audio(payload.text, resolved)


@router.post("/agents/{agent_id}/synthesize")
def synthesize_agent_voice(agent_id: int, payload: SpeechRequest) -> Response:
    try:
        resolved = agent_voice_profiles.get_profile(agent_id)
    except agent_voice_profiles.AgentVoiceProfileError as exc:
        _raise_profile(exc)
    return _profile_audio(payload.text, resolved)


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


@router.post("/preview")
def preview_local_voice(payload: VoicePreviewRequest) -> Response:
    try:
        audio = local_voice.synthesize(
            payload.text,
            voice_key=payload.voice,
            speaking_rate=payload.speaking_rate,
            sentence_silence=payload.sentence_silence,
        )
    except local_voice.LocalVoiceError as exc:
        _raise(exc)
    return Response(
        content=audio,
        media_type="audio/wav",
        headers={
            "Cache-Control": "no-store",
            "X-HomeServer-Voice-Provider": "piper",
            "X-HomeServer-Voice": payload.voice,
        },
    )


@router.post("/synthesize")
def synthesize_local_voice(payload: SpeechRequest) -> Response:
    # Owner Conversation Mode speaks the primary Agent. Agents with no voice
    # override resolve to the existing global Voice Settings, preserving the
    # v0.42/v0.43 behavior by default while enabling v0.45 personas.
    try:
        resolved = agent_voice_profiles.get_profile(agent_voice_profiles.primary_agent_id())
    except agent_voice_profiles.AgentVoiceProfileError as exc:
        _raise_profile(exc)
    return _profile_audio(payload.text, resolved)
