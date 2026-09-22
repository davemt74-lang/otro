from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from . import (
    agent_voice_profiles,
    cognitive_runtime,
    context_chat,
    device_audio,
    hardware_adapters,
    local_voice,
    vp3_os,
)
from .inference_cancellation import CancellationToken

PHYSICAL_AGENT_VERSION = "v0.30"
SOURCE_APP_KEY = "owner"
IDLE_CONVERSATION_RESET_SECONDS = 15 * 60

_ALLOWED_STATES = {
    "disabled",
    "idle",
    "listening",
    "transcribing",
    "thinking",
    "speaking",
    "privacy",
    "error",
}


class PhysicalAgentError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PhysicalAgentRuntime:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._started = False
        self._state = "disabled"
        self._state_changed_at = _now_iso()
        self._session_id: str | None = None
        self._conversation_id: str | None = None
        self._last_turn_at_monotonic: float | None = None
        self._generation = 0
        self._worker: threading.Thread | None = None
        self._inference_token: CancellationToken | None = None
        self._external_mode = ""
        self._last_error = ""
        self._last_transcript_chars = 0
        self._last_reply_chars = 0
        self._last_compute_source = ""
        self._turn_count = 0
        self._cancel_reason = ""

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._state = "idle"
            self._state_changed_at = _now_iso()
        hardware_adapters.add_event_handler(self.handle_hardware_event)
        self._sync_privacy_state()

    def stop(self) -> None:
        hardware_adapters.remove_event_handler(self.handle_hardware_event)
        self.cancel("runtime_stopped")
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
            "listening": "listening",
            "transcribing": "thinking",
            "thinking": "thinking",
            "speaking": "speaking",
            "privacy": "privacy",
            "error": "error",
        }.get(state, "error")
        try:
            hardware_adapters.set_status_light(light)
        except Exception:
            pass

    def _privacy_engaged(self) -> bool:
        privacy = vp3_os.manifest(
            include_hardware=False,
            include_device_id=False,
        )["privacy"]
        return bool(privacy.get("privacy_switch_engaged"))

    def _hardware_ready(self) -> tuple[bool, str]:
        inventory = vp3_os.hardware_inventory()
        if self._privacy_engaged():
            return False, "Physical microphone privacy is engaged."
        if not inventory["microphone"]["ready"]:
            return False, "Microphone hardware is unavailable."
        if not inventory["speaker"]["ready"]:
            return False, "Speaker hardware is unavailable."
        return True, ""

    def _sync_privacy_state(self) -> None:
        if self._privacy_engaged():
            self.cancel("privacy_engaged")
            self._set_state("privacy")
        else:
            with self._lock:
                current = self._state
            if current in {"privacy", "disabled"} and self._started:
                self._set_state("idle")

    def handle_hardware_event(self, event: dict[str, Any]) -> None:
        if not self._started:
            return
        event_type = str(event.get("event") or "")
        action = str(event.get("action") or "")
        if event_type == "privacy_switch":
            if action == "engaged":
                self.cancel("privacy_engaged")
                self._set_state("privacy")
            elif action == "disengaged":
                self._set_state("idle")
            return

        if event_type != "agent_button":
            return
        with self._lock:
            if self._external_mode:
                return
        if action == "press":
            self.begin_listening()
        elif action == "release":
            self.finish_listening()
        elif action == "hold":
            # Hold is an explicit physical cancel gesture in v0.30.
            self.cancel("button_hold")
            self._set_state("idle" if not self._privacy_engaged() else "privacy")

    def begin_listening(self) -> dict[str, Any]:
        if not self._started:
            raise PhysicalAgentError("Physical Agent Runtime is not running.")

        with self._lock:
            state = self._state
            external_mode = self._external_mode
        if external_mode:
            return {"started": False, "reason": f"external_mode:{external_mode}"}

        # v0.40 barge-in supersedes any current physical turn. Provider
        # inference is cooperatively cancelled when it is already in flight.
        if state in {"transcribing", "thinking", "speaking"}:
            self.cancel("barge_in")
            state = "idle"

        if state not in {"idle", "error"}:
            return {"started": False, "reason": f"busy:{state}"}

        ready, reason = self._hardware_ready()
        if not ready:
            self._set_state("privacy" if self._privacy_engaged() else "error", error=reason)
            return {"started": False, "reason": reason}

        voice = local_voice.status()
        if not bool(voice.get("stt", {}).get("available")):
            message = "Local Whisper STT is not ready."
            self._set_state("error", error=message)
            return {"started": False, "reason": message}

        try:
            device_audio.device_audio.start_capture()
        except device_audio.DeviceAudioError as exc:
            self._set_state("error", error=str(exc))
            return {"started": False, "reason": str(exc)}

        with self._lock:
            self._generation += 1
            self._session_id = uuid.uuid4().hex
            self._cancel_reason = ""
            generation = self._generation
        self._set_state("listening")
        return {"started": True, "generation": generation}

    def finish_listening(self) -> dict[str, Any]:
        with self._lock:
            if self._state != "listening":
                return {"accepted": False, "reason": f"not_listening:{self._state}"}
            generation = self._generation

        try:
            audio = device_audio.device_audio.stop_capture()
        except device_audio.DeviceAudioError as exc:
            self._set_state("error", error=str(exc))
            return {"accepted": False, "reason": str(exc)}

        self._set_state("transcribing")
        worker = threading.Thread(
            target=self._run_turn,
            args=(generation, audio),
            name="vp3-physical-agent-turn",
            daemon=True,
        )
        with self._lock:
            self._worker = worker
        worker.start()
        return {"accepted": True, "generation": generation}

    def _generation_active(self, generation: int) -> bool:
        with self._lock:
            return self._started and generation == self._generation

    def _conversation_for_turn(self) -> str | None:
        with self._lock:
            conversation_id = self._conversation_id
            last_turn = self._last_turn_at_monotonic
        if (
            conversation_id
            and last_turn is not None
            and (time.monotonic() - last_turn) > IDLE_CONVERSATION_RESET_SECONDS
        ):
            return None
        return conversation_id

    def _run_turn(self, generation: int, audio: bytes) -> None:
        try:
            if not self._generation_active(generation) or self._privacy_engaged():
                return

            transcript_result = local_voice.transcribe(audio)
            transcript = str(transcript_result.get("text") or "").strip()
            if not transcript:
                raise PhysicalAgentError("No speech was detected.")

            with self._lock:
                self._last_transcript_chars = len(transcript)

            if not self._generation_active(generation) or self._privacy_engaged():
                return

            # Physical meeting mode is an explicit local control command, not an
            # LLM interpretation. Import lazily to avoid a runtime cycle.
            try:
                from . import physical_meeting
                if physical_meeting.handle_start_voice_command(transcript):
                    return
            except Exception:
                pass

            self._set_state("thinking")

            conversation_id = self._conversation_for_turn()
            token = CancellationToken()
            with self._lock:
                if generation != self._generation:
                    return
                self._inference_token = token
            try:
                result = context_chat.chat(
                    SOURCE_APP_KEY,
                    transcript,
                    conversation_id,
                    include_memory=True,
                    include_knowledge=True,
                    include_contacts=True,
                    context_options=None,
                    tool_permissions=set(),
                    owner_tools=True,
                    cancellation_token=token,
                )
            finally:
                with self._lock:
                    if self._inference_token is token:
                        self._inference_token = None
            reply = str(result.get("reply") or "").strip()
            if not reply:
                raise PhysicalAgentError("The Agent returned no reply.")

            if not self._generation_active(generation) or self._privacy_engaged():
                return

            agent = result.get("agent") if isinstance(result.get("agent"), dict) else {}
            agent_id = int(agent.get("id") or agent_voice_profiles.primary_agent_id())
            voice_profile = agent_voice_profiles.resolve_effective(agent_id)
            effective = voice_profile["effective"]
            if not effective["ready"]:
                raise PhysicalAgentError(
                    effective.get("warning") or "The Agent's local speaking voice is not ready."
                )
            spoken = local_voice.synthesize(
                reply,
                voice_key=effective["voice"],
                speaking_rate=effective["speaking_rate"],
                sentence_silence=effective["sentence_silence"],
            )

            if not self._generation_active(generation) or self._privacy_engaged():
                return

            with self._lock:
                self._conversation_id = str(result["conversation_id"])
                self._last_turn_at_monotonic = time.monotonic()
                self._last_reply_chars = len(reply)
                self._last_compute_source = str(result.get("compute_source") or "")
                self._turn_count += 1

            self._set_state("speaking")
            try:
                device_audio.device_audio.play_wav(spoken)
            except device_audio.DeviceAudioError:
                if not self._generation_active(generation):
                    return
                raise

            if not self._generation_active(generation):
                return

            try:
                cognitive_runtime.emit_event(
                    source_app_key=SOURCE_APP_KEY,
                    source_kind="owner",
                    event_id=f"physical-agent:{self._session_id}:{self._turn_count}",
                    event_type="physical_agent.turn",
                    summary="A VP3 Physical Agent voice turn completed.",
                    entity_type="conversation",
                    entity_key=str(result["conversation_id"]),
                    conversation_id=str(result["conversation_id"]),
                    correlation_id=self._session_id,
                    importance=0.55,
                    privacy_scope="private",
                    payload={
                        "physical_agent_version": PHYSICAL_AGENT_VERSION,
                        "audio_local": True,
                        "stt_provider": "whisper.cpp",
                        "tts_provider": "piper",
                        "compute_source": str(result.get("compute_source") or ""),
                        "run_id": int(result.get("run_id") or 0),
                    },
                    memory_candidate=False,
                )
            except Exception:
                pass

            self._set_state("idle")
        except Exception as exc:
            if self._generation_active(generation):
                self._set_state("error", error=str(exc))
        finally:
            with self._lock:
                if self._worker is threading.current_thread():
                    self._worker = None

    def cancel(self, reason: str = "cancelled") -> None:
        with self._lock:
            self._generation += 1
            self._cancel_reason = str(reason)[:80]
            token = self._inference_token
            self._inference_token = None
        if token is not None:
            token.cancel(reason)
        device_audio.device_audio.cancel_capture()
        device_audio.device_audio.stop_playback()

    def set_external_mode(self, mode: str | None) -> None:
        normalized = str(mode or "").strip().lower()[:40]
        with self._lock:
            self._external_mode = normalized
        if normalized:
            self.cancel(f"external_mode:{normalized}")
            if self._started:
                self._set_state("idle" if not self._privacy_engaged() else "privacy")
        # Clearing an external mode deliberately preserves the current Agent
        # state. Hardware privacy events are authoritative and may arrive just
        # before the controller's state snapshot.

    def external_mode(self) -> str:
        with self._lock:
            return self._external_mode

    def reset_conversation(self) -> None:
        with self._lock:
            self._conversation_id = None
            self._last_turn_at_monotonic = None

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "version": PHYSICAL_AGENT_VERSION,
                "started": self._started,
                "state": self._state,
                "state_changed_at": self._state_changed_at,
                "session_id": self._session_id,
                "conversation_id": self._conversation_id,
                "turn_count": self._turn_count,
                "last_error": self._last_error,
                "last_cancel_reason": self._cancel_reason,
                "last_transcript_chars": self._last_transcript_chars,
                "last_reply_chars": self._last_reply_chars,
                "last_compute_source": self._last_compute_source,
                "external_mode": self._external_mode,
                "audio": device_audio.device_audio.runtime_state(),
            }



def public_capability() -> dict[str, Any]:
    return {
        "version": PHYSICAL_AGENT_VERSION,
        "push_to_talk": True,
        "barge_in": True,
        "provider_cancellation": True,
        "privacy_interrupt": True,
        "local_stt": "whisper.cpp",
        "local_tts": "piper",
    }


def paired_status() -> dict[str, Any]:
    raw = runtime.status()
    return {
        "version": PHYSICAL_AGENT_VERSION,
        "started": bool(raw.get("started")),
        "state": str(raw.get("state") or "")[:40],
        "turn_count": int(raw.get("turn_count") or 0),
        "last_compute_source": str(raw.get("last_compute_source") or "")[:80],
        "external_mode": str(raw.get("external_mode") or "")[:40],
        "audio": {
            "capturing": bool(raw.get("audio", {}).get("capturing")),
            "playing": bool(raw.get("audio", {}).get("playing")),
        },
    }

runtime = PhysicalAgentRuntime()


def start() -> None:
    runtime.start()


def stop() -> None:
    runtime.stop()


def status() -> dict[str, Any]:
    return runtime.status()


def reset_conversation() -> dict[str, Any]:
    runtime.reset_conversation()
    return runtime.status()


def begin_listening() -> dict[str, Any]:
    return runtime.begin_listening()


def finish_listening() -> dict[str, Any]:
    return runtime.finish_listening()


def cancel(reason: str = "owner_cancelled") -> dict[str, Any]:
    runtime.cancel(reason)
    runtime._set_state("privacy" if runtime._privacy_engaged() else "idle")
    return runtime.status()


def set_external_mode(mode: str | None) -> dict[str, Any]:
    runtime.set_external_mode(mode)
    return runtime.status()
