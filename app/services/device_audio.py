from __future__ import annotations

import io
import os
import threading
import wave
from dataclasses import dataclass
from typing import Any

AUDIO_RUNTIME_VERSION = "v0.30"
SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2
MAX_CAPTURE_SECONDS = 60
MAX_CAPTURE_BYTES = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH * MAX_CAPTURE_SECONDS


class DeviceAudioError(RuntimeError):
    pass


@dataclass(frozen=True)
class AudioConfig:
    input_device: str | int | None
    output_device: str | int | None


def _selector(name: str) -> str | int | None:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return value[:160]


def config() -> AudioConfig:
    return AudioConfig(
        input_device=_selector("VP3_OS_AUDIO_INPUT_DEVICE"),
        output_device=_selector("VP3_OS_AUDIO_OUTPUT_DEVICE"),
    )


def _sounddevice():
    try:
        import sounddevice as sd  # type: ignore
    except Exception as exc:
        raise DeviceAudioError("The VP3 OS audio backend is unavailable.") from exc
    return sd


def status() -> dict[str, Any]:
    cfg = config()
    try:
        sd = _sounddevice()
        devices = sd.query_devices()
        default = getattr(sd.default, "device", (None, None))
        return {
            "version": AUDIO_RUNTIME_VERSION,
            "available": True,
            "backend": "sounddevice-portaudio",
            "sample_rate": SAMPLE_RATE,
            "channels": CHANNELS,
            "input_device_configured": cfg.input_device is not None,
            "output_device_configured": cfg.output_device is not None,
            "default_input_index": default[0] if isinstance(default, (list, tuple)) and len(default) > 0 else None,
            "default_output_index": default[1] if isinstance(default, (list, tuple)) and len(default) > 1 else None,
            "device_count": len(devices),
        }
    except Exception as exc:
        return {
            "version": AUDIO_RUNTIME_VERSION,
            "available": False,
            "backend": "sounddevice-portaudio",
            "sample_rate": SAMPLE_RATE,
            "channels": CHANNELS,
            "input_device_configured": cfg.input_device is not None,
            "output_device_configured": cfg.output_device is not None,
            "error": type(exc).__name__,
        }


class DeviceAudio:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._capture_stream: Any = None
        self._capture_chunks: list[bytes] = []
        self._capture_bytes = 0
        self._capture_overflow = False
        self._playback_stream: Any = None
        self._playing = False

    def start_capture(self) -> None:
        with self._lock:
            if self._capture_stream is not None:
                raise DeviceAudioError("Microphone capture is already active.")
            self.stop_playback()
            self._capture_chunks = []
            self._capture_bytes = 0
            self._capture_overflow = False

        sd = _sounddevice()
        cfg = config()

        def callback(indata, frames, time_info, status_flags):
            del frames, time_info
            payload = bytes(indata)
            with self._lock:
                if self._capture_stream is None:
                    return
                if status_flags:
                    # PortAudio overflow does not expose private content; mark
                    # the recording unusable instead of returning partial audio.
                    self._capture_overflow = True
                if self._capture_bytes + len(payload) > MAX_CAPTURE_BYTES:
                    self._capture_overflow = True
                    return
                self._capture_chunks.append(payload)
                self._capture_bytes += len(payload)

        try:
            stream = sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                blocksize=0,
                device=cfg.input_device,
                channels=CHANNELS,
                dtype="int16",
                callback=callback,
            )
            with self._lock:
                self._capture_stream = stream
            stream.start()
        except Exception as exc:
            with self._lock:
                self._capture_stream = None
                self._capture_chunks = []
                self._capture_bytes = 0
            raise DeviceAudioError("VP3 OS could not open the microphone device.") from exc

    def stop_capture(self) -> bytes:
        with self._lock:
            stream = self._capture_stream
            self._capture_stream = None
        if stream is None:
            raise DeviceAudioError("Microphone capture is not active.")
        try:
            stream.stop()
        except Exception:
            pass
        try:
            stream.close()
        except Exception:
            pass

        with self._lock:
            overflow = self._capture_overflow
            pcm = b"".join(self._capture_chunks)
            self._capture_chunks = []
            self._capture_bytes = 0
            self._capture_overflow = False

        if overflow:
            raise DeviceAudioError("Microphone capture overflowed or exceeded the safe duration.")
        if len(pcm) < SAMPLE_RATE * SAMPLE_WIDTH // 10:
            raise DeviceAudioError("No usable speech audio was captured.")

        target = io.BytesIO()
        with wave.open(target, "wb") as wav:
            wav.setnchannels(CHANNELS)
            wav.setsampwidth(SAMPLE_WIDTH)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(pcm)
        return target.getvalue()

    def cancel_capture(self) -> None:
        with self._lock:
            stream = self._capture_stream
            self._capture_stream = None
            self._capture_chunks = []
            self._capture_bytes = 0
            self._capture_overflow = False
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass

    def play_wav(self, audio: bytes) -> None:
        if not audio:
            raise DeviceAudioError("Playback audio is empty.")
        try:
            with wave.open(io.BytesIO(audio), "rb") as wav:
                channels = int(wav.getnchannels())
                sample_width = int(wav.getsampwidth())
                sample_rate = int(wav.getframerate())
                frames = wav.readframes(wav.getnframes())
        except (wave.Error, EOFError) as exc:
            raise DeviceAudioError("Playback requires valid PCM WAV audio.") from exc

        if sample_width != 2:
            raise DeviceAudioError("Playback requires 16-bit PCM WAV audio.")
        if channels not in {1, 2}:
            raise DeviceAudioError("Playback requires mono or stereo WAV audio.")
        if not frames:
            raise DeviceAudioError("Playback WAV contains no audio.")

        self.stop_playback()
        sd = _sounddevice()
        cfg = config()
        try:
            stream = sd.RawOutputStream(
                samplerate=sample_rate,
                blocksize=0,
                device=cfg.output_device,
                channels=channels,
                dtype="int16",
            )
            with self._lock:
                self._playback_stream = stream
                self._playing = True
            stream.start()
            stream.write(frames)
            stream.stop()
        except Exception as exc:
            raise DeviceAudioError("VP3 OS could not play audio through the speaker device.") from exc
        finally:
            with self._lock:
                current = self._playback_stream
                self._playback_stream = None
                self._playing = False
            if current is not None:
                try:
                    current.close()
                except Exception:
                    pass

    def stop_playback(self) -> None:
        with self._lock:
            stream = self._playback_stream
            self._playback_stream = None
            self._playing = False
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass

    def runtime_state(self) -> dict[str, Any]:
        with self._lock:
            return {
                "capturing": self._capture_stream is not None,
                "playing": self._playing,
                "captured_bytes": self._capture_bytes,
            }


device_audio = DeviceAudio()
