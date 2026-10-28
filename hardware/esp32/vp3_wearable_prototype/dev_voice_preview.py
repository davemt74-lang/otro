"""Explicit, audio-free Developer Voice preview for the Waveshare prototype.

No microphone capture, audio bytes, stream, server connection, or production
permission is created by this module. It only models the proposed UI lifecycle.
The eventual source-bound audio transport must be implemented and validated
separately before the hardware can claim a ready microphone.
"""
from __future__ import annotations

import threading
import time

MAX_PTT_SECONDS = 30.0
SOURCE_ID = "waveshare-esp32-s3-touch-lcd-1.46"
STATES = frozenset({
    "disabled", "idle", "ptt_requested", "turn_finished", "cancelled", "timed_out"
})


class VoicePreview:
    def __init__(self, *, clock=time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._enabled = False
        self._state = "disabled"
        self._pressed_at: float | None = None
        self._reason = "audio_unavailable"

    def configure(self, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise ValueError("expected_boolean")
        with self._lock:
            # Local UI acknowledgement only; never an OTRO owner permission.
            self._enabled = enabled
            self._pressed_at = None
            self._state = "idle" if enabled else "disabled"
            self._reason = "preview_only" if enabled else "audio_unavailable"

    def reset(self) -> None:
        # Fail closed on reconnect: explicit opt-in must happen again.
        self.configure(False)

    def stop(self) -> None:
        with self._lock:
            self._pressed_at = None
            self._state = "cancelled" if self._enabled else "disabled"
            self._reason = "manual_stop"

    def action(self, value: str) -> None:
        if value not in {"press", "release", "hold"}:
            raise ValueError("invalid_action")
        with self._lock:
            self._expire_locked()
            if not self._enabled:
                return
            if value == "press":
                if self._state != "ptt_requested":
                    self._state = "ptt_requested"
                    self._pressed_at = self._clock()
                    self._reason = "simulated_button_press"
            elif value == "hold":
                # The existing OTRO hold behavior is Cancel, not more recording.
                self._state = "cancelled"
                self._pressed_at = None
                self._reason = "button_hold_cancel"
            elif value == "release" and self._state == "ptt_requested":
                self._state = "turn_finished"
                self._pressed_at = None
                self._reason = "no_audio_payload"

    def _expire_locked(self) -> None:
        if (self._state == "ptt_requested"
                and self._pressed_at is not None
                and self._clock() - self._pressed_at >= MAX_PTT_SECONDS):
            self._state = "timed_out"
            self._pressed_at = None
            self._reason = "ptt_timeout"

    def snapshot(self) -> dict:
        with self._lock:
            self._expire_locked()
            return {
                "source_id": SOURCE_ID,
                "state": self._state,
                "preview_enabled": self._enabled,
                "reason": self._reason,
                "simulated_only": True,
                "audio_transport": "unbound",
                "capture_capable": False,
                "microphone_ready": False,
                "recording": False,
                "audio_bytes": 0,
                "physical_mute_verified": False,
            }
