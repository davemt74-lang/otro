from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from array import array
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


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
            "microphone": {
                "present": True,
                "ready": (not privacy and mic_powered),
            },
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


def wait_for(predicate, timeout: float = 6.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("condition did not become true before timeout")


with tempfile.TemporaryDirectory(prefix="vp3-os-v040-meeting-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir
    os.environ["VP3_OS_HARDWARE_PROFILE"] = "vp3_node"
    os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import (  # noqa: E402
        cognitive_runtime,
        device_audio,
        hardware_adapters,
        local_voice,
        meeting_intelligence,
        physical_agent,
        physical_meeting,
        vp3_os,
    )
    from app.services.tasks import scheduler  # noqa: E402

    audio = device_audio.device_audio
    original_start_capture = audio.start_capture
    original_stop_capture = audio.stop_capture
    original_start_stream = audio.start_stream_capture
    original_stop_stream = audio.stop_stream_capture
    original_cancel_capture = audio.cancel_capture
    original_stop_playback = audio.stop_playback
    original_runtime_state = audio.runtime_state
    original_voice_status = local_voice.status
    original_transcribe = local_voice.transcribe
    original_analyze = meeting_intelligence.analyze_owner
    original_emit = cognitive_runtime.emit_event

    capture = {"active": False, "callback": None}
    ptt = {"active": False}
    transcript_calls: list[int] = []
    intelligence_calls: list[dict] = []
    cognitive_events: list[dict] = []

    def fake_start_capture():
        assert not ptt["active"]
        ptt["active"] = True

    def fake_stop_capture():
        assert ptt["active"]
        ptt["active"] = False
        # The Physical Agent worker only needs a distinguishable in-memory
        # payload here; fake_transcribe handles it without consuming a meeting
        # transcript fixture.
        return b"RIFFPTT"

    def fake_start_stream(callback):
        assert not capture["active"]
        capture["active"] = True
        capture["callback"] = callback

    def fake_stop_stream():
        assert capture["active"]
        capture["active"] = False
        capture["callback"] = None

    def fake_cancel_capture():
        ptt["active"] = False
        capture["active"] = False
        capture["callback"] = None

    def fake_stop_playback():
        return None

    def fake_audio_state():
        return {
            "capturing": bool(capture["active"] or ptt["active"]),
            "capture_mode": "stream" if capture["active"] else ("turn" if ptt["active"] else "idle"),
            "playing": False,
            "captured_bytes": 0,
        }

    local_voice.status = lambda: {
        "version": "v0.42",
        "stt": {"available": True},
        "tts": {"available": True},
        "conversation_ready": True,
    }

    transcripts = [
        "We decided to launch Tuesday. David will prepare the meeting brief.",
        "end meeting",
    ]

    def fake_transcribe(payload: bytes):
        assert payload[:4] == b"RIFF"
        if payload == b"RIFFPTT":
            return {
                "text": "",
                "provider": "whisper.cpp",
                "local": True,
                "model": "tiny.en-q8_0",
            }
        index = len(transcript_calls)
        transcript_calls.append(len(payload))
        text = transcripts[index] if index < len(transcripts) else "Additional meeting note."
        return {
            "text": text,
            "provider": "whisper.cpp",
            "local": True,
            "model": "tiny.en-q8_0",
        }

    snapshot = {
        "summary": "The team agreed to launch Tuesday and prepare the final brief.",
        "key_points": [{"text": "Launch timing was confirmed."}],
        "decisions": [{"decision": "Launch Tuesday."}],
        "actions": [{"action": "Prepare the final brief.", "owner": "David"}],
        "questions": [{"question": "Who owns launch-day monitoring?"}],
        "risks": [{"risk": "Final review could slip."}],
        "topics": [{"topic": "Launch readiness"}],
        "task_candidates": [{"title": "Prepare final brief", "owner": "David"}],
        "crm_candidates": [],
        "follow_up_draft": "Thanks everyone. We agreed to launch Tuesday.",
        "agent_brief": "Launch Tuesday; brief is the immediate follow-up.",
    }

    def fake_analyze(payload, *, cancellation_token=None):
        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled()
        intelligence_calls.append(payload)
        return {
            "snapshot": snapshot,
            "compute_source": "homeserver_local",
            "provider": "ollama",
            "model": "local-meeting-test",
        }

    def fake_emit(**kwargs):
        cognitive_events.append(kwargs)
        return {"duplicate": False, "event": {"id": len(cognitive_events)}}

    audio.start_capture = fake_start_capture
    audio.stop_capture = fake_stop_capture
    audio.start_stream_capture = fake_start_stream
    audio.stop_stream_capture = fake_stop_stream
    audio.cancel_capture = fake_cancel_capture
    audio.stop_playback = fake_stop_playback
    audio.runtime_state = fake_audio_state
    local_voice.transcribe = fake_transcribe
    meeting_intelligence.analyze_owner = fake_analyze
    cognitive_runtime.emit_event = fake_emit

    try:
        with TestClient(app) as client:
            scheduler.stop()
            assert client.post(
                "/__owner/session",
                headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN},
            ).status_code == 200

            hardware_adapters.manager.handle_message(controller_hello())
            hardware_adapters.manager.handle_message(controller_state(1))

            public = client.get("/api/v1/capabilities")
            assert public.status_code == 200, public.text
            caps = public.json()
            assert caps["vp3_os"]["os_version"] == "v0.40"
            assert caps["vp3_os_physical_meeting"]["version"] == "v0.40"
            assert caps["vp3_os_physical_meeting"]["raw_audio_persisted"] is False
            for feature in (
                "vp3.os.v040",
                "vp3.os.physical_meeting.v1",
                "vp3.os.streaming_meeting_stt.v1",
                "vp3.os.meeting_card.v1",
                "vp3.os.provider_cancellation.v1",
            ):
                assert feature in caps["features"]

            # Owner endpoints remain behind the existing owner session gateway.
            # The session above is active, so the runtime is visible now.
            initial = client.get("/api/v1/control/vp3-os/physical-meeting")
            assert initial.status_code == 200
            assert initial.json()["state"] == "idle"

            # Preserve v0.30: press begins push-to-talk and hold cancels
            # that turn. It must never start meeting mode.
            hardware_adapters.manager.handle_message(
                {"type": "event", "seq": 1, "event": "agent_button", "action": "press"}
            )
            assert physical_agent.status()["state"] == "listening"
            assert ptt["active"] is True
            hardware_adapters.manager.handle_message(
                {"type": "event", "seq": 2, "event": "agent_button", "action": "hold"}
            )
            assert physical_agent.status()["state"] == "idle"
            assert physical_meeting.status()["state"] == "idle"
            assert ptt["active"] is False
            assert capture["active"] is False
            hardware_adapters.manager.handle_message(
                {"type": "event", "seq": 3, "event": "agent_button", "action": "release"}
            )

            # The real ESP32 emits press/release pairs. A second press within
            # the bounded gesture window starts meeting mode and cancels any
            # short push-to-talk turn created by the first click.
            hardware_adapters.manager.handle_message(
                {"type": "event", "seq": 4, "event": "agent_button", "action": "press"}
            )
            hardware_adapters.manager.handle_message(
                {"type": "event", "seq": 5, "event": "agent_button", "action": "release"}
            )
            hardware_adapters.manager.handle_message(
                {"type": "event", "seq": 6, "event": "agent_button", "action": "press"}
            )
            hardware_adapters.manager.handle_message(
                {"type": "event", "seq": 7, "event": "agent_button", "action": "release"}
            )
            active = wait_for(
                lambda: (
                    physical_meeting.status()
                    if physical_meeting.status()["state"] == "recording"
                    else None
                )
            )
            first_meeting_id = active["meeting_id"]
            assert len(first_meeting_id) == 32
            assert capture["active"] is True
            assert physical_agent.status()["external_mode"] == "meeting"

            # Feed one second of speech and one second of silence. The bounded
            # local segment goes to Whisper and raw PCM is discarded afterward.
            speech = (array("h", [1400]) * 16000).tobytes()
            silence = b"\x00\x00" * 16000
            callback = capture["callback"]
            assert callable(callback)
            callback(speech, False)
            callback(silence, False)

            wait_for(lambda: physical_meeting.status()["segment_count"] == 1)
            owner_status = physical_meeting.status()
            assert owner_status["transcript_preview"][0]["text"].startswith("We decided")
            assert owner_status["raw_audio_persisted"] is False

            # Paired applications see meeting state/count only, never title,
            # meeting ID, transcript preview, or transcript text.
            request = client.post(
                "/api/v1/pairing/request",
                json={
                    "app_key": "physical-meeting-test",
                    "app_name": "Physical Meeting Test",
                    "permissions": ["agent.chat"],
                },
            ).json()
            assert client.post(
                "/api/v1/pairing/approve",
                json={"code": request["code"]},
            ).status_code == 200
            headers = {"Authorization": f"Bearer {request['claim_token']}"}
            paired = client.get("/api/v1/vp3-os/status", headers=headers)
            assert paired.status_code == 200, paired.text
            paired_meeting = paired.json()["physical_meeting"]
            assert paired_meeting["active"] is True
            assert paired_meeting["segment_count"] == 1
            paired_text = json.dumps(paired_meeting)
            for forbidden in (
                "meeting_id",
                "title",
                "transcript_preview",
                "We decided",
                "launch Tuesday",
            ):
                assert forbidden not in paired_text

            # The ESP32 emits press before hold. During meeting mode the
            # Physical Agent ignores both; the meeting runtime ends on hold.
            hardware_adapters.manager.handle_message(
                {"type": "event", "seq": 8, "event": "agent_button", "action": "press"}
            )
            hardware_adapters.manager.handle_message(
                {"type": "event", "seq": 9, "event": "agent_button", "action": "hold"}
            )
            hardware_adapters.manager.handle_message(
                {"type": "event", "seq": 10, "event": "agent_button", "action": "release"}
            )
            completed = wait_for(
                lambda: (
                    physical_meeting.status()["last_meeting"]
                    if (
                        physical_meeting.status()["state"] == "idle"
                        and physical_meeting.status()["last_meeting"]
                        and physical_meeting.status()["last_meeting"]["meeting_id"] == first_meeting_id
                    )
                    else None
                ),
                timeout=8,
            )
            assert completed["status"] == "completed"
            assert completed["segment_count"] == 1
            assert capture["active"] is False
            assert physical_agent.status()["external_mode"] == ""
            assert len(intelligence_calls) == 1
            intelligence_payload = intelligence_calls[0]
            assert intelligence_payload["cloud_processing_allowed"] is False
            assert intelligence_payload["requested_compute"] == "homeserver"
            assert intelligence_payload["segments"][0]["text"].startswith("We decided")

            conversation_id = completed["conversation_id"]
            conversation = client.get(
                f"/api/v1/control/conversations/{conversation_id}"
            )
            assert conversation.status_code == 200, conversation.text
            messages = conversation.json()["messages"]
            card_message = next(item for item in messages if item.get("card"))
            card = card_message["card"]
            assert card["card_type"] == "meeting"
            assert card["version"] == "v0.40"
            assert card["status"] == "completed"
            assert card["summary"] == snapshot["summary"]
            assert card["decisions"][0]["decision"] == "Launch Tuesday."
            assert card["actions"][0]["owner"] == "David"
            assert card["task_candidates"][0]["title"] == "Prepare final brief"
            card_encoded = json.dumps(card)
            assert "We decided to launch Tuesday" not in card_encoded
            assert "transcript" not in card_encoded.lower()
            assert "raw_audio" not in card_encoded.lower()

            completed_events = [
                item for item in cognitive_events
                if item.get("event_type") == "physical_meeting.completed"
            ]
            assert completed_events
            event_encoded = json.dumps(completed_events[-1])
            assert "We decided to launch Tuesday" not in event_encoded
            assert completed_events[-1]["payload"]["raw_audio_persisted"] is False
            assert completed_events[-1]["payload"]["decision_count"] == 1

            # Privacy engagement interrupts an active meeting immediately,
            # creates an interrupted card, and never invokes final intelligence.
            started = client.post(
                "/api/v1/control/vp3-os/physical-meeting/start",
                json={"title": "Privacy interruption test"},
            )
            assert started.status_code == 200, started.text
            second_id = started.json()["meeting_id"]
            assert capture["active"] is True
            analyzer_count = len(intelligence_calls)

            hardware_adapters.manager.handle_message(
                {"type": "event", "seq": 11, "event": "privacy_switch", "action": "engaged"}
            )
            hardware_adapters.manager.handle_message(
                controller_state(2, privacy=True, mic_powered=False)
            )
            interrupted = wait_for(
                lambda: (
                    physical_meeting.status()["last_meeting"]
                    if (
                        physical_meeting.status()["last_meeting"]
                        and physical_meeting.status()["last_meeting"]["meeting_id"] == second_id
                    )
                    else None
                )
            )
            assert interrupted["status"] == "interrupted"
            assert interrupted["reason"] == "privacy_engaged"
            assert capture["active"] is False
            assert physical_agent.status()["state"] == "privacy"
            assert len(intelligence_calls) == analyzer_count
            assert physical_meeting.status()["state"] == "privacy"

            interrupted_conversation = client.get(
                f"/api/v1/control/conversations/{interrupted['conversation_id']}"
            ).json()
            interrupted_card = next(
                item["card"] for item in interrupted_conversation["messages"] if item.get("card")
            )
            assert interrupted_card["status"] == "interrupted"
            assert interrupted_card["decisions"] == []
            assert interrupted_card["actions"] == []

            # Restore physical privacy state, then prove exact on-device voice
            # commands can start and stop meeting mode without an LLM.
            hardware_adapters.manager.handle_message(
                {"type": "event", "seq": 12, "event": "privacy_switch", "action": "disengaged"}
            )
            hardware_adapters.manager.handle_message(
                controller_state(3, privacy=False, mic_powered=True)
            )
            wait_for(lambda: physical_meeting.status()["state"] == "idle")
            assert physical_meeting.handle_start_voice_command("start meeting mode") is True
            third_id = physical_meeting.status()["meeting_id"]
            assert third_id

            callback = capture["callback"]
            callback(speech, False)
            callback(silence, False)
            third = wait_for(
                lambda: (
                    physical_meeting.status()["last_meeting"]
                    if (
                        physical_meeting.status()["state"] == "idle"
                        and physical_meeting.status()["last_meeting"]
                        and physical_meeting.status()["last_meeting"]["meeting_id"] == third_id
                    )
                    else None
                ),
                timeout=8,
            )
            assert third["status"] == "completed"
            assert third["segment_count"] == 0, "end-meeting voice command must not become transcript content"
            assert len(intelligence_calls) == analyzer_count, "empty command-only meeting must not run intelligence"

            registry = client.get("/api/v1/capability-registry", headers=headers)
            assert registry.status_code == 200, registry.text
            reg = registry.json()["vp3_os_physical_meeting"]
            assert reg["version"] == "v0.40"
            assert "transcript_preview" not in reg
            assert "meeting_id" not in reg
    finally:
        audio.start_capture = original_start_capture
        audio.stop_capture = original_stop_capture
        audio.start_stream_capture = original_start_stream
        audio.stop_stream_capture = original_stop_stream
        audio.cancel_capture = original_cancel_capture
        audio.stop_playback = original_stop_playback
        audio.runtime_state = original_runtime_state
        local_voice.status = original_voice_status
        local_voice.transcribe = original_transcribe
        meeting_intelligence.analyze_owner = original_analyze
        cognitive_runtime.emit_event = original_emit

        physical_meeting.runtime.stop_runtime()
        physical_agent.runtime.stop()
        hardware_adapters.manager.stop()
        vp3_os.clear_reported_hardware()

        os.environ.pop("VP3_OS_HARDWARE_PROFILE", None)
        os.environ.pop("VP3_OS_HARDWARE_ADAPTER", None)

print("VP3 OS v0.40 physical meeting runtime regression passed")
