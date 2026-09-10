from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient


with tempfile.TemporaryDirectory(prefix="homeserver-multi-agent-v046-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import agent_voice_profiles  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    with TestClient(app) as client:
        scheduler.stop()

        # All v0.46 administration remains behind the local owner boundary.
        assert client.get("/api/v1/control/agents").status_code == 401
        assert client.post(
            "/api/v1/control/agents",
            json={"name": "Blocked", "instructions": "", "model": ""},
        ).status_code == 401
        assert client.post(
            "/__owner/session",
            headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN},
        ).status_code == 200

        initial = client.get("/api/v1/control/agents")
        assert initial.status_code == 200, initial.text
        payload = initial.json()
        assert payload["version"] == "v0.46"
        assert payload["total"] == 1
        assert payload["secondary_total"] == 0
        primary = payload["items"][0]
        assert primary["is_primary"] is True
        assert primary["voice"]["profile_version"] == "v0.45"

        # Existing primary-Agent compatibility surface remains intact.
        legacy_primary = client.get("/api/v1/control/agent")
        assert legacy_primary.status_code == 200
        assert legacy_primary.json()["agent"]["id"] == primary["id"]
        assert client.delete(f"/api/v1/control/agents/{primary['id']}").status_code == 409

        # Validation is strict and extra fields cannot mutate primary/ownership state.
        assert client.post(
            "/api/v1/control/agents",
            json={"name": "   ", "instructions": "", "model": ""},
        ).status_code == 422
        assert client.post(
            "/api/v1/control/agents",
            json={"name": "Bad", "instructions": "", "model": "", "is_primary": True},
        ).status_code == 422

        created = client.post(
            "/api/v1/control/agents",
            json={
                "name": "Research Agent",
                "instructions": "Focus on source-grounded local research.",
                "model": "local-research-model",
            },
        )
        assert created.status_code == 200, created.text
        secondary = created.json()["agent"]
        secondary_id = int(secondary["id"])
        assert secondary["is_primary"] is False
        assert secondary["voice_profile"]["overrides"] == {
            "voice": None,
            "speaking_rate": None,
            "sentence_silence": None,
        }

        updated = client.put(
            f"/api/v1/control/agents/{secondary_id}",
            json={
                "name": "Travel Research Agent",
                "instructions": "Research travel options without exposing private local data.",
                "model": "local-research-model-v2",
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["agent"]["name"] == "Travel Research Agent"
        assert updated.json()["agent"]["is_primary"] is False

        voice = client.put(
            f"/api/v1/control/voice/agents/{secondary_id}/profile",
            json={
                "voice": "en_US-amy-medium",
                "speaking_rate": 1.15,
                "sentence_silence": 0.3,
            },
        )
        assert voice.status_code == 200, voice.text
        assert voice.json()["overrides"]["voice"] == "en_US-amy-medium"

        listed = client.get("/api/v1/control/agents").json()
        listed_secondary = next(item for item in listed["items"] if item["id"] == secondary_id)
        assert listed_secondary["voice"]["customized"] is True
        assert listed_secondary["voice"]["profile_version"] == "v0.45"
        assert listed_secondary["voice"]["source"] in {"agent_override", "fallback_global", "fallback_default"}

        # A linked memory survives secondary-Agent deletion by becoming unassigned.
        with db() as connection:
            cursor = connection.execute(
                "INSERT INTO agent_memory(agent_id, memory_key, content) VALUES (?, ?, ?)",
                (secondary_id, "v046.fixture", "Keep this memory after Agent deletion."),
            )
            memory_id = int(cursor.lastrowid)

        duplicated = client.post(f"/api/v1/control/agents/{secondary_id}/duplicate")
        assert duplicated.status_code == 200, duplicated.text
        duplicate = duplicated.json()["agent"]
        duplicate_id = int(duplicate["id"])
        assert duplicate_id != secondary_id
        assert duplicate["is_primary"] is False
        assert duplicate["name"] == "Travel Research Agent Copy"
        assert duplicate["instructions"] == updated.json()["agent"]["instructions"]
        assert duplicate["model"] == updated.json()["agent"]["model"]
        assert duplicate["voice_profile"]["overrides"] == voice.json()["overrides"]
        assert duplicated.json()["duplicated_from"] == secondary_id

        with db() as connection:
            copied_memory = connection.execute(
                "SELECT COUNT(*) FROM agent_memory WHERE agent_id=?",
                (duplicate_id,),
            ).fetchone()[0]
        assert copied_memory == 0, "duplicating a persona must not duplicate private memory"

        refs = agent_voice_profiles.agents_referencing_voice("en_US-amy-medium")
        assert len(refs) == 2
        assert {item["id"] for item in refs} == {secondary_id, duplicate_id}

        catalog = client.get("/api/v1/control/voice/catalog")
        assert catalog.status_code == 200
        amy = next(item for item in catalog.json()["voices"] if item["key"] == "en_US-amy-medium")
        assert amy["agent_reference_count"] == 2
        assert amy["can_uninstall"] is False

        removed = client.delete(f"/api/v1/control/agents/{secondary_id}")
        assert removed.status_code == 200, removed.text
        assert removed.json()["detached_memory_items"] == 1
        with db() as connection:
            memory_agent_id = connection.execute(
                "SELECT agent_id FROM agent_memory WHERE id=?",
                (memory_id,),
            ).fetchone()["agent_id"]
        assert memory_agent_id is None
        assert client.get(f"/api/v1/control/agents/{secondary_id}").status_code == 404
        assert len(agent_voice_profiles.agents_referencing_voice("en_US-amy-medium")) == 1

        removed_copy = client.delete(f"/api/v1/control/agents/{duplicate_id}")
        assert removed_copy.status_code == 200
        assert len(agent_voice_profiles.agents_referencing_voice("en_US-amy-medium")) == 0

        # Primary Agent was never replaced or deleted by secondary persona operations.
        final_primary = client.get("/api/v1/control/agent").json()["agent"]
        assert final_primary["id"] == primary["id"]
        assert final_primary["is_primary"] is True

        capabilities = client.get("/api/v1/capabilities")
        assert capabilities.status_code == 200
        caps = capabilities.json()
        assert caps["agent_personas"]["version"] == "v0.46"
        assert caps["agent_personas"]["voice_profiles"] == "v0.45"
        assert "agent.personas.v046" in caps["features"]

print("HomeServer v0.46 Multi-Agent Persona Management regression passed")
