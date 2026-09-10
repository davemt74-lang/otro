from __future__ import annotations

import json
from typing import Any

from ..database import db
from . import local_apps

SETTING_KEY = "voice.preferences"
VOICE_CATALOG_VERSION = "v0.43"
VOICE_CATALOG_MANAGEMENT_VERSION = "v0.44"
PIPER_RUNTIME_APP_KEY = "piper-tts"

STT_MODELS = {
    "tiny.en-q8_0": {
        "label": "Whisper Tiny English (q8)",
        "app_key": "whisper-stt",
        "path": "models/ggml-tiny.en-q8_0.bin",
        "language": "en",
    },
}

# Voice definitions deliberately separate the Piper runtime from voice assets.
# The original Lessac voice remains inside piper-tts for backwards compatibility;
# additional packs install only their ONNX model/config and share that runtime.
TTS_VOICES = {
    "en_US-lessac-medium": {
        "label": "Lessac — US English (Medium)",
        "runtime_app_key": PIPER_RUNTIME_APP_KEY,
        "app_key": PIPER_RUNTIME_APP_KEY,
        "model": "voices/en_US-lessac-medium.onnx",
        "config": "voices/en_US-lessac-medium.onnx.json",
        "language": "en-US",
        "region": "United States",
        "quality": "medium",
        "bundled_with_runtime": True,
    },
    "en_US-amy-medium": {
        "label": "Amy — US English (Medium)",
        "runtime_app_key": PIPER_RUNTIME_APP_KEY,
        "app_key": "piper-voice-amy-medium",
        "model": "voices/en_US-amy-medium.onnx",
        "config": "voices/en_US-amy-medium.onnx.json",
        "language": "en-US",
        "region": "United States",
        "quality": "medium",
        "bundled_with_runtime": False,
    },
    "en_US-ryan-medium": {
        "label": "Ryan — US English (Medium)",
        "runtime_app_key": PIPER_RUNTIME_APP_KEY,
        "app_key": "piper-voice-ryan-medium",
        "model": "voices/en_US-ryan-medium.onnx",
        "config": "voices/en_US-ryan-medium.onnx.json",
        "language": "en-US",
        "region": "United States",
        "quality": "medium",
        "bundled_with_runtime": False,
    },
    "en_GB-alan-medium": {
        "label": "Alan — British English (Medium)",
        "runtime_app_key": PIPER_RUNTIME_APP_KEY,
        "app_key": "piper-voice-alan-medium",
        "model": "voices/en_GB-alan-medium.onnx",
        "config": "voices/en_GB-alan-medium.onnx.json",
        "language": "en-GB",
        "region": "United Kingdom",
        "quality": "medium",
        "bundled_with_runtime": False,
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


def get_voice_definition(voice_key: str) -> dict[str, Any]:
    voice = TTS_VOICES.get(str(voice_key or ""))
    if voice is None:
        raise local_apps.LocalAppError("Voice is not present in the trusted HomeServer catalog.", 404)
    return voice


def _install_state(app_key: str) -> dict[str, Any]:
    package = local_apps.CATALOG.get(app_key)
    if package is None:
        return {"installed": False, "healthy": False, "reason": "Not in trusted catalog."}
    row = local_apps._installed_row(app_key)
    installed = bool(row is not None and row["status"] == "installed")
    if not installed:
        return {
            "installed": False,
            "healthy": False,
            "reason": f"Install {package['name']} from Local Apps.",
        }
    healthy, reason = local_apps._active_health(package)
    return {
        "installed": True,
        "healthy": bool(healthy),
        "reason": reason,
        "version": row["installed_version"],
    }


def _package_metadata(app_key: str) -> dict[str, Any]:
    package = local_apps.CATALOG.get(app_key) or {}
    return {
        "download_bytes": sum(int(item.get("size_bytes") or 0) for item in package.get("artifacts", [])),
        "source_label": package.get("source_label"),
        "license": package.get("license"),
        "package_version": package.get("version"),
    }


def voice_catalog() -> dict[str, Any]:
    runtime_state = _install_state(PIPER_RUNTIME_APP_KEY)
    active_voice = get_preferences()["tts_voice"]
    runtime_meta = _package_metadata(PIPER_RUNTIME_APP_KEY)
    voices = []
    for key, value in TTS_VOICES.items():
        pack_state = runtime_state if value["app_key"] == PIPER_RUNTIME_APP_KEY else _install_state(value["app_key"])
        pack_meta = _package_metadata(value["app_key"])
        available = bool(runtime_state["healthy"] and pack_state["healthy"])
        installed = bool(pack_state["installed"])
        if available:
            management_state = "ready"
        elif not value["bundled_with_runtime"] and not installed:
            # Installing a missing optional pack also repairs/installs its shared runtime if needed.
            management_state = "install"
        elif (runtime_state["installed"] and not runtime_state["healthy"]) or (installed and not pack_state["healthy"]):
            management_state = "repair"
        else:
            management_state = "install"
        active = key == active_voice
        voices.append({
            "key": key,
            **value,
            "install_app_key": value["app_key"],
            "runtime_installed": runtime_state["installed"],
            "runtime_healthy": runtime_state["healthy"],
            "installed": installed,
            "healthy": available,
            "available": available,
            "active": active,
            "can_preview": available,
            "can_uninstall": bool(not value["bundled_with_runtime"] and installed and not active),
            "management_state": management_state,
            "install_reason": runtime_state.get("reason") or pack_state.get("reason"),
            "installed_version": pack_state.get("version"),
            **pack_meta,
        })
    return {
        "version": VOICE_CATALOG_VERSION,
        "management_version": VOICE_CATALOG_MANAGEMENT_VERSION,
        "runtime": {
            "app_key": PIPER_RUNTIME_APP_KEY,
            **runtime_state,
            **runtime_meta,
        },
        "active_voice": active_voice,
        "voices": voices,
    }


def _voice_catalog_item(voice_key: str) -> dict[str, Any]:
    for item in voice_catalog()["voices"]:
        if item["key"] == voice_key:
            return item
    raise local_apps.LocalAppError("Voice is not present in the trusted HomeServer catalog.", 404)


def install_voice(voice_key: str, *, repair: bool = False) -> dict[str, Any]:
    voice = get_voice_definition(voice_key)
    current = _voice_catalog_item(voice_key)
    if repair and current["management_state"] != "repair":
        if current["management_state"] == "install":
            raise local_apps.LocalAppError("Voice is not installed; use Install first.", 409)
        raise local_apps.LocalAppError("Voice is already healthy and does not need repair.", 409)

    runtime_key = voice["runtime_app_key"]
    runtime_state = _install_state(runtime_key)
    pack_state = runtime_state if voice["app_key"] == runtime_key else _install_state(voice["app_key"])

    changed = False
    if not runtime_state["healthy"]:
        result = local_apps.install(runtime_key, update=bool(runtime_state["installed"]))
        changed = bool(result.get("changed")) or changed

    if voice["app_key"] != runtime_key and not pack_state["healthy"]:
        result = local_apps.install(voice["app_key"], update=bool(pack_state["installed"]))
        changed = bool(result.get("changed")) or changed

    return {"changed": changed, "voice": _voice_catalog_item(voice_key), "catalog": voice_catalog()}


def uninstall_voice(voice_key: str) -> dict[str, Any]:
    voice = get_voice_definition(voice_key)
    if voice["bundled_with_runtime"]:
        raise local_apps.LocalAppError("The bundled Lessac voice is part of the Piper runtime and cannot be removed separately.", 409)
    if get_preferences()["tts_voice"] == voice_key:
        raise local_apps.LocalAppError("Select and save another speaking voice before uninstalling the active voice pack.", 409)
    result = local_apps.uninstall(voice["app_key"])
    return {"changed": bool(result.get("changed")), "voice": _voice_catalog_item(voice_key), "catalog": voice_catalog()}


def choices() -> dict[str, Any]:
    catalog = voice_catalog()
    return {
        "stt_models": [{"key": key, **value} for key, value in STT_MODELS.items()],
        "tts_voices": catalog["voices"],
        "default_modes": [
            {"key": "conversation", "label": "Conversation (Talk)"},
            {"key": "dictation", "label": "Dictation"},
        ],
    }
