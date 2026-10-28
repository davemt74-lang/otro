#!/usr/bin/env python3
"""Phase 2 source-bound wearable audio gateway acceptance; no live audio devices.

Run from OTRO root: python tests/vp3_wearable_voice_gateway_v01.py
Uses a real temporary OTRO paired-app DB and synthetic PCM WAV audio.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import threading
import wave
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def wav(seconds: float = 0.2, sample_rate: int = 16000, channels: int = 1) -> bytes:
    data = io.BytesIO()
    with wave.open(data, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(b"\x00\x00" * int(seconds * sample_rate) * channels)
    return data.getvalue()


def expect_error(fn, code: int) -> None:
    try:
        fn()
    except Exception as exc:
        assert getattr(exc, "status_code", None) == code, (code, repr(exc))
    else:
        raise AssertionError(f"Expected HTTP {code}")


def run() -> None:
    with tempfile.TemporaryDirectory(prefix="vp3-wearable-voice-") as root:
        os.environ["HOMESERVER_DATA_DIR"] = root
        os.environ["VP3_WEARABLE_AUDIO_ENABLED"] = "1"
        os.environ["VP3_WEARABLE_AUDIO_ALLOW_LOCAL_HTTP"] = "1"  # TestClient only.
        os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

        from app.database import initialize_database
        from app.services import pairing, wearable_voice_gateway as voice
        from app.wearable_voice_api import router, _guard_transport

        initialize_database()
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        device = "vp3-wearable-lab01"

        def pair(name: str, permissions: list[str]) -> str:
            pending = pairing.create_pairing_request(name, name, permissions)
            approved = pairing.approve_pairing_request(pending["request_id"])
            assert approved is not None
            return pending["claim_token"]

        token = pair(device, ["devices.control", "agent.chat"])
        token_other = pair("vp3-wearable-lab02", ["devices.control", "agent.chat"])
        token_underprivileged = pair("vp3-wearable-readonly", ["agent.chat"])

        def headers(t: str, *, wav_body: bool = False) -> dict:
            h = {"Authorization": "Bearer " + t}
            if wav_body:
                h["Content-Type"] = "audio/wav"
            return h

        def start(t: str = token, name: str = device):
            return client.post(
                "/api/v1/wearable/audio/start",
                headers=headers(t),
                json={"device_id": name, "intent": "press"},
            )

        # Feature is explicitly opt-in and cannot be activated by a request.
        os.environ["VP3_WEARABLE_AUDIO_ENABLED"] = "0"
        assert start().status_code == 503
        os.environ["VP3_WEARABLE_AUDIO_ENABLED"] = "1"
        assert client.post(
            "/api/v1/wearable/audio/start",
            json={"device_id": device, "intent": "press"},
        ).status_code == 401
        assert start("wrong-token").status_code == 401
        assert start(token_underprivileged, "vp3-wearable-readonly").status_code == 403
        assert start(token, "vp3-wearable-lab02").status_code == 403
        assert start(token, "some-unpaired-device").status_code == 403
        assert start(token, device).status_code == 200

        # Only one active turn per identity; turn IDs are never shared across devices.
        open_turn = start()
        assert open_turn.status_code == 409
        first = client.post(
            "/api/v1/wearable/audio/start",
            headers=headers(token_other),
            json={"device_id": "vp3-wearable-lab02", "intent": "press"},
        )
        assert first.status_code == 200, first.text
        other_id = first.json()["turn_id"]
        current_id = voice.registry._turns[next(
            key for key, item in voice.registry._turns.items() if item.device_id == device
        )].id
        assert client.post(
            f"/api/v1/wearable/audio/stop/{other_id}",
            headers=headers(token),
        ).status_code == 409
        assert client.post(
            f"/api/v1/wearable/audio/stop/{other_id}",
            headers=headers(token_other),
        ).status_code == 204
        assert client.post(
            f"/api/v1/wearable/audio/stop/{current_id}",
            headers=headers(token),
        ).status_code == 204
        assert client.post(
            f"/api/v1/wearable/audio/finish/{current_id}",
            headers=headers(token, wav_body=True),
            content=wav(),
        ).status_code == 409

        # WAV parser accepts exact PCM16 mono 16k and rejects bad source formats.
        voice.validate_wav(wav())
        for invalid in (b"not audio", wav(channels=2), wav(sample_rate=8000), wav(seconds=0.01)):
            expect_error(lambda data=invalid: voice.validate_wav(data), 413 if len(invalid) < 44 else 422)
        expect_error(lambda: voice.validate_wav(b"\x00" * (voice.MAX_WAV_BYTES + 1)), 413)

        turn_id = start().json()["turn_id"]
        assert client.post(
            f"/api/v1/wearable/audio/finish/{turn_id}",
            headers=headers(token),
            content=wav(),
        ).status_code == 415
        # A missing/incorrect MIME type never consumes an otherwise valid turn.

        response_wav = wav(seconds=0.1)
        with (
            patch.object(voice.local_voice, "transcribe", return_value={"text": "Hello VP3"}) as transcribe,
            patch.object(voice.context_chat, "chat", return_value={"reply": "Hello David"}) as chat,
            patch.object(voice.local_voice, "synthesize", return_value=response_wav) as synth,
        ):
            finish = client.post(
                f"/api/v1/wearable/audio/finish/{turn_id}",
                headers=headers(token, wav_body=True),
                content=wav(),
            )
            assert finish.status_code == 200, finish.text
            assert finish.content == response_wav
            assert finish.headers["cache-control"] == "no-store"
            assert transcribe.call_count == chat.call_count == synth.call_count == 1
            assert chat.call_args.args[0] == "app:" + device
            kwargs = chat.call_args.kwargs
            assert kwargs["owner_tools"] is False
            assert kwargs["read_only"] is True
            assert kwargs["tool_permissions"] == set()
            assert kwargs["include_memory"] is False
            assert kwargs["include_knowledge"] is False
        # One-time turn identifier, no replay of raw audio or chat.
        assert client.post(
            f"/api/v1/wearable/audio/finish/{turn_id}",
            headers=headers(token, wav_body=True),
            content=wav(),
        ).status_code == 409

        # A malformed upload consumes its turn and invokes no inference.
        bad_id = start().json()["turn_id"]
        with patch.object(voice.local_voice, "transcribe") as transcribe:
            malformed = client.post(
                f"/api/v1/wearable/audio/finish/{bad_id}",
                headers=headers(token, wav_body=True),
                content=wav(channels=2),
            )
            assert malformed.status_code == 422
            assert not transcribe.called
        assert client.post(
            f"/api/v1/wearable/audio/finish/{bad_id}",
            headers=headers(token, wav_body=True),
            content=wav(),
        ).status_code == 409

        # Cancel while Whisper is running: no downstream chat or TTS.
        cancelled_id = start().json()["turn_id"]
        entered = threading.Event()
        release = threading.Event()
        holder: list = []

        def blocked_transcribe(data: bytes, *, cancel: threading.Event):
            del data
            entered.set()
            assert release.wait(4)
            return {"text": "Should never reach chat"}

        with (
            patch.object(voice.local_voice, "transcribe", side_effect=blocked_transcribe),
            patch.object(voice.context_chat, "chat") as chat,
        ):
            def finish_async() -> None:
                holder.append(client.post(
                    f"/api/v1/wearable/audio/finish/{cancelled_id}",
                    headers=headers(token, wav_body=True),
                    content=wav(),
                ))
            thread = threading.Thread(target=finish_async, daemon=True)
            thread.start()
            assert entered.wait(4)
            assert client.post(
                f"/api/v1/wearable/audio/stop/{cancelled_id}",
                headers=headers(token),
            ).status_code == 204
            release.set()
            thread.join(timeout=6)
            assert not thread.is_alive()
            assert holder[0].status_code == 409
            assert not chat.called

        # Revocation is effective without restarting the HomeServer.
        revoke_id = start().json()["turn_id"]
        pairing.revoke_paired_app(device)
        assert client.post(
            f"/api/v1/wearable/audio/finish/{revoke_id}",
            headers=headers(token, wav_body=True),
            content=wav(),
        ).status_code == 401
        assert start().status_code == 401
        voice.registry.stop(device, revoke_id)  # Remove stale in-memory fixture.

        # Plaintext HTTP is rejected even on loopback unless explicitly opted in.
        os.environ["VP3_WEARABLE_AUDIO_ALLOW_LOCAL_HTTP"] = "0"
        assert client.post(
            "/api/v1/wearable/audio/start",
            headers=headers(token_other),
            json={"device_id": "vp3-wearable-lab02", "intent": "press"},
        ).status_code == 426
        os.environ["VP3_WEARABLE_AUDIO_ALLOW_LOCAL_HTTP"] = "1"

        # HTTP on a non-local network is rejected even with valid pairing.
        class RemoteRequest:
            url = type("URL", (), {"scheme": "http"})()
            client = type("Client", (), {"host": "203.0.113.50"})()
        try:
            _guard_transport(RemoteRequest())
            raise AssertionError("Non-TLS remote bearer upload accepted")
        except HTTPException as exc:
            assert exc.status_code == 426

        # Session timeout, per-device conflict, and source-bound revocation.
        now = [0.0]
        registry = voice.TurnRegistry(clock=lambda: now[0])
        expiring = registry.begin(device)
        expect_error(lambda: registry.begin(device), 409)
        now[0] = voice.MAX_SECONDS + 0.1
        expect_error(lambda: registry.consume(device, expiring.id), 409)

        # The endpoint is registered on the real OTRO app as a dark-launch API.
        from app.bridge import app as real_app
        assert "/api/v1/wearable/audio/start" in {r.path for r in real_app.routes}

        print("VP3 wearable source-bound voice v0.1 security & voice path: PASS")


if __name__ == "__main__":
    run()
