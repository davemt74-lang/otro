from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import device_audio  # noqa: E402


class FakeInputStream:
    def __init__(self, *, callback, **kwargs):
        self.callback = callback
        self.kwargs = kwargs
        self.started = False
        self.closed = False

    def start(self):
        self.started = True
        self.callback(b"\x01\x00" * 800, 800, None, False)

    def stop(self):
        self.started = False

    def abort(self):
        self.started = False

    def close(self):
        self.closed = True


class FakeSoundDevice:
    def __init__(self):
        self.inputs: list[FakeInputStream] = []

    def RawInputStream(self, **kwargs):
        stream = FakeInputStream(**kwargs)
        self.inputs.append(stream)
        return stream


fake_sd = FakeSoundDevice()
original_loader = device_audio._sounddevice
device_audio._sounddevice = lambda: fake_sd

try:
    runtime = device_audio.DeviceAudio()
    frames: list[tuple[bytes, bool]] = []

    runtime.start_stream_capture(
        lambda payload, status_flags: frames.append((bytes(payload), bool(status_flags)))
    )
    assert runtime.runtime_state()["capturing"] is True
    assert runtime.runtime_state()["capture_mode"] == "stream"
    assert frames == [(b"\x01\x00" * 800, False)]
    assert len(fake_sd.inputs) == 1

    runtime.stop_stream_capture()
    assert runtime.runtime_state()["capturing"] is False
    assert runtime.runtime_state()["capture_mode"] == "idle"
    assert fake_sd.inputs[0].closed is True

    runtime.start_stream_capture(
        lambda payload, status_flags: frames.append((bytes(payload), bool(status_flags)))
    )
    runtime.cancel_capture()
    assert runtime.runtime_state()["capturing"] is False
    assert runtime.runtime_state()["capture_mode"] == "idle"

    try:
        runtime.stop_stream_capture()
        raise AssertionError("inactive stream capture was accepted")
    except device_audio.DeviceAudioError:
        pass
finally:
    device_audio._sounddevice = original_loader

print("VP3 OS v0.40 streaming device audio regression passed")
