from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from ..database import db
from . import ambient_agent, approvals, cognitive_runtime, local_automation, physical_meeting, room_device_automation

ORCHESTRATION_VERSION = "v0.90"
_OPEN_STATES = {"suggested", "requested", "active", "suspended"}
_CONFLICT_STATES = {"requested", "active"}
_TERMINAL_STATES = {"ended", "failed"}

_STOP = threading.Event()
_THREAD: threading.Thread | None = None
_LOCK = threading.RLock()


class OrchestrationError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _decode(value: Any, fallback: Any) -> Any:
    try:
        decoded = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    return decoded


def _encoded(value: Any, *, max_bytes: int = 16384) -> str:
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise OrchestrationError(
            "Orchestration payload must be JSON serializable."
        ) from exc
    if len(text.encode("utf-8")) > max_bytes:
        raise OrchestrationError(
            "Orchestration payload exceeds the size limit.", 413
        )
    return text


def _key(value: Any, label: str) -> str:
    try:
        return room_device_automation._safe_key(value, label)
    except room_device_automation.RoomDeviceError as exc:
        raise OrchestrationError(str(exc), exc.status_code) from exc


def _text(
    value: Any,
    limit: int,
    *,
    required: bool = False,
    label: str = "value",
) -> str:
    text = " ".join(str(value or "").split())[:limit]
    if required and not text:
        raise OrchestrationError(f"{label} is required.")
    return text


def get_settings() -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            "SELECT * FROM orchestration_settings WHERE id=1"
        ).fetchone()
    if row is None:
        raise OrchestrationError(
            "Orchestration settings are unavailable.", 500
        )
    item = dict(row)
    item["enabled"] = bool(item["enabled"])
    return item


def update_settings(
    *,
    enabled: bool,
    poll_seconds: int,
    suggestion_cooldown_seconds: int,
    max_open_sessions: int,
) -> dict[str, Any]:
    if poll_seconds < 10 or poll_seconds > 300:
        raise OrchestrationError(
            "poll_seconds must be between 10 and 300."
        )
    if (
        suggestion_cooldown_seconds < 300
        or suggestion_cooldown_seconds > 604800
    ):
        raise OrchestrationError(
            "suggestion_cooldown_seconds must be between 300 and 604800."
        )
    if max_open_sessions < 1 or max_open_sessions > 50:
        raise OrchestrationError(
            "max_open_sessions must be between 1 and 50."
        )
    with db() as connection:
        connection.execute(
            """
            UPDATE orchestration_settings
            SET enabled=?,poll_seconds=?,suggestion_cooldown_seconds=?,
                max_open_sessions=?,updated_at=CURRENT_TIMESTAMP
            WHERE id=1
            """,
            (
                1 if enabled else 0,
                int(poll_seconds),
                int(suggestion_cooldown_seconds),
                int(max_open_sessions),
            ),
        )
    return get_settings()


def _validate_rooms(room_keys: list[str] | None) -> list[str]:
    output: list[str] = []
    for raw in list(room_keys or []):
        key = _key(raw, "room_key")
        if key in output:
            continue
        try:
            room = room_device_automation.get_room(key)
        except room_device_automation.RoomDeviceError as exc:
            raise OrchestrationError(str(exc), exc.status_code) from exc
        if not room["enabled"]:
            raise OrchestrationError(
                f"Room {key} is disabled.", 409
            )
        output.append(key)
        if len(output) > 12:
            raise OrchestrationError(
                "A mode can include at most 12 rooms."
            )
    return output


def _clock_minutes(value: Any, label: str) -> int:
    text = str(value or "").strip()
    pieces = text.split(":")
    if len(pieces) != 2:
        raise OrchestrationError(
            f"{label} must use HH:MM UTC."
        )
    try:
        hour = int(pieces[0])
        minute = int(pieces[1])
    except ValueError as exc:
        raise OrchestrationError(
            f"{label} must use HH:MM UTC."
        ) from exc
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise OrchestrationError(
            f"{label} must use HH:MM UTC."
        )
    return hour * 60 + minute


def _validate_suggest_trigger(
    trigger: dict[str, Any] | None,
) -> dict[str, Any]:
    raw = dict(trigger or {})
    unknown = set(raw) - {
        "presence",
        "meeting",
        "weekdays",
        "time_start",
        "time_end",
    }
    if unknown:
        raise OrchestrationError(
            f"Unsupported suggestion trigger: {sorted(unknown)[0]}"
        )
    output: dict[str, Any] = {}
    if "presence" in raw:
        presence = str(raw["presence"] or "").strip().lower()
        if presence not in {"present", "absent"}:
            raise OrchestrationError(
                "presence trigger must be present or absent."
            )
        output["presence"] = presence
    if "meeting" in raw:
        meeting = str(raw["meeting"] or "").strip().lower()
        if meeting not in {"active", "inactive"}:
            raise OrchestrationError(
                "meeting trigger must be active or inactive."
            )
        output["meeting"] = meeting
    if "weekdays" in raw:
        weekdays = raw["weekdays"]
        if not isinstance(weekdays, list) or not weekdays:
            raise OrchestrationError(
                "weekdays must be a non-empty list."
            )
        normalized = sorted({int(day) for day in weekdays})
        if any(day < 0 or day > 6 for day in normalized):
            raise OrchestrationError(
                "weekdays must use 0=Monday through 6=Sunday."
            )
        output["weekdays"] = normalized
    has_start = "time_start" in raw
    has_end = "time_end" in raw
    if has_start != has_end:
        raise OrchestrationError(
            "time_start and time_end must be supplied together."
        )
    if has_start:
        _clock_minutes(raw["time_start"], "time_start")
        _clock_minutes(raw["time_end"], "time_end")
        output["time_start"] = str(raw["time_start"])
        output["time_end"] = str(raw["time_end"])
    return output


def _routine_resources(routine: dict[str, Any]) -> dict[str, Any]:
    devices = []
    rooms = []
    steps = []
    for step in routine["steps"]:
        device = room_device_automation.get_device(
            str(step["device_key"])
        )
        device_key = str(device["device_key"])
        if device_key not in devices:
            devices.append(device_key)
        room_key = str(device.get("room_key") or "")
        if room_key and room_key not in rooms:
            rooms.append(room_key)
        steps.append(
            {
                "position": int(step["position"]),
                "device_key": device_key,
                "device_name": str(step["device_name"]),
                "category": str(step["category"]),
                "room_key": room_key or None,
                "command": str(step["command"]),
                "arguments": dict(step["arguments"]),
                "state": dict(device.get("state") or {}),
            }
        )
    return {
        "device_keys": devices,
        "room_keys": rooms,
        "steps": steps,
    }


def upsert_mode(
    mode_key: str,
    name: str,
    *,
    routine_key: str,
    description: str = "",
    room_keys: list[str] | None = None,
    priority: int = 50,
    suggest_trigger: dict[str, Any] | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    key = _key(mode_key, "mode_key")
    with db() as connection:
        existing_mode = connection.execute(
            "SELECT id FROM orchestration_modes WHERE mode_key=? LIMIT 1",
            (key,),
        ).fetchone()
        if existing_mode is not None:
            open_session = connection.execute(
                """
                SELECT 1 FROM orchestration_mode_sessions
                WHERE mode_id=? AND state IN (
                    'suggested','requested','active','suspended'
                )
                LIMIT 1
                """,
                (int(existing_mode["id"]),),
            ).fetchone()
            if open_session is not None:
                raise OrchestrationError(
                    "Room Mode cannot be edited while it has an open session.",
                    409,
                )
    safe_name = _text(
        name, 160, required=True, label="mode name"
    )
    if priority < 0 or priority > 100:
        raise OrchestrationError(
            "priority must be between 0 and 100."
        )
    try:
        routine = local_automation.get_routine(routine_key)
    except local_automation.LocalAutomationError as exc:
        raise OrchestrationError(str(exc), exc.status_code) from exc
    if routine["approval_mode"] != "ask_every_time":
        raise OrchestrationError(
            "Room Modes require an ask_every_time v0.70 routine.",
            409,
        )
    resources = _routine_resources(routine)
    safe_rooms = _validate_rooms(room_keys)
    if not safe_rooms:
        safe_rooms = list(resources["room_keys"])
    missing_scope = sorted(
        set(resources["room_keys"]) - set(safe_rooms)
    )
    if missing_scope:
        raise OrchestrationError(
            "Room Mode scope does not include every room used by its routine.",
            409,
        )
    trigger = _validate_suggest_trigger(suggest_trigger)

    with db() as connection:
        connection.execute(
            """
            INSERT INTO orchestration_modes(
                mode_key,name,description,routine_id,room_keys_json,
                priority,suggest_trigger_json,enabled
            ) VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(mode_key) DO UPDATE SET
                name=excluded.name,
                description=excluded.description,
                routine_id=excluded.routine_id,
                room_keys_json=excluded.room_keys_json,
                priority=excluded.priority,
                suggest_trigger_json=excluded.suggest_trigger_json,
                enabled=excluded.enabled,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                key,
                safe_name,
                _text(description, 1000) or None,
                int(routine["id"]),
                _encoded(safe_rooms, max_bytes=4096),
                int(priority),
                _encoded(trigger, max_bytes=4096),
                1 if enabled else 0,
            ),
        )
        connection.execute(
            """
            INSERT INTO activity_log(
                actor_type,actor_key,action,resource_type,
                resource_key,metadata_json
            ) VALUES (
                'owner','control-center','orchestration.mode.upserted',
                'orchestration_mode',?,?
            )
            """,
            (
                key,
                _encoded(
                    {
                        "routine_key": routine["routine_key"],
                        "priority": int(priority),
                        "enabled": bool(enabled),
                    },
                    max_bytes=4096,
                ),
            ),
        )
    return get_mode(key)


def get_mode(mode_key: str) -> dict[str, Any]:
    key = _key(mode_key, "mode_key")
    with db() as connection:
        row = connection.execute(
            """
            SELECT m.*,r.routine_key,r.name AS routine_name,
                   r.enabled AS routine_enabled,r.approval_mode
            FROM orchestration_modes m
            JOIN automation_routines r ON r.id=m.routine_id
            WHERE m.mode_key=? LIMIT 1
            """,
            (key,),
        ).fetchone()
    if row is None:
        raise OrchestrationError("Room Mode not found.", 404)
    item = dict(row)
    item["enabled"] = bool(item["enabled"])
    item["routine_enabled"] = bool(item["routine_enabled"])
    item["room_keys"] = _decode(
        item.pop("room_keys_json", "[]"), []
    )
    item["suggest_trigger"] = _decode(
        item.pop("suggest_trigger_json", "{}"), {}
    )
    routine = local_automation.get_routine(
        str(item["routine_key"])
    )
    resources = _routine_resources(routine)
    item["device_keys"] = resources["device_keys"]
    item["steps"] = resources["steps"]
    return item


def list_modes() -> list[dict[str, Any]]:
    with db() as connection:
        rows = connection.execute(
            """
            SELECT mode_key FROM orchestration_modes
            ORDER BY priority DESC,name COLLATE NOCASE,id
            """
        ).fetchall()
    return [get_mode(str(row["mode_key"])) for row in rows]


def set_mode_enabled(
    mode_key: str,
    enabled: bool,
) -> dict[str, Any]:
    mode = get_mode(mode_key)
    with db() as connection:
        connection.execute(
            """
            UPDATE orchestration_modes
            SET enabled=?,updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (1 if enabled else 0, int(mode["id"])),
        )
    return get_mode(mode["mode_key"])


def _context_snapshot() -> dict[str, Any]:
    now = _now()
    presence_history = None
    presence_history_at = None
    with db() as connection:
        row = connection.execute(
            """
            SELECT state,occurred_at
            FROM automation_context_events
            WHERE event_type='presence'
            ORDER BY occurred_at DESC,id DESC LIMIT 1
            """
        ).fetchone()
        if row is not None:
            presence_history = str(row["state"] or "")
            presence_history_at = row["occurred_at"]

        meeting_row = connection.execute(
            """
            SELECT event_type,occurred_at
            FROM cognitive_events
            WHERE event_type IN (
                'physical_meeting.started',
                'physical_meeting.completed',
                'physical_meeting.interrupted'
            )
            ORDER BY occurred_at DESC,id DESC LIMIT 1
            """
        ).fetchone()

    meeting_event = None
    meeting_at = None
    if meeting_row is not None:
        meeting_event = str(meeting_row["event_type"])
        meeting_at = meeting_row["occurred_at"]

    try:
        ambient_status = ambient_agent.status()
        ambient_settings = ambient_status.get("settings") or {}
        raw_presence = str(ambient_status.get("presence") or "").lower()
        presence = (
            raw_presence
            if bool(ambient_settings.get("enabled"))
            and raw_presence in {"present", "absent"}
            else None
        )
        presence_at = (
            ambient_status.get("last_presence_at")
            if presence is not None
            else None
        )
    except Exception:
        presence = None
        presence_at = None

    try:
        meeting_status = physical_meeting.status()
        meeting = (
            "active"
            if bool(meeting_status.get("meeting_id"))
            else "inactive"
        )
        live_meeting_id = meeting_status.get("meeting_id")
        live_meeting_state = meeting_status.get("state")
    except Exception:
        meeting = "inactive"
        live_meeting_id = None
        live_meeting_state = "unavailable"

    return {
        "evaluated_at": _iso(now),
        "presence": presence,
        "presence_at": presence_at,
        "presence_history": presence_history,
        "presence_history_at": presence_history_at,
        "meeting": meeting,
        "meeting_id": live_meeting_id,
        "meeting_state": live_meeting_state,
        "meeting_event": meeting_event,
        "meeting_at": meeting_at,
        "weekday": now.weekday(),
        "minute_of_day": now.hour * 60 + now.minute,
        "timezone": "UTC",
    }


def _trigger_matches(
    trigger: dict[str, Any],
    context: dict[str, Any],
) -> bool:
    if not trigger:
        return False
    if "presence" in trigger:
        if context.get("presence") != trigger["presence"]:
            return False
    if "meeting" in trigger:
        if context.get("meeting") != trigger["meeting"]:
            return False
    if "weekdays" in trigger:
        if int(context["weekday"]) not in trigger["weekdays"]:
            return False
    if "time_start" in trigger:
        start = _clock_minutes(
            trigger["time_start"], "time_start"
        )
        end = _clock_minutes(trigger["time_end"], "time_end")
        current = int(context["minute_of_day"])
        if start <= end:
            if current < start or current > end:
                return False
        else:
            if current < start and current > end:
                return False
    return True


def _transition(
    session_id: int,
    to_state: str,
    *,
    reason: str = "",
    metadata: dict[str, Any] | None = None,
    superseded_by_session_id: int | None = None,
) -> dict[str, Any]:
    if to_state not in _OPEN_STATES | _TERMINAL_STATES:
        raise OrchestrationError(
            "Invalid Room Mode session state.", 500
        )
    session = get_session(session_id, refresh=False)
    from_state = str(session["state"])
    now = _iso()
    columns = [
        "state=?",
        "updated_at=CURRENT_TIMESTAMP",
    ]
    values: list[Any] = [to_state]
    if to_state == "requested":
        columns.append("requested_at=?")
        values.append(now)
    elif to_state == "active":
        columns.append("active_at=?")
        values.append(now)
    elif to_state == "suspended":
        columns.append("suspended_at=?")
        values.append(now)
    elif to_state in _TERMINAL_STATES:
        columns.append("ended_at=?")
        values.append(now)
    if to_state == "failed":
        columns.append("failure_reason=?")
        values.append(_text(reason, 1000) or "Mode failed.")
    if superseded_by_session_id is not None:
        columns.append("superseded_by_session_id=?")
        values.append(int(superseded_by_session_id))
    values.append(int(session_id))
    with db() as connection:
        connection.execute(
            f"""
            UPDATE orchestration_mode_sessions
            SET {','.join(columns)}
            WHERE id=?
            """,
            values,
        )
        connection.execute(
            """
            INSERT INTO orchestration_mode_transitions(
                session_id,from_state,to_state,reason,metadata_json
            ) VALUES (?,?,?,?,?)
            """,
            (
                int(session_id),
                from_state,
                to_state,
                _text(reason, 1000) or None,
                _encoded(metadata or {}, max_bytes=4096),
            ),
        )
    return get_session(session_id, refresh=False)


def _open_session_for_mode(mode_id: int) -> dict[str, Any] | None:
    with db() as connection:
        row = connection.execute(
            """
            SELECT id FROM orchestration_mode_sessions
            WHERE mode_id=?
              AND state IN ('suggested','requested','active','suspended')
            ORDER BY id DESC LIMIT 1
            """,
            (int(mode_id),),
        ).fetchone()
    if row is None:
        return None
    return get_session(int(row["id"]), refresh=False)


def _create_session(
    mode: dict[str, Any],
    *,
    state: str,
    source_kind: str,
    reason: str,
    request_ids: list[str] | None = None,
    context_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    with db() as connection:
        open_count = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM orchestration_mode_sessions
                WHERE state IN ('suggested','requested','active','suspended')
                """
            ).fetchone()[0]
        )
        if open_count >= int(settings["max_open_sessions"]):
            raise OrchestrationError(
                "Too many open Room Mode sessions.", 409
            )
        cursor = connection.execute(
            """
            INSERT INTO orchestration_mode_sessions(
                mode_id,state,source_kind,reason,request_ids_json,
                context_snapshot_json,requested_at
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                int(mode["id"]),
                state,
                _text(source_kind, 80, required=True, label="source_kind"),
                _text(reason, 1000) or None,
                _encoded(request_ids or [], max_bytes=4096),
                _encoded(context_snapshot or {}, max_bytes=8192),
                _iso() if state == "requested" else None,
            ),
        )
        session_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO orchestration_mode_transitions(
                session_id,from_state,to_state,reason,metadata_json
            ) VALUES (?,NULL,?,?,?)
            """,
            (
                session_id,
                state,
                _text(reason, 1000) or None,
                "{}",
            ),
        )
    return get_session(session_id, refresh=False)


def _session_row(session_id: int):
    with db() as connection:
        row = connection.execute(
            """
            SELECT s.*,m.mode_key,m.name AS mode_name,m.priority,
                   m.room_keys_json,r.routine_key,r.name AS routine_name
            FROM orchestration_mode_sessions s
            JOIN orchestration_modes m ON m.id=s.mode_id
            JOIN automation_routines r ON r.id=m.routine_id
            WHERE s.id=? LIMIT 1
            """,
            (int(session_id),),
        ).fetchone()
    if row is None:
        raise OrchestrationError(
            "Room Mode session not found.", 404
        )
    return row


def get_session(
    session_id: int,
    *,
    refresh: bool = True,
) -> dict[str, Any]:
    row = _session_row(session_id)
    item = dict(row)
    item["request_ids"] = _decode(
        item.pop("request_ids_json", "[]"), []
    )
    item["context_snapshot"] = _decode(
        item.pop("context_snapshot_json", "{}"), {}
    )
    item["room_keys"] = _decode(
        item.pop("room_keys_json", "[]"), []
    )
    if refresh and item["state"] in {"requested", "active"}:
        return refresh_session(int(session_id))
    return item


def list_sessions(
    state: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 500))
    params: list[Any] = []
    where = ""
    if state:
        if state not in _OPEN_STATES | _TERMINAL_STATES:
            raise OrchestrationError(
                "Invalid Room Mode session state."
            )
        where = "WHERE s.state=?"
        params.append(state)
    params.append(bounded)
    with db() as connection:
        rows = connection.execute(
            f"""
            SELECT s.id FROM orchestration_mode_sessions s
            {where}
            ORDER BY s.id DESC LIMIT ?
            """,
            params,
        ).fetchall()
    return [
        get_session(int(row["id"]))
        for row in rows
    ]


def _request_states(request_ids: list[str]) -> dict[str, str]:
    if not request_ids:
        return {}
    placeholders = ",".join("?" for _ in request_ids)
    with db() as connection:
        rows = connection.execute(
            f"""
            SELECT id,status FROM action_requests
            WHERE id IN ({placeholders})
            """,
            request_ids,
        ).fetchall()
    return {
        str(row["id"]): str(row["status"])
        for row in rows
    }


def _manual_override(
    session: dict[str, Any],
) -> dict[str, Any] | None:
    active_at = _parse_time(session.get("active_at"))
    if active_at is None:
        return None
    mode = get_mode(str(session["mode_key"]))
    device_keys = list(mode["device_keys"])
    if not device_keys:
        return None
    placeholders = ",".join("?" for _ in device_keys)
    params: list[Any] = [*device_keys, _iso(active_at)]
    with db() as connection:
        row = connection.execute(
            f"""
            SELECT a.id,d.device_key,a.source_app_key,a.command,a.completed_at
            FROM automation_device_actions a
            JOIN automation_devices d ON d.id=a.device_id
            WHERE d.device_key IN ({placeholders})
              AND a.status='completed'
              AND julianday(a.completed_at)>julianday(?)
              AND a.source_app_key NOT LIKE 'automation:%'
            ORDER BY a.completed_at DESC,a.id DESC LIMIT 1
            """,
            params,
        ).fetchone()
    return dict(row) if row is not None else None


def refresh_session(session_id: int) -> dict[str, Any]:
    session = get_session(session_id, refresh=False)
    if session["state"] == "requested":
        request_ids = list(session["request_ids"])
        states = _request_states(request_ids)
        if not request_ids or len(states) != len(request_ids):
            return _transition(
                session_id,
                "failed",
                reason="Mode request tracking is incomplete.",
            )
        failed = {
            request_id: state
            for request_id, state in states.items()
            if state in {"denied", "failed", "expired"}
        }
        if failed:
            return _transition(
                session_id,
                "failed",
                reason="One or more governed device requests did not execute.",
                metadata={"request_states": states},
            )
        if all(state == "executed" for state in states.values()):
            return _transition(
                session_id,
                "active",
                reason="All governed device requests executed.",
                metadata={"request_states": states},
            )
    elif session["state"] == "active":
        override = _manual_override(session)
        if override is not None:
            return _transition(
                session_id,
                "suspended",
                reason="Manual device change suspended the Room Mode.",
                metadata={"manual_override": override},
            )
    return get_session(session_id, refresh=False)


def refresh_open_sessions() -> list[dict[str, Any]]:
    with db() as connection:
        rows = connection.execute(
            """
            SELECT id FROM orchestration_mode_sessions
            WHERE state IN ('requested','active')
            ORDER BY id
            """
        ).fetchall()
    output = []
    for row in rows:
        try:
            output.append(refresh_session(int(row["id"])))
        except Exception:
            continue
    return output


def _mode_conflicts(
    mode: dict[str, Any],
    *,
    exclude_session_id: int | None = None,
) -> list[dict[str, Any]]:
    target = set(mode["device_keys"])
    if not target:
        return []
    params: list[Any] = []
    clause = ""
    if exclude_session_id is not None:
        clause = " AND s.id<>?"
        params.append(int(exclude_session_id))
    with db() as connection:
        rows = connection.execute(
            f"""
            SELECT s.id,m.id AS mode_id,m.mode_key,m.name,m.priority,r.routine_key
            FROM orchestration_mode_sessions s
            JOIN orchestration_modes m ON m.id=s.mode_id
            JOIN automation_routines r ON r.id=m.routine_id
            WHERE s.state IN ('requested','active'){clause}
            ORDER BY m.priority DESC,s.id DESC
            """,
            params,
        ).fetchall()
    conflicts = []
    for row in rows:
        other = get_mode(str(row["mode_key"]))
        shared = sorted(target & set(other["device_keys"]))
        if not shared:
            continue
        conflicts.append(
            {
                "session_id": int(row["id"]),
                "mode_id": int(row["mode_id"]),
                "mode_key": str(row["mode_key"]),
                "mode_name": str(row["name"]),
                "priority": int(row["priority"]),
                "shared_devices": shared,
            }
        )
    return conflicts


def simulate_mode(mode_key: str) -> dict[str, Any]:
    mode = get_mode(mode_key)
    conflicts = _mode_conflicts(mode)
    return {
        "mode_key": mode["mode_key"],
        "name": mode["name"],
        "priority": int(mode["priority"]),
        "rooms": list(mode["room_keys"]),
        "steps": list(mode["steps"]),
        "device_count": len(mode["device_keys"]),
        "approval_requests_if_activated": len(mode["steps"]),
        "physical_actions_without_owner_approval": 0,
        "conflicts": conflicts,
        "can_supersede": bool(
            conflicts
            and all(
                int(mode["priority"]) > int(item["priority"])
                for item in conflicts
            )
        ),
    }


def _record_conflicts(
    mode: dict[str, Any],
    conflicts: list[dict[str, Any]],
    *,
    session_id: int | None,
    resolution: str,
) -> None:
    with db() as connection:
        for conflict in conflicts:
            connection.execute(
                """
                INSERT INTO orchestration_mode_conflicts(
                    session_id,mode_id,conflicting_session_id,
                    conflicting_mode_id,shared_devices_json,resolution
                ) VALUES (?,?,?,?,?,?)
                """,
                (
                    session_id,
                    int(mode["id"]),
                    int(conflict["session_id"]),
                    int(conflict["mode_id"]),
                    _encoded(
                        conflict["shared_devices"],
                        max_bytes=4096,
                    ),
                    resolution,
                ),
            )


def _emit_mode_event(
    event_type: str,
    session: dict[str, Any],
    summary: str,
) -> None:
    try:
        cognitive_runtime.emit_event(
            source_app_key="vp3-os:ambient-orchestration",
            event_id=(
                f"{event_type}:{session['id']}:{session['state']}"
            ),
            event_type=event_type,
            summary=summary,
            source_kind="system",
            entity_type="room_mode",
            entity_key=str(session["mode_key"]),
            importance=0.62,
            privacy_scope="private",
            payload={
                "session_id": int(session["id"]),
                "mode_key": str(session["mode_key"]),
                "state": str(session["state"]),
                "priority": int(session["priority"]),
            },
            memory_candidate=False,
        )
    except Exception:
        return


def suggest_mode(
    mode_key: str,
    *,
    source_kind: str = "ambient",
    reason: str = "",
    context_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with _LOCK:
        mode = get_mode(mode_key)
        if not mode["enabled"]:
            raise OrchestrationError("Room Mode is disabled.", 409)
        if not mode["routine_enabled"]:
            raise OrchestrationError(
                "Room Mode routine is disabled.", 409
            )
        existing = _open_session_for_mode(int(mode["id"]))
        if existing is not None:
            return existing

        settings = get_settings()
        cutoff = _iso(
            _now()
            - timedelta(
                seconds=int(settings["suggestion_cooldown_seconds"])
            )
        )
        with db() as connection:
            recent = connection.execute(
                """
                SELECT id FROM orchestration_mode_sessions
                WHERE mode_id=?
                  AND julianday(started_at)>=julianday(?)
                ORDER BY id DESC LIMIT 1
                """,
                (int(mode["id"]), cutoff),
            ).fetchone()
        if recent is not None:
            return get_session(int(recent["id"]))

        session = _create_session(
            mode,
            state="suggested",
            source_kind=source_kind,
            reason=reason or "Ambient context matched this Room Mode.",
            context_snapshot=context_snapshot or _context_snapshot(),
        )
        _emit_mode_event(
            "orchestration.mode_suggested",
            session,
            f"Room Mode suggestion: {mode['name']}. Owner activation is required.",
        )
        return session

def evaluate_mode_suggestions() -> list[dict[str, Any]]:
    settings = get_settings()
    if not settings["enabled"]:
        return []
    context = _context_snapshot()
    output = []
    for mode in list_modes():
        if not mode["enabled"] or not mode["routine_enabled"]:
            continue
        trigger = dict(mode["suggest_trigger"])
        if not _trigger_matches(trigger, context):
            continue
        try:
            session = suggest_mode(
                mode["mode_key"],
                source_kind="ambient",
                reason="Ambient context matched the configured Room Mode trigger.",
                context_snapshot=context,
            )
            if session["state"] == "suggested":
                output.append(session)
        except OrchestrationError:
            continue
    return output


def _activate(
    mode: dict[str, Any],
    *,
    source_kind: str,
    reason: str,
    supersede_conflicts: bool,
    suggested_session_id: int | None = None,
) -> dict[str, Any]:
    if not get_settings()["enabled"]:
        raise OrchestrationError(
            "Ambient orchestration is disabled.", 409
        )
    if not mode["enabled"]:
        raise OrchestrationError("Room Mode is disabled.", 409)
    if not mode["routine_enabled"]:
        raise OrchestrationError(
            "Room Mode routine is disabled.", 409
        )
    conflicts = _mode_conflicts(
        mode,
        exclude_session_id=suggested_session_id,
    )
    if conflicts:
        _record_conflicts(
            mode,
            conflicts,
            session_id=suggested_session_id,
            resolution="blocked",
        )
        if not supersede_conflicts:
            raise OrchestrationError(
                "Room Mode conflicts with an active or requested mode. "
                "Review the simulation before superseding.",
                409,
            )
        if not all(
            int(mode["priority"]) > int(item["priority"])
            for item in conflicts
        ):
            raise OrchestrationError(
                "Room Mode priority is not high enough to supersede every conflict.",
                409,
            )

    try:
        result = local_automation.run_routine(
            str(mode["routine_key"]),
            source_kind=f"mode:{mode['mode_key']}",
            snapshot={
                "mode_key": mode["mode_key"],
                "source_kind": source_kind,
                "reason": reason,
            },
        )
    except local_automation.LocalAutomationError as exc:
        raise OrchestrationError(str(exc), exc.status_code) from exc
    if not result["request_ids"]:
        raise OrchestrationError(
            "Room Mode routine did not create governed approval requests.",
            409,
        )

    if suggested_session_id is not None:
        session = get_session(
            suggested_session_id, refresh=False
        )
        if session["state"] != "suggested":
            approvals.cancel_pending_requests(
                result["request_ids"],
                source_app_key="automation:local-rule",
                reason="Room Mode suggestion was no longer activatable.",
            )
            raise OrchestrationError(
                "Room Mode suggestion is no longer activatable.",
                409,
            )
        with db() as connection:
            connection.execute(
                """
                UPDATE orchestration_mode_sessions
                SET request_ids_json=?,source_kind=?,reason=?,
                    context_snapshot_json=?,updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    _encoded(result["request_ids"], max_bytes=4096),
                    _text(source_kind, 80),
                    _text(reason, 1000) or session.get("reason"),
                    _encoded(_context_snapshot(), max_bytes=8192),
                    int(suggested_session_id),
                ),
            )
        session = _transition(
            suggested_session_id,
            "requested",
            reason="Owner accepted the Room Mode suggestion.",
            metadata={"request_ids": result["request_ids"]},
        )
    else:
        try:
            session = _create_session(
                mode,
                state="requested",
                source_kind=source_kind,
                reason=reason or "Owner requested Room Mode activation.",
                request_ids=list(result["request_ids"]),
                context_snapshot=_context_snapshot(),
            )
        except Exception:
            approvals.cancel_pending_requests(
                result["request_ids"],
                source_app_key="automation:local-rule",
                reason="Room Mode session could not be created.",
            )
            raise

    if conflicts:
        for conflict in conflicts:
            conflicting_session = get_session(
                int(conflict["session_id"]),
                refresh=False,
            )
            if conflicting_session["state"] == "requested":
                approvals.cancel_pending_requests(
                    list(conflicting_session["request_ids"]),
                    source_app_key="automation:local-rule",
                    reason=(
                        "Pending Room Mode requests were cancelled because "
                        f"{mode['name']} explicitly superseded the mode."
                    ),
                )
            _transition(
                int(conflict["session_id"]),
                "suspended",
                reason=f"Superseded by higher-priority Room Mode {mode['name']}.",
                superseded_by_session_id=int(session["id"]),
            )
        _record_conflicts(
            mode,
            conflicts,
            session_id=int(session["id"]),
            resolution="superseded",
        )

    _emit_mode_event(
        "orchestration.mode_requested",
        session,
        f"Room Mode {mode['name']} created governed device approval requests.",
    )
    return session


def activate_mode(
    mode_key: str,
    *,
    source_kind: str = "owner",
    reason: str = "",
    supersede_conflicts: bool = False,
) -> dict[str, Any]:
    with _LOCK:
        refresh_open_sessions()
        mode = get_mode(mode_key)
        existing = _open_session_for_mode(int(mode["id"]))
        if existing is not None:
            if existing["state"] == "suggested":
                return _activate(
                    mode,
                    source_kind=source_kind,
                    reason=reason or "Owner activated the suggested Room Mode.",
                    supersede_conflicts=supersede_conflicts,
                    suggested_session_id=int(existing["id"]),
                )
            raise OrchestrationError(
                f"Room Mode already has an open {existing['state']} session.",
                409,
            )
        return _activate(
            mode,
            source_kind=source_kind,
            reason=reason,
            supersede_conflicts=supersede_conflicts,
        )


def accept_suggestion(
    session_id: int,
    *,
    supersede_conflicts: bool = False,
) -> dict[str, Any]:
    with _LOCK:
        session = get_session(session_id, refresh=False)
        if session["state"] != "suggested":
            raise OrchestrationError(
                "Only a suggested Room Mode can be accepted.", 409
            )
        mode = get_mode(str(session["mode_key"]))
        return _activate(
            mode,
            source_kind="owner",
            reason="Owner accepted ambient Room Mode suggestion.",
            supersede_conflicts=supersede_conflicts,
            suggested_session_id=int(session_id),
        )


def dismiss_suggestion(session_id: int) -> dict[str, Any]:
    session = get_session(session_id, refresh=False)
    if session["state"] != "suggested":
        raise OrchestrationError(
            "Only a suggested Room Mode can be dismissed.", 409
        )
    result = _transition(
        session_id,
        "ended",
        reason="Owner dismissed the Room Mode suggestion.",
    )
    _emit_mode_event(
        "orchestration.mode_dismissed",
        result,
        f"Room Mode suggestion {result['mode_name']} was dismissed.",
    )
    return result


def suspend_session(
    session_id: int,
    *,
    reason: str = "Owner suspended the Room Mode.",
) -> dict[str, Any]:
    session = get_session(session_id, refresh=False)
    if session["state"] not in {"requested", "active"}:
        raise OrchestrationError(
            "Only requested or active Room Modes can be suspended.",
            409,
        )
    if session["state"] == "requested":
        approvals.cancel_pending_requests(
            list(session["request_ids"]),
            source_app_key="automation:local-rule",
            reason=reason,
        )
    result = _transition(
        session_id,
        "suspended",
        reason=reason,
    )
    _emit_mode_event(
        "orchestration.mode_suspended",
        result,
        f"Room Mode {result['mode_name']} was suspended.",
    )
    return result


def end_session(
    session_id: int,
    *,
    reason: str = "Owner ended the Room Mode.",
) -> dict[str, Any]:
    session = get_session(session_id, refresh=False)
    if session["state"] in _TERMINAL_STATES:
        return session
    if session["state"] == "requested":
        approvals.cancel_pending_requests(
            list(session["request_ids"]),
            source_app_key="automation:local-rule",
            reason=reason,
        )
    result = _transition(
        session_id,
        "ended",
        reason=reason,
    )
    _emit_mode_event(
        "orchestration.mode_ended",
        result,
        f"Room Mode {result['mode_name']} ended. Device states were not automatically reversed.",
    )
    return result


def list_conflicts(limit: int = 100) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 500))
    with db() as connection:
        rows = connection.execute(
            """
            SELECT c.*,m.mode_key,cm.mode_key AS conflicting_mode_key
            FROM orchestration_mode_conflicts c
            JOIN orchestration_modes m ON m.id=c.mode_id
            JOIN orchestration_modes cm ON cm.id=c.conflicting_mode_id
            ORDER BY c.id DESC LIMIT ?
            """,
            (bounded,),
        ).fetchall()
    output = []
    for row in rows:
        item = dict(row)
        item["shared_devices"] = _decode(
            item.pop("shared_devices_json", "[]"), []
        )
        output.append(item)
    return output


def overview() -> dict[str, Any]:
    refresh_open_sessions()
    return {
        "version": ORCHESTRATION_VERSION,
        "settings": get_settings(),
        "modes": list_modes(),
        "sessions": list_sessions(limit=100),
        "conflicts": list_conflicts(100),
        "context": _context_snapshot(),
        "governance": {
            "ambient_auto_activation": False,
            "owner_activation_required": True,
            "mode_actions_use_v070_routines": True,
            "device_commands_still_require_v060_approval": True,
            "ending_mode_reverts_device_state": False,
        },
    }


def _worker() -> None:
    while not _STOP.is_set():
        wait = 30
        try:
            settings = get_settings()
            wait = max(10, int(settings["poll_seconds"]))
            if settings["enabled"]:
                refresh_open_sessions()
                evaluate_mode_suggestions()
        except Exception:
            wait = max(10, wait)
        _STOP.wait(wait)


def start() -> None:
    global _THREAD
    with _LOCK:
        if _THREAD and _THREAD.is_alive():
            return
        _STOP.clear()
        _THREAD = threading.Thread(
            target=_worker,
            name="vp3-ambient-orchestration",
            daemon=True,
        )
        _THREAD.start()


def stop() -> None:
    global _THREAD
    _STOP.set()
    with _LOCK:
        thread = _THREAD
        _THREAD = None
    if thread and thread.is_alive():
        thread.join(timeout=2.0)


def public_capability() -> dict[str, Any]:
    return {
        "version": ORCHESTRATION_VERSION,
        "room_modes": True,
        "cross_device_coordination": True,
        "cross_room_scopes": True,
        "presence_context": True,
        "meeting_context": True,
        "conflict_detection": True,
        "priority_supersession_requires_owner": True,
        "simulation": True,
        "ambient_auto_activation": False,
        "direct_physical_execution": False,
        "creates_v070_governed_requests": True,
        "device_commands_require_v060_owner_approval": True,
    }
