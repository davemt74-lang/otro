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


with tempfile.TemporaryDirectory(prefix="homeserver-voice-catalog-v044-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import local_apps, local_voice, voice_settings  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    fixtures = Path(data_dir) / "voice-preview-fixtures"
    fixtures.mkdir(parents=True)
    piper_exe = fixtures / "piper.exe"
    amy_model = fixtures / "amy.onnx"
    amy_config = fixtures / "amy.onnx.json"
    for item in (piper_exe, amy_model, amy_config):
        item.write_bytes(b"fixture")

    original_resolver = local_voice._resolve_managed_file
    original_run = subprocess.run
    original_install = local_apps.install
    original_uninstall = local_apps.uninstall
    original_install_state = voice_settings._install_state

    resolver_calls: list[tuple[str, str]] = []
    commands: list[list[str]] = []
    install_calls: list[tuple[str, bool]] = []
    uninstall_calls: list[str] = []
    installed = {
        "piper-tts": True,
        "piper-voice-amy-medium": True,
        "piper-voice-ryan-medium": False,
        "piper-voice-alan-medium": False,
    }
    healthy = dict(installed)

    def fake_install_state(app_key: str) -> dict:
        return {
            "installed": bool(installed.get(app_key)),
            "healthy": bool(healthy.get(app_key)),
            "reason": None if healthy.get(app_key) else "Repair required." if installed.get(app_key) else "Not installed.",
            "version": "fixture-v1" if installed.get(app_key) else None,
        }

    def fake_resolver(app_key: str, relative_path: str) -> Path:
        resolver_calls.append((app_key, relative_path))
        lookup = {
            ("piper-tts", "runtime/piper/piper.exe"): piper_exe,
            ("piper-voice-amy-medium", "voices/en_US-amy-medium.onnx"): amy_model,
            ("piper-voice-amy-medium", "voices/en_US-amy-medium.onnx.json"): amy_config,
        }
        return lookup[(app_key, relative_path)]

    def fake_run(command, **kwargs):
        commands.append(list(command))
        output = Path(command[command.index("--output_file") + 1])
        output.write_bytes(b"RIFF" + (b"\x00" * 4) + b"WAVE" + (b"\x00" * 40))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def fake_install(app_key: str, *, update: bool = False) -> dict:
        install_calls.append((app_key, update))
        installed[app_key] = True
        healthy[app_key] = True
        return {"changed": True, "package": {"key": app_key}}

    def fake_uninstall(app_key: str) -> dict:
        uninstall_calls.append(app_key)
        installed[app_key] = False
        healthy[app_key] = False
        return {"changed": True, "app_key": app_key}

    try:
        voice_settings._install_state = fake_install_state
        local_voice._resolve_managed_file = fake_resolver
        subprocess.run = fake_run
        local_apps.install = fake_install
        local_apps.uninstall = fake_uninstall

        with TestClient(app) as client:
            scheduler.stop()
            assert client.get("/api/v1/control/voice/catalog").status_code == 401
            assert client.post(
                "/api/v1/control/voice/preview",
                json={"voice": "en_US-amy-medium", "text": "Unauthorized preview."},
            ).status_code == 401
            assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200

            catalog = client.get("/api/v1/control/voice/catalog").json()
            assert catalog["version"] == "v0.43"
            assert catalog["management_version"] == "v0.44"
            amy = next(item for item in catalog["voices"] if item["key"] == "en_US-amy-medium")
            ryan = next(item for item in catalog["voices"] if item["key"] == "en_US-ryan-medium")
            assert amy["available"] is True
            assert amy["active"] is False
            assert amy["can_uninstall"] is True
            assert amy["download_bytes"] > 0
            assert amy["source_label"]
            assert ryan["available"] is False
            assert ryan["management_state"] == "install"

            preview = client.post(
                "/api/v1/control/voice/preview",
                json={
                    "voice": "en_US-amy-medium",
                    "text": "Preview Amy without changing the saved voice.",
                    "speaking_rate": 1.25,
                    "sentence_silence": 0.35,
                },
            )
            assert preview.status_code == 200, preview.text
            assert preview.headers["content-type"].startswith("audio/wav")
            assert preview.headers["x-homeserver-voice"] == "en_US-amy-medium"
            assert ("piper-tts", "runtime/piper/piper.exe") in resolver_calls
            assert ("piper-voice-amy-medium", "voices/en_US-amy-medium.onnx") in resolver_calls
            command = commands[-1]
            assert command[command.index("--length_scale") + 1] == "0.8"
            assert command[command.index("--sentence_silence") + 1] == "0.35"
            assert voice_settings.get_preferences()["tts_voice"] == "en_US-lessac-medium"

            bad_preview = client.post(
                "/api/v1/control/voice/preview",
                json={"voice": "external-voice", "text": "No."},
            )
            assert bad_preview.status_code == 422

            install_response = client.post("/api/v1/control/voice/catalog/en_US-ryan-medium/install")
            assert install_response.status_code == 200, install_response.text
            assert ("piper-voice-ryan-medium", False) in install_calls
            assert install_response.json()["voice"]["available"] is True

            healthy["piper-voice-ryan-medium"] = False
            repair_response = client.post("/api/v1/control/voice/catalog/en_US-ryan-medium/repair")
            assert repair_response.status_code == 200, repair_response.text
            assert ("piper-voice-ryan-medium", True) in install_calls

            installed["piper-voice-alan-medium"] = False
            healthy["piper-voice-alan-medium"] = False
            invalid_repair = client.post("/api/v1/control/voice/catalog/en_GB-alan-medium/repair")
            assert invalid_repair.status_code == 409

            bundled_delete = client.delete("/api/v1/control/voice/catalog/en_US-lessac-medium")
            assert bundled_delete.status_code == 409

            saved = client.put(
                "/api/v1/control/voice/settings",
                json={**voice_settings.DEFAULTS, "tts_voice": "en_US-amy-medium"},
            )
            assert saved.status_code == 200
            active_delete = client.delete("/api/v1/control/voice/catalog/en_US-amy-medium")
            assert active_delete.status_code == 409
            assert uninstall_calls == []

            switched = client.put(
                "/api/v1/control/voice/settings",
                json={**voice_settings.DEFAULTS, "tts_voice": "en_US-lessac-medium"},
            )
            assert switched.status_code == 200
            removed = client.delete("/api/v1/control/voice/catalog/en_US-amy-medium")
            assert removed.status_code == 200, removed.text
            assert uninstall_calls == ["piper-voice-amy-medium"]
            assert removed.json()["voice"]["installed"] is False
    finally:
        voice_settings._install_state = original_install_state
        local_voice._resolve_managed_file = original_resolver
        subprocess.run = original_run
        local_apps.install = original_install
        local_apps.uninstall = original_uninstall

print("HomeServer v0.44 Voice Preview / Catalog Management regression passed")
