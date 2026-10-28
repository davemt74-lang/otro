"""Source-bound, owner-paired VP3 wearable audio turn gateway (feature-gated).

Only an authenticated, owner-approved wearable app can open a short-lived
push-to-talk turn. No generic hardware-ready flag may select the Windows mic.
This module has no microphone driver and receives only explicitly uploaded WAV.
"""
from __future__ import annotations

import io
import os
import re
import secrets
import threading
import time
import wave
from dataclasses import dataclass, field

from . import context_chat, local_voice, pairing
from .inference_cancellation import CancellationToken, InferenceCancelled

VERSION = "wearable-audio-turn-v1"
SAMPLE_RATE = 16000
MAX_SECONDS = 20
MAX_WAV_BYTES = SAMPLE_RATE * 2 * MAX_SECONDS + 4096
MAX_REPLY_BYTES = 8 * 1024 * 1024
MAX_SESSIONS = 8
_REQUIRED_PERMISSIONS = frozenset({"devices.control", "agent.chat"})
_DEVICE_ID = re.compile(r"^vp3-wearable-[a-z0-9-]{1,64}$")
_COMPUTE_SLOT = threading.BoundedSemaphore(1)


class WearableVoiceError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def enabled() -> bool:
    return os.environ.get("VP3_WEARABLE_AUDIO_ENABLED", "").strip() == "1"


def authenticate_device(bearer: str, device_id: str | None = None) -> dict:
    if not bearer or len(bearer) > 256:
        raise WearableVoiceError("Device authentication required.", 401)
    paired = pairing.authenticate(bearer)
    if not paired:
        raise WearableVoiceError("Device authentication failed.", 401)
    actual_device = str(paired.get("app_key") or "")
    if not _DEVICE_ID.fullmatch(actual_device):
        raise WearableVoiceError("Unknown wearable device identity.", 403)
    if device_id is not None and actual_device != device_id:
        raise WearableVoiceError("Wearable identity does not match pairing.", 403)
    if not _REQUIRED_PERMISSIONS.issubset(set(paired.get("permissions") or [])):
        raise WearableVoiceError("Wearable permissions not approved.", 403)
    return paired


def validate_wav(data: bytes) -> None:
    if not isinstance(data, bytes) or len(data) < 44 or len(data) > MAX_WAV_BYTES:
        raise WearableVoiceError("Audio length exceeds the device turn limit.", 413)
    try:
        with wave.open(io.BytesIO(data), "rb") as source:
            if (source.getnchannels() != 1 or source.getsampwidth() != 2
                    or source.getframerate() != SAMPLE_RATE
                    or source.getcomptype() != "NONE"):
                raise WearableVoiceError("Audio must be mono 16 kHz 16-bit PCM WAV.", 422)
            frames = source.getnframes()
            if frames < SAMPLE_RATE // 10 or frames > SAMPLE_RATE * MAX_SECONDS:
                raise WearableVoiceError("Audio duration is outside the PTT limit.", 422)
            # The file may contain RIFF metadata, but never an unlimited sample payload.
            if len(source.readframes(frames)) != frames * 2:
                raise WearableVoiceError("WAV sample data is incomplete.", 422)
    except (wave.Error, EOFError, OSError) as exc:
        raise WearableVoiceError("Audio is not a valid PCM WAV.", 422) from exc


@dataclass
class Turn:
    id: str
    device_id: str
    start: float
    state: str = "capturing"
    stopped: threading.Event = field(default_factory=threading.Event)
    inference: CancellationToken = field(default_factory=CancellationToken)

    def cancel(self) -> None:
        self.stopped.set()
        self.inference.cancel("wearable_ptt_stopped")


class TurnRegistry:
    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.RLock()
        self._turns: dict[str, Turn] = {}

    def _reap_locked(self) -> None:
        now = self._clock()
        for key, turn in list(self._turns.items()):
            if turn.state == "capturing" and now - turn.start > MAX_SECONDS:
                turn.cancel()
                self._turns.pop(key, None)

    def begin(self, device_id: str) -> Turn:
        with self._lock:
            self._reap_locked()
            if any(t.device_id == device_id for t in self._turns.values()):
                raise WearableVoiceError("A wearable PTT turn is already open.", 409)
            if len(self._turns) >= MAX_SESSIONS:
                raise WearableVoiceError("Wearable audio is temporarily busy.", 429)
            turn = Turn(id=secrets.token_urlsafe(24), device_id=device_id, start=self._clock())
            self._turns[turn.id] = turn
            return turn

    def consume(self, device_id: str, turn_id: str) -> Turn:
        with self._lock:
            self._reap_locked()
            turn = self._turns.get(turn_id)
            if not turn or turn.device_id != device_id or turn.state != "capturing":
                raise WearableVoiceError("Wearable PTT turn is unavailable or expired.", 409)
            turn.state = "processing"
            return turn

    def stop(self, device_id: str, turn_id: str) -> None:
        with self._lock:
            turn = self._turns.get(turn_id)
            if not turn or turn.device_id != device_id:
                raise WearableVoiceError("Wearable PTT turn is unavailable.", 409)
            turn.cancel()
            self._turns.pop(turn.id, None)

    def complete(self, turn: Turn) -> bool:
        with self._lock:
            existing = self._turns.get(turn.id)
            if existing is not turn or turn.stopped.is_set():
                return False
            self._turns.pop(turn.id, None)
            return True

    def discard(self, turn: Turn) -> None:
        with self._lock:
            turn.cancel()
            self._turns.pop(turn.id, None)


registry = TurnRegistry()


def process_audio_turn(turn: Turn, audio: bytes) -> bytes:
    """Invoke existing OTRO STT/Chat/TTS with no owner tools or cloud raw audio."""
    if not _COMPUTE_SLOT.acquire(blocking=False):
        raise WearableVoiceError("Voice inference is busy.", 429)
    try:
        if turn.stopped.is_set():
            raise WearableVoiceError("Wearable turn was stopped.", 409)
        transcript_result = local_voice.transcribe(audio, cancel=turn.stopped)
        transcript = str(transcript_result.get("text") or "").strip()
        if not transcript:
            raise WearableVoiceError("No speech detected.", 422)
        if turn.stopped.is_set():
            raise WearableVoiceError("Wearable turn was stopped.", 409)
        result = context_chat.chat(
            f"app:{turn.device_id}", transcript, None,
            include_memory=False,
            include_knowledge=False,
            include_contacts=False,
            tool_permissions=set(),
            owner_tools=False,
            read_only=True,
            cancellation_token=turn.inference,
        )
        if turn.stopped.is_set():
            raise WearableVoiceError("Wearable turn was stopped.", 409)
        reply = str(result.get("reply") or "").strip()
        if not reply:
            raise WearableVoiceError("Agent returned no reply.", 502)
        spoken = local_voice.synthesize(reply)
        if turn.stopped.is_set():
            raise WearableVoiceError("Wearable turn was stopped.", 409)
        if not isinstance(spoken, bytes) or len(spoken) > MAX_REPLY_BYTES:
            raise WearableVoiceError("Spoken response exceeded the size limit.", 502)
        return spoken
    except InferenceCancelled as exc:
        raise WearableVoiceError("Wearable turn was stopped.", 409) from exc
    finally:
        _COMPUTE_SLOT.release()
