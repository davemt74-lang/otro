from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..config import settings
from . import local_apps, voice_settings

VOICE_RUNTIME_VERSION = "v0.42"
MAX_AUDIO_BYTES = 16 * 1024 * 1024
MAX_TTS_CHARS = 4000
TRANSCRIBE_TIMEOUT_SECONDS = 90
SYNTHESIZE_TIMEOUT_SECONDS = 60


class LocalVoiceError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _voice_temp_root() -> Path:
    root = settings.data_dir / "voice-runtime" / ".tmp"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve_managed_file(app_key: str, relative_path: str) -> Path:
    package = local_apps.CATALOG.get(app_key)
    if package is None:
        raise LocalVoiceError(f"Required Local App is not in the trusted catalog: {app_key}.", 503)
    row = local_apps._installed_row(app_key)
    if row is None or row["status"] != "installed":
        raise LocalVoiceError(f"Install {package['name']} from Local Apps to use local voice.", 409)
    healthy, reason = local_apps._active_health(package)
    if not healthy:
        raise LocalVoiceError(reason or f"{package['name']} is not healthy.", 409)
    active = local_apps._apps_root() / app_key
    path = local_apps._under(active, relative_path)
    if not path.is_file():
        raise LocalVoiceError(f"{package['name']} runtime file is missing. Reinstall this Local App.", 409)
    return path


def _creationflags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0


def _provider_status(app_key: str, required: list[str]) -> dict[str, Any]:
    package = local_apps.CATALOG.get(app_key)
    if package is None:
        return {"available": False, "installed": False, "healthy": False, "reason": "Not in trusted catalog."}
    row = local_apps._installed_row(app_key)
    installed = bool(row is not None and row["status"] == "installed")
    if not installed:
        return {
            "available": False,
            "installed": False,
            "healthy": False,
            "app_key": app_key,
            "name": package["name"],
            "reason": f"Install {package['name']} from Local Apps.",
        }
    healthy, reason = local_apps._active_health(package)
    if healthy:
        try:
            for relative in required:
                _resolve_managed_file(app_key, relative)
        except LocalVoiceError as exc:
            healthy = False
            reason = str(exc)
    return {
        "available": bool(healthy),
        "installed": True,
        "healthy": bool(healthy),
        "app_key": app_key,
        "name": package["name"],
        "version": row["installed_version"],
        "reason": reason,
    }


def _active_preferences() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    preferences = voice_settings.get_preferences()
    stt_model = voice_settings.STT_MODELS[preferences["stt_model"]]
    tts_voice = voice_settings.TTS_VOICES[preferences["tts_voice"]]
    return preferences, stt_model, tts_voice


def _synthesis_preferences(
    *,
    voice_key: str | None = None,
    speaking_rate: float | None = None,
    sentence_silence: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    preferences = dict(voice_settings.get_preferences())
    if voice_key is not None:
        if voice_key not in voice_settings.TTS_VOICES:
            raise LocalVoiceError("Voice is not present in the trusted HomeServer catalog.", 422)
        preferences["tts_voice"] = voice_key
    if speaking_rate is not None:
        preferences["speaking_rate"] = speaking_rate
    if sentence_silence is not None:
        preferences["sentence_silence"] = sentence_silence
    preferences = voice_settings.normalize(preferences)
    return preferences, voice_settings.TTS_VOICES[preferences["tts_voice"]]


def _tts_status(tts_voice: dict[str, Any]) -> dict[str, Any]:
    runtime_key = tts_voice["runtime_app_key"]
    voice_key = tts_voice["app_key"]
    runtime = _provider_status(runtime_key, ["runtime/piper/piper.exe"])
    voice = _provider_status(voice_key, [tts_voice["model"], tts_voice["config"]])
    reason = runtime.get("reason") or voice.get("reason")
    return {
        "available": bool(runtime["available"] and voice["available"]),
        "installed": bool(runtime["installed"] and voice["installed"]),
        "healthy": bool(runtime["healthy"] and voice["healthy"]),
        "runtime_app_key": runtime_key,
        "voice_app_key": voice_key,
        "runtime": runtime,
        "voice_pack": voice,
        "reason": reason,
    }


def status() -> dict[str, Any]:
    preferences, stt_model, tts_voice = _active_preferences()
    stt = _provider_status(
        stt_model["app_key"],
        ["runtime/Release/whisper-cli.exe", stt_model["path"]],
    )
    tts = _tts_status(tts_voice)
    stt["model"] = preferences["stt_model"]
    tts["voice"] = preferences["tts_voice"]
    return {
        "version": VOICE_RUNTIME_VERSION,
        "local": True,
        "strict_local_supported": True,
        "conversation_ready": bool(stt["available"] and tts["available"]),
        "preferences": preferences,
        "choices": voice_settings.choices(),
        "voice_catalog": voice_settings.voice_catalog(),
        "stt": stt,
        "tts": tts,
    }


def _validate_wav(audio: bytes) -> None:
    if not audio:
        raise LocalVoiceError("Recorded audio is empty.", 422)
    if len(audio) > MAX_AUDIO_BYTES:
        raise LocalVoiceError("Recorded audio exceeds the local voice size limit.", 413)
    if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        raise LocalVoiceError("Local transcription requires a 16-bit PCM WAV recording.", 415)

    offset = 12
    audio_format = None
    bits_per_sample = None
    while offset + 8 <= len(audio):
        chunk_id = audio[offset:offset + 4]
        chunk_size = int.from_bytes(audio[offset + 4:offset + 8], "little", signed=False)
        data_start = offset + 8
        data_end = data_start + chunk_size
        if data_end > len(audio):
            raise LocalVoiceError("Recorded WAV is truncated.", 422)
        if chunk_id == b"fmt " and chunk_size >= 16:
            audio_format = int.from_bytes(audio[data_start:data_start + 2], "little", signed=False)
            bits_per_sample = int.from_bytes(audio[data_start + 14:data_start + 16], "little", signed=False)
            break
        offset = data_end + (chunk_size & 1)
    if audio_format != 1 or bits_per_sample != 16:
        raise LocalVoiceError("Local transcription requires 16-bit PCM WAV audio.", 415)


def transcribe(audio: bytes) -> dict[str, Any]:
    _validate_wav(audio)
    preferences, stt_model, _ = _active_preferences()
    executable = _resolve_managed_file(stt_model["app_key"], "runtime/Release/whisper-cli.exe")
    model = _resolve_managed_file(stt_model["app_key"], stt_model["path"])

    work = Path(tempfile.mkdtemp(prefix="stt-", dir=_voice_temp_root()))
    input_path = work / "input.wav"
    output_prefix = work / "result"
    output_path = work / "result.txt"
    try:
        input_path.write_bytes(audio)
        command = [
            str(executable),
            "-m", str(model),
            "-f", str(input_path),
            "-otxt",
            "-of", str(output_prefix),
            "-np",
            "-nt",
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=str(executable.parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=TRANSCRIBE_TIMEOUT_SECONDS,
                check=False,
                shell=False,
                creationflags=_creationflags(),
            )
        except subprocess.TimeoutExpired as exc:
            raise LocalVoiceError("Local Whisper transcription timed out.", 504) from exc
        except OSError as exc:
            raise LocalVoiceError("Local Whisper runtime could not start. Repair Whisper STT in Local Apps.", 503) from exc

        if completed.returncode != 0 or not output_path.is_file():
            raise LocalVoiceError("Local Whisper could not transcribe this recording.", 422)
        try:
            text = output_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError as exc:
            raise LocalVoiceError("Local Whisper transcript could not be read.", 500) from exc
        return {
            "text": text,
            "provider": "whisper.cpp",
            "local": True,
            "model": preferences["stt_model"],
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def synthesize(
    text: str,
    *,
    voice_key: str | None = None,
    speaking_rate: float | None = None,
    sentence_silence: float | None = None,
) -> bytes:
    content = str(text or "").strip()
    if not content:
        raise LocalVoiceError("Speech text is empty.", 422)
    if len(content) > MAX_TTS_CHARS:
        raise LocalVoiceError(f"Speech text exceeds the {MAX_TTS_CHARS}-character local voice limit.", 413)

    preferences, tts_voice = _synthesis_preferences(
        voice_key=voice_key,
        speaking_rate=speaking_rate,
        sentence_silence=sentence_silence,
    )
    executable = _resolve_managed_file(tts_voice["runtime_app_key"], "runtime/piper/piper.exe")
    model = _resolve_managed_file(tts_voice["app_key"], tts_voice["model"])
    config = _resolve_managed_file(tts_voice["app_key"], tts_voice["config"])

    work = Path(tempfile.mkdtemp(prefix="tts-", dir=_voice_temp_root()))
    output_path = work / "reply.wav"
    try:
        command = [
            str(executable),
            "--model", str(model),
            "--config", str(config),
            "--output_file", str(output_path),
            "--length_scale", str(voice_settings.piper_length_scale(preferences)),
            "--sentence_silence", str(preferences["sentence_silence"]),
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=str(executable.parent),
                input=content,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=SYNTHESIZE_TIMEOUT_SECONDS,
                check=False,
                shell=False,
                creationflags=_creationflags(),
            )
        except subprocess.TimeoutExpired as exc:
            raise LocalVoiceError("Local Piper speech synthesis timed out.", 504) from exc
        except OSError as exc:
            raise LocalVoiceError("Local Piper runtime could not start. Repair Piper TTS in Local Apps.", 503) from exc

        if completed.returncode != 0 or not output_path.is_file():
            raise LocalVoiceError("Local Piper could not synthesize this reply.", 502)
        try:
            audio = output_path.read_bytes()
        except OSError as exc:
            raise LocalVoiceError("Local Piper audio could not be read.", 500) from exc
        if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            raise LocalVoiceError("Local Piper returned invalid WAV audio.", 502)
        return audio
    finally:
        shutil.rmtree(work, ignore_errors=True)
