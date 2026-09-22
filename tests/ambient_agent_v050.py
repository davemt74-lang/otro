from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def wait_for(predicate, timeout: float = 5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("condition did not become true before timeout")


def controller_hello() -> dict:
    return {
        "type": "hello",
        "protocol": "vp3-hw-v1",
        "controller_id": "vp3-ambient-test",
        "firmware": "0.50-test",
        "hardware_revision": "ambient-test-a",
        "components": [
            "agent_button",
            "status_light",
            "microphone",
            "speaker",
            "privacy_switch",
            "presence_sensor",
        ],
        "capabilities": [
            "status_light",
            "agent_button",
            "privacy_switch",
            "mic_power_cut",
            "mic_power_sense",
            "presence_sensor",
            "wake_word",
            "voice_activity",
        ],
    }


def controller_state(seq: int, *, occupied: bool = False) -> dict:
    return {
        "type": "state",
        "seq": seq,
        "components": {
            "agent_button": {"present": True, "ready": True, "pressed": False},
            "status_light": {"present": True, "ready": True},
            "microphone": {"present": True, "ready": True},
            "speaker": {"present": True, "ready": True},
            "privacy_switch": {
                "present": True,
                "ready": True,
                "engaged": False,
                "physical_disconnect": True,
                "microphone_powered": True,
            },
            "presence_sensor": {
                "present": True,
                "ready": True,
                "occupied": occupied,
            },
        },
    }


with tempfile.TemporaryDirectory(prefix="vp3-os-v050-ambient-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir
    os.environ["VP3_OS_HARDWARE_PROFILE"] = "vp3_node"
    os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import (  # noqa: E402
        agent_voice_profiles,
        ambient_agent,
        cognitive_runtime,
        device_audio,
        hardware_adapters,
        local_voice,
        physical_agent,
        physical_meeting,
        vp3_os,
    )
    from app.services.tasks import scheduler  # noqa: E402

    ambient_agent.LOOP_SECONDS = 0.05

    original_agent_status = physical_agent.status
    original_agent_begin = physical_agent.begin_listening
    original_agent_finish = physical_agent.finish_listening
    original_agent_cancel = physical_agent.cancel
    original_meeting_status = physical_meeting.status
    original_voice_resolve = agent_voice_profiles.resolve_effective
    original_primary_agent_id = agent_voice_profiles.primary_agent_id
    original_synthesize = local_voice.synthesize
    original_audio_state = device_audio.device_audio.runtime_state
    original_play = device_audio.device_audio.play_wav
    original_emit = cognitive_runtime.emit_event

    physical_calls: list[tuple[str, str]] = []
    synth_calls: list[dict] = []
    play_calls: list[bytes] = []
    cognitive_events: list[dict] = []

    physical_agent.status = lambda: {
        "state": "idle",
        "turn_count": 0,
        "external_mode": "",
    }
    physical_agent.begin_listening = lambda: (
        physical_calls.append(("begin", "")) or {"started": True, "generation": 1}
    )
    physical_agent.finish_listening = lambda: (
        physical_calls.append(("finish", "")) or {"accepted": True, "generation": 1}
    )
    physical_agent.cancel = lambda reason="cancelled": (
        physical_calls.append(("cancel", str(reason))) or {"state": "idle"}
    )
    physical_meeting.status = lambda: {
        "state": "idle",
        "meeting_id": None,
    }
    agent_voice_profiles.primary_agent_id = lambda: 1
    agent_voice_profiles.resolve_effective = lambda agent_id: {
        "agent": {"id": int(agent_id), "name": "VP3 Agent"},
        "effective": {
            "voice": "en_US-lessac-medium",
            "speaking_rate": 1.0,
            "sentence_silence": 0.2,
            "ready": True,
            "warning": None,
        },
    }

    def fake_synthesize(text: str, **kwargs):
        synth_calls.append({"text": text, "kwargs": kwargs})
        return b"RIFF-ambient-test"

    local_voice.synthesize = fake_synthesize
    device_audio.device_audio.runtime_state = lambda: {
        "capturing": False,
        "capture_mode": "idle",
        "playing": False,
        "captured_bytes": 0,
    }
    device_audio.device_audio.play_wav = lambda payload: play_calls.append(bytes(payload))

    def fake_emit(**kwargs):
        cognitive_events.append(kwargs)
        return {"duplicate": False, "event": {"id": len(cognitive_events)}}

    cognitive_runtime.emit_event = fake_emit

    try:
        with TestClient(app) as client:
            scheduler.stop()

            assert client.get("/api/v1/control/vp3-os/ambient").status_code == 401
            assert client.put(
                "/api/v1/control/vp3-os/ambient/settings",
                json={"enabled": True},
            ).status_code == 401

            assert client.post(
                "/__owner/session",
                headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN},
            ).status_code == 200

            public = client.get("/api/v1/capabilities")
            assert public.status_code == 200, public.text
            caps = public.json()
            assert caps["vp3_os"]["os_version"] == "v0.50"
            ambient_caps = caps["vp3_os_ambient_agent"]
            assert ambient_caps["version"] == "v0.50"
            assert ambient_caps["opt_in_default"] is False
            assert ambient_caps["ambient_microphone_capture"] is False
            assert ambient_caps["ambient_transcription"] is False
            assert ambient_caps["ambient_memory"] is False
            for feature in (
                "vp3.os.v050",
                "vp3.os.ambient_agent.v1",
                "vp3.os.presence_events.v1",
                "vp3.os.wake_word_events.v1",
                "vp3.os.proactive_voice.v1",
            ):
                assert feature in caps["features"]

            initial = client.get("/api/v1/control/vp3-os/ambient")
            assert initial.status_code == 200
            assert initial.json()["settings"]["enabled"] is False
            assert initial.json()["state"] == "disabled"

            # The v0.20 protocol remains compatible while accepting optional
            # v0.50 ambient extensions.
            hello = hardware_adapters.manager.handle_message(controller_hello())
            assert "presence_sensor" in hello["components"]
            assert "wake_word" in hello["capabilities"]
            state = hardware_adapters.manager.handle_message(controller_state(1))
            assert state["duplicate"] is False
            assert vp3_os.hardware_inventory()["presence_sensor"]["occupied"] is False

            settings = client.put(
                "/api/v1/control/vp3-os/ambient/settings",
                json={
                    "enabled": True,
                    "wake_enabled": True,
                    "proactive_voice": True,
                    "presence_policy": "sensor_required",
                    "announcement_levels": ["warning"],
                    "announcement_detail": "title_only",
                    "cooldown_seconds": 5,
                    "wake_timeout_seconds": 5,
                    "max_announcements_per_hour": 6,
                },
            )
            assert settings.status_code == 200, settings.text
            assert settings.json()["settings"]["enabled"] is True
            assert settings.json()["status"]["presence"] == "absent"

            # Presence is owner-local ambient state.
            hardware_adapters.manager.handle_message(
                {"type": "event", "seq": 1, "event": "presence_sensor", "action": "present"}
            )
            present = wait_for(
                lambda: (
                    ambient_agent.status()
                    if ambient_agent.status()["presence"] == "present"
                    else None
                )
            )
            assert present["state"] in {"present", "armed"}

            secret_title = "Private reminder title 98231"
            secret_body = "Private reminder body must not leave the room."
            with db() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO notifications(source, title, body, level)
                    VALUES ('ambient-test', ?, ?, 'warning')
                    """,
                    (secret_title, secret_body),
                )
                notification_id = int(cursor.lastrowid)

            announced = wait_for(
                lambda: (
                    ambient_agent.status()
                    if ambient_agent.status()["last_announcement_id"] == notification_id
                    else None
                )
            )
            assert announced["last_announcement_id"] == notification_id
            assert len(synth_calls) == 1
            assert synth_calls[0]["text"] == secret_title
            assert secret_body not in synth_calls[0]["text"]
            assert play_calls == [b"RIFF-ambient-test"]

            # Spoken does not mean read/dismissed.
            with db() as connection:
                row = connection.execute(
                    "SELECT read_at, dismissed_at FROM notifications WHERE id=?",
                    (notification_id,),
                ).fetchone()
            assert row["read_at"] is None
            assert row["dismissed_at"] is None

            # Delivery state prevents repeated announcements after the loop
            # wakes again.
            time.sleep(0.2)
            assert len(synth_calls) == 1

            ambient_event = next(
                item for item in cognitive_events
                if item.get("event_type") == "ambient.notification_announced"
            )
            event_encoded = json.dumps(ambient_event, ensure_ascii=False)
            assert secret_title not in event_encoded
            assert secret_body not in event_encoded
            assert ambient_event["memory_candidate"] is False
            assert ambient_event["payload"]["notification_id"] == notification_id

            # Wake-word + VAD hands one bounded turn to the existing Physical
            # Agent. Ambient Runtime never opens the microphone directly.
            hardware_adapters.manager.handle_message(
                {"type": "event", "seq": 2, "event": "wake_word", "action": "detected"}
            )
            wait_for(lambda: ambient_agent.status()["wake_active"] is True)
            assert physical_calls[-1] == ("begin", "")
            hardware_adapters.manager.handle_message(
                {"type": "event", "seq": 3, "event": "voice_activity", "action": "started"}
            )
            hardware_adapters.manager.handle_message(
                {"type": "event", "seq": 4, "event": "voice_activity", "action": "stopped"}
            )
            wait_for(lambda: ambient_agent.status()["wake_active"] is False)
            assert physical_calls[-1] == ("finish", "")

            wake_event = next(
                item for item in cognitive_events
                if item.get("event_type") == "ambient.wake"
            )
            assert wake_event["memory_candidate"] is False
            assert wake_event["payload"]["local_only_trigger"] is True

            # Privacy cancels an ambient-owned wake turn immediately.
            hardware_adapters.manager.handle_message(
                {"type": "event", "seq": 5, "event": "wake_word", "action": "detected"}
            )
            wait_for(lambda: ambient_agent.status()["wake_active"] is True)
            hardware_adapters.manager.handle_message(
                {"type": "event", "seq": 6, "event": "privacy_switch", "action": "engaged"}
            )
            wait_for(lambda: ambient_agent.status()["state"] == "privacy")
            assert ("cancel", "ambient_privacy_engaged") in physical_calls
            assert ambient_agent.status()["wake_active"] is False

            # Invalid ambient hardware actions still fail closed.
            try:
                hardware_adapters.normalize_controller_message(
                    {"type": "event", "seq": 99, "event": "wake_word", "action": "raw_phrase"}
                )
                raise AssertionError("invalid wake-word action was accepted")
            except hardware_adapters.HardwareAdapterError:
                pass

            # Paired apps can discover ambient capability but never current
            # room occupancy, timestamps, notification IDs, or spoken content.
            request = client.post(
                "/api/v1/pairing/request",
                json={
                    "app_key": "ambient-test-app",
                    "app_name": "Ambient Test App",
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
            paired_ambient = paired.json()["ambient_agent"]
            assert paired_ambient["version"] == "v0.50"
            assert paired_ambient["enabled"] is True
            paired_encoded = json.dumps(paired_ambient, ensure_ascii=False)
            for forbidden in (
                "presence",
                "last_presence_at",
                "last_announcement",
                secret_title,
                secret_body,
                str(notification_id),
            ):
                assert forbidden not in paired_encoded
            assert paired_ambient["ambient_microphone_capture"] is False
            assert paired_ambient["ambient_transcription"] is False
            assert paired_ambient["ambient_memory"] is False

            registry = client.get("/api/v1/capability-registry", headers=headers)
            assert registry.status_code == 200
            reg = registry.json()["vp3_os_ambient_agent"]
            assert reg["version"] == "v0.50"
            assert "presence" not in reg

            # Disable clears local presence tracking and stops ambient actions.
            disabled = client.put(
                "/api/v1/control/vp3-os/ambient/settings",
                json={
                    "enabled": False,
                    "wake_enabled": True,
                    "proactive_voice": True,
                    "presence_policy": "sensor_required",
                    "announcement_levels": ["warning"],
                    "announcement_detail": "title_only",
                    "cooldown_seconds": 30,
                    "wake_timeout_seconds": 20,
                    "max_announcements_per_hour": 6,
                },
            )
            assert disabled.status_code == 200
            assert disabled.json()["status"]["state"] == "disabled"
            assert disabled.json()["status"]["presence"] == "unknown"
    finally:
        physical_agent.status = original_agent_status
        physical_agent.begin_listening = original_agent_begin
        physical_agent.finish_listening = original_agent_finish
        physical_agent.cancel = original_agent_cancel
        physical_meeting.status = original_meeting_status
        agent_voice_profiles.resolve_effective = original_voice_resolve
        agent_voice_profiles.primary_agent_id = original_primary_agent_id
        local_voice.synthesize = original_synthesize
        device_audio.device_audio.runtime_state = original_audio_state
        device_audio.device_audio.play_wav = original_play
        cognitive_runtime.emit_event = original_emit

        ambient_agent.runtime.stop()
        hardware_adapters.manager.stop()
        vp3_os.clear_reported_hardware()

        os.environ.pop("VP3_OS_HARDWARE_PROFILE", None)
        os.environ.pop("VP3_OS_HARDWARE_ADAPTER", None)

print("VP3 OS v0.50 Ambient Agent Runtime regression passed")
