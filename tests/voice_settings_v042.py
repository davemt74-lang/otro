from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


with tempfile.TemporaryDirectory(prefix="homeserver-voice-settings-v042-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import voice_settings  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    with TestClient(app) as client:
        scheduler.stop()

        # Voice preferences remain behind the canonical owner-control session.
        assert client.get("/api/v1/control/voice/settings").status_code == 401
        assert client.put("/api/v1/control/voice/settings", json={}).status_code == 401
        assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200

        initial = client.get("/api/v1/control/voice/settings")
        assert initial.status_code == 200, initial.text
        initial_json = initial.json()
        assert initial_json["preferences"] == voice_settings.DEFAULTS
        assert initial_json["storage"] == {
            "server": "HomeServer system_settings",
            "device_selection": "browser-local",
        }
        assert initial_json["choices"]["stt_models"][0]["key"] == "tiny.en-q8_0"
        assert initial_json["choices"]["tts_voices"][0]["key"] == "en_US-lessac-medium"
        assert {item["key"] for item in initial_json["choices"]["default_modes"]} == {"conversation", "dictation"}

        preferences = {
            "stt_model": "tiny.en-q8_0",
            "tts_voice": "en_US-lessac-medium",
            "speaking_rate": 1.25,
            "sentence_silence": 0.35,
            "listen_silence_ms": 1300,
            "no_speech_timeout_ms": 10500,
            "max_segment_ms": 45000,
            "default_mode": "dictation",
            "strict_local_default": True,
        }
        saved = client.put("/api/v1/control/voice/settings", json=preferences)
        assert saved.status_code == 200, saved.text
        assert saved.json()["saved"] is True
        assert saved.json()["preferences"] == preferences

        # Persistence uses the existing system_settings table, avoiding a schema
        # migration and keeping the preference bundle portable with HomeServer.
        with db() as connection:
            row = connection.execute(
                "SELECT value_json FROM system_settings WHERE setting_key=?",
                (voice_settings.SETTING_KEY,),
            ).fetchone()
        assert row is not None
        assert json.loads(row["value_json"]) == preferences
        assert client.get("/api/v1/control/voice/settings").json()["preferences"] == preferences

        status = client.get("/api/v1/control/voice/status")
        assert status.status_code == 200, status.text
        assert status.json()["version"] == "v0.42"
        assert status.json()["preferences"] == preferences

        # Speaking speed is converted into Piper's inverse length scale.
        assert voice_settings.piper_length_scale(preferences) == 0.8

        # Public API validation is fail-closed rather than silently clamping bad
        # settings. The service normalizer is still defensive for old/corrupt DB data.
        invalid_cases = [
            {**preferences, "stt_model": "unknown-model"},
            {**preferences, "tts_voice": "unknown-voice"},
            {**preferences, "speaking_rate": 2.5},
            {**preferences, "sentence_silence": -0.1},
            {**preferences, "listen_silence_ms": 399},
            {**preferences, "no_speech_timeout_ms": 1999},
            {**preferences, "max_segment_ms": 60001},
            {**preferences, "default_mode": "always-listening"},
        ]
        for payload in invalid_cases:
            response = client.put("/api/v1/control/voice/settings", json=payload)
            assert response.status_code == 422, (payload, response.text)

        # Browser audio device identifiers are intentionally forbidden at this
        # boundary, not merely ignored, so they can never enter portable settings.
        for device_field in ("input_device_id", "output_device_id", "microphone_id", "speaker_id"):
            response = client.put(
                "/api/v1/control/voice/settings",
                json={**preferences, device_field: "synthetic-device-identifier"},
            )
            assert response.status_code == 422, (device_field, response.text)

        # A rejected request must not mutate the last valid stored preferences.
        assert client.get("/api/v1/control/voice/settings").json()["preferences"] == preferences

        # Defensive normalization of legacy/corrupt values remains bounded.
        normalized = voice_settings.normalize({
            "stt_model": "bad",
            "tts_voice": "bad",
            "speaking_rate": 99,
            "sentence_silence": -5,
            "listen_silence_ms": 99999,
            "no_speech_timeout_ms": -1,
            "max_segment_ms": 1,
            "default_mode": "bad",
            "strict_local_default": 1,
        })
        assert normalized == {
            "stt_model": "tiny.en-q8_0",
            "tts_voice": "en_US-lessac-medium",
            "speaking_rate": 1.6,
            "sentence_silence": 0.0,
            "listen_silence_ms": 3000,
            "no_speech_timeout_ms": 2000,
            "max_segment_ms": 5000,
            "default_mode": "conversation",
            "strict_local_default": True,
        }

print("HomeServer v0.42 Voice Settings backend regression passed")
