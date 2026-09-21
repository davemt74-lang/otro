from __future__ import annotations

import io
import sys
import wave
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
        pcm = b"\x01\x00" * 3200
        self.callback(pcm, 3200, None, False)

    def stop(self):
        self.started = False

    def abort(self):
        self.started = False

    def close(self):
        self.closed = True


class FakeOutputStream:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.closed = False
        self.writes: list[bytes] = []

    def start(self):
        self.started = True

    def write(self, payload):
        self.writes.append(bytes(payload))

    def stop(self):
        self.started = False

    def abort(self):
        self.started = False

    def close(self):
        self.closed = True


class FakeDefault:
    device = (2, 4)


class FakeSoundDevice:
    default = FakeDefault()

    def __init__(self):
        self.inputs: list[FakeInputStream] = []
        self.outputs: list[FakeOutputStream] = []

    def query_devices(self):
        return [{"name": "VP3 Mic"}, {"name": "VP3 Speaker"}]

    def RawInputStream(self, **kwargs):
        stream = FakeInputStream(**kwargs)
        self.inputs.append(stream)
        return stream

    def RawOutputStream(self, **kwargs):
        stream = FakeOutputStream(**kwargs)
        self.outputs.append(stream)
        return stream


fake_sd = FakeSoundDevice()
original_loader = device_audio._sounddevice
device_audio._sounddevice = lambda: fake_sd
try:
    status = device_audio.status()
    assert status["available"] is True
    assert status["backend"] == "sounddevice-portaudio"
    assert status["default_input_index"] == 2
    assert status["default_output_index"] == 4

    runtime = device_audio.DeviceAudio()
    runtime.start_capture()
    active = runtime.runtime_state()
    assert active["capturing"] is True
    assert active["captured_bytes"] == 6400

    recorded = runtime.stop_capture()
    assert recorded[:4] == b"RIFF" and recorded[8:12] == b"WAVE"
    with wave.open(io.BytesIO(recorded), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 16000
        assert wav.getnframes() == 3200

    runtime.play_wav(recorded)
    assert len(fake_sd.outputs) == 1
    output = fake_sd.outputs[0]
    assert output.kwargs["channels"] == 1
    assert output.kwargs["dtype"] == "int16"
    assert output.writes == [b"\x01\x00" * 3200]
    assert runtime.runtime_state()["playing"] is False

    runtime.start_capture()
    runtime.cancel_capture()
    assert runtime.runtime_state()["capturing"] is False

    try:
        runtime.play_wav(b"not a wave")
        raise AssertionError("invalid playback audio was accepted")
    except device_audio.DeviceAudioError:
        pass
finally:
    device_audio._sounddevice = original_loader

print("VP3 OS v0.30 device audio backend regression passed")
