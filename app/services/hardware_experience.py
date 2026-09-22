from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from typing import Any

from ..database import db
from . import (
    ambient_agent,
    device_rollout,
    hardware_adapters,
    physical_agent,
    physical_meeting,
    vp3_os,
)

HARDWARE_EXPERIENCE_VERSION = "v1.3"
_LOOP_SECONDS = 0.5
_ALLOWED_WAKE = {"manual", "presence", "always_on"}
_ALLOWED_DISPLAY_DETAIL = {"minimal", "standard", "detailed"}
_ALLOWED_AGENT_BUTTON_ACTION = {"push_to_talk", "ask_agent", "none"}
_ALLOWED_HOLD_ACTION = {"cancel", "meeting_toggle", "privacy_hint", "none"}
_ALLOWED_CARD_TYPES = {
    "agent",
    "meeting",
    "notification",
    "room_mode",
    "timer",
    "system",
    "update",
}
_ALLOWED_CARD_STATES = {"active", "dismissed", "expired"}

_PROFILE_EXPERIENCES: dict[str, dict[str, Any]] = {
    "custom": {
        "experience": "generic",
        "capabilities": ["status"],
        "display_modes": [],
        "controls": [],
        "required": [],
        "optional": ["status_light", "microphone", "speaker", "display"],
    },
    "vp3_node": {
        "experience": "personal_voice",
        "capabilities": [
            "push_to_talk",
            "voice_agent",
            "status_language",
            "privacy",
            "ambient_presence",
        ],
        "display_modes": [],
        "controls": ["agent_button", "privacy_switch"],
        "required": ["agent_button", "status_light", "microphone", "speaker", "privacy_switch"],
        "optional": ["presence_sensor", "accelerator"],
    },
    "vp3_desk": {
        "experience": "desk_companion",
        "capabilities": [
            "push_to_talk",
            "voice_agent",
            "status_language",
            "privacy",
            "display_cards",
            "ambient_presence",
            "meeting_controls",
        ],
        "display_modes": ["agent", "meeting", "notification", "room_mode", "timer", "system", "update"],
        "controls": ["agent_button", "privacy_switch", "control_dial"],
        "required": ["agent_button", "status_light", "microphone", "speaker", "privacy_switch", "display"],
        "optional": ["presence_sensor", "accelerator", "control_dial"],
    },
    "vp3_studio": {
        "experience": "creator_console",
        "capabilities": [
            "push_to_talk",
            "voice_agent",
            "status_language",
            "privacy",
            "creator_audio",
            "meeting_controls",
            "recording_state",
        ],
        "display_modes": ["meeting", "system", "update"],
        "controls": ["agent_button", "privacy_switch", "control_dial"],
        "required": ["agent_button", "status_light", "microphone", "speaker", "privacy_switch", "audio_io"],
        "optional": ["display", "presence_sensor", "accelerator", "control_dial"],
    },
    "vp3_team_node": {
        "experience": "shared_room",
        "capabilities": [
            "status_language",
            "privacy",
            "meeting_controls",
            "room_presence",
            "shared_notifications",
        ],
        "display_modes": ["meeting", "notification", "room_mode", "system", "update"],
        "controls": ["agent_button", "privacy_switch"],
        "required": ["agent_button", "status_light", "privacy_switch"],
        "optional": ["microphone", "speaker", "display", "presence_sensor"],
    },
    "vp3_pocket": {
        "experience": "portable_companion",
        "capabilities": [
            "push_to_talk",
            "voice_agent",
            "status_language",
            "privacy",
            "display_cards",
            "portable_power",
            "ambient_presence",
        ],
        "display_modes": ["agent", "notification", "timer", "system", "update"],
        "controls": ["agent_button", "privacy_switch", "control_dial"],
        "required": ["agent_button", "status_light", "microphone", "speaker", "privacy_switch", "display", "battery"],
        "optional": ["presence_sensor", "accelerator", "control_dial"],
    },
}

_STATUS_TO_LIGHT = {
    "off": "off",
    "idle": "idle",
    "listening": "listening",
    "thinking": "thinking",
    "speaking": "speaking",
    "meeting": "listening",
    "privacy": "privacy",
    "error": "error",
    "updating": "updating",
}


class HardwareExperienceError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


_LOCK = threading.RLock()
_STOP = threading.Event()
_THREAD: threading.Thread | None = None
_STARTED = False
_LAST_VISUAL_STATE = ""
_LAST_LIGHT_MODE = ""
_SCREEN_AWAKE = True
_LAST_PRESENCE_AT = ""
_LAST_EVENT_AT = ""
_LAST_ERROR = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _read_json(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        result = default
    return max(minimum, min(maximum, result))


def profile_experience(profile_key: str | None = None) -> dict[str, Any]:
    profile = vp3_os.profile_definition(profile_key)
    definition = _PROFILE_EXPERIENCES.get(profile["key"], _PROFILE_EXPERIENCES["custom"])
    return {
        "profile": profile,
        "experience": definition["experience"],
        "capabilities": list(definition["capabilities"]),
        "display_modes": list(definition["display_modes"]),
        "controls": list(definition["controls"]),
        "required_hardware": list(definition["required"]),
        "optional_hardware": list(definition["optional"]),
    }


def get_settings() -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            """
            SELECT enabled,brightness_percent,volume_percent,led_intensity_percent,
                   screen_timeout_seconds,wake_behavior,agent_button_action,
                   hold_action,display_detail,quiet_visuals,updated_at
            FROM vp3_hardware_experience_settings WHERE id=1
            """
        ).fetchone()
    if row is None:
        raise HardwareExperienceError("Hardware Experience settings are unavailable.", 503)
    return {
        "enabled": bool(row["enabled"]),
        "brightness_percent": int(row["brightness_percent"]),
        "volume_percent": int(row["volume_percent"]),
        "led_intensity_percent": int(row["led_intensity_percent"]),
        "screen_timeout_seconds": int(row["screen_timeout_seconds"]),
        "wake_behavior": row["wake_behavior"],
        "agent_button_action": row["agent_button_action"],
        "hold_action": row["hold_action"],
        "display_detail": row["display_detail"],
        "quiet_visuals": bool(row["quiet_visuals"]),
        "updated_at": row["updated_at"],
    }


def update_settings(
    *,
    enabled: bool | None = None,
    brightness_percent: int | None = None,
    volume_percent: int | None = None,
    led_intensity_percent: int | None = None,
    screen_timeout_seconds: int | None = None,
    wake_behavior: str | None = None,
    agent_button_action: str | None = None,
    hold_action: str | None = None,
    display_detail: str | None = None,
    quiet_visuals: bool | None = None,
) -> dict[str, Any]:
    current = get_settings()
    wake = str(wake_behavior or current["wake_behavior"]).strip().lower()
    button = str(agent_button_action or current["agent_button_action"]).strip().lower()
    hold = str(hold_action or current["hold_action"]).strip().lower()
    detail = str(display_detail or current["display_detail"]).strip().lower()
    if wake not in _ALLOWED_WAKE:
        raise HardwareExperienceError("Wake behavior is invalid.")
    if button not in _ALLOWED_AGENT_BUTTON_ACTION:
        raise HardwareExperienceError("Agent button action is invalid.")
    if hold not in _ALLOWED_HOLD_ACTION:
        raise HardwareExperienceError("Hold action is invalid.")
    if detail not in _ALLOWED_DISPLAY_DETAIL:
        raise HardwareExperienceError("Display detail mode is invalid.")
    values = {
        "enabled": current["enabled"] if enabled is None else bool(enabled),
        "brightness_percent": _bounded_int(
            current["brightness_percent"] if brightness_percent is None else brightness_percent,
            current["brightness_percent"], 0, 100,
        ),
        "volume_percent": _bounded_int(
            current["volume_percent"] if volume_percent is None else volume_percent,
            current["volume_percent"], 0, 100,
        ),
        "led_intensity_percent": _bounded_int(
            current["led_intensity_percent"] if led_intensity_percent is None else led_intensity_percent,
            current["led_intensity_percent"], 0, 100,
        ),
        "screen_timeout_seconds": _bounded_int(
            current["screen_timeout_seconds"] if screen_timeout_seconds is None else screen_timeout_seconds,
            current["screen_timeout_seconds"], 15, 86400,
        ),
        "wake_behavior": wake,
        "agent_button_action": button,
        "hold_action": hold,
        "display_detail": detail,
        "quiet_visuals": current["quiet_visuals"] if quiet_visuals is None else bool(quiet_visuals),
    }
    with db() as connection:
        connection.execute(
            """
            UPDATE vp3_hardware_experience_settings
            SET enabled=?,brightness_percent=?,volume_percent=?,led_intensity_percent=?,
                screen_timeout_seconds=?,wake_behavior=?,agent_button_action=?,
                hold_action=?,display_detail=?,quiet_visuals=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=1
            """,
            (
                int(values["enabled"]),
                values["brightness_percent"],
                values["volume_percent"],
                values["led_intensity_percent"],
                values["screen_timeout_seconds"],
                values["wake_behavior"],
                values["agent_button_action"],
                values["hold_action"],
                values["display_detail"],
                int(values["quiet_visuals"]),
            ),
        )
    return get_settings()


def button_policy() -> dict[str, str]:
    settings = get_settings()
    return {
        "agent_button_action": str(settings["agent_button_action"]),
        "hold_action": str(settings["hold_action"]),
    }


def _record_event(
    event_type: str,
    action: str,
    *,
    source: str = "hardware",
    handled: bool = False,
    outcome: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    safe_metadata: dict[str, Any] = {}
    allowed = {"seq", "profile", "visual_state", "screen_awake", "reason"}
    for key, value in (metadata or {}).items():
        if key in allowed:
            safe_metadata[key] = value
    with db() as connection:
        connection.execute(
            """
            INSERT INTO vp3_hardware_experience_events(
                event_type,action,source,handled,outcome,metadata_json
            ) VALUES (?,?,?,?,?,?)
            """,
            (
                str(event_type or "")[:80],
                str(action or "")[:80],
                str(source or "")[:40],
                int(bool(handled)),
                str(outcome or "")[:120],
                _json(safe_metadata),
            ),
        )


def recent_events(limit: int = 50) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 200))
    with db() as connection:
        rows = connection.execute(
            """
            SELECT id,event_type,action,source,handled,outcome,metadata_json,created_at
            FROM vp3_hardware_experience_events
            ORDER BY id DESC LIMIT ?
            """,
            (bounded,),
        ).fetchall()
    result = []
    for row in rows:
        result.append(
            {
                "id": int(row["id"]),
                "event_type": row["event_type"],
                "action": row["action"],
                "source": row["source"],
                "handled": bool(row["handled"]),
                "outcome": row["outcome"],
                "metadata": _read_json(row["metadata_json"], {}),
                "created_at": row["created_at"],
            }
        )
    return result


def upsert_card(
    card_key: str,
    card_type: str,
    title: str,
    *,
    subtitle: str = "",
    priority: int = 50,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    key = str(card_key or "").strip()[:100]
    kind = str(card_type or "").strip().lower()
    clean_title = " ".join(str(title or "").split())[:180]
    if not key or not clean_title:
        raise HardwareExperienceError("Display card key and title are required.")
    if kind not in _ALLOWED_CARD_TYPES:
        raise HardwareExperienceError("Display card type is invalid.")
    safe_payload = {}
    for name in ("state", "duration_ms", "count", "level", "progress_percent"):
        if name in (payload or {}):
            safe_payload[name] = (payload or {})[name]
    with db() as connection:
        connection.execute(
            """
            INSERT INTO vp3_hardware_experience_cards(
                card_key,card_type,title,subtitle,state,priority,payload_json
            ) VALUES (?,?,?,?,'active',?,?)
            ON CONFLICT(card_key) DO UPDATE SET
                card_type=excluded.card_type,
                title=excluded.title,
                subtitle=excluded.subtitle,
                state='active',
                priority=excluded.priority,
                payload_json=excluded.payload_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                key,
                kind,
                clean_title,
                " ".join(str(subtitle or "").split())[:240],
                _bounded_int(priority, 50, 0, 100),
                _json(safe_payload),
            ),
        )
    return get_card(key)


def get_card(card_key: str) -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            """
            SELECT card_key,card_type,title,subtitle,state,priority,payload_json,updated_at
            FROM vp3_hardware_experience_cards WHERE card_key=?
            """,
            (str(card_key),),
        ).fetchone()
    if row is None:
        raise HardwareExperienceError("Display card was not found.", 404)
    return {
        "card_key": row["card_key"],
        "card_type": row["card_type"],
        "title": row["title"],
        "subtitle": row["subtitle"],
        "state": row["state"],
        "priority": int(row["priority"]),
        "payload": _read_json(row["payload_json"], {}),
        "updated_at": row["updated_at"],
    }


def list_cards(limit: int = 12) -> list[dict[str, Any]]:
    experience = profile_experience()
    if "display_cards" not in experience["capabilities"] and not experience["display_modes"]:
        return []
    bounded = max(1, min(int(limit), 50))
    allowed = set(experience["display_modes"])
    with db() as connection:
        rows = connection.execute(
            """
            SELECT card_key FROM vp3_hardware_experience_cards
            WHERE state='active'
            ORDER BY priority DESC,updated_at DESC
            LIMIT ?
            """,
            (bounded * 3,),
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        card = get_card(row["card_key"])
        if allowed and card["card_type"] not in allowed:
            continue
        result.append(card)
        if len(result) >= bounded:
            break
    return result


def set_card_state(card_key: str, state: str) -> dict[str, Any]:
    normalized = str(state or "").strip().lower()
    if normalized not in _ALLOWED_CARD_STATES:
        raise HardwareExperienceError("Display card state is invalid.")
    with db() as connection:
        cursor = connection.execute(
            """
            UPDATE vp3_hardware_experience_cards
            SET state=?,updated_at=CURRENT_TIMESTAMP
            WHERE card_key=?
            """,
            (normalized, str(card_key)),
        )
        if cursor.rowcount != 1:
            raise HardwareExperienceError("Display card was not found.", 404)
    return get_card(card_key)


def _privacy_engaged() -> bool:
    return bool(
        vp3_os.manifest(include_hardware=False, include_device_id=False)["privacy"].get(
            "privacy_switch_engaged"
        )
    )


def degraded_state() -> dict[str, Any]:
    experience = profile_experience()
    inventory = vp3_os.hardware_inventory()
    required = experience["required_hardware"]
    missing = [key for key in required if not inventory.get(key, {}).get("present")]
    not_ready = [
        key
        for key in required
        if inventory.get(key, {}).get("present") and not inventory.get(key, {}).get("ready")
    ]
    adapter = hardware_adapters.status()
    reasons: list[str] = []
    if adapter.get("mode") != "disabled" and not adapter.get("connected"):
        reasons.append("controller_offline")
    if missing:
        reasons.append("required_hardware_missing")
    if not_ready:
        reasons.append("required_hardware_not_ready")
    if _privacy_engaged():
        reasons.append("privacy")
    if "microphone" in missing or "microphone" in not_ready:
        reasons.append("voice_input_unavailable")
    if "speaker" in missing or "speaker" in not_ready:
        reasons.append("voice_output_unavailable")
    if "display" in missing or "display" in not_ready:
        reasons.append("display_unavailable")
    return {
        "degraded": bool(reasons),
        "reasons": reasons,
        "missing_hardware": missing,
        "not_ready_hardware": not_ready,
        "voice_available": (
            inventory.get("microphone", {}).get("ready")
            and inventory.get("speaker", {}).get("ready")
            and not _privacy_engaged()
        ),
        "display_available": bool(inventory.get("display", {}).get("ready")),
        "status_light_available": bool(inventory.get("status_light", {}).get("ready")),
    }


def visual_state() -> dict[str, Any]:
    settings = get_settings()
    rollout_packages = device_rollout.list_packages(10)
    updating = any(
        str(item.get("status") or "") in {"applying"}
        for item in rollout_packages
    )
    meeting = physical_meeting.status()
    agent = physical_agent.status()
    ambient = ambient_agent.status()
    degraded = degraded_state()

    state = "idle"
    reason = "idle"
    if not settings["enabled"]:
        state, reason = "off", "experience_disabled"
    elif _privacy_engaged():
        state, reason = "privacy", "physical_privacy"
    elif updating:
        state, reason = "updating", "software_update"
    elif str(meeting.get("state") or "") in {
        "meeting_starting",
        "recording",
        "meeting_ending",
        "finalizing",
    }:
        state, reason = "meeting", "physical_meeting"
    elif str(agent.get("state") or "") in {"listening", "transcribing", "thinking", "speaking"}:
        agent_state = str(agent.get("state"))
        state = "thinking" if agent_state == "transcribing" else agent_state
        reason = "physical_agent"
    elif degraded["degraded"] and any(
        item in degraded["reasons"]
        for item in {"controller_offline", "required_hardware_missing", "required_hardware_not_ready"}
    ):
        state, reason = "error", "hardware_degraded"
    elif str(ambient.get("state") or "") == "listening":
        state, reason = "listening", "ambient_agent"
    elif str(ambient.get("state") or "") == "present":
        state, reason = "idle", "presence"
    return {
        "state": state,
        "reason": reason,
        "light_mode": _STATUS_TO_LIGHT[state],
        "agent_state": str(agent.get("state") or ""),
        "meeting_state": str(meeting.get("state") or ""),
        "ambient_state": str(ambient.get("state") or ""),
    }


def _compute_screen_awake(event_type: str = "", action: str = "") -> bool:
    settings = get_settings()
    experience = profile_experience()
    if not experience["display_modes"]:
        return False
    if settings["wake_behavior"] == "always_on":
        return True
    if settings["wake_behavior"] == "manual":
        return _SCREEN_AWAKE
    if event_type == "presence_sensor":
        return action == "present"
    sensor = vp3_os.hardware_inventory().get("presence_sensor", {})
    if sensor.get("present") and sensor.get("ready"):
        return bool(sensor.get("occupied"))
    return _SCREEN_AWAKE


def handle_hardware_event(event: dict[str, Any]) -> None:
    global _SCREEN_AWAKE, _LAST_PRESENCE_AT, _LAST_EVENT_AT
    event_type = str(event.get("event") or "")[:80]
    action = str(event.get("action") or "")[:80]
    handled = False
    outcome = "observed"

    if event_type == "presence_sensor":
        _SCREEN_AWAKE = _compute_screen_awake(event_type, action)
        _LAST_PRESENCE_AT = _now_iso()
        handled = True
        outcome = "screen_awake" if _SCREEN_AWAKE else "screen_sleep"
    elif event_type == "privacy_switch":
        handled = True
        outcome = "privacy" if action == "engaged" else "privacy_released"
    elif event_type == "agent_button":
        handled = True
        outcome = "delegated_to_physical_runtimes"
    elif event_type == "control_dial":
        experience = profile_experience()
        if "control_dial" in experience["controls"]:
            settings = get_settings()
            if action == "clockwise":
                update_settings(volume_percent=min(100, int(settings["volume_percent"]) + 5))
                handled = True
                outcome = "volume_up"
            elif action == "counterclockwise":
                update_settings(volume_percent=max(0, int(settings["volume_percent"]) - 5))
                handled = True
                outcome = "volume_down"
            elif action == "press":
                handled = True
                outcome = "dial_press"
    elif event_type in {"wake_word", "voice_activity"}:
        handled = True
        outcome = "delegated_to_ambient_agent"

    _LAST_EVENT_AT = _now_iso()
    try:
        _record_event(
            event_type,
            action,
            handled=handled,
            outcome=outcome,
            metadata={
                "seq": int(event.get("seq") or 0),
                "profile": vp3_os.configured_profile(),
                "screen_awake": _SCREEN_AWAKE,
            },
        )
    except Exception:
        pass


def _sync_visual_state() -> None:
    global _LAST_VISUAL_STATE, _LAST_LIGHT_MODE, _LAST_ERROR
    current = visual_state()
    with _LOCK:
        previous_mode = _LAST_LIGHT_MODE
        _LAST_VISUAL_STATE = current["state"]
    if current["light_mode"] == previous_mode:
        return
    try:
        hardware_adapters.set_status_light(current["light_mode"])
        with _LOCK:
            _LAST_LIGHT_MODE = current["light_mode"]
            _LAST_ERROR = ""
    except Exception as exc:
        with _LOCK:
            _LAST_ERROR = str(exc)[:240]


def _loop() -> None:
    while not _STOP.wait(_LOOP_SECONDS):
        try:
            _sync_visual_state()
        except Exception as exc:
            global _LAST_ERROR
            with _LOCK:
                _LAST_ERROR = str(exc)[:240]


def start() -> None:
    global _STARTED, _THREAD, _SCREEN_AWAKE
    with _LOCK:
        if _STARTED:
            return
        _STARTED = True
        _STOP.clear()
        _SCREEN_AWAKE = _compute_screen_awake()
    hardware_adapters.add_event_handler(handle_hardware_event)
    _sync_visual_state()
    thread = threading.Thread(
        target=_loop,
        name="vp3-hardware-experience",
        daemon=True,
    )
    with _LOCK:
        _THREAD = thread
    thread.start()


def stop() -> None:
    global _STARTED, _THREAD
    hardware_adapters.remove_event_handler(handle_hardware_event)
    _STOP.set()
    with _LOCK:
        thread = _THREAD
    if thread and thread.is_alive() and thread is not threading.current_thread():
        thread.join(timeout=3)
    with _LOCK:
        _THREAD = None
        _STARTED = False


def certification() -> dict[str, Any]:
    experience = profile_experience()
    inventory = vp3_os.hardware_inventory()
    degraded = degraded_state()
    checks: dict[str, Any] = {}

    for component in experience["required_hardware"]:
        item = inventory.get(component, {})
        checks[f"required:{component}"] = {
            "passed": bool(item.get("present") and item.get("ready")),
            "present": bool(item.get("present")),
            "ready": bool(item.get("ready")),
        }

    for component in experience["optional_hardware"]:
        item = inventory.get(component, {})
        checks[f"optional:{component}"] = {
            "passed": bool(item.get("present") and item.get("ready")),
            "present": bool(item.get("present")),
            "ready": bool(item.get("ready")),
            "optional": True,
        }

    privacy = vp3_os.manifest(include_hardware=False, include_device_id=False)["privacy"]
    if "privacy_switch" in experience["required_hardware"]:
        checks["privacy:physical_disconnect"] = {
            "passed": (
                not bool(privacy.get("privacy_switch_engaged"))
                or bool(privacy.get("physical_microphone_disconnect_verified"))
            ),
        }

    checks["experience:visual_state"] = {
        "passed": visual_state()["state"] in _STATUS_TO_LIGHT,
    }
    checks["experience:settings"] = {
        "passed": get_settings()["enabled"],
    }

    required_failed = [
        key for key, value in checks.items()
        if not value.get("optional") and not value.get("passed")
    ]
    optional_failed = [
        key for key, value in checks.items()
        if value.get("optional") and value.get("present") and not value.get("passed")
    ]
    result = "failed" if required_failed else ("degraded" if optional_failed or degraded["degraded"] else "passed")

    with db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO vp3_hardware_experience_certifications(
                profile_key,os_version,result,checks_json
            ) VALUES (?,?,?,?)
            """,
            (
                experience["profile"]["key"],
                vp3_os.VP3_OS_VERSION,
                result,
                _json(checks),
            ),
        )
        certification_id = int(cursor.lastrowid)
    return {
        "id": certification_id,
        "profile_key": experience["profile"]["key"],
        "os_version": vp3_os.VP3_OS_VERSION,
        "result": result,
        "checks": checks,
        "required_failures": required_failed,
        "optional_failures": optional_failed,
        "certified_at": _now_iso(),
    }


def list_certifications(limit: int = 20) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 100))
    with db() as connection:
        rows = connection.execute(
            """
            SELECT id,profile_key,os_version,result,checks_json,certified_at
            FROM vp3_hardware_experience_certifications
            ORDER BY id DESC LIMIT ?
            """,
            (bounded,),
        ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "profile_key": row["profile_key"],
            "os_version": row["os_version"],
            "result": row["result"],
            "checks": _read_json(row["checks_json"], {}),
            "certified_at": row["certified_at"],
        }
        for row in rows
    ]


def status() -> dict[str, Any]:
    with _LOCK:
        runtime = {
            "started": _STARTED,
            "visual_state": _LAST_VISUAL_STATE,
            "last_light_mode": _LAST_LIGHT_MODE,
            "screen_awake": _SCREEN_AWAKE,
            "last_presence_at": _LAST_PRESENCE_AT,
            "last_event_at": _LAST_EVENT_AT,
            "last_error": _LAST_ERROR,
        }
    return {
        "version": HARDWARE_EXPERIENCE_VERSION,
        "vp3_os_version": vp3_os.VP3_OS_VERSION,
        "experience": profile_experience(),
        "settings": get_settings(),
        "runtime": runtime,
        "visual": visual_state(),
        "degraded": degraded_state(),
        "cards": list_cards(),
        "latest_certification": (list_certifications(1) or [None])[0],
    }


def public_capability() -> dict[str, Any]:
    experience = profile_experience()
    return {
        "version": HARDWARE_EXPERIENCE_VERSION,
        "profile_key": experience["profile"]["key"],
        "experience": experience["experience"],
        "capabilities": experience["capabilities"],
        "display_modes": experience["display_modes"],
        "device_specific_profiles": True,
        "normalized_event_bus": True,
        "unified_visual_state": True,
        "persistent_preferences": True,
        "display_cards": bool(experience["display_modes"]),
        "degraded_mode": True,
        "experience_certification": True,
        "physical_action_authority": False,
    }
