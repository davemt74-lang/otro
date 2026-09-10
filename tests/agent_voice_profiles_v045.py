from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


with tempfile.TemporaryDirectory(prefix="homeserver-agent-voice-v045-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import agent_voice_profiles, local_apps, local_voice, voice_settings  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    original_install_state = voice_settings._install_state
    original_synthesize = local_voice.synthesize
    original_uninstall = local_apps.uninstall

    installed = {
        "piper-tts": True,
        "piper-voice-amy-medium": True,
        "piper-voice-ryan-medium": False,
        "piper-voice-alan-medium": True,
    }
    synth_calls: list[dict] = []
    uninstall_calls: list[str] = []

    def fake_install_state(app_key: str) -> dict:
        ready = bool(installed.get(app_key))
        return {
            "installed": ready,
            "healthy": ready,
            "reason": None if ready else "Not installed.",
            "version": "fixture-v1" if ready else None,
        }

    def fake_synthesize(text: str, *, voice_key=None, speaking_rate=None, sentence_silence=None) -> bytes:
        synth_calls.append({
            "text": text,
            "voice_key": voice_key,
            "speaking_rate": speaking_rate,
            "sentence_silence": sentence_silence,
        })
        return b"RIFF" + (b"\x00" * 4) + b"WAVE" + (b"\x00" * 40)

    def fake_uninstall(app_key: str) -> dict:
        uninstall_calls.append(app_key)
        installed[app_key] = False
        return {"changed": True, "app_key": app_key}

    try:
        voice_settings._install_state = fake_install_state
        local_voice.synthesize = fake_synthesize
        local_apps.uninstall = fake_uninstall

        with TestClient(app) as client:
            scheduler.stop()
            assert client.get("/api/v1/control/voice/agents/1/profile").status_code == 401
            assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200

            with db() as connection:
                schema_version = int(connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0])
                assert schema_version == 20
                table = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_voice_profiles'"
                ).fetchone()
                assert table is not None

            agent = client.get("/api/v1/control/agent").json()["agent"]
            agent_id = int(agent["id"])

            inherited = client.get(f"/api/v1/control/voice/agents/{agent_id}/profile")
            assert inherited.status_code == 200, inherited.text
            inherited_profile = inherited.json()
            assert inherited_profile["version"] == "v0.45"
            assert inherited_profile["overrides"] == {
                "voice": None,
                "speaking_rate": None,
                "sentence_silence": None,
            }
            assert inherited_profile["effective"]["voice"] == "en_US-lessac-medium"
            assert inherited_profile["effective"]["voice_source"] == "global"
            assert inherited_profile["effective"]["ready"] is True

            # Legacy v0.42/v0.43 synthesis remains global when every Agent field inherits.
            synth_calls.clear()
            legacy = client.post("/api/v1/control/voice/synthesize", json={"text": "Inherited voice."})
            assert legacy.status_code == 200, legacy.text
            assert synth_calls[-1]["voice_key"] is None
            assert synth_calls[-1]["speaking_rate"] is None
            assert synth_calls[-1]["sentence_silence"] is None

            saved = client.put(
                f"/api/v1/control/voice/agents/{agent_id}/profile",
                json={"voice": "en_US-amy-medium", "speaking_rate": 1.25, "sentence_silence": 0.35},
            )
            assert saved.status_code == 200, saved.text
            saved_profile = saved.json()
            assert saved_profile["overrides"]["voice"] == "en_US-amy-medium"
            assert saved_profile["effective"]["voice"] == "en_US-amy-medium"
            assert saved_profile["effective"]["voice_source"] == "agent_override"
            assert saved_profile["effective"]["speaking_rate"] == 1.25
            assert saved_profile["effective"]["sentence_silence"] == 0.35

            catalog = client.get("/api/v1/control/voice/catalog").json()
            assert catalog["version"] == "v0.43"
            assert catalog["management_version"] == "v0.44"
            amy = next(item for item in catalog["voices"] if item["key"] == "en_US-amy-medium")
            assert amy["agent_reference_count"] == 1
            assert amy["assigned_agents"][0]["id"] == agent_id
            assert amy["can_uninstall"] is False

            synth_calls.clear()
            spoken = client.post("/api/v1/control/voice/synthesize", json={"text": "Agent override voice."})
            assert spoken.status_code == 200, spoken.text
            assert spoken.headers["x-homeserver-voice"] == "en_US-amy-medium"
            assert spoken.headers["x-homeserver-voice-source"] == "agent_override"
            assert synth_calls[-1]["voice_key"] == "en_US-amy-medium"
            assert synth_calls[-1]["speaking_rate"] == 1.25
            assert synth_calls[-1]["sentence_silence"] == 0.35

            # Preview an unsaved missing Ryan pack. It deterministically falls back
            # to the trusted global Lessac voice while retaining preview traits.
            synth_calls.clear()
            preview = client.post(
                f"/api/v1/control/voice/agents/{agent_id}/preview",
                json={
                    "voice": "en_US-ryan-medium",
                    "speaking_rate": 1.3,
                    "sentence_silence": None,
                    "text": "Unsaved preview.",
                },
            )
            assert preview.status_code == 200, preview.text
            assert preview.headers["x-homeserver-voice"] == "en_US-lessac-medium"
            assert preview.headers["x-homeserver-voice-source"] == "fallback_global"
            assert synth_calls[-1]["voice_key"] == "en_US-lessac-medium"
            assert synth_calls[-1]["speaking_rate"] == 1.3
            assert synth_calls[-1]["sentence_silence"] == 0.2
            unchanged = client.get(f"/api/v1/control/voice/agents/{agent_id}/profile").json()
            assert unchanged["overrides"]["voice"] == "en_US-amy-medium"
            assert unchanged["overrides"]["speaking_rate"] == 1.25

            assert client.put(
                f"/api/v1/control/voice/agents/{agent_id}/profile",
                json={"voice": "external-voice", "speaking_rate": None, "sentence_silence": None},
            ).status_code == 422
            assert client.put(
                f"/api/v1/control/voice/agents/{agent_id}/profile",
                json={"voice": None, "speaking_rate": 1.7, "sentence_silence": None},
            ).status_code == 422
            assert client.get("/api/v1/control/voice/agents/999999/profile").status_code == 404

            blocked = client.delete("/api/v1/control/voice/catalog/en_US-amy-medium")
            assert blocked.status_code == 409
            assert "assigned to 1 Agent" in blocked.json()["detail"]
            assert uninstall_calls == []

            # Persistence is generic by agent_id even though the current Control
            # Center exposes the primary Agent editor.
            with db() as connection:
                cursor = connection.execute(
                    "INSERT INTO agents(name, instructions, model, is_primary) VALUES ('Travel Agent', '', '', 0)"
                )
                second_agent_id = int(cursor.lastrowid)
            second = agent_voice_profiles.save_profile(
                second_agent_id,
                {"voice": "en_GB-alan-medium", "speaking_rate": 0.9, "sentence_silence": None},
            )
            assert second["effective"]["voice"] == "en_GB-alan-medium"
            assert second["effective"]["speaking_rate"] == 0.9
            assert agent_voice_profiles.agents_referencing_voice("en_GB-alan-medium")[0]["id"] == second_agent_id

            # Releasing the primary override removes the pack dependency.
            reset = client.put(
                f"/api/v1/control/voice/agents/{agent_id}/profile",
                json={"voice": None, "speaking_rate": None, "sentence_silence": None},
            )
            assert reset.status_code == 200, reset.text
            assert reset.json()["effective"]["voice_source"] == "global"
            amy_after = next(
                item for item in client.get("/api/v1/control/voice/catalog").json()["voices"]
                if item["key"] == "en_US-amy-medium"
            )
            assert amy_after["agent_reference_count"] == 0
            assert amy_after["can_uninstall"] is True
            removed = client.delete("/api/v1/control/voice/catalog/en_US-amy-medium")
            assert removed.status_code == 200, removed.text
            assert uninstall_calls == ["piper-voice-amy-medium"]

            # Global changes continue to flow through inherited Agents.
            voice_settings.save_preferences({**voice_settings.DEFAULTS, "tts_voice": "en_GB-alan-medium"})
            inherited_after_global_change = client.get(
                f"/api/v1/control/voice/agents/{agent_id}/profile"
            ).json()
            assert inherited_after_global_change["effective"]["voice"] == "en_GB-alan-medium"
            assert inherited_after_global_change["effective"]["voice_source"] == "global"
    finally:
        voice_settings._install_state = original_install_state
        local_voice.synthesize = original_synthesize
        local_apps.uninstall = original_uninstall

print("HomeServer v0.45 Agent Voice Profiles regression passed")
