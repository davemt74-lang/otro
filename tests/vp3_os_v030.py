from __future__ import annotations

import io
import os
import struct
import sys
import tempfile
import time
import wave
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def wav_bytes(samples: int = 1600, sample_rate: int = 16000) -> bytes:
    pcm = b"\x00\x00" * samples
    target = io.BytesIO()
    with wave.open(target, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return target.getvalue()


def controller_hello() -> dict:
    return {
        "type": "hello",
        "protocol": "vp3-hw-v1",
        "controller_id": "vp3-node-devkit",
        "firmware": "0.20.0",
        "hardware_revision": "node-devkit-a",
        "components": [
            "agent_button",
            "status_light",
            "microphone",
            "speaker",
            "privacy_switch",
        ],
        "capabilities": [
            "status_light",
            "agent_button",
            "privacy_switch",
            "mic_power_cut",
            "mic_power_sense",
        ],
    }


def controller_state(seq: int, *, privacy: bool = False, mic_powered: bool = True) -> dict:
    return {
        "type": "state",
        "seq": seq,
        "components": {
            "agent_button": {"present": True, "ready": True, "pressed": False},
            "status_light": {"present": True, "ready": True},
            "microphone": {"present": True, "ready": not privacy and mic_powered},
            "speaker": {"present": True, "ready": True},
            "privacy_switch": {
                "present": True,
                "ready": True,
                "engaged": privacy,
                "physical_disconnect": True,
                "microphone_powered": mic_powered,
            },
        },
    }


with tempfile.TemporaryDirectory(prefix="vp3-os-v030-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir
    os.environ["VP3_OS_HARDWARE_PROFILE"] = "vp3_node"
    os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import (  # noqa: E402
        agent_voice_profiles,
        cognitive_runtime,
        context_chat,
        device_audio,
        hardware_adapters,
        local_voice,
        physical_agent,
        vp3_os,
    )
    from app.services.tasks import scheduler  # noqa: E402

    calls: dict[str, list] = {
        "capture": [],
        "chat": [],
        "tts": [],
        "play": [],
        "cognition": [],
        "cancel_capture": [],
        "stop_playback": [],
    }

    original_voice_status = local_voice.status
    original_transcribe = local_voice.transcribe
    original_synthesize = local_voice.synthesize
    original_chat = context_chat.chat
    original_resolve = agent_voice_profiles.resolve_effective
    original_emit = cognitive_runtime.emit_event

    audio = device_audio.device_audio
    original_start_capture = audio.start_capture
    original_stop_capture = audio.stop_capture
    original_cancel_capture = audio.cancel_capture
    original_play_wav = audio.play_wav
    original_stop_playback = audio.stop_playback
    original_runtime_state = audio.runtime_state

    capturing = {"value": False}
    playing = {"value": False}

    def fake_start_capture():
        capturing["value"] = True
        calls["capture"].append("start")

    def fake_stop_capture():
        assert capturing["value"] is True
        capturing["value"] = False
        calls["capture"].append("stop")
        return wav_bytes()

    def fake_cancel_capture():
        capturing["value"] = False
        calls["cancel_capture"].append("cancel")

    def fake_play_wav(payload: bytes):
        assert payload[:4] == b"RIFF"
        playing["value"] = True
        calls["play"].append(len(payload))
        playing["value"] = False

    def fake_stop_playback():
        playing["value"] = False
        calls["stop_playback"].append("stop")

    def fake_runtime_state():
        return {
            "capturing": capturing["value"],
            "playing": playing["value"],
            "captured_bytes": 0,
        }

    local_voice.status = lambda: {
        "version": "v0.42",
        "stt": {"available": True},
        "tts": {"available": True},
        "conversation_ready": True,
    }
    local_voice.transcribe = lambda payload: {
        "text": "What should I work on?",
        "provider": "whisper.cpp",
        "local": True,
        "model": "tiny.en-q8_0",
    }

    def fake_chat(source_app_key, message, conversation_id=None, **kwargs):
        calls["chat"].append(
            {
                "source": source_app_key,
                "message": message,
                "conversation_id": conversation_id,
                "kwargs": kwargs,
            }
        )
        return {
            "conversation_id": conversation_id or "physical-conversation-1",
            "reply": "Prepare the meeting brief and review the open research items.",
            "provider": "ollama",
            "model": "local-test",
            "compute_source": "homeserver_local",
            "run_id": 71,
            "agent": {"id": 1, "name": "VP3 Agent"},
            "context": {},
            "tools": {},
        }

    context_chat.chat = fake_chat
    agent_voice_profiles.resolve_effective = lambda agent_id: {
        "agent": {"id": agent_id, "name": "VP3 Agent"},
        "effective": {
            "voice": "en_US-lessac-medium",
            "speaking_rate": 1.0,
            "sentence_silence": 0.2,
            "ready": True,
            "warning": None,
        },
    }

    def fake_synthesize(text, **kwargs):
        calls["tts"].append({"text": text, "kwargs": kwargs})
        return wav_bytes(800)

    local_voice.synthesize = fake_synthesize

    def fake_emit_event(**kwargs):
        calls["cognition"].append(kwargs)
        return {"duplicate": False, "event": {"id": 1}}

    cognitive_runtime.emit_event = fake_emit_event

    audio.start_capture = fake_start_capture
    audio.stop_capture = fake_stop_capture
    audio.cancel_capture = fake_cancel_capture
    audio.play_wav = fake_play_wav
    audio.stop_playback = fake_stop_playback
    audio.runtime_state = fake_runtime_state

    try:
        with TestClient(app) as client:
            scheduler.stop()

            # Physical Agent controls stay owner-session only.
            assert client.get("/api/v1/control/vp3-os/physical-agent").status_code == 401
            assert client.post("/api/v1/control/vp3-os/physical-agent/listen").status_code == 401
            assert client.post("/api/v1/control/vp3-os/physical-agent/cancel").status_code == 401

            assert client.post(
                "/__owner/session",
                headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN},
            ).status_code == 200

            public = client.get("/api/v1/capabilities")
            assert public.status_code == 200, public.text
            advertised = public.json()
            assert advertised["vp3_os"]["os_version"] == "v0.30"
            assert advertised["vp3_os_physical_agent"]["version"] == "v0.30"
            assert advertised["vp3_os_physical_agent"]["push_to_talk"] is True
            assert advertised["vp3_os_physical_agent"]["barge_in"] is True
            for feature in (
                "vp3.os.v030",
                "vp3.os.physical_agent.v1",
                "vp3.os.push_to_talk.v1",
                "vp3.os.barge_in.v1",
            ):
                assert feature in advertised["features"]

            hardware_adapters.manager.handle_message(controller_hello())
            hardware_adapters.manager.handle_message(controller_state(1))

            initial = client.get("/api/v1/control/vp3-os/physical-agent")
            assert initial.status_code == 200
            assert initial.json()["state"] == "idle"

            # Actual controller event path: press -> local capture.
            hardware_adapters.manager.handle_message(
                {"type": "event", "seq": 1, "event": "agent_button", "action": "press"}
            )
            assert physical_agent.status()["state"] == "listening"
            assert capturing["value"] is True

            # Release -> local STT -> existing Agent Chat/Cognition -> Agent
            # voice -> local speaker.
            hardware_adapters.manager.handle_message(
                {"type": "event", "seq": 2, "event": "agent_button", "action": "release"}
            )

            deadline = time.time() + 5
            while time.time() < deadline and physical_agent.status()["state"] not in {"idle", "error"}:
                time.sleep(0.01)

            done = physical_agent.status()
            assert done["state"] == "idle", done
            assert done["turn_count"] == 1
            assert done["conversation_id"] == "physical-conversation-1"
            assert done["last_compute_source"] == "homeserver_local"
            assert calls["capture"] == ["start", "stop"]
            assert len(calls["chat"]) == 1
            assert calls["chat"][0]["source"] == "owner"
            assert calls["chat"][0]["message"] == "What should I work on?"
            assert calls["chat"][0]["kwargs"]["owner_tools"] is True
            assert calls["chat"][0]["kwargs"]["include_memory"] is True
            assert calls["chat"][0]["kwargs"]["include_knowledge"] is True
            assert calls["chat"][0]["kwargs"]["include_contacts"] is True
            assert calls["tts"][0]["text"].startswith("Prepare the meeting brief")
            assert calls["tts"][0]["kwargs"]["voice_key"] == "en_US-lessac-medium"
            assert len(calls["play"]) == 1

            # The cognitive event contains modality/run metadata, not transcript,
            # raw audio, or reply text.
            assert len(calls["cognition"]) == 1
            event = calls["cognition"][0]
            assert event["event_type"] == "physical_agent.turn"
            assert event["privacy_scope"] == "private"
            assert event["conversation_id"] == "physical-conversation-1"
            encoded_event = str(event)
            assert "What should I work on?" not in encoded_event
            assert "Prepare the meeting brief" not in encoded_event
            assert "audio_local" in encoded_event

            # Conversation continuity: a second turn reuses the same owner
            # conversation rather than creating a hardware-only chat silo.
            hardware_adapters.manager.handle_message(
                {"type": "event", "seq": 3, "event": "agent_button", "action": "press"}
            )
            hardware_adapters.manager.handle_message(
                {"type": "event", "seq": 4, "event": "agent_button", "action": "release"}
            )
            deadline = time.time() + 5
            while time.time() < deadline and physical_agent.status()["state"] not in {"idle", "error"}:
                time.sleep(0.01)
            assert physical_agent.status()["turn_count"] == 2
            assert calls["chat"][1]["conversation_id"] == "physical-conversation-1"

            # Barge-in: a press while the Agent is speaking stops playback
            # and immediately starts a new microphone turn.
            playing["value"] = True
            physical_agent.runtime._set_state("speaking")
            before_stops = len(calls["stop_playback"])
            hardware_adapters.manager.handle_message(
                {"type": "event", "seq": 5, "event": "agent_button", "action": "press"}
            )
            assert physical_agent.status()["state"] == "listening"
            assert capturing["value"] is True
            assert len(calls["stop_playback"]) > before_stops
            hardware_adapters.manager.handle_message(
                {"type": "event", "seq": 6, "event": "agent_button", "action": "hold"}
            )
            assert physical_agent.status()["state"] == "idle"
            assert capturing["value"] is False

            # Privacy switch engagement cancels capture immediately and blocks a
            # new listen until the hardware state confirms privacy is released.
            hardware_adapters.manager.handle_message(
                {"type": "event", "seq": 7, "event": "agent_button", "action": "press"}
            )
            assert capturing["value"] is True
            hardware_adapters.manager.handle_message(
                {"type": "event", "seq": 8, "event": "privacy_switch", "action": "engaged"}
            )
            assert capturing["value"] is False
            assert physical_agent.status()["state"] == "privacy"
            assert calls["cancel_capture"]

            # Apply authoritative hardware state for the physical cut.
            hardware_adapters.manager.handle_message(
                controller_state(2, privacy=True, mic_powered=False)
            )
            blocked = client.post("/api/v1/control/vp3-os/physical-agent/listen")
            assert blocked.status_code == 200
            assert blocked.json()["started"] is False
            assert "privacy" in blocked.json()["reason"].lower()

            # Release privacy, return to idle, and verify hold cancels.
            hardware_adapters.manager.handle_message(
                {"type": "event", "seq": 9, "event": "privacy_switch", "action": "disengaged"}
            )
            hardware_adapters.manager.handle_message(controller_state(3, privacy=False, mic_powered=True))
            assert physical_agent.status()["state"] == "idle"
            hardware_adapters.manager.handle_message(
                {"type": "event", "seq": 10, "event": "agent_button", "action": "press"}
            )
            assert physical_agent.status()["state"] == "listening"
            hardware_adapters.manager.handle_message(
                {"type": "event", "seq": 11, "event": "agent_button", "action": "hold"}
            )
            assert physical_agent.status()["state"] == "idle"
            assert capturing["value"] is False

            # Owner can explicitly start a new physical conversation.
            reset = client.post("/api/v1/control/vp3-os/physical-agent/reset-conversation")
            assert reset.status_code == 200
            assert reset.json()["conversation_id"] is None

            # Paired apps get state only, never conversation/session identifiers
            # or transcript/reply contents.
            request = client.post(
                "/api/v1/pairing/request",
                json={
                    "app_key": "physical-agent-test",
                    "app_name": "Physical Agent Test",
                    "permissions": ["agent.chat"],
                },
            ).json()
            assert client.post(
                "/api/v1/pairing/approve",
                json={"code": request["code"]},
            ).status_code == 200
            headers = {"Authorization": f"Bearer {request['claim_token']}"}
            paired = client.get("/api/v1/vp3-os/status", headers=headers)
            assert paired.status_code == 200
            paired_json = paired.json()["physical_agent"]
            assert paired_json["version"] == "v0.30"
            assert "conversation_id" not in paired_json
            assert "session_id" not in paired_json
            assert "last_transcript_chars" not in paired_json

            registry = client.get("/api/v1/capability-registry", headers=headers)
            assert registry.status_code == 200
            assert registry.json()["vp3_os_physical_agent"]["version"] == "v0.30"

    finally:
        local_voice.status = original_voice_status
        local_voice.transcribe = original_transcribe
        local_voice.synthesize = original_synthesize
        context_chat.chat = original_chat
        agent_voice_profiles.resolve_effective = original_resolve
        cognitive_runtime.emit_event = original_emit

        audio.start_capture = original_start_capture
        audio.stop_capture = original_stop_capture
        audio.cancel_capture = original_cancel_capture
        audio.play_wav = original_play_wav
        audio.stop_playback = original_stop_playback
        audio.runtime_state = original_runtime_state

        physical_agent.runtime.stop()
        hardware_adapters.manager.stop()
        vp3_os.clear_reported_hardware()

        os.environ.pop("VP3_OS_HARDWARE_PROFILE", None)
        os.environ.pop("VP3_OS_HARDWARE_ADAPTER", None)

print("VP3 OS v0.30 physical agent runtime regression passed")
