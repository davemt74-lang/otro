from __future__ import annotations

import base64
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-v235-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import initialize_database  # noqa: E402
    from app.services import remote_bridge  # noqa: E402
    from app.services.pairing import approve_pairing_request, create_pairing_request  # noqa: E402

    initialize_database()
    pair = create_pairing_request("vp3", "VP3", ["agent.chat"])
    approved = approve_pairing_request(pair["request_id"])
    assert approved is not None
    token = str(pair["claim_token"])

    seen: dict = {}

    def fake_generate(messages, model_override=None, cancellation_token=None):
        seen["messages"] = messages
        seen["model_override"] = model_override
        return {
            "provider": "ollama",
            "model": "local-test",
            "content": "Profile-safe local answer",
            "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
        }

    remote_bridge.providers.generate_ollama = fake_generate
    remote_bridge.local_voice.status = lambda: {
        "tts": {"available": True},
        "stt": {"available": True},
    }
    remote_bridge.agent_voice_profiles.primary_agent_id = lambda: 1
    remote_bridge.agent_voice_profiles.get_profile = lambda agent_id: {
        "effective": {
            "voice": "en_US-lessac-medium",
            "voice_source": "global",
            "speaking_rate": 1.0,
            "sentence_silence": 0.2,
            "ready": True,
            "fallback": False,
            "warning": None,
        }
    }
    fake_wav = b"RIFF" + (b"\x00" * 4) + b"WAVE" + (b"\x00" * 96)
    remote_bridge.local_voice.synthesize = lambda text, **kwargs: fake_wav
    remote_bridge.local_voice.transcribe = lambda audio: {
        "text": "local transcript",
        "provider": "whisper.cpp",
        "model": "tiny.en",
        "local": True,
    }

    infer = remote_bridge.dispatch_remote_request(
        "agent.infer.local",
        {
            "messages": [
                {"role": "system", "content": "Use only approved public profile context."},
                {"role": "user", "content": "What does the approved profile say?"},
            ]
        },
        token,
    )
    assert infer["ok"] is True
    assert infer["payload"]["reply"] == "Profile-safe local answer"
    assert infer["payload"]["compute_source"] == "homeserver_local"
    assert infer["payload"]["stateless"] is True
    assert infer["payload"]["tools_enabled"] is False
    assert seen["messages"][0]["content"] == "Use only approved public profile context."

    try:
        remote_bridge.dispatch_remote_request(
            "agent.infer.local",
            {"messages": [{"role": "tool", "content": "should fail"}]},
            token,
        )
        raise AssertionError("invalid stateless inference role was accepted")
    except remote_bridge.RemoteBridgeError:
        pass

    status = remote_bridge.dispatch_remote_request("speech.status", {}, token)
    assert status["ok"] is True
    assert status["payload"]["available"] is True
    assert status["payload"]["transcription_available"] is True
    assert status["payload"]["max_audio_bytes"] == 150 * 1024
    assert status["payload"]["max_text_chars"] == 220

    synth = remote_bridge.dispatch_remote_request(
        "speech.synthesize",
        {"text": "Short local voice sentence."},
        token,
    )
    assert synth["ok"] is True
    assert base64.b64decode(synth["payload"]["audio_base64"]) == fake_wav
    assert synth["payload"]["content_type"] == "audio/wav"
    assert synth["payload"]["provider"] == "piper"

    try:
        remote_bridge.dispatch_remote_request(
            "speech.synthesize",
            {"text": "x" * 221},
            token,
        )
        raise AssertionError("oversized speech text was accepted")
    except remote_bridge.RemoteBridgeError:
        pass

    transcript = remote_bridge.dispatch_remote_request(
        "speech.transcribe",
        {"audio_base64": base64.b64encode(fake_wav).decode("ascii")},
        token,
    )
    assert transcript["ok"] is True
    assert transcript["payload"]["text"] == "local transcript"
    assert transcript["payload"]["provider"] == "whisper.cpp"

    try:
        remote_bridge.dispatch_remote_request(
            "speech.transcribe",
            {"audio_base64": base64.b64encode(b"x" * (150 * 1024 + 1)).decode("ascii")},
            token,
        )
        raise AssertionError("oversized voice audio was accepted")
    except remote_bridge.RemoteBridgeError:
        pass

    try:
        remote_bridge.dispatch_remote_request("speech.status", {}, "invalid-token")
        raise AssertionError("invalid paired-app token was accepted")
    except remote_bridge.RemoteBridgeError:
        pass

    assert remote_bridge.settings.max_remote_bridge_message_bytes == 256 * 1024

print("HomeServer v2.3 Section 5 voice/profile-safe execution: PASS")
