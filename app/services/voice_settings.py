from __future__ import annotations

import json
from typing import Any

from ..database import db

SETTING_KEY = "voice.preferences"

STT_MODELS = {
    "tiny.en-q8_0": {
        "label": "Whisper Tiny English (q8)",
        "app_key": "whisper-stt",
        "path": "models/ggml-tiny.en-q8_0.bin",
        "language": "en",
    },
}

TTS_VOICES = {
    "en_US-lessac-medium": {
        "label": "Lessac — US English (Medium)",
        "app_key": "piper-tts",
        "model": "voices/en_US-lessac-medium.onnx",
        "config": "voices/en_US-lessac-medium.onnx.json",
        "language": "en-US",
    },
}

DEFAULTS: dict[str, Any] = {
    "stt_model": "tiny.en-q8_0",
    "tts_voice": "en_US-lessac-medium",
    "speaking_rate": 1.0,
    "sentence_silence": 0.2,
    "listen_silence_ms": 900,
    "no_speech_timeout_ms": 8000,
    "max_segment_ms": 30000,
    "default_mode": "conversation",
    "strict_local_default": False,
}


def _bounded_number(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def normalize(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    stt_model = str(source.get("stt_model") or DEFAULTS["stt_model"])
    if stt_model not in STT_MODELS:
        stt_model = DEFAULTS["stt_model"]
    tts_voice = str(source.get("tts_voice") or DEFAULTS["tts_voice"])
    if tts_voice not in TTS_VOICES:
        tts_voice = DEFAULTS["tts_voice"]
    default_mode = str(source.get("default_mode") or DEFAULTS["default_mode"])
    if default_mode not in {"conversation", "dictation"}:
        default_mode = DEFAULTS["default_mode"]
    return {
        "stt_model": stt_model,
        "tts_voice": tts_voice,
        "speaking_rate": round(_bounded_number(source.get("speaking_rate"), 1.0, 0.6, 1.6), 2),
        "sentence_silence": round(_bounded_number(source.get("sentence_silence"), 0.2, 0.0, 1.5), 2),
        "listen_silence_ms": _bounded_int(source.get("listen_silence_ms"), 900, 400, 3000),
        "no_speech_timeout_ms": _bounded_int(source.get("no_speech_timeout_ms"), 8000, 2000, 30000),
        "max_segment_ms": _bounded_int(source.get("max_segment_ms"), 30000, 5000, 60000),
        "default_mode": default_mode,
        "strict_local_default": bool(source.get("strict_local_default", False)),
    }


def piper_length_scale(preferences: Any) -> float:
    normalized = normalize(preferences)
    # Piper length_scale is inverse speed: values below 1 are faster.
    return round(1.0 / float(normalized["speaking_rate"]), 4)


def get_preferences() -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            "SELECT value_json FROM system_settings WHERE setting_key=? LIMIT 1",
            (SETTING_KEY,),
        ).fetchone()
    if row is None:
        return dict(DEFAULTS)
    try:
        value = json.loads(row["value_json"])
    except (TypeError, ValueError, json.JSONDecodeError):
        value = {}
    return normalize(value)


def save_preferences(value: Any) -> dict[str, Any]:
    preferences = normalize(value)
    encoded = json.dumps(preferences, ensure_ascii=False, separators=(",", ":"))
    with db() as connection:
        connection.execute(
            """
            INSERT INTO system_settings(setting_key, value_json)
            VALUES (?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET
                value_json=excluded.value_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (SETTING_KEY, encoded),
        )
    return preferences


def choices() -> dict[str, Any]:
    return {
        "stt_models": [{"key": key, **value} for key, value in STT_MODELS.items()],
        "tts_voices": [{"key": key, **value} for key, value in TTS_VOICES.items()],
        "default_modes": [
            {"key": "conversation", "label": "Conversation (Talk)"},
            {"key": "dictation", "label": "Dictation"},
        ],
    }
