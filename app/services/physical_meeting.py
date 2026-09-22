from __future__ import annotations

import hashlib
import io
import json
import math
import queue
import threading
import time
import uuid
import wave
from array import array
from datetime import datetime, timezone
from typing import Any

from . import (
    cognitive_runtime,
    device_audio,
    hardware_adapters,
    local_voice,
    meeting_cards,
    meeting_intelligence,
    physical_agent,
    vp3_os,
)
from .inference_cancellation import CancellationToken, InferenceCancelled

PHYSICAL_MEETING_VERSION = "v0.40"
SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2
MIN_SEGMENT_MS = 700
SPEECH_RMS_THRESHOLD = 220
SILENCE_MS = 900
MAX_SEGMENT_MS = 30_000
MAX_SEGMENTS = 500
MAX_PENDING_SEGMENTS = 16
TRANSCRIBER_JOIN_SECONDS = 45.0
MEETING_START_HOLD_SECONDS = 2.0

_START_COMMANDS = {
    "start meeting",
    "start meeting mode",
    "begin meeting",
    "begin meeting mode",
}
_END_COMMANDS = {
    "end meeting",
    "end meeting mode",
    "stop meeting",
    "stop meeting mode",
}

_ALLOWED_STATES = {
    "disabled",
    "idle",
    "meeting_starting",
    "recording",
    "meeting_ending",
    "finalizing",
    "privacy",
    "error",
}


class PhysicalMeetingError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _command(text: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() or ch.isspace() else " " for ch in str(text or ""))
    return " ".join(cleaned.split())


def _pcm_wav(pcm: bytes) -> bytes:
    target = io.BytesIO()
    with wave.open(target, "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(SAMPLE_WIDTH)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)
    return target.getvalue()


def _rms(pcm: bytes) -> int:
    if len(pcm) < 2:
        return 0
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if not samples:
        return 0
    total = sum(int(value) * int(value) for value in samples)
    return int(math.sqrt(total / len(samples)))


def _source_hash(segments: list[dict[str, Any]]) -> str:
    canonical = json.dumps(segments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PhysicalMeetingRuntime:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._started = False
        self._state = "disabled"
        self._state_changed_at = _now_iso()
        self._meeting_id: str | None = None
        self._title = ""
        self._trigger = ""
        self._started_at = ""
        self._started_monotonic = 0.0
        self._ended_at = ""
        self._end_reason = ""
        self._segments: list[dict[str, Any]] = []
        self._segment_pcm = bytearray()
        self._speech_seen = False
        self._trailing_silence_ms = 0
        self._segment_start_ms = 0
        self._sequence = 0
        self._queue: queue.Queue[Any] | None = None
        self._transcriber_thread: threading.Thread | None = None
        self._finalizer_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._intelligence_token: CancellationToken | None = None
        self._last_error = ""
        self._last_card: dict[str, Any] | None = None
        self._last_meeting: dict[str, Any] | None = None
        self._dropped_segments = 0
        self._button_pressed_at_monotonic = 0.0
        self._button_hold_seen = False

    def start_runtime(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._state = "idle"
            self._state_changed_at = _now_iso()
        hardware_adapters.add_event_handler(self.handle_hardware_event)
        if self._privacy_engaged():
            self._set_state("privacy")

    def stop_runtime(self) -> None:
        hardware_adapters.remove_event_handler(self.handle_hardware_event)
        self.interrupt("runtime_stopped", create_card=False)
        with self._lock:
            self._started = False
            self._state = "disabled"
            self._state_changed_at = _now_iso()

    def _set_state(self, state: str, *, error: str = "") -> None:
        if state not in _ALLOWED_STATES:
            state = "error"
        with self._lock:
            self._state = state
            self._state_changed_at = _now_iso()
            if error:
                self._last_error = str(error)[:240]
            elif state != "error":
                self._last_error = ""
        light = {
            "disabled": "off",
            "idle": "idle",
            "meeting_starting": "thinking",
            "recording": "listening",
            "meeting_ending": "thinking",
            "finalizing": "thinking",
            "privacy": "privacy",
            "error": "error",
        }.get(state, "error")
        try:
            hardware_adapters.set_status_light(light)
        except Exception:
            pass

    def _privacy_engaged(self) -> bool:
        privacy = vp3_os.manifest(include_hardware=False, include_device_id=False)["privacy"]
        return bool(privacy.get("privacy_switch_engaged"))

    def _hardware_ready(self) -> tuple[bool, str]:
        inventory = vp3_os.hardware_inventory()
        if self._privacy_engaged():
            return False, "Physical microphone privacy is engaged."
        if not inventory["microphone"]["ready"]:
            return False, "Microphone hardware is unavailable."
        return True, ""

    def handle_hardware_event(self, event: dict[str, Any]) -> None:
        if not self._started:
            return
        event_type = str(event.get("event") or "")
        action = str(event.get("action") or "")
        if event_type == "privacy_switch":
            if action == "engaged":
                self.interrupt("privacy_engaged", create_card=True)
                self._set_state("privacy")
            elif action == "disengaged":
                # The controller emits the switch event before its authoritative
                # state frame. Physical Agent owns its own event-state transition;
                # do not re-read the still-stale hardware snapshot here.
                with self._lock:
                    active = self._meeting_id is not None and self._state not in {"idle", "privacy", "error"}
                if not active:
                    self._set_state("idle")
            return

        if event_type == "agent_button":
            try:
                from . import hardware_experience
                policy = hardware_experience.button_policy()
            except Exception:
                policy = {
                    "agent_button_action": "push_to_talk",
                    "hold_action": "cancel",
                }
            hold_action = str(policy.get("hold_action") or "cancel")

            with self._lock:
                state = self._state

            if state == "recording":
                if action == "hold" and hold_action in {"cancel", "meeting_toggle"}:
                    self.request_end(
                        "button_hold_toggle" if hold_action == "meeting_toggle" else "button_hold"
                    )
                return

            if action == "press" and state in {"idle", "error"}:
                with self._lock:
                    self._button_pressed_at_monotonic = time.monotonic()
                    self._button_hold_seen = False
                return

            if action == "hold":
                if hold_action == "meeting_toggle" and state in {"idle", "error"}:
                    with self._lock:
                        self._button_hold_seen = True
                    self.start_meeting(trigger="button_hold_toggle")
                    return
                if hold_action == "cancel":
                    # Preserve the v0.40 extended-hold meeting gesture while the
                    # default hold action remains the Physical Agent cancel gesture.
                    with self._lock:
                        if self._button_pressed_at_monotonic:
                            self._button_hold_seen = True
                return

            if action == "release":
                now = time.monotonic()
                with self._lock:
                    pressed_at = self._button_pressed_at_monotonic
                    hold_seen = self._button_hold_seen
                    self._button_pressed_at_monotonic = 0.0
                    self._button_hold_seen = False
                duration = (now - pressed_at) if pressed_at else 0.0
                if (
                    hold_action == "cancel"
                    and state in {"idle", "error"}
                    and hold_seen
                    and duration >= MEETING_START_HOLD_SECONDS
                ):
                    self.start_meeting(trigger="button_extended_hold")
                return

    def start_meeting(self, title: str = "", *, trigger: str = "owner") -> dict[str, Any]:
        if not self._started:
            raise PhysicalMeetingError("Physical Meeting Runtime is not running.")
        with self._lock:
            if self._state not in {"idle", "error"}:
                return {"started": False, "reason": f"busy:{self._state}", "status": self.status()}

        ready, reason = self._hardware_ready()
        if not ready:
            self._set_state("privacy" if self._privacy_engaged() else "error", error=reason)
            return {"started": False, "reason": reason, "status": self.status()}
        voice = local_voice.status()
        if not bool(voice.get("stt", {}).get("available")):
            message = "Local Whisper STT is not ready."
            self._set_state("error", error=message)
            return {"started": False, "reason": message, "status": self.status()}

        meeting_id = uuid.uuid4().hex
        clean_title = " ".join(str(title or "").split())[:240]
        if not clean_title:
            clean_title = f"Physical meeting {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M')}"
        self._set_state("meeting_starting")
        physical_agent.set_external_mode("meeting")

        with self._lock:
            self._meeting_id = meeting_id
            self._title = clean_title
            self._trigger = str(trigger or "owner")[:80]
            self._started_at = _now_iso()
            self._started_monotonic = time.monotonic()
            self._ended_at = ""
            self._end_reason = ""
            self._segments = []
            self._segment_pcm = bytearray()
            self._speech_seen = False
            self._trailing_silence_ms = 0
            self._segment_start_ms = 0
            self._sequence = 0
            self._queue = queue.Queue(maxsize=MAX_PENDING_SEGMENTS)
            self._stop_event = threading.Event()
            self._intelligence_token = None
            self._dropped_segments = 0
            self._button_pressed_at_monotonic = 0.0
            self._button_hold_seen = False
            meeting_queue = self._queue

        transcriber = threading.Thread(
            target=self._transcriber_loop,
            args=(meeting_id, meeting_queue),
            name="vp3-physical-meeting-stt",
            daemon=True,
        )
        with self._lock:
            self._transcriber_thread = transcriber
        transcriber.start()

        try:
            device_audio.device_audio.start_stream_capture(self._on_audio)
        except Exception as exc:
            self._stop_event.set()
            try:
                meeting_queue.put_nowait(None)
            except Exception:
                pass
            physical_agent.set_external_mode(None)
            with self._lock:
                self._meeting_id = None
            self._set_state("error", error=str(exc))
            return {"started": False, "reason": str(exc), "status": self.status()}

        self._set_state("recording")
        self._emit_event("physical_meeting.started", "Physical meeting recording started.")
        return {"started": True, "meeting_id": meeting_id, "status": self.status()}

    def _on_audio(self, pcm: bytes, status_flags: bool) -> None:
        if not pcm:
            return
        if status_flags:
            self.request_interrupt("audio_overflow")
            return
        with self._lock:
            if self._state != "recording" or self._stop_event.is_set():
                return
            frame_ms = max(1, int(len(pcm) / (SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH) * 1000))
            now_ms = max(0, int((time.monotonic() - self._started_monotonic) * 1000))
            rms = _rms(pcm)
            if not self._speech_seen:
                if rms < SPEECH_RMS_THRESHOLD:
                    return
                self._speech_seen = True
                self._segment_start_ms = max(0, now_ms - frame_ms)
            self._segment_pcm.extend(pcm)
            self._trailing_silence_ms = (
                self._trailing_silence_ms + frame_ms if rms < SPEECH_RMS_THRESHOLD else 0
            )
            duration_ms = int(
                len(self._segment_pcm) / (SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH) * 1000
            )
            should_flush = duration_ms >= MAX_SEGMENT_MS or self._trailing_silence_ms >= SILENCE_MS
        if should_flush:
            self._flush_segment(now_ms)

    def _flush_segment(self, end_ms: int) -> None:
        with self._lock:
            if not self._speech_seen or not self._segment_pcm:
                self._segment_pcm = bytearray()
                self._speech_seen = False
                self._trailing_silence_ms = 0
                self._segment_start_ms = 0
                return
            pcm = bytes(self._segment_pcm)
            start_ms = int(self._segment_start_ms)
            duration_ms = int(len(pcm) / (SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH) * 1000)
            self._segment_pcm = bytearray()
            self._speech_seen = False
            self._trailing_silence_ms = 0
            self._segment_start_ms = 0
            if duration_ms < MIN_SEGMENT_MS:
                return
            self._sequence += 1
            sequence = self._sequence
            meeting_queue = self._queue
        if meeting_queue is None:
            return
        try:
            meeting_queue.put_nowait((sequence, start_ms, max(start_ms, int(end_ms)), pcm))
        except queue.Full:
            with self._lock:
                self._dropped_segments += 1
            self.request_interrupt("transcription_backpressure")

    def _transcriber_loop(self, meeting_id: str, meeting_queue: queue.Queue[Any]) -> None:
        while True:
            item = meeting_queue.get()
            try:
                if item is None:
                    return
                if self._stop_event.is_set():
                    continue
                sequence, start_ms, end_ms, pcm = item
                try:
                    result = local_voice.transcribe(_pcm_wav(pcm))
                except Exception as exc:
                    with self._lock:
                        self._last_error = f"Meeting transcription failed: {str(exc)[:180]}"
                    continue
                if self._stop_event.is_set():
                    continue
                text = " ".join(str(result.get("text") or "").split())[:20_000]
                if not text:
                    continue
                normalized = _command(text)
                if normalized in _END_COMMANDS:
                    self.request_end("voice_command")
                    continue
                segment = {
                    "sequence": int(sequence),
                    "speaker_name": "Room",
                    "start_ms": max(0, int(start_ms)),
                    "end_ms": max(int(start_ms), int(end_ms)),
                    "text": text,
                }
                with self._lock:
                    if self._meeting_id != meeting_id or self._stop_event.is_set():
                        continue
                    if len(self._segments) >= MAX_SEGMENTS:
                        self.request_interrupt("segment_limit")
                        continue
                    self._segments.append(segment)
            finally:
                meeting_queue.task_done()

    def request_end(self, reason: str = "owner") -> None:
        with self._lock:
            if self._state != "recording":
                return
            if self._finalizer_thread and self._finalizer_thread.is_alive():
                return
            thread = threading.Thread(
                target=self.end_meeting,
                kwargs={"reason": reason},
                name="vp3-physical-meeting-finalize",
                daemon=True,
            )
            self._finalizer_thread = thread
        thread.start()

    def request_interrupt(self, reason: str) -> None:
        thread = threading.Thread(
            target=self.interrupt,
            args=(reason,),
            kwargs={"create_card": True},
            name="vp3-physical-meeting-interrupt",
            daemon=True,
        )
        thread.start()

    def end_meeting(self, *, reason: str = "owner") -> dict[str, Any]:
        with self._lock:
            if self._state != "recording" or not self._meeting_id:
                return {"ended": False, "reason": f"not_recording:{self._state}", "status": self.status()}
            meeting_id = self._meeting_id
            self._end_reason = str(reason or "owner")[:80]
        self._set_state("meeting_ending")
        try:
            device_audio.device_audio.stop_stream_capture()
        except Exception:
            device_audio.device_audio.cancel_capture()

        end_ms = max(0, int((time.monotonic() - self._started_monotonic) * 1000))
        self._flush_segment(end_ms)

        with self._lock:
            meeting_queue = self._queue
            transcriber = self._transcriber_thread
        if meeting_queue is not None:
            try:
                meeting_queue.put(None, timeout=2)
            except Exception:
                pass
        if transcriber is not None and transcriber is not threading.current_thread():
            transcriber.join(timeout=TRANSCRIBER_JOIN_SECONDS)

        if self._privacy_engaged() or self._stop_event.is_set():
            return self._finish_interrupted(meeting_id, self._end_reason or "interrupted")

        return self._finalize(meeting_id, end_ms)

    def _finalize(self, meeting_id: str, duration_ms: int) -> dict[str, Any]:
        self._set_state("finalizing")
        with self._lock:
            segments = [dict(item) for item in self._segments]
            title = self._title
            self._ended_at = _now_iso()

        source_hash = _source_hash(segments) if segments else hashlib.sha256(b"[]").hexdigest()
        snapshot: dict[str, Any] | None = None
        intelligence_error = ""
        if segments:
            payload = {
                "contract": meeting_intelligence.CONTRACT,
                "meeting": meeting_id,
                "source_hash": source_hash,
                "mode": "final",
                "idempotency_key": f"vp3-meeting-intelligence:{meeting_id}:{source_hash}",
                "title": title,
                "cloud_processing_allowed": False,
                "requested_compute": "homeserver",
                "segments": segments,
            }
            token = CancellationToken()
            with self._lock:
                self._intelligence_token = token
            try:
                intelligence = meeting_intelligence.analyze_owner(
                    payload,
                    cancellation_token=token,
                )
                snapshot = intelligence.get("snapshot") if isinstance(intelligence, dict) else None
            except InferenceCancelled:
                return self._finish_interrupted(meeting_id, "intelligence_cancelled")
            except Exception as exc:
                intelligence_error = str(exc)[:240]
            finally:
                with self._lock:
                    if self._intelligence_token is token:
                        self._intelligence_token = None

        if snapshot is None:
            snapshot = {
                "summary": (
                    "Meeting completed. Local meeting intelligence was unavailable."
                    if segments
                    else "Meeting completed without analyzable speech."
                ),
                "key_points": [],
                "decisions": [],
                "actions": [],
                "questions": [],
                "risks": [],
                "topics": [],
                "task_candidates": [],
                "crm_candidates": [],
                "follow_up_draft": "",
                "agent_brief": "",
            }

        card_result = meeting_cards.create(
            meeting_id=meeting_id,
            title=title,
            status="completed",
            duration_ms=duration_ms,
            segment_count=len(segments),
            snapshot=snapshot,
            source_hash=source_hash,
        )
        with self._lock:
            self._last_card = card_result
            self._last_meeting = {
                "meeting_id": meeting_id,
                "title": title,
                "status": "completed",
                "duration_ms": duration_ms,
                "segment_count": len(segments),
                "conversation_id": card_result["conversation_id"],
                "source_hash": source_hash,
                "intelligence_error": intelligence_error,
                "ended_at": self._ended_at,
            }
        self._emit_event(
            "physical_meeting.completed",
            "Physical meeting completed and a Meeting Card was added to Agent Chat.",
            snapshot=snapshot,
            conversation_id=card_result["conversation_id"],
        )
        self._reset_active()
        physical_agent.set_external_mode(None)
        self._set_state("idle")
        return {"ended": True, "meeting": dict(self._last_meeting), "card": card_result["card"]}

    def interrupt(self, reason: str, *, create_card: bool = True) -> dict[str, Any]:
        with self._lock:
            state = self._state
            meeting_id = self._meeting_id
            token = self._intelligence_token
            self._intelligence_token = None
            if not meeting_id:
                if state != "disabled" and self._started:
                    self._set_state("privacy" if self._privacy_engaged() else "idle")
                return {"interrupted": False, "reason": "no_active_meeting"}
            self._end_reason = str(reason or "interrupted")[:80]
            self._stop_event.set()
            self._segment_pcm = bytearray()
            self._speech_seen = False
        if token is not None:
            token.cancel(reason)
        device_audio.device_audio.cancel_capture()
        device_audio.device_audio.stop_playback()
        with self._lock:
            meeting_queue = self._queue
        if meeting_queue is not None:
            try:
                while True:
                    meeting_queue.get_nowait()
                    meeting_queue.task_done()
            except queue.Empty:
                pass
            try:
                meeting_queue.put_nowait(None)
            except Exception:
                pass
        result = self._finish_interrupted(meeting_id, reason, create_card=create_card)
        return result

    def _finish_interrupted(
        self,
        meeting_id: str,
        reason: str,
        *,
        create_card: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            if self._meeting_id != meeting_id:
                return {"interrupted": False, "reason": "meeting_already_closed"}
            title = self._title
            duration_ms = max(0, int((time.monotonic() - self._started_monotonic) * 1000))
            segment_count = len(self._segments)
            self._ended_at = _now_iso()
        card_result = None
        if create_card:
            card_result = meeting_cards.create(
                meeting_id=meeting_id,
                title=title,
                status="interrupted",
                duration_ms=duration_ms,
                segment_count=segment_count,
                snapshot=None,
            )
        with self._lock:
            self._last_card = card_result
            self._last_meeting = {
                "meeting_id": meeting_id,
                "title": title,
                "status": "interrupted",
                "reason": str(reason or "interrupted")[:80],
                "duration_ms": duration_ms,
                "segment_count": segment_count,
                "conversation_id": card_result["conversation_id"] if card_result else None,
                "ended_at": self._ended_at,
            }
        self._emit_event(
            "physical_meeting.interrupted",
            "Physical meeting was interrupted before final intelligence.",
            conversation_id=card_result["conversation_id"] if card_result else None,
        )
        self._reset_active()
        if not self._privacy_engaged():
            physical_agent.set_external_mode(None)
            self._set_state("idle")
        return {"interrupted": True, "meeting": dict(self._last_meeting)}

    def _reset_active(self) -> None:
        with self._lock:
            self._meeting_id = None
            self._title = ""
            self._trigger = ""
            self._started_at = ""
            self._started_monotonic = 0.0
            self._end_reason = ""
            self._segments = []
            self._segment_pcm = bytearray()
            self._speech_seen = False
            self._trailing_silence_ms = 0
            self._segment_start_ms = 0
            self._sequence = 0
            self._button_pressed_at_monotonic = 0.0
            self._button_hold_seen = False
            self._queue = None
            self._transcriber_thread = None
            self._intelligence_token = None
            self._stop_event = threading.Event()

    def _emit_event(
        self,
        event_type: str,
        summary: str,
        *,
        snapshot: dict[str, Any] | None = None,
        conversation_id: str | None = None,
    ) -> None:
        with self._lock:
            meeting_id = self._meeting_id or (
                str(self._last_meeting.get("meeting_id")) if self._last_meeting else ""
            )
            segment_count = len(self._segments) if self._meeting_id else int(
                (self._last_meeting or {}).get("segment_count") or 0
            )
        try:
            cognitive_runtime.emit_event(
                source_app_key="owner",
                source_kind="owner",
                event_id=f"{event_type}:{meeting_id}:{uuid.uuid4().hex[:10]}",
                event_type=event_type,
                summary=summary,
                entity_type="meeting",
                entity_key=meeting_id or "physical",
                conversation_id=conversation_id,
                correlation_id=meeting_id or None,
                importance=0.7 if event_type.endswith("completed") else 0.55,
                privacy_scope="private",
                payload={
                    "physical_meeting_version": PHYSICAL_MEETING_VERSION,
                    "audio_local": True,
                    "raw_audio_persisted": False,
                    "segment_count": segment_count,
                    "decision_count": len((snapshot or {}).get("decisions") or []),
                    "action_count": len((snapshot or {}).get("actions") or []),
                    "task_candidate_count": len((snapshot or {}).get("task_candidates") or []),
                },
                memory_candidate=False,
            )
        except Exception:
            pass

    def status(self) -> dict[str, Any]:
        with self._lock:
            preview = [
                {
                    "sequence": int(item["sequence"]),
                    "speaker_name": str(item["speaker_name"]),
                    "start_ms": int(item["start_ms"]),
                    "end_ms": int(item["end_ms"]),
                    "text": str(item["text"]),
                }
                for item in self._segments[-8:]
            ]
            duration_ms = (
                max(0, int((time.monotonic() - self._started_monotonic) * 1000))
                if self._meeting_id and self._started_monotonic
                else 0
            )
            return {
                "version": PHYSICAL_MEETING_VERSION,
                "started": self._started,
                "state": self._state,
                "state_changed_at": self._state_changed_at,
                "meeting_id": self._meeting_id,
                "title": self._title,
                "trigger": self._trigger,
                "started_at": self._started_at,
                "duration_ms": duration_ms,
                "segment_count": len(self._segments),
                "transcript_preview": preview,
                "speaker_mode": "room_channel",
                "raw_audio_persisted": False,
                "last_error": self._last_error,
                "dropped_segments": self._dropped_segments,
                "last_meeting": dict(self._last_meeting) if self._last_meeting else None,
                "audio": device_audio.device_audio.runtime_state(),
            }


runtime = PhysicalMeetingRuntime()


def public_capability() -> dict[str, Any]:
    return {
        "version": PHYSICAL_MEETING_VERSION,
        "explicit_start_stop": True,
        "button_extended_hold_start": True,
        "button_hold_end": True,
        "local_streaming_stt": "whisper.cpp",
        "meeting_intelligence": "homeserver_local",
        "meeting_card": True,
        "privacy_interrupt": True,
        "raw_audio_persisted": False,
        "speaker_mode": "room_channel",
    }


def paired_status() -> dict[str, Any]:
    raw = runtime.status()
    return {
        "version": PHYSICAL_MEETING_VERSION,
        "state": str(raw.get("state") or "")[:40],
        "active": bool(raw.get("meeting_id")),
        "duration_ms": int(raw.get("duration_ms") or 0),
        "segment_count": int(raw.get("segment_count") or 0),
        "speaker_mode": "room_channel",
        "raw_audio_persisted": False,
    }


def start_runtime() -> None:
    runtime.start_runtime()


def stop_runtime() -> None:
    runtime.stop_runtime()


def start_meeting(title: str = "", *, trigger: str = "owner") -> dict[str, Any]:
    return runtime.start_meeting(title, trigger=trigger)


def end_meeting(reason: str = "owner") -> dict[str, Any]:
    return runtime.end_meeting(reason=reason)


def interrupt(reason: str = "owner_cancelled") -> dict[str, Any]:
    return runtime.interrupt(reason, create_card=True)


def status() -> dict[str, Any]:
    return runtime.status()


def handle_start_voice_command(text: str) -> bool:
    if _command(text) not in _START_COMMANDS:
        return False
    result = runtime.start_meeting(trigger="voice_command")
    return bool(result.get("started"))
