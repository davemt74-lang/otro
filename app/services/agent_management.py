from __future__ import annotations

import json
from typing import Any

from ..database import db
from . import agent_voice_profiles, voice_settings

AGENT_PERSONA_MANAGEMENT_VERSION = "v0.46"


class AgentManagementError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _required_text(value: Any, *, label: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise AgentManagementError(f"{label} is required.")
    if len(text) > maximum:
        raise AgentManagementError(f"{label} must be {maximum} characters or fewer.")
    return text


def _optional_text(value: Any, *, label: str, maximum: int) -> str:
    text = str(value or "").strip()
    if len(text) > maximum:
        raise AgentManagementError(f"{label} must be {maximum} characters or fewer.")
    return text


def normalize_agent(value: Any) -> dict[str, str]:
    source = value if isinstance(value, dict) else {}
    return {
        "name": _required_text(source.get("name"), label="Agent name", maximum=120),
        "instructions": _optional_text(source.get("instructions"), label="Instructions", maximum=12000),
        "model": _optional_text(source.get("model"), label="Model", maximum=120),
    }


def _row(agent_id: int) -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            """
            SELECT id, name, instructions, model, is_primary, created_at, updated_at
            FROM agents WHERE id=? LIMIT 1
            """,
            (int(agent_id),),
        ).fetchone()
    if row is None:
        raise AgentManagementError("Agent not found.", 404)
    return dict(row)


def _voice_summary(profile: dict[str, Any]) -> dict[str, Any]:
    effective = profile["effective"]
    overrides = profile["overrides"]
    voice_key = str(effective["voice"])
    voice = voice_settings.TTS_VOICES.get(voice_key) or {}
    customized = any(value is not None for value in overrides.values())
    return {
        "profile_version": profile["version"],
        "key": voice_key,
        "label": voice.get("label") or voice_key,
        "source": effective["voice_source"],
        "ready": bool(effective["ready"]),
        "fallback": bool(effective["fallback"]),
        "customized": customized,
        "warning": effective.get("warning"),
    }


def _decorate(agent: dict[str, Any], *, include_profile: bool = False) -> dict[str, Any]:
    profile = agent_voice_profiles.get_profile(int(agent["id"]))
    result = {
        "id": int(agent["id"]),
        "name": agent["name"],
        "model": agent["model"],
        "is_primary": bool(agent["is_primary"]),
        "created_at": agent["created_at"],
        "updated_at": agent["updated_at"],
        "voice": _voice_summary(profile),
    }
    if include_profile:
        result["instructions"] = agent["instructions"]
        result["voice_profile"] = profile
    return result


def _log(connection: Any, action: str, agent_id: int, metadata: dict[str, Any] | None = None) -> None:
    connection.execute(
        """
        INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
        VALUES ('owner', 'control-center', ?, 'agent', ?, ?)
        """,
        (
            action,
            str(int(agent_id)),
            json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":")),
        ),
    )


def list_agents() -> dict[str, Any]:
    with db() as connection:
        rows = connection.execute(
            """
            SELECT id, name, instructions, model, is_primary, created_at, updated_at
            FROM agents
            ORDER BY is_primary DESC, name COLLATE NOCASE, id
            """
        ).fetchall()
    items = [_decorate(dict(row)) for row in rows]
    return {
        "version": AGENT_PERSONA_MANAGEMENT_VERSION,
        "items": items,
        "total": len(items),
        "secondary_total": sum(1 for item in items if not item["is_primary"]),
    }


def get_agent(agent_id: int) -> dict[str, Any]:
    return {
        "version": AGENT_PERSONA_MANAGEMENT_VERSION,
        "agent": _decorate(_row(agent_id), include_profile=True),
    }


def create_agent(value: Any) -> dict[str, Any]:
    agent = normalize_agent(value)
    with db() as connection:
        cursor = connection.execute(
            "INSERT INTO agents(name, instructions, model, is_primary) VALUES (?, ?, ?, 0)",
            (agent["name"], agent["instructions"], agent["model"]),
        )
        agent_id = int(cursor.lastrowid)
        _log(connection, "agent.secondary.created", agent_id)
    return get_agent(agent_id)


def update_agent(agent_id: int, value: Any) -> dict[str, Any]:
    current = _row(agent_id)
    agent = normalize_agent(value)
    with db() as connection:
        connection.execute(
            """
            UPDATE agents
            SET name=?, instructions=?, model=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (agent["name"], agent["instructions"], agent["model"], int(current["id"])),
        )
        _log(
            connection,
            "agent.secondary.updated" if not current["is_primary"] else "agent.updated",
            int(current["id"]),
        )
    return get_agent(int(current["id"]))


def delete_agent(agent_id: int) -> dict[str, Any]:
    current = _row(agent_id)
    if current["is_primary"]:
        raise AgentManagementError("The primary Agent cannot be deleted.", 409)
    with db() as connection:
        detached_memories = int(
            connection.execute(
                "SELECT COUNT(*) FROM agent_memory WHERE agent_id=?",
                (int(current["id"]),),
            ).fetchone()[0]
        )
        connection.execute("DELETE FROM agents WHERE id=?", (int(current["id"]),))
        _log(
            connection,
            "agent.secondary.deleted",
            int(current["id"]),
            {"name": current["name"], "detached_memory_items": detached_memories},
        )
    return {
        "version": AGENT_PERSONA_MANAGEMENT_VERSION,
        "deleted": True,
        "id": int(current["id"]),
        "detached_memory_items": detached_memories,
    }


def duplicate_agent(agent_id: int) -> dict[str, Any]:
    source = _row(agent_id)
    suffix = " Copy"
    base_name = str(source["name"])
    name = (base_name[: 120 - len(suffix)] + suffix).strip()
    with db() as connection:
        cursor = connection.execute(
            "INSERT INTO agents(name, instructions, model, is_primary) VALUES (?, ?, ?, 0)",
            (name, source["instructions"], source["model"]),
        )
        new_id = int(cursor.lastrowid)
        copied_profile = connection.execute(
            """
            INSERT INTO agent_voice_profiles(agent_id, voice_key, speaking_rate, sentence_silence)
            SELECT ?, voice_key, speaking_rate, sentence_silence
            FROM agent_voice_profiles WHERE agent_id=?
            """,
            (new_id, int(source["id"])),
        ).rowcount > 0
        _log(
            connection,
            "agent.secondary.duplicated",
            new_id,
            {"source_agent_id": int(source["id"]), "copied_voice_profile": copied_profile},
        )
    result = get_agent(new_id)
    result["duplicated_from"] = int(source["id"])
    return result
