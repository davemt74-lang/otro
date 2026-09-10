from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


with tempfile.TemporaryDirectory(prefix="homeserver-voice-catalog-v043-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import local_voice, voice_settings  # noqa: E402
    from app.services.local_app_catalog import CATALOG  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    expected_voices = {
        "en_US-lessac-medium",
        "en_US-amy-medium",
        "en_US-ryan-medium",
        "en_GB-alan-medium",
    }
    expected_packs = {
        "piper-voice-amy-medium",
        "piper-voice-ryan-medium",
        "piper-voice-alan-medium",
    }

    assert expected_packs.issubset(CATALOG)
    for pack_key in expected_packs:
        package = CATALOG[pack_key]
        assert package["runtime_app_key"] == "piper-tts"
        assert package["capabilities"] == ["voice.pack", "voice.tts.voice", "voice.tts.piper"]
        assert len(package["artifacts"]) == 2
        assert all(len(item["sha256"]) == 64 for item in package["artifacts"])
        assert all(item["url"].startswith("https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/") for item in package["artifacts"])
        assert not any(path.startswith("runtime/") for path in package["required_paths"])

    fixtures = Path(data_dir) / "voice-pack-fixtures"
    fixtures.mkdir(parents=True)
    piper_exe = fixtures / "piper.exe"
    amy_model = fixtures / "amy.onnx"
    amy_config = fixtures / "amy.onnx.json"
    for item in (piper_exe, amy_model, amy_config):
        item.write_bytes(b"fixture")

    resolver_calls: list[tuple[str, str]] = []
    original_resolver = local_voice._resolve_managed_file
    original_run = subprocess.run

    def fake_resolver(app_key: str, relative_path: str) -> Path:
        resolver_calls.append((app_key, relative_path))
        lookup = {
            ("piper-tts", "runtime/piper/piper.exe"): piper_exe,
            ("piper-voice-amy-medium", "voices/en_US-amy-medium.onnx"): amy_model,
            ("piper-voice-amy-medium", "voices/en_US-amy-medium.onnx.json"): amy_config,
        }
        return lookup[(app_key, relative_path)]

    def fake_run(command, **kwargs):
        output = Path(command[command.index("--output_file") + 1])
        output.write_bytes(b"RIFF" + (b"\x00" * 4) + b"WAVE" + (b"\x00" * 40))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    try:
        with TestClient(app) as client:
            scheduler.stop()
            assert client.get("/api/v1/control/voice/catalog").status_code == 401
            assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200

            catalog_response = client.get("/api/v1/control/voice/catalog")
            assert catalog_response.status_code == 200, catalog_response.text
            catalog = catalog_response.json()
            assert catalog["version"] == "v0.43"
            assert catalog["runtime"]["app_key"] == "piper-tts"
            assert {item["key"] for item in catalog["voices"]} == expected_voices

            choices = client.get("/api/v1/control/voice/settings").json()["choices"]["tts_voices"]
            assert {item["key"] for item in choices} == expected_voices
            amy = next(item for item in choices if item["key"] == "en_US-amy-medium")
            assert amy["install_app_key"] == "piper-voice-amy-medium"
            assert amy["runtime_app_key"] == "piper-tts"
            assert amy["bundled_with_runtime"] is False

            preferences = dict(voice_settings.DEFAULTS)
            preferences["tts_voice"] = "en_US-amy-medium"
            saved = client.put("/api/v1/control/voice/settings", json=preferences)
            assert saved.status_code == 200, saved.text
            assert saved.json()["preferences"]["tts_voice"] == "en_US-amy-medium"

            rejected = client.put(
                "/api/v1/control/voice/settings",
                json={**preferences, "tts_voice": "untrusted-external-voice"},
            )
            assert rejected.status_code == 422

            local_voice._resolve_managed_file = fake_resolver
            subprocess.run = fake_run
            spoken = client.post("/api/v1/control/voice/synthesize", json={"text": "Synthetic voice-pack routing test."})
            assert spoken.status_code == 200, spoken.text
            assert ("piper-tts", "runtime/piper/piper.exe") in resolver_calls
            assert ("piper-voice-amy-medium", "voices/en_US-amy-medium.onnx") in resolver_calls
            assert ("piper-voice-amy-medium", "voices/en_US-amy-medium.onnx.json") in resolver_calls
            assert not any(app_key == "piper-voice-amy-medium" and path.startswith("runtime/") for app_key, path in resolver_calls)
    finally:
        local_voice._resolve_managed_file = original_resolver
        subprocess.run = original_run

print("HomeServer v0.43 Local Voice Packs / Voice Catalog regression passed")
