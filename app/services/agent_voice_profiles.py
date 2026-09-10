from __future__ import annotations

from typing import Any

from ..database import db
from . import voice_settings

VOICE_AGENT_PROFILE_VERSION = "v0.45"


class AgentVoiceProfileError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _agent(agent_id: int) -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            "SELECT id, name, is_primary FROM agents WHERE id=? LIMIT 1",
            (int(agent_id),),
        ).fetchone()
    if row is None:
        raise AgentVoiceProfileError("Agent not found.", 404)
    return dict(row)


def primary_agent_id() -> int:
    with db() as connection:
        row = connection.execute(
            "SELECT id FROM agents WHERE is_primary=1 LIMIT 1"
        ).fetchone()
    if row is None:
        raise AgentVoiceProfileError("Primary agent is not configured.", 503)
    return int(row["id"])


def _voice_override(value: Any) -> str | None:
    if value is None or str(value).strip() in {"", "inherit"}:
        return None
    key = str(value).strip()
    if key not in voice_settings.TTS_VOICES:
        raise AgentVoiceProfileError("Voice is not present in the trusted HomeServer catalog.")
    return key


def _optional_number(value: Any, *, minimum: float, maximum: float, label: str) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise AgentVoiceProfileError(f"{label} must be a number.") from exc
    if number < minimum or number > maximum:
        raise AgentVoiceProfileError(f"{label} must be between {minimum} and {maximum}.")
    return round(number, 2)


def normalize_overrides(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    return {
        "voice": _voice_override(source.get("voice")),
        "speaking_rate": _optional_number(
            source.get("speaking_rate"), minimum=0.6, maximum=1.6, label="Speaking rate"
        ),
        "sentence_silence": _optional_number(
            source.get("sentence_silence"), minimum=0.0, maximum=1.5, label="Sentence silence"
        ),
    }


def _stored_overrides(agent_id: int) -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            """
            SELECT voice_key, speaking_rate, sentence_silence
            FROM agent_voice_profiles WHERE agent_id=? LIMIT 1
            """,
            (int(agent_id),),
        ).fetchone()
    if row is None:
        return {"voice": None, "speaking_rate": None, "sentence_silence": None}
    return {
        "voice": row["voice_key"],
        "speaking_rate": row["speaking_rate"],
        "sentence_silence": row["sentence_silence"],
    }


def _catalog_item(catalog: dict[str, Any], voice_key: str) -> dict[str, Any] | None:
    return next((item for item in catalog.get("voices", []) if item.get("key") == voice_key), None)


def resolve_effective(agent_id: int, overrides: Any | None = None) -> dict[str, Any]:
    agent = _agent(agent_id)
    profile = normalize_overrides(overrides) if overrides is not None else normalize_overrides(_stored_overrides(agent_id))
    global_preferences = voice_settings.get_preferences()
    catalog = voice_settings.voice_catalog()

    requested_voice = profile["voice"] or global_preferences["tts_voice"]
    requested_source = "agent_override" if profile["voice"] else "global"
    requested_item = _catalog_item(catalog, requested_voice)
    effective_voice = requested_voice
    voice_source = requested_source
    fallback = False
    warning = None

    if not requested_item or not requested_item.get("available"):
        fallback = True
        global_voice = str(global_preferences["tts_voice"])
        global_item = _catalog_item(catalog, global_voice)
        default_voice = str(voice_settings.DEFAULTS["tts_voice"])
        default_item = _catalog_item(catalog, default_voice)
        if profile["voice"] and global_voice != requested_voice and global_item and global_item.get("available"):
            effective_voice = global_voice
            voice_source = "fallback_global"
            warning = f"{requested_voice} is not ready. Using the global HomeServer voice instead."
        elif default_voice != requested_voice and default_item and default_item.get("available"):
            effective_voice = default_voice
            voice_source = "fallback_default"
            warning = f"{requested_voice} is not ready. Using the bundled HomeServer voice instead."
        else:
            warning = f"{requested_voice} is not ready and no trusted local fallback voice is available."

    effective_item = _catalog_item(catalog, effective_voice)
    ready = bool(effective_item and effective_item.get("available"))
    if not ready and not warning:
        warning = f"{effective_voice} is not ready for local speech."

    return {
        "version": VOICE_AGENT_PROFILE_VERSION,
        "agent": {"id": int(agent["id"]), "name": agent["name"], "is_primary": bool(agent["is_primary"])},
        "overrides": profile,
        "effective": {
            "voice": effective_voice,
            "requested_voice": requested_voice,
            "speaking_rate": profile["speaking_rate"] if profile["speaking_rate"] is not None else global_preferences["speaking_rate"],
            "sentence_silence": profile["sentence_silence"] if profile["sentence_silence"] is not None else global_preferences["sentence_silence"],
            "voice_source": voice_source,
            "speaking_rate_source": "agent_override" if profile["speaking_rate"] is not None else "global",
            "sentence_silence_source": "agent_override" if profile["sentence_silence"] is not None else "global",
            "ready": ready,
            "fallback": fallback,
            "warning": warning,
        },
    }


def get_profile(agent_id: int) -> dict[str, Any]:
    return resolve_effective(agent_id)


def save_profile(agent_id: int, value: Any) -> dict[str, Any]:
    agent = _agent(agent_id)
    profile = normalize_overrides(value)
    with db() as connection:
        connection.execute(
            """
            INSERT INTO agent_voice_profiles(agent_id, voice_key, speaking_rate, sentence_silence)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                voice_key=excluded.voice_key,
                speaking_rate=excluded.speaking_rate,
                sentence_silence=excluded.sentence_silence,
                updated_at=CURRENT_TIMESTAMP
            """,
            (int(agent_id), profile["voice"], profile["speaking_rate"], profile["sentence_silence"]),
        )
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES ('owner', 'control-center', 'agent.voice.updated', 'agent', ?, '{}')
            """,
            (str(agent["id"]),),
        )
    return resolve_effective(agent_id)


def agents_referencing_voice(voice_key: str) -> list[dict[str, Any]]:
    with db() as connection:
        rows = connection.execute(
            """
            SELECT a.id, a.name, a.is_primary
            FROM agent_voice_profiles p
            JOIN agents a ON a.id=p.agent_id
            WHERE p.voice_key=?
            ORDER BY a.is_primary DESC, a.name COLLATE NOCASE, a.id
            """,
            (str(voice_key),),
        ).fetchall()
    return [
        {"id": int(row["id"]), "name": row["name"], "is_primary": bool(row["is_primary"])}
        for row in rows
    ]
