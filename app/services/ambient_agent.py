from __future__ import annotations

import json
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from ..database import db
from . import (
    agent_voice_profiles,
    cognitive_runtime,
    device_audio,
    hardware_adapters,
    local_voice,
    physical_agent,
    physical_meeting,
    tasks,
    vp3_os,
)

AMBIENT_AGENT_VERSION = "v0.50"
SETTING_KEY = "ambient.preferences"
DELIVERY_KEY = "ambient.notification_delivery"
LOOP_SECONDS = 1.0

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "wake_enabled": True,
    "proactive_voice": True,
    "presence_policy": "sensor_required",
    "announcement_levels": ["warning"],
    "announcement_detail": "title_only",
    "cooldown_seconds": 30,
    "wake_timeout_seconds": 20,
    "max_announcements_per_hour": 6,
}

_ALLOWED_STATES = {
    "disabled",
    "armed",
    "present",
    "listening",
    "speaking",
    "privacy",
    "error",
}
_ALLOWED_LEVELS = {"info", "warning", "error"}
_ALLOWED_PRESENCE_POLICIES = {"sensor_required", "assume_present"}
_ALLOWED_DETAILS = {"title_only", "title_and_body"}


class AmbientAgentError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(minimum, min(maximum, number))


def normalize_settings(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    presence_policy = str(source.get("presence_policy") or DEFAULTS["presence_policy"])
    if presence_policy not in _ALLOWED_PRESENCE_POLICIES:
        presence_policy = DEFAULTS["presence_policy"]
    detail = str(source.get("announcement_detail") or DEFAULTS["announcement_detail"])
    if detail not in _ALLOWED_DETAILS:
        detail = DEFAULTS["announcement_detail"]

    raw_levels = source.get("announcement_levels")
    levels: list[str] = []
    if isinstance(raw_levels, list):
        for raw in raw_levels[:8]:
            level = str(raw or "").strip().lower()
            if level in _ALLOWED_LEVELS and level not in levels:
                levels.append(level)
    if not levels:
        levels = list(DEFAULTS["announcement_levels"])

    return {
        "enabled": bool(source.get("enabled", DEFAULTS["enabled"])),
        "wake_enabled": bool(source.get("wake_enabled", DEFAULTS["wake_enabled"])),
        "proactive_voice": bool(source.get("proactive_voice", DEFAULTS["proactive_voice"])),
        "presence_policy": presence_policy,
        "announcement_levels": levels,
        "announcement_detail": detail,
        "cooldown_seconds": _bounded_int(
            source.get("cooldown_seconds"),
            int(DEFAULTS["cooldown_seconds"]),
            5,
            3600,
        ),
        "wake_timeout_seconds": _bounded_int(
            source.get("wake_timeout_seconds"),
            int(DEFAULTS["wake_timeout_seconds"]),
            5,
            120,
        ),
        "max_announcements_per_hour": _bounded_int(
            source.get("max_announcements_per_hour"),
            int(DEFAULTS["max_announcements_per_hour"]),
            1,
            60,
        ),
    }


def get_settings() -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            "SELECT value_json FROM system_settings WHERE setting_key=? LIMIT 1",
            (SETTING_KEY,),
        ).fetchone()
    if row is None:
        return dict(DEFAULTS)
    try:
        raw = json.loads(row["value_json"])
    except (TypeError, ValueError, json.JSONDecodeError):
        raw = {}
    return normalize_settings(raw)


def save_settings(value: Any) -> dict[str, Any]:
    settings = normalize_settings(value)
    encoded = json.dumps(settings, ensure_ascii=False, separators=(",", ":"))
    with db() as connection:
        connection.execute(
            """
            INSERT INTO system_settings(setting_key, value_json)
            VALUES (?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET
                value_json=excluded.value_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (SETTING_KEY, encoded),
        )
        connection.execute(
            """
            INSERT INTO activity_log(
                actor_type, actor_key, action, resource_type, resource_key, metadata_json
            ) VALUES ('owner', 'control-center', 'ambient.settings.updated', 'vp3_os', 'ambient', ?)
            """,
            (
                json.dumps(
                    {
                        "enabled": bool(settings["enabled"]),
                        "wake_enabled": bool(settings["wake_enabled"]),
                        "proactive_voice": bool(settings["proactive_voice"]),
                        "presence_policy": settings["presence_policy"],
                    },
                    separators=(",", ":"),
                ),
            ),
        )
    return settings


def _load_delivered_ids() -> list[int]:
    with db() as connection:
        row = connection.execute(
            "SELECT value_json FROM system_settings WHERE setting_key=? LIMIT 1",
            (DELIVERY_KEY,),
        ).fetchone()
    if row is None:
        return []
    try:
        raw = json.loads(row["value_json"])
    except (TypeError, ValueError, json.JSONDecodeError):
        raw = {}
    values = raw.get("notification_ids") if isinstance(raw, dict) else []
    ids: list[int] = []
    if isinstance(values, list):
        for value in values[-200:]:
            try:
                item = int(value)
            except (TypeError, ValueError):
                continue
            if item > 0 and item not in ids:
                ids.append(item)
    return ids[-200:]


def _save_delivered_ids(ids: list[int]) -> None:
    payload = json.dumps(
        {"notification_ids": [int(value) for value in ids[-200:]]},
        separators=(",", ":"),
    )
    with db() as connection:
        connection.execute(
            """
            INSERT INTO system_settings(setting_key, value_json)
            VALUES (?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET
                value_json=excluded.value_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (DELIVERY_KEY, payload),
        )


class AmbientAgentRuntime:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._started = False
        self._state = "disabled"
        self._state_changed_at = _now_iso()
        self._presence = "unknown"
        self._last_presence_at = ""
        self._wake_active = False
        self._wake_started_monotonic = 0.0
        self._last_wake_at = ""
        self._last_announcement_id: int | None = None
        self._last_announcement_at = ""
        self._last_error = ""
        self._privacy_blocked = False
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._delivered_ids: list[int] = []
        self._announcement_times: deque[float] = deque(maxlen=120)

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._delivered_ids = _load_delivered_ids()
            self._privacy_blocked = bool(
                vp3_os.manifest(include_hardware=False, include_device_id=False)["privacy"].get(
                    "privacy_switch_engaged"
                )
            )
            self._stop.clear()
        hardware_adapters.add_event_handler(self.handle_hardware_event)
        self._sync_state()
        thread = threading.Thread(
            target=self._loop,
            name="vp3-ambient-agent",
            daemon=True,
        )
        with self._lock:
            self._thread = thread
        thread.start()

    def stop(self) -> None:
        hardware_adapters.remove_event_handler(self.handle_hardware_event)
        self._stop.set()
        with self._lock:
            wake_active = self._wake_active
            thread = self._thread
            self._wake_active = False
            self._wake_started_monotonic = 0.0
        if wake_active:
            physical_agent.cancel("ambient_runtime_stopped")
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5)
        with self._lock:
            self._thread = None
            self._started = False
            self._state = "disabled"
            self._state_changed_at = _now_iso()

    def _privacy_engaged(self) -> bool:
        with self._lock:
            return bool(self._privacy_blocked)

    def _settings(self) -> dict[str, Any]:
        return get_settings()

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

    def _sync_presence_from_hardware(self) -> None:
        sensor = vp3_os.hardware_inventory().get("presence_sensor", {})
        if not bool(sensor.get("present")) or not bool(sensor.get("ready")):
            return
        occupied = "present" if bool(sensor.get("occupied")) else "absent"
        with self._lock:
            if self._presence == occupied:
                return
            self._presence = occupied
            self._last_presence_at = _now_iso()

    def _presence_allowed(self, settings: dict[str, Any]) -> bool:
        if settings["presence_policy"] == "assume_present":
            return True
        with self._lock:
            return self._presence == "present"

    def _sync_state(self) -> None:
        settings = self._settings()
        if not self._started or not settings["enabled"]:
            self._set_state("disabled")
            return
        if self._privacy_engaged():
            self._set_state("privacy")
            return
        with self._lock:
            wake_active = self._wake_active
            presence = self._presence
        if wake_active:
            self._set_state("listening")
        elif presence == "present":
            self._set_state("present")
        else:
            self._set_state("armed")

    def handle_hardware_event(self, event: dict[str, Any]) -> None:
        if not self._started:
            return
        settings = self._settings()
        event_type = str(event.get("event") or "")
        action = str(event.get("action") or "")

        if event_type == "privacy_switch":
            if action == "engaged":
                with self._lock:
                    self._privacy_blocked = True
                    wake_active = self._wake_active
                    self._wake_active = False
                    self._wake_started_monotonic = 0.0
                if wake_active:
                    physical_agent.cancel("ambient_privacy_engaged")
                self._set_state("privacy")
            elif action == "disengaged":
                with self._lock:
                    self._privacy_blocked = False
                self._sync_state()
            return

        if not settings["enabled"]:
            return

        if event_type == "presence_sensor":
            with self._lock:
                self._presence = "present" if action == "present" else "absent"
                self._last_presence_at = _now_iso()
            self._sync_state()
            return

        if event_type == "wake_word" and action == "detected":
            self._handle_wake(settings)
            return

        if event_type == "voice_activity" and action == "stopped":
            with self._lock:
                wake_active = self._wake_active
                self._wake_active = False
                self._wake_started_monotonic = 0.0
            if wake_active:
                physical_agent.finish_listening()
                self._sync_state()
            return

    def _meeting_active(self) -> bool:
        status = physical_meeting.status()
        return bool(status.get("meeting_id")) or str(status.get("state") or "") not in {
            "idle",
            "disabled",
            "privacy",
            "error",
        }

    def _handle_wake(self, settings: dict[str, Any]) -> None:
        if not settings["wake_enabled"] or self._privacy_engaged() or self._meeting_active():
            return

        with self._lock:
            if self._wake_active:
                return
            self._presence = "present"
            self._last_presence_at = _now_iso()

        agent_state = str(physical_agent.status().get("state") or "")
        if agent_state not in {"idle", "error", "speaking", "thinking", "transcribing"}:
            return

        result = physical_agent.begin_listening()
        if not bool(result.get("started")):
            return

        with self._lock:
            self._wake_active = True
            self._wake_started_monotonic = time.monotonic()
            self._last_wake_at = _now_iso()
        self._set_state("listening")
        self._emit_metadata_event(
            "ambient.wake",
            "Ambient wake event opened a bounded Physical Agent turn.",
            {"local_only_trigger": True},
        )

    def _eligible_notification(
        self,
        settings: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not settings["proactive_voice"] or not self._presence_allowed(settings):
            return None
        with self._lock:
            delivered = set(self._delivered_ids)
        items = tasks.list_notifications(
            unread_only=True,
            include_dismissed=False,
            limit=50,
        )
        allowed_levels = set(settings["announcement_levels"])
        for item in reversed(items):
            notification_id = int(item.get("id") or 0)
            if notification_id <= 0 or notification_id in delivered:
                continue
            if str(item.get("level") or "info").lower() not in allowed_levels:
                continue
            return item
        return None

    def _can_announce(self, settings: dict[str, Any]) -> bool:
        if self._privacy_engaged() or self._meeting_active():
            return False
        with self._lock:
            if self._state == "privacy":
                return False
        agent_state = str(physical_agent.status().get("state") or "")
        if agent_state not in {"idle", "error"}:
            return False
        audio = device_audio.device_audio.runtime_state()
        if bool(audio.get("capturing")) or bool(audio.get("playing")):
            return False
        speaker = vp3_os.hardware_inventory().get("speaker", {})
        if not bool(speaker.get("ready")):
            return False
        with self._lock:
            if self._wake_active:
                return False
            last_at = self._last_announcement_at
        if last_at:
            try:
                previous = datetime.fromisoformat(last_at)
                elapsed = (datetime.now(timezone.utc) - previous.astimezone(timezone.utc)).total_seconds()
                if elapsed < int(settings["cooldown_seconds"]):
                    return False
            except ValueError:
                pass

        now = time.monotonic()
        with self._lock:
            while self._announcement_times and (now - self._announcement_times[0]) >= 3600:
                self._announcement_times.popleft()
            if len(self._announcement_times) >= int(settings["max_announcements_per_hour"]):
                return False
        return True

    def _announcement_text(
        self,
        notification: dict[str, Any],
        settings: dict[str, Any],
    ) -> str:
        title = " ".join(str(notification.get("title") or "Notification").split())[:300]
        if settings["announcement_detail"] == "title_only":
            return title
        body = " ".join(str(notification.get("body") or "").split())[:600]
        return f"{title}. {body}" if body else title

    def _announce(self, notification: dict[str, Any], settings: dict[str, Any]) -> None:
        notification_id = int(notification["id"])
        try:
            agent_id = agent_voice_profiles.primary_agent_id()
            profile = agent_voice_profiles.resolve_effective(agent_id)
            effective = profile["effective"]
            if not effective["ready"]:
                raise AmbientAgentError(
                    effective.get("warning") or "Local Agent voice is not ready."
                )
            text = self._announcement_text(notification, settings)
            spoken = local_voice.synthesize(
                text,
                voice_key=effective["voice"],
                speaking_rate=effective["speaking_rate"],
                sentence_silence=effective["sentence_silence"],
            )
            self._set_state("speaking")
            try:
                hardware_adapters.set_status_light("speaking")
            except Exception:
                pass
            device_audio.device_audio.play_wav(spoken)
        except Exception as exc:
            self._set_state("error", error=str(exc))
            return
        finally:
            try:
                if str(physical_agent.status().get("state") or "") in {"idle", "error"}:
                    hardware_adapters.set_status_light("idle")
            except Exception:
                pass

        now_monotonic = time.monotonic()
        with self._lock:
            if notification_id not in self._delivered_ids:
                self._delivered_ids.append(notification_id)
                self._delivered_ids = self._delivered_ids[-200:]
            self._last_announcement_id = notification_id
            self._last_announcement_at = _now_iso()
            self._announcement_times.append(now_monotonic)
            delivered = list(self._delivered_ids)
        _save_delivered_ids(delivered)
        self._emit_metadata_event(
            "ambient.notification_announced",
            "Ambient Agent announced a local notification.",
            {
                "notification_id": notification_id,
                "level": str(notification.get("level") or "")[:40],
                "task_id": int(notification.get("task_id") or 0) or None,
                "detail_mode": settings["announcement_detail"],
            },
        )
        self._sync_state()

    def _emit_metadata_event(
        self,
        event_type: str,
        summary: str,
        payload: dict[str, Any],
    ) -> None:
        try:
            cognitive_runtime.emit_event(
                source_app_key="owner",
                source_kind="owner",
                event_id=f"{event_type}:{int(time.time() * 1000)}",
                event_type=event_type,
                summary=summary,
                entity_type="ambient_agent",
                entity_key="local_room",
                importance=0.35,
                privacy_scope="private",
                payload={
                    "ambient_agent_version": AMBIENT_AGENT_VERSION,
                    **payload,
                },
                memory_candidate=False,
            )
        except Exception:
            pass

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                settings = self._settings()
                if settings["enabled"]:
                    self._sync_presence_from_hardware()
                if not settings["enabled"]:
                    self._sync_state()
                    self._stop.wait(LOOP_SECONDS)
                    continue

                with self._lock:
                    wake_active = self._wake_active
                    wake_started = self._wake_started_monotonic
                if (
                    wake_active
                    and wake_started
                    and (time.monotonic() - wake_started) >= int(settings["wake_timeout_seconds"])
                ):
                    with self._lock:
                        self._wake_active = False
                        self._wake_started_monotonic = 0.0
                    physical_agent.cancel("ambient_wake_timeout")
                    self._emit_metadata_event(
                        "ambient.wake_timeout",
                        "Ambient wake session timed out without a bounded voice-activity stop.",
                        {},
                    )
                    self._sync_state()

                if self._can_announce(settings):
                    notification = self._eligible_notification(settings)
                    if notification is not None:
                        self._announce(notification, settings)
                else:
                    self._sync_state()
            except Exception as exc:
                self._set_state("error", error=str(exc))
            self._stop.wait(LOOP_SECONDS)

    def update_settings(self, value: Any) -> dict[str, Any]:
        settings = save_settings(value)
        if settings["enabled"]:
            self._sync_presence_from_hardware()
        if not settings["enabled"]:
            with self._lock:
                wake_active = self._wake_active
                self._wake_active = False
                self._wake_started_monotonic = 0.0
                self._presence = "unknown"
                self._last_presence_at = ""
            if wake_active:
                physical_agent.cancel("ambient_disabled")
        self._sync_state()
        return {"settings": settings, "status": self.status()}

    def announce_test(self) -> dict[str, Any]:
        settings = self._settings()
        if not settings["enabled"]:
            raise AmbientAgentError("Ambient Agent is disabled.")
        if self._privacy_engaged():
            raise AmbientAgentError("Physical microphone privacy is engaged.")
        if not self._presence_allowed(settings):
            raise AmbientAgentError("Ambient presence policy does not currently allow speech.")
        if not self._can_announce(settings):
            raise AmbientAgentError("Ambient Agent is currently busy or rate-limited.")
        fake = {
            "id": -1,
            "title": "VP3 Ambient Agent is ready.",
            "body": "",
            "level": "info",
            "task_id": None,
        }
        try:
            agent_id = agent_voice_profiles.primary_agent_id()
            profile = agent_voice_profiles.resolve_effective(agent_id)
            effective = profile["effective"]
            if not effective["ready"]:
                raise AmbientAgentError(
                    effective.get("warning") or "Local Agent voice is not ready."
                )
            spoken = local_voice.synthesize(
                self._announcement_text(fake, settings),
                voice_key=effective["voice"],
                speaking_rate=effective["speaking_rate"],
                sentence_silence=effective["sentence_silence"],
            )
            self._set_state("speaking")
            device_audio.device_audio.play_wav(spoken)
        except AmbientAgentError:
            raise
        except Exception as exc:
            raise AmbientAgentError(f"Ambient local voice test failed: {str(exc)[:180]}") from exc
        finally:
            self._sync_state()
        return {"spoken": True}

    def status(self) -> dict[str, Any]:
        settings = self._settings()
        inventory = vp3_os.hardware_inventory()
        controller = hardware_adapters.status().get("controller")
        capabilities = (
            {
                str(value)
                for value in controller.get("capabilities", [])
                if isinstance(value, str)
            }
            if isinstance(controller, dict)
            else set()
        )
        with self._lock:
            return {
                "version": AMBIENT_AGENT_VERSION,
                "started": self._started,
                "state": self._state,
                "state_changed_at": self._state_changed_at,
                "settings": settings,
                "presence": self._presence,
                "last_presence_at": self._last_presence_at,
                "wake_active": self._wake_active,
                "last_wake_at": self._last_wake_at,
                "last_announcement_id": self._last_announcement_id,
                "last_announcement_at": self._last_announcement_at,
                "last_error": self._last_error,
                "hardware": {
                    "presence_sensor": inventory.get("presence_sensor", {}),
                    "wake_word_event": "wake_word" in capabilities,
                    "voice_activity_event": "voice_activity" in capabilities,
                },
            }


runtime = AmbientAgentRuntime()


def public_capability() -> dict[str, Any]:
    return {
        "version": AMBIENT_AGENT_VERSION,
        "opt_in_default": False,
        "presence_events": True,
        "wake_word_events": True,
        "voice_activity_events": True,
        "proactive_voice": True,
        "ambient_microphone_capture": False,
        "ambient_transcription": False,
        "ambient_memory": False,
    }


def paired_status() -> dict[str, Any]:
    raw = runtime.status()
    settings = raw["settings"]
    return {
        "version": AMBIENT_AGENT_VERSION,
        "enabled": bool(settings["enabled"]),
        "wake_enabled": bool(settings["wake_enabled"]),
        "proactive_voice": bool(settings["proactive_voice"]),
        "state": "active" if settings["enabled"] else "disabled",
        "ambient_microphone_capture": False,
        "ambient_transcription": False,
        "ambient_memory": False,
    }


def start() -> None:
    runtime.start()


def stop() -> None:
    runtime.stop()


def status() -> dict[str, Any]:
    return runtime.status()


def update_settings(value: Any) -> dict[str, Any]:
    return runtime.update_settings(value)


def announce_test() -> dict[str, Any]:
    return runtime.announce_test()
