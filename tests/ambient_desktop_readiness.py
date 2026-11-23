"""Desktop Ambient output, live speech authority and playback ownership regression."""
from __future__ import annotations

from datetime import datetime, timezone
import io
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

with tempfile.TemporaryDirectory(prefix="ambient-desktop-readiness-") as directory:
    os.environ["HOMESERVER_DATA_DIR"] = directory
    os.environ["VP3_OS_HARDWARE_PROFILE"] = "custom"
    os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"
    from app.database import db, initialize_database
    from app.services import ambient_agent as ambient, device_audio as audio, vp3_os
    initialize_database()
    runtime = ambient.AmbientAgentRuntime()
    runtime._started = True
    runtime._set_state("armed")
    ambient.save_settings({"enabled": True, "presence_policy": "assume_present", "proactive_voice": True})
    settings = ambient.get_settings()
    played = []
    synth_started, synth_release = threading.Event(), threading.Event()
    output_calls = []

    class OutputBackend:
        def query_devices(self, device=None, kind=None):
            output_calls.append((device, kind))
            if device == "invalid": raise ValueError("Invalid fixture output")
            return {"max_output_channels": 0 if device == "no-output" else 2}

    def fake_synthesize(*args, **kwargs): return b"synthetic-wav"
    profile = {"effective": {"ready": True, "voice": "fixture-voice", "speaking_rate": 1.0,
                             "sentence_silence": .2, "warning": None}}
    with patch.object(audio, "_sounddevice", return_value=OutputBackend()), \
         patch.object(ambient.physical_agent, "status", return_value={"state": "idle"}), \
         patch.object(ambient.physical_meeting, "status", return_value={"state": "idle", "meeting_id": None}), \
         patch.object(audio.device_audio, "runtime_state", return_value={"capturing": False, "playing": False}), \
         patch.object(audio.device_audio, "play_wav", side_effect=lambda value: played.append(value)), \
         patch.object(ambient.agent_voice_profiles, "primary_agent_id", return_value=1), \
         patch.object(ambient.agent_voice_profiles, "resolve_effective", return_value=profile), \
         patch.object(ambient.local_voice, "synthesize", side_effect=fake_synthesize):
        vp3_os.clear_reported_hardware()
        before = vp3_os.hardware_inventory()
        for selector, expected in (("", None), ("3", 3), ("Fixture Output", "Fixture Output")):
            with patch.dict(os.environ, {"VP3_OS_AUDIO_OUTPUT_DEVICE": selector}):
                assert audio.output_status()["available"]
                assert output_calls[-1] == (expected, "output")
        for selector in ("invalid", "no-output"):
            with patch.dict(os.environ, {"VP3_OS_AUDIO_OUTPUT_DEVICE": selector}):
                result = audio.output_status()
                assert not result["available"] and "speaker output" in result["message"]
        with patch.object(audio, "_sounddevice", side_effect=RuntimeError("Fixture backend unavailable")):
            assert not audio.output_status()["available"]
            assert "speaker output" in runtime._announcement_blocker(settings)
        assert runtime.announce_test()["spoken"]
        assert len(played) == 1 and vp3_os.hardware_inventory() == before
        vp3_os.report_hardware_state("speaker", present=True, ready=False)
        assert "reported speaker" in runtime._announcement_blocker(settings)
        vp3_os.clear_reported_hardware()
        print("PASS default/configured desktop outputs work without inventing hardware; invalid and explicit managed failures remain blocked")

        with runtime._lock:
            runtime._last_announcement_at = datetime.now(timezone.utc).isoformat()
        assert "cooldown" in runtime._announcement_blocker(settings)
        runtime._last_announcement_at = ""
        runtime._announcement_times.extend([time.monotonic()] * settings["max_announcements_per_hour"])
        assert "hourly" in runtime._announcement_blocker(settings)
        runtime._announcement_times.clear()
        with patch.object(ambient.physical_meeting, "status", return_value={"state": "listening", "meeting_id": "fixture"}):
            assert "meeting" in runtime._announcement_blocker(settings)
        with patch.object(audio.device_audio, "runtime_state", return_value={"capturing": True, "playing": False}):
            assert "microphone" in runtime._announcement_blocker(settings)
        with patch.object(ambient.physical_agent, "status", return_value={"state": "thinking"}):
            assert "thinking" in runtime._announcement_blocker(settings)
        print("PASS exact busy reasons preserve meeting, capture, physical-turn, cooldown and hourly limits")

        def blocked_synthesize(*args, **kwargs):
            synth_started.set()
            assert synth_release.wait(3), "Fixture synthesis was not released"
            return b"synthetic-wav"
        errors = []
        def speak_test():
            try: runtime.announce_test()
            except Exception as exc: errors.append(exc)
        notification = {"id": 1234, "title": "Synthetic fixture notification", "body": "", "level": "warning"}
        with patch.object(ambient.local_voice, "synthesize", side_effect=blocked_synthesize) as synthesis:
            first = threading.Thread(target=speak_test)
            first.start(); assert synth_started.wait(2)
            runtime._announce(notification, settings)
            try: runtime.announce_test()
            except ambient.AmbientAgentError as exc: assert "already preparing" in str(exc)
            else: raise AssertionError("Parallel voice test was allowed")
            assert synthesis.call_count == 1
            runtime.handle_hardware_event({"event": "privacy_switch", "action": "engaged"})
            synth_release.set(); first.join(3); assert not first.is_alive()
        assert len(played) == 1 and errors and "cancelled" in str(errors[-1])
        assert 1234 not in runtime._delivered_ids
        runtime.handle_hardware_event({"event": "privacy_switch", "action": "disengaged"})
        assert runtime.announce_test()["spoken"] and len(played) == 2
        print("PASS one Ambient reservation covers tests and notifications; privacy cancels late synthesis and a later explicit test recovers")

        for transition in ("disable", "stop"):
            synth_started.clear(); synth_release.clear(); errors.clear()
            runtime._started = True
            runtime.update_settings({"enabled": True, "presence_policy": "assume_present"})
            with patch.object(ambient.local_voice, "synthesize", side_effect=blocked_synthesize):
                first = threading.Thread(target=speak_test)
                first.start(); assert synth_started.wait(2)
                if transition == "disable": runtime.update_settings({"enabled": False, "presence_policy": "assume_present"})
                else: runtime.stop()
                synth_release.set(); first.join(3); assert not first.is_alive()
            assert len(played) == 2 and errors and "cancelled" in str(errors[-1])
        print("PASS disable and runtime Stop suppress obsolete synthesis before playback")

        # A suppressed automatic notification remains eligible, rather than
        # being marked delivered merely because synthesis already completed.
        synth_started.clear(); synth_release.clear()
        runtime._started = True
        runtime.update_settings({"enabled": True, "presence_policy": "assume_present"})
        with patch.object(ambient.local_voice, "synthesize", side_effect=blocked_synthesize):
            automatic = threading.Thread(target=runtime._announce, args=(notification, ambient.get_settings()))
            automatic.start(); assert synth_started.wait(2)
            runtime.handle_hardware_event({"event": "privacy_switch", "action": "engaged"})
            synth_release.set(); automatic.join(3); assert not automatic.is_alive()
        assert len(played) == 2 and notification["id"] not in runtime._delivered_ids
        assert notification["id"] not in ambient._load_delivered_ids()
        print("PASS privacy-suppressed automatic notifications are never marked delivered")

        # Ambient presence remains ephemeral even if the separate automation
        # history service is enabled and successfully retains its own events.
        from app.services import automation_intelligence
        with db() as connection:
            connection.execute("UPDATE automation_intelligence_settings SET enabled=1 WHERE id=1")
        assert automation_intelligence.get_settings()["enabled"]
        seeded = automation_intelligence.record_context_event("fixture", "retained", source_kind="system")
        assert seeded["id"] > 0 and seeded["event_type"] == "fixture"
        with db() as connection:
            before_events = connection.execute("SELECT COUNT(*) FROM automation_context_events").fetchone()[0]
        runtime.handle_hardware_event({"event": "privacy_switch", "action": "disengaged"})
        for action, expected in (("present", "present"), ("absent", "absent")):
            runtime.handle_hardware_event({"event": "presence_sensor", "action": action})
            current = runtime.status()
            assert current["presence"] == expected and current["last_presence_at"]
        with db() as connection:
            after_events = connection.execute("SELECT COUNT(*) FROM automation_context_events").fetchone()[0]
            presence_events = connection.execute("SELECT COUNT(*) FROM automation_context_events WHERE event_type='presence'").fetchone()[0]
        assert before_events == after_events and presence_events == 0
        print("PASS live presence updates stay ephemeral while independently enabled automation history remains functional")

    # An older playback can finish after a newer stream has replaced it. Its
    # cleanup must close only its own stream and preserve current playing state.
    wav = io.BytesIO()
    with wave.open(wav, "wb") as target:
        target.setnchannels(1); target.setsampwidth(2); target.setframerate(16000)
        target.writeframes(b"\x00\x00" * 160)
    streams = []
    class Stream:
        def __init__(self):
            self.entered, self.release = threading.Event(), threading.Event()
            self.closed = False
        def start(self): pass
        def write(self, frames):
            self.entered.set(); assert self.release.wait(3)
        def stop(self): pass
        def abort(self): pass
        def close(self): self.closed = True
    class Backend:
        def RawOutputStream(self, **kwargs):
            stream = Stream(); streams.append(stream); return stream
    runtime_audio = audio.DeviceAudio()
    failures = []
    def play():
        try: runtime_audio.play_wav(wav.getvalue())
        except Exception as exc: failures.append(exc)
    def wait_streams(count):
        deadline = time.monotonic() + 2
        while len(streams) < count and time.monotonic() < deadline:
            time.sleep(.005)
        assert len(streams) >= count, failures
    with patch.object(audio, "_sounddevice", return_value=Backend()):
        one = threading.Thread(target=play); one.start()
        wait_streams(1)
        assert streams[0].entered.wait(2)
        two = threading.Thread(target=play); two.start()
        wait_streams(2)
        assert streams[1].entered.wait(2)
        streams[0].release.set(); one.join(2); assert not one.is_alive()
        assert not streams[1].closed and runtime_audio.runtime_state()["playing"]
        streams[1].release.set(); two.join(2); assert not two.is_alive()
    assert not failures and streams[1].closed and not runtime_audio.runtime_state()["playing"]
    print("PASS obsolete playback cleanup cannot close or clear a replacement stream")
