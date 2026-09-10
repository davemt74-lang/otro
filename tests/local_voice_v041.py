from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pcm16_wav(samples: int = 1600, sample_rate: int = 16000) -> bytes:
    data = b"\x00\x00" * samples
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(data))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )


with tempfile.TemporaryDirectory(prefix="homeserver-local-voice-v041-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import local_voice  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    fixtures = Path(data_dir) / "voice-fixtures"
    fixtures.mkdir(parents=True)
    paths = {
        ("whisper-stt", "runtime/Release/whisper-cli.exe"): fixtures / "whisper-cli.exe",
        ("whisper-stt", "models/ggml-tiny.en-q8_0.bin"): fixtures / "ggml-tiny.en-q8_0.bin",
        ("piper-tts", "runtime/piper/piper.exe"): fixtures / "piper.exe",
        ("piper-tts", "voices/en_US-lessac-medium.onnx"): fixtures / "voice.onnx",
        ("piper-tts", "voices/en_US-lessac-medium.onnx.json"): fixtures / "voice.onnx.json",
    }
    for fixture in paths.values():
        fixture.write_bytes(b"fixture")

    original_resolver = local_voice._resolve_managed_file
    original_run = subprocess.run
    calls: list[dict] = []
    private_text = "SYNTHETIC_PRIVATE_SPEECH_91827 ; & should-never-be-command"

    def fake_resolver(app_key: str, relative_path: str) -> Path:
        return paths[(app_key, relative_path)]

    def fake_run(command, **kwargs):
        calls.append({"command": list(command), "kwargs": dict(kwargs)})
        assert kwargs.get("shell") is False
        assert kwargs.get("timeout") in {
            local_voice.TRANSCRIBE_TIMEOUT_SECONDS,
            local_voice.SYNTHESIZE_TIMEOUT_SECONDS,
        }
        executable = Path(command[0]).name.lower()
        if executable == "whisper-cli.exe":
            assert "-m" in command and "-f" in command and "-otxt" in command and "-of" in command
            assert "-np" in command and "-nt" in command
            output_prefix = Path(command[command.index("-of") + 1])
            output_prefix.with_suffix(".txt").write_text("Synthetic local transcript\n", encoding="utf-8")
        elif executable == "piper.exe":
            assert kwargs.get("input") == private_text
            assert private_text not in " ".join(command)
            output_path = Path(command[command.index("--output_file") + 1])
            output_path.write_bytes(pcm16_wav(10))
        else:
            raise AssertionError(f"unexpected executable: {command[0]}")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    local_voice._resolve_managed_file = fake_resolver
    subprocess.run = fake_run
    try:
        with TestClient(app) as client:
            scheduler.stop()

            # Every runtime route is owner-session only because it is below the
            # canonical /api/v1/control boundary.
            assert client.get("/api/v1/control/voice/status").status_code == 401
            assert client.post(
                "/api/v1/control/voice/transcribe",
                files={"file": ("speech.wav", pcm16_wav(), "audio/wav")},
            ).status_code == 401
            assert client.post(
                "/api/v1/control/voice/synthesize",
                json={"text": "blocked before owner auth"},
            ).status_code == 401

            assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200
            status = client.get("/api/v1/control/voice/status")
            assert status.status_code == 200
            assert status.json()["version"] == "v0.42"
            assert status.json()["local"] is True
            assert status.json()["strict_local_supported"] is True

            invalid = client.post(
                "/api/v1/control/voice/transcribe",
                files={"file": ("bad.wav", b"not-a-wave", "audio/wav")},
            )
            assert invalid.status_code == 415

            transcribed = client.post(
                "/api/v1/control/voice/transcribe",
                files={"file": ("speech.wav", pcm16_wav(), "audio/wav")},
            )
            assert transcribed.status_code == 200, transcribed.text
            assert transcribed.json() == {
                "text": "Synthetic local transcript",
                "provider": "whisper.cpp",
                "local": True,
                "model": "tiny.en-q8_0",
            }

            spoken = client.post("/api/v1/control/voice/synthesize", json={"text": private_text})
            assert spoken.status_code == 200, spoken.text
            assert spoken.headers["content-type"].startswith("audio/wav")
            assert spoken.headers["cache-control"] == "no-store"
            assert spoken.headers["x-homeserver-voice-provider"] == "piper"
            assert spoken.content[:4] == b"RIFF" and spoken.content[8:12] == b"WAVE"

            # Speech content is never put in the audited activity stream.
            activity = json.dumps(client.get("/api/v1/control/activity?limit=200").json(), ensure_ascii=False)
            assert private_text not in activity
            assert "Synthetic local transcript" not in activity

            # Voice temp workspaces are removed after each operation.
            temp_root = Path(data_dir) / "voice-runtime" / ".tmp"
            assert temp_root.is_dir()
            assert list(temp_root.iterdir()) == []

            whisper_call = next(item for item in calls if Path(item["command"][0]).name.lower() == "whisper-cli.exe")
            piper_call = next(item for item in calls if Path(item["command"][0]).name.lower() == "piper.exe")
            assert whisper_call["kwargs"]["stdin"] is subprocess.DEVNULL
            assert piper_call["kwargs"]["input"] == private_text
            assert "--length_scale" in piper_call["command"]
            assert "--sentence_silence" in piper_call["command"]
            assert private_text not in " ".join(piper_call["command"])

            # Do not trust whisper.cpp's process exit code by itself. A missing
            # output artifact is an error even if the executable returns zero.
            def no_output_run(command, **kwargs):
                return SimpleNamespace(returncode=0, stdout="", stderr="decode failed")

            subprocess.run = no_output_run
            missing_output = client.post(
                "/api/v1/control/voice/transcribe",
                files={"file": ("speech.wav", pcm16_wav(), "audio/wav")},
            )
            assert missing_output.status_code == 422
    finally:
        local_voice._resolve_managed_file = original_resolver
        subprocess.run = original_run

print("HomeServer v0.42 local Whisper/Piper voice runtime regression passed")
