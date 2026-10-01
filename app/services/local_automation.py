from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from ..database import db
from . import approvals, homeserver_app_control, homeserver_apps, room_device_automation

AUTOMATION_RULES_VERSION = "v0.80"
MAX_ROUTINE_STEPS = 16
_ALLOWED_TRIGGER_KINDS = {"manual", "daily", "device_state", "app_event"}
_ALLOWED_APPROVAL_MODES = {"suggest_only", "ask_every_time"}

_STOP = threading.Event()
_THREAD: threading.Thread | None = None
_LOCK = threading.RLock()


class LocalAutomationError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _key(value: Any, label: str) -> str:
    try:
        return room_device_automation._safe_key(value, label)
    except room_device_automation.RoomDeviceError as exc:
        raise LocalAutomationError(str(exc), exc.status_code) from exc


def _text(value: Any, limit: int, *, required: bool = False, label: str = "value") -> str:
    text = " ".join(str(value or "").split())[:limit]
    if required and not text:
        raise LocalAutomationError(f"{label} is required.")
    return text


def _json(value: Any, *, label: str, allow_list: bool = False) -> Any:
    if value is None:
        value = [] if allow_list else {}
    if allow_list:
        if not isinstance(value, list):
            raise LocalAutomationError(f"{label} must be a list.")
    elif not isinstance(value, dict):
        raise LocalAutomationError(f"{label} must be an object.")
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise LocalAutomationError(f"{label} must be JSON serializable.") from exc
    if len(encoded.encode("utf-8")) > 16 * 1024:
        raise LocalAutomationError(f"{label} exceeds the size limit.", 413)
    return json.loads(encoded)


def _decode(value: Any, fallback: Any) -> Any:
    try:
        decoded = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    return decoded


def get_settings() -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            "SELECT enabled,poll_seconds,max_actions_per_run,max_rule_fires_per_minute,updated_at FROM automation_runtime_settings WHERE id=1"
        ).fetchone()
    if row is None:
        raise LocalAutomationError("Automation runtime settings are unavailable.", 500)
    item = dict(row)
    item["enabled"] = bool(item["enabled"])
    return item


def update_settings(
    *,
    enabled: bool,
    poll_seconds: int,
    max_actions_per_run: int,
    max_rule_fires_per_minute: int,
) -> dict[str, Any]:
    if poll_seconds < 5 or poll_seconds > 300:
        raise LocalAutomationError("poll_seconds must be between 5 and 300.")
    if max_actions_per_run < 1 or max_actions_per_run > MAX_ROUTINE_STEPS:
        raise LocalAutomationError(f"max_actions_per_run must be between 1 and {MAX_ROUTINE_STEPS}.")
    if max_rule_fires_per_minute < 1 or max_rule_fires_per_minute > 60:
        raise LocalAutomationError("max_rule_fires_per_minute must be between 1 and 60.")
    with db() as connection:
        connection.execute(
            """
            UPDATE automation_runtime_settings
            SET enabled=?,poll_seconds=?,max_actions_per_run=?,max_rule_fires_per_minute=?,updated_at=CURRENT_TIMESTAMP
            WHERE id=1
            """,
            (
                1 if enabled else 0,
                int(poll_seconds),
                int(max_actions_per_run),
                int(max_rule_fires_per_minute),
            ),
        )
    return get_settings()


def _validate_step(step: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(step, dict):
        raise LocalAutomationError("Each routine step must be an object.")
    step_kind = str(step.get("step_kind") or ("app_action" if step.get("app_key") or step.get("action_key") else "device")).strip().lower()
    if step_kind == "device":
        device_key = _key(step.get("device_key"), "device_key")
        try:
            validated = room_device_automation.validate_command_request(
                device_key,
                str(step.get("command") or ""),
                step.get("arguments") or {},
            )
        except room_device_automation.RoomDeviceError as exc:
            raise LocalAutomationError(str(exc), exc.status_code) from exc
        return {
            "step_kind": "device",
            "device_id": int(validated["device"]["id"]),
            "device_key": validated["device_key"],
            "command": validated["command"],
            "arguments": validated["arguments"],
        }
    if step_kind == "app_action":
        app_key = str(step.get("app_key") or "").strip().lower()
        action_key = str(step.get("action_key") or step.get("action") or "").strip()
        if not app_key or not action_key:
            raise LocalAutomationError("App action steps require app_key and action_key.")
        try:
            app = homeserver_apps.get(app_key)
            spec = homeserver_app_control.action_spec(app_key, action_key)
            arguments = homeserver_app_control._validate_arguments(spec, dict(step.get("arguments") or {}))
        except (homeserver_apps.HomeServerAppError, homeserver_app_control.AppControlError) as exc:
            raise LocalAutomationError(str(exc), getattr(exc, "status_code", 422)) from exc
        if app.get("lifecycle_state") != "running":
            raise LocalAutomationError(f"App {app_key} must be running before it can be used in an automation.", 409)
        return {
            "step_kind": "app_action",
            "app_key": app_key,
            "app_name": str(app.get("name") or app_key),
            "action_key": action_key,
            "risk": str(spec.get("risk") or "write"),
            "requires_confirmation": bool(spec.get("requires_confirmation")),
            "arguments": arguments,
        }
    raise LocalAutomationError("Unsupported routine step kind.")


def _assert_routine_not_bound_to_room_mode(routine_key: str) -> None:
    key = _key(routine_key, "routine_key")
    with db() as connection:
        table = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='orchestration_modes'
            """
        ).fetchone()
        if table is None:
            return
        bound = connection.execute(
            """
            SELECT m.mode_key
            FROM orchestration_modes m
            JOIN automation_routines r ON r.id=m.routine_id
            WHERE r.routine_key=?
            LIMIT 1
            """,
            (key,),
        ).fetchone()
    if bound is not None:
        raise LocalAutomationError(
            "Routine is bound to Room Mode "
            f"{bound['mode_key']} and cannot be edited in place. "
            "Create a new routine and rebind the mode instead.",
            409,
        )


def upsert_routine(
    routine_key: str,
    name: str,
    *,
    description: str = "",
    enabled: bool = True,
    approval_mode: str = "ask_every_time",
    steps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    key = _key(routine_key, "routine_key")
    _assert_routine_not_bound_to_room_mode(key)
    safe_name = _text(name, 160, required=True, label="routine name")
    mode = str(approval_mode or "").strip().lower()
    if mode not in _ALLOWED_APPROVAL_MODES:
        raise LocalAutomationError("approval_mode must be suggest_only or ask_every_time.")
    raw_steps = list(steps or [])
    if not raw_steps or len(raw_steps) > MAX_ROUTINE_STEPS:
        raise LocalAutomationError(f"Routine must contain between 1 and {MAX_ROUTINE_STEPS} steps.")
    validated_steps = [_validate_step(step) for step in raw_steps]

    with db() as connection:
        connection.execute(
            """
            INSERT INTO automation_routines(routine_key,name,description,enabled,approval_mode)
            VALUES (?,?,?,?,?)
            ON CONFLICT(routine_key) DO UPDATE SET
                name=excluded.name,
                description=excluded.description,
                enabled=excluded.enabled,
                approval_mode=excluded.approval_mode,
                updated_at=CURRENT_TIMESTAMP
            """,
            (key, safe_name, _text(description, 1000) or None, 1 if enabled else 0, mode),
        )
        routine_id = int(connection.execute(
            "SELECT id FROM automation_routines WHERE routine_key=?",
            (key,),
        ).fetchone()["id"])
        connection.execute("DELETE FROM automation_routine_steps WHERE routine_id=?", (routine_id,))
        for position, step in enumerate(validated_steps, start=1):
            connection.execute(
                """
                INSERT INTO automation_routine_steps(
                    routine_id,position,step_kind,device_id,command,app_key,action_key,arguments_json
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    routine_id,
                    position,
                    step["step_kind"],
                    step.get("device_id"),
                    step.get("command"),
                    step.get("app_key"),
                    step.get("action_key"),
                    json.dumps(step["arguments"], separators=(",", ":")),
                ),
            )
        connection.execute(
            """
            INSERT INTO activity_log(actor_type,actor_key,action,resource_type,resource_key,metadata_json)
            VALUES ('owner','control-center','automation.routine.upserted','automation_routine',?,?)
            """,
            (key, json.dumps({"step_count": len(validated_steps), "approval_mode": mode}, separators=(",", ":"))),
        )
    return get_routine(key)


def get_routine(routine_key: str) -> dict[str, Any]:
    key = _key(routine_key, "routine_key")
    with db() as connection:
        row = connection.execute(
            "SELECT * FROM automation_routines WHERE routine_key=? LIMIT 1",
            (key,),
        ).fetchone()
        if row is None:
            raise LocalAutomationError("Routine not found.", 404)
        steps = connection.execute(
            """
            SELECT s.position,s.step_kind,s.device_id,d.device_key,d.name AS device_name,d.category,
                   s.command,s.app_key,s.action_key,s.arguments_json
            FROM automation_routine_steps s
            LEFT JOIN automation_devices d ON d.id=s.device_id
            WHERE s.routine_id=?
            ORDER BY s.position
            """,
            (int(row["id"]),),
        ).fetchall()
    item = dict(row)
    item["enabled"] = bool(item["enabled"])
    item["steps"] = []
    for step in steps:
        raw = dict(step)
        payload = {
            **{k: value for k, value in raw.items() if k != "arguments_json" and value is not None},
            "arguments": _decode(raw["arguments_json"], {}),
        }
        if payload.get("step_kind") == "app_action":
            try:
                spec = homeserver_app_control.action_spec(str(payload["app_key"]), str(payload["action_key"]))
                payload["risk"] = str(spec.get("risk") or "write")
                payload["requires_confirmation"] = bool(spec.get("requires_confirmation"))
            except Exception:
                payload["risk"] = "unknown"
                payload["requires_confirmation"] = True
        item["steps"].append(payload)
    return item


def list_routines() -> list[dict[str, Any]]:
    with db() as connection:
        rows = connection.execute(
            "SELECT routine_key FROM automation_routines ORDER BY name COLLATE NOCASE,id"
        ).fetchall()
    return [get_routine(str(row["routine_key"])) for row in rows]


def set_routine_enabled(routine_key: str, enabled: bool) -> dict[str, Any]:
    routine = get_routine(routine_key)
    with db() as connection:
        connection.execute(
            """
            UPDATE automation_routines
            SET enabled=?,updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (1 if enabled else 0, int(routine["id"])),
        )
        connection.execute(
            """
            INSERT INTO activity_log(
                actor_type,actor_key,action,resource_type,resource_key,metadata_json
            ) VALUES ('owner','control-center','automation.routine.enabled','automation_routine',?,?)
            """,
            (
                routine["routine_key"],
                json.dumps({"enabled": bool(enabled)}, separators=(",", ":")),
            ),
        )
    return get_routine(routine["routine_key"])


def _next_daily(trigger: dict[str, Any], *, from_time: datetime | None = None) -> str:
    hour = int(trigger.get("hour", -1))
    minute = int(trigger.get("minute", -1))
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise LocalAutomationError("Daily trigger requires hour 0-23 and minute 0-59.")
    weekdays = trigger.get("weekdays", [0, 1, 2, 3, 4, 5, 6])
    if not isinstance(weekdays, list) or not weekdays:
        raise LocalAutomationError("Daily trigger weekdays must be a non-empty list.")
    normalized = sorted({int(day) for day in weekdays})
    if any(day < 0 or day > 6 for day in normalized):
        raise LocalAutomationError("Daily trigger weekdays must use 0=Monday through 6=Sunday.")
    now = from_time or _now()
    for offset in range(0, 8):
        candidate_date = (now + timedelta(days=offset)).date()
        candidate = datetime(
            candidate_date.year,
            candidate_date.month,
            candidate_date.day,
            hour,
            minute,
            tzinfo=timezone.utc,
        )
        if candidate.weekday() in normalized and candidate > now:
            return candidate.isoformat()
    raise LocalAutomationError("Could not calculate the next daily run.", 500)


def _validate_trigger(kind: str, trigger: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    safe = _json(trigger, label="trigger")
    if kind == "manual":
        return {}, None
    if kind == "daily":
        next_run = _next_daily(safe)
        return {
            "hour": int(safe["hour"]),
            "minute": int(safe["minute"]),
            "weekdays": sorted({int(day) for day in safe.get("weekdays", [0,1,2,3,4,5,6])}),
            "timezone": "UTC",
        }, next_run
    if kind == "device_state":
        device_key = _key(safe.get("device_key"), "trigger.device_key")
        field = _text(safe.get("field"), 80, required=True, label="trigger field")
        operator = str(safe.get("operator") or "eq").strip().lower()
        if operator not in {"eq", "ne", "gt", "gte", "lt", "lte"}:
            raise LocalAutomationError("Unsupported device-state trigger operator.")
        room_device_automation.get_device(device_key)
        return {"device_key": device_key, "field": field, "operator": operator, "value": safe.get("value")}, None
    if kind == "app_event":
        app_key = str(safe.get("app_key") or "").strip().lower()
        event_type = _text(safe.get("event_type"), 160, required=True, label="trigger event_type")
        if not app_key:
            raise LocalAutomationError("App-event trigger requires app_key.")
        try:
            homeserver_apps.get(app_key)
        except homeserver_apps.HomeServerAppError as exc:
            raise LocalAutomationError(str(exc), exc.status_code) from exc
        return {"app_key": app_key, "event_type": event_type}, None
    raise LocalAutomationError("Unsupported trigger kind.")


def _validate_conditions(conditions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    safe = _json(conditions, label="conditions", allow_list=True)
    if len(safe) > 8:
        raise LocalAutomationError("A rule can contain at most 8 conditions.")
    output: list[dict[str, Any]] = []
    for condition in safe:
        if not isinstance(condition, dict):
            raise LocalAutomationError("Each condition must be an object.")
        kind = str(condition.get("kind") or ("app_state" if condition.get("app_key") else "device_state")).strip().lower()
        operator = str(condition.get("operator") or "eq").strip().lower()
        if operator not in {"eq", "ne", "gt", "gte", "lt", "lte"}:
            raise LocalAutomationError("Unsupported condition operator.")
        if kind == "device_state":
            device_key = _key(condition.get("device_key"), "condition.device_key")
            field = _text(condition.get("field"), 80, required=True, label="condition field")
            room_device_automation.get_device(device_key)
            output.append({"kind":"device_state","device_key": device_key, "field": field, "operator": operator, "value": condition.get("value")})
            continue
        if kind == "app_state":
            app_key = str(condition.get("app_key") or "").strip().lower()
            field = _text(condition.get("field"), 80, required=True, label="condition field")
            if field not in {"lifecycle_state","installed_version","desired_version"}:
                raise LocalAutomationError("App-state conditions support lifecycle_state, installed_version, or desired_version.")
            if not app_key:
                raise LocalAutomationError("App-state condition requires app_key.")
            try:
                homeserver_apps.get(app_key)
            except homeserver_apps.HomeServerAppError as exc:
                raise LocalAutomationError(str(exc), exc.status_code) from exc
            output.append({"kind":"app_state","app_key":app_key,"field":field,"operator":operator,"value":condition.get("value")})
            continue
        raise LocalAutomationError("Unsupported condition kind.")
    return output


def upsert_rule(
    rule_key: str,
    name: str,
    *,
    routine_key: str,
    trigger_kind: str,
    trigger: dict[str, Any] | None = None,
    conditions: list[dict[str, Any]] | None = None,
    description: str = "",
    enabled: bool = True,
    cooldown_seconds: int = 60,
) -> dict[str, Any]:
    key = _key(rule_key, "rule_key")
    safe_name = _text(name, 160, required=True, label="rule name")
    kind = str(trigger_kind or "").strip().lower()
    if kind not in _ALLOWED_TRIGGER_KINDS:
        raise LocalAutomationError("Unsupported trigger kind.")
    routine = get_routine(routine_key)
    if cooldown_seconds < 0 or cooldown_seconds > 86400:
        raise LocalAutomationError("cooldown_seconds must be between 0 and 86400.")
    safe_trigger, next_run = _validate_trigger(kind, trigger or {})
    safe_conditions = _validate_conditions(conditions or [])
    initial_event_id = None
    if kind == "app_event":
        with db() as connection:
            app_row = connection.execute(
                "SELECT app_id FROM homeserver_apps WHERE app_key=?",
                (str(safe_trigger["app_key"]),),
            ).fetchone()
            if app_row is not None:
                latest = connection.execute(
                    """SELECT MAX(id) AS id FROM homeserver_app_events
                       WHERE app_id=? AND event_type=?""",
                    (str(app_row["app_id"]),str(safe_trigger["event_type"])),
                ).fetchone()
                initial_event_id = int(latest["id"] or 0)
    with db() as connection:
        connection.execute(
            """
            INSERT INTO automation_rules(
                rule_key,name,description,enabled,trigger_kind,trigger_json,conditions_json,
                routine_id,cooldown_seconds,last_event_id,next_run_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(rule_key) DO UPDATE SET
                name=excluded.name,
                description=excluded.description,
                enabled=excluded.enabled,
                trigger_kind=excluded.trigger_kind,
                trigger_json=excluded.trigger_json,
                conditions_json=excluded.conditions_json,
                routine_id=excluded.routine_id,
                cooldown_seconds=excluded.cooldown_seconds,
                next_run_at=excluded.next_run_at,
                last_condition=NULL,
                last_event_id=CASE
                    WHEN excluded.trigger_kind='app_event' THEN excluded.last_event_id
                    ELSE NULL
                END,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                key,
                safe_name,
                _text(description, 1000) or None,
                1 if enabled else 0,
                kind,
                json.dumps(safe_trigger, separators=(",", ":")),
                json.dumps(safe_conditions, separators=(",", ":")),
                int(routine["id"]),
                int(cooldown_seconds),
                initial_event_id,
                next_run,
            ),
        )
        connection.execute(
            """
            INSERT INTO activity_log(actor_type,actor_key,action,resource_type,resource_key,metadata_json)
            VALUES ('owner','control-center','automation.rule.upserted','automation_rule',?,?)
            """,
            (key, json.dumps({"trigger_kind": kind, "routine_key": routine["routine_key"]}, separators=(",", ":"))),
        )
    return get_rule(key)


def get_rule(rule_key: str) -> dict[str, Any]:
    key = _key(rule_key, "rule_key")
    with db() as connection:
        row = connection.execute(
            """
            SELECT r.*,u.routine_key,u.name AS routine_name,u.approval_mode,u.enabled AS routine_enabled
            FROM automation_rules r
            JOIN automation_routines u ON u.id=r.routine_id
            WHERE r.rule_key=? LIMIT 1
            """,
            (key,),
        ).fetchone()
    if row is None:
        raise LocalAutomationError("Rule not found.", 404)
    item = dict(row)
    item["enabled"] = bool(item["enabled"])
    item["routine_enabled"] = bool(item["routine_enabled"])
    item["trigger"] = _decode(item.pop("trigger_json", "{}"), {})
    item["conditions"] = _decode(item.pop("conditions_json", "[]"), [])
    item["last_condition"] = None if item["last_condition"] is None else bool(item["last_condition"])
    return item


def list_rules() -> list[dict[str, Any]]:
    with db() as connection:
        rows = connection.execute(
            "SELECT rule_key FROM automation_rules ORDER BY name COLLATE NOCASE,id"
        ).fetchall()
    return [get_rule(str(row["rule_key"])) for row in rows]


def set_rule_enabled(rule_key: str, enabled: bool) -> dict[str, Any]:
    rule = get_rule(rule_key)
    next_run = rule.get("next_run_at")
    if enabled and rule["trigger_kind"] == "daily":
        next_run = _next_daily(rule["trigger"])
    with db() as connection:
        connection.execute(
            """
            UPDATE automation_rules
            SET enabled=?,next_run_at=?,last_condition=NULL,updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (1 if enabled else 0, next_run, int(rule["id"])),
        )
        connection.execute(
            """
            INSERT INTO activity_log(
                actor_type,actor_key,action,resource_type,resource_key,metadata_json
            ) VALUES ('owner','control-center','automation.rule.enabled','automation_rule',?,?)
            """,
            (
                rule["rule_key"],
                json.dumps({"enabled": bool(enabled)}, separators=(",", ":")),
            ),
        )
    return get_rule(rule["rule_key"])


def create_disabled_draft_pair(
    *,
    routine_key: str,
    routine_name: str,
    routine_description: str,
    steps: list[dict[str, Any]],
    rule_key: str,
    rule_name: str,
    rule_description: str,
    trigger_kind: str,
    trigger: dict[str, Any] | None = None,
    conditions: list[dict[str, Any]] | None = None,
    cooldown_seconds: int = 60,
) -> dict[str, Any]:
    """Atomically create one disabled routine + disabled rule without overwrites."""
    safe_routine_key = _key(routine_key, "routine_key")
    safe_rule_key = _key(rule_key, "rule_key")
    safe_routine_name = _text(
        routine_name, 160, required=True, label="routine name"
    )
    safe_rule_name = _text(
        rule_name, 160, required=True, label="rule name"
    )
    raw_steps = list(steps or [])
    if not raw_steps or len(raw_steps) > MAX_ROUTINE_STEPS:
        raise LocalAutomationError(
            f"Routine must contain between 1 and {MAX_ROUTINE_STEPS} steps."
        )
    validated_steps = [_validate_step(step) for step in raw_steps]

    kind = str(trigger_kind or "").strip().lower()
    if kind not in _ALLOWED_TRIGGER_KINDS:
        raise LocalAutomationError("Unsupported trigger kind.")
    if cooldown_seconds < 0 or cooldown_seconds > 86400:
        raise LocalAutomationError(
            "cooldown_seconds must be between 0 and 86400."
        )
    safe_trigger, next_run = _validate_trigger(kind, trigger or {})
    safe_conditions = _validate_conditions(conditions or [])

    with db() as connection:
        routine_exists = connection.execute(
            "SELECT 1 FROM automation_routines WHERE routine_key=? LIMIT 1",
            (safe_routine_key,),
        ).fetchone()
        if routine_exists is not None:
            raise LocalAutomationError(
                "Draft routine key already exists; refusing to overwrite it.",
                409,
            )
        rule_exists = connection.execute(
            "SELECT 1 FROM automation_rules WHERE rule_key=? LIMIT 1",
            (safe_rule_key,),
        ).fetchone()
        if rule_exists is not None:
            raise LocalAutomationError(
                "Draft rule key already exists; refusing to overwrite it.",
                409,
            )

        routine_cursor = connection.execute(
            """
            INSERT INTO automation_routines(
                routine_key,name,description,enabled,approval_mode
            ) VALUES (?,?,?,0,'ask_every_time')
            """,
            (
                safe_routine_key,
                safe_routine_name,
                _text(routine_description, 1000) or None,
            ),
        )
        routine_id = int(routine_cursor.lastrowid)
        for position, step in enumerate(validated_steps, start=1):
            connection.execute(
                """
                INSERT INTO automation_routine_steps(
                    routine_id,position,device_id,command,arguments_json
                ) VALUES (?,?,?,?,?)
                """,
                (
                    routine_id,
                    position,
                    step["device_id"],
                    step["command"],
                    json.dumps(step["arguments"], separators=(",", ":")),
                ),
            )

        connection.execute(
            """
            INSERT INTO automation_rules(
                rule_key,name,description,enabled,trigger_kind,trigger_json,
                conditions_json,routine_id,cooldown_seconds,next_run_at
            ) VALUES (?,?,?,0,?,?,?,?,?,?)
            """,
            (
                safe_rule_key,
                safe_rule_name,
                _text(rule_description, 1000) or None,
                kind,
                json.dumps(safe_trigger, separators=(",", ":")),
                json.dumps(safe_conditions, separators=(",", ":")),
                routine_id,
                int(cooldown_seconds),
                next_run,
            ),
        )
        connection.execute(
            """
            INSERT INTO activity_log(
                actor_type,actor_key,action,resource_type,resource_key,metadata_json
            ) VALUES (
                'owner','control-center','automation.learned_draft.created',
                'automation_rule',?,?
            )
            """,
            (
                safe_rule_key,
                json.dumps(
                    {
                        "routine_key": safe_routine_key,
                        "rule_enabled": False,
                        "routine_enabled": False,
                        "approval_mode": "ask_every_time",
                    },
                    separators=(",", ":"),
                ),
            ),
        )

    return {
        "routine": get_routine(safe_routine_key),
        "rule": get_rule(safe_rule_key),
    }


def _compare(actual: Any, operator: str, expected: Any) -> bool:
    if operator == "eq":
        return actual == expected
    if operator == "ne":
        return actual != expected
    try:
        left = float(actual)
        right = float(expected)
    except (TypeError, ValueError):
        return False
    return {
        "gt": left > right,
        "gte": left >= right,
        "lt": left < right,
        "lte": left <= right,
    }[operator]


def _state_match(spec: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    kind = str(spec.get("kind") or ("app_state" if spec.get("app_key") else "device_state"))
    if kind == "app_state":
        try:
            app = homeserver_apps.get(str(spec["app_key"]))
        except homeserver_apps.HomeServerAppError as exc:
            raise LocalAutomationError(str(exc), exc.status_code) from exc
        actual = app.get(str(spec["field"]))
        return _compare(actual, str(spec["operator"]), spec.get("value")), {
            "kind":"app_state",
            "app_key": app["app_key"],
            "field": spec["field"],
            "actual": actual,
            "operator": spec["operator"],
            "expected": spec.get("value"),
        }
    device = room_device_automation.get_device(str(spec["device_key"]))
    actual = (device.get("state") or {}).get(str(spec["field"]))
    return _compare(actual, str(spec["operator"]), spec.get("value")), {
        "kind":"device_state",
        "device_key": device["device_key"],
        "field": spec["field"],
        "actual": actual,
        "operator": spec["operator"],
        "expected": spec.get("value"),
    }


def _conditions_match(rule: dict[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
    snapshots = []
    for condition in rule.get("conditions") or []:
        matched, snapshot = _state_match(condition)
        snapshots.append({**snapshot, "matched": matched})
        if not matched:
            return False, snapshots
    return True, snapshots


def _within_cooldown(rule: dict[str, Any], now: datetime) -> bool:
    raw = rule.get("last_fired_at")
    if not raw:
        return False
    try:
        previous = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return False
    return (now - previous).total_seconds() < int(rule.get("cooldown_seconds") or 0)


def _rate_limited(settings: dict[str, Any], now: datetime) -> bool:
    cutoff = (now - timedelta(minutes=1)).isoformat()
    with db() as connection:
        count = int(connection.execute(
            "SELECT COUNT(*) FROM automation_rule_executions WHERE created_at>=? AND status IN ('suggested','requested')",
            (cutoff,),
        ).fetchone()[0])
    return count >= int(settings["max_rule_fires_per_minute"])


def _record_execution(
    *,
    rule_id: int | None,
    routine_id: int,
    trigger_kind: str,
    snapshot: dict[str, Any],
    status: str,
    action_count: int,
    request_ids: list[str],
    suggestion_ids: list[int],
    error: str | None = None,
) -> int:
    with db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO automation_rule_executions(
                rule_id,routine_id,trigger_kind,trigger_snapshot_json,status,action_count,
                request_ids_json,suggestion_ids_json,error,completed_at
            ) VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
            """,
            (
                rule_id,
                routine_id,
                trigger_kind,
                json.dumps(snapshot, separators=(",", ":")),
                status,
                action_count,
                json.dumps(request_ids, separators=(",", ":")),
                json.dumps(suggestion_ids, separators=(",", ":")),
                (error or "")[:1000] or None,
            ),
        )
        return int(cursor.lastrowid)


def run_routine(
    routine_key: str,
    *,
    source_kind: str,
    rule: dict[str, Any] | None = None,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    routine = get_routine(routine_key)
    if not routine["enabled"]:
        raise LocalAutomationError("Routine is disabled.", 409)
    settings = get_settings()
    if len(routine["steps"]) > int(settings["max_actions_per_run"]):
        raise LocalAutomationError("Routine exceeds the current max actions per run.", 409)

    request_ids: list[str] = []
    suggestion_ids: list[int] = []
    for step in routine["steps"]:
        if step.get("step_kind") == "app_action":
            if routine["approval_mode"] == "suggest_only":
                with db() as connection:
                    cursor = connection.execute(
                        """INSERT INTO automation_app_suggestions(
                             routine_id,rule_id,app_key,action_key,arguments_json,risk,source_kind
                           ) VALUES (?,?,?,?,?,?,?)""",
                        (
                            int(routine["id"]),
                            int(rule["id"]) if rule else None,
                            str(step["app_key"]),
                            str(step["action_key"]),
                            json.dumps(step["arguments"], separators=(",", ":")),
                            str(step.get("risk") or "write"),
                            source_kind,
                        ),
                    )
                    suggestion_ids.append(int(cursor.lastrowid))
                continue
            try:
                request = approvals.create_app_action_request(
                    "automation:local-rule",
                    "apps.invoke",
                    {
                        "app_key": step["app_key"],
                        "action": step["action_key"],
                        "arguments": step["arguments"],
                    },
                    owner=True,
                )
            except approvals.ApprovalError as exc:
                raise LocalAutomationError(str(exc), exc.status_code) from exc
        else:
            if routine["approval_mode"] == "suggest_only":
                suggestion = room_device_automation.create_suggestion(
                    source_kind=source_kind,
                    reason=f"Routine {routine['name']} proposes {step['command']} for {step['device_name']}.",
                    device_key=step["device_key"],
                    command=step["command"],
                    arguments=step["arguments"],
                    source_event_type="automation.routine",
                )
                suggestion_ids.append(int(suggestion["id"]))
                continue
            request = approvals.create_device_command_request(
                "automation:local-rule",
                {
                    "device_key": step["device_key"],
                    "command": step["command"],
                    "arguments": step["arguments"],
                },
                owner=True,
            )
        request_id = str(((request.get("result") or {}).get("request_id") or ""))
        if not request_id:
            raise LocalAutomationError("Routine could not create an approval request.", 500)
        request_ids.append(request_id)

    status = "suggested" if routine["approval_mode"] == "suggest_only" else "requested"
    execution_id = _record_execution(
        rule_id=int(rule["id"]) if rule else None,
        routine_id=int(routine["id"]),
        trigger_kind=str(rule["trigger_kind"]) if rule else "manual",
        snapshot=snapshot or {"source_kind": source_kind},
        status=status,
        action_count=len(routine["steps"]),
        request_ids=request_ids,
        suggestion_ids=suggestion_ids,
    )
    return {
        "execution_id": execution_id,
        "routine_key": routine["routine_key"],
        "status": status,
        "action_count": len(routine["steps"]),
        "request_ids": request_ids,
        "suggestion_ids": suggestion_ids,
        "approval_required": bool(request_ids),
    }


def _fire_rule(rule: dict[str, Any], snapshot: dict[str, Any], now: datetime) -> dict[str, Any]:
    if _within_cooldown(rule, now):
        _record_execution(
            rule_id=int(rule["id"]),
            routine_id=int(rule["routine_id"]),
            trigger_kind=str(rule["trigger_kind"]),
            snapshot=snapshot,
            status="skipped",
            action_count=0,
            request_ids=[],
            suggestion_ids=[],
            error="Rule cooldown active.",
        )
        return {"fired": False, "reason": "cooldown"}
    settings = get_settings()
    if _rate_limited(settings, now):
        _record_execution(
            rule_id=int(rule["id"]),
            routine_id=int(rule["routine_id"]),
            trigger_kind=str(rule["trigger_kind"]),
            snapshot=snapshot,
            status="skipped",
            action_count=0,
            request_ids=[],
            suggestion_ids=[],
            error="Automation rule rate limit reached.",
        )
        return {"fired": False, "reason": "rate_limit"}
    result = run_routine(
        str(rule["routine_key"]),
        source_kind=f"rule:{rule['rule_key']}",
        rule=rule,
        snapshot=snapshot,
    )
    with db() as connection:
        connection.execute(
            "UPDATE automation_rules SET last_fired_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (_iso(now), int(rule["id"])),
        )
    return {"fired": True, **result}


def evaluate_rule(rule_key: str, *, force_manual: bool = False) -> dict[str, Any]:
    rule = get_rule(rule_key)
    if not rule["enabled"] or not rule["routine_enabled"]:
        return {"fired": False, "reason": "disabled", "rule_key": rule["rule_key"]}
    now = _now()
    trigger_snapshot: dict[str, Any] = {"evaluated_at": _iso(now), "trigger_kind": rule["trigger_kind"]}
    triggered = False

    if rule["trigger_kind"] == "manual":
        triggered = bool(force_manual)
    elif rule["trigger_kind"] == "daily":
        next_run = rule.get("next_run_at")
        if next_run:
            try:
                due = datetime.fromisoformat(str(next_run).replace("Z", "+00:00"))
            except ValueError:
                due = now + timedelta(days=1)
            triggered = due <= now
        if triggered:
            next_run = _next_daily(rule["trigger"], from_time=now)
            with db() as connection:
                connection.execute("UPDATE automation_rules SET next_run_at=? WHERE id=?", (next_run, int(rule["id"])))
            trigger_snapshot["scheduled_for"] = rule.get("next_run_at")
    elif rule["trigger_kind"] == "device_state":
        matched, state_snapshot = _state_match(rule["trigger"])
        prior = rule.get("last_condition")
        triggered = bool(matched and prior is not True)
        trigger_snapshot["device_state"] = {**state_snapshot, "matched": matched}
        with db() as connection:
            connection.execute(
                "UPDATE automation_rules SET last_condition=? WHERE id=?",
                (1 if matched else 0, int(rule["id"])),
            )
    elif rule["trigger_kind"] == "app_event":
        app_key = str(rule["trigger"].get("app_key") or "")
        event_type = str(rule["trigger"].get("event_type") or "")
        with db() as connection:
            app_row = connection.execute(
                "SELECT app_id FROM homeserver_apps WHERE app_key=?",
                (app_key,),
            ).fetchone()
            event = None
            if app_row is not None:
                event = connection.execute(
                    """SELECT id,event_type,actor_type,actor_key,created_at
                       FROM homeserver_app_events
                       WHERE app_id=? AND event_type=? AND id>?
                       ORDER BY id ASC LIMIT 1""",
                    (str(app_row["app_id"]), event_type, int(rule.get("last_event_id") or 0)),
                ).fetchone()
            if event is not None:
                connection.execute(
                    "UPDATE automation_rules SET last_event_id=? WHERE id=?",
                    (int(event["id"]), int(rule["id"])),
                )
        if event is not None:
            triggered = True
            trigger_snapshot["app_event"] = {
                "event_id": int(event["id"]),
                "app_key": app_key,
                "event_type": str(event["event_type"]),
                "actor_type": str(event["actor_type"]),
                "actor_key": str(event["actor_key"]),
                "created_at": str(event["created_at"]),
                "metadata_exposed": False,
            }

    conditions_ok, condition_snapshot = _conditions_match(rule)
    trigger_snapshot["conditions"] = condition_snapshot
    with db() as connection:
        connection.execute(
            "UPDATE automation_rules SET last_evaluated_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (_iso(now), int(rule["id"])),
        )
    if not triggered:
        return {"fired": False, "reason": "trigger_not_met", "rule_key": rule["rule_key"], "snapshot": trigger_snapshot}
    if not conditions_ok:
        _record_execution(
            rule_id=int(rule["id"]),
            routine_id=int(rule["routine_id"]),
            trigger_kind=str(rule["trigger_kind"]),
            snapshot=trigger_snapshot,
            status="skipped",
            action_count=0,
            request_ids=[],
            suggestion_ids=[],
            error="Rule conditions were not satisfied.",
        )
        return {"fired": False, "reason": "conditions_not_met", "rule_key": rule["rule_key"], "snapshot": trigger_snapshot}
    return {"rule_key": rule["rule_key"], **_fire_rule(rule, trigger_snapshot, now)}


def evaluate_due_rules() -> list[dict[str, Any]]:
    settings = get_settings()
    if not settings["enabled"]:
        return []
    results = []
    for rule in list_rules():
        if rule["trigger_kind"] == "manual":
            continue
        try:
            results.append(evaluate_rule(rule["rule_key"]))
        except Exception as exc:
            _record_execution(
                rule_id=int(rule["id"]),
                routine_id=int(rule["routine_id"]),
                trigger_kind=str(rule["trigger_kind"]),
                snapshot={"evaluated_at": _iso(), "rule_key": rule["rule_key"]},
                status="failed",
                action_count=0,
                request_ids=[],
                suggestion_ids=[],
                error=str(exc),
            )
    return results


def list_app_suggestions(status: str = "suggested", limit: int = 100) -> list[dict[str, Any]]:
    state = str(status or "suggested").strip().lower()
    if state not in {"suggested","accepted","dismissed",""}:
        raise LocalAutomationError("Unsupported app suggestion status.")
    bounded = max(1, min(int(limit), 500))
    with db() as connection:
        if state:
            rows = connection.execute(
                """SELECT s.*,r.routine_key,r.name AS routine_name,ar.rule_key
                   FROM automation_app_suggestions s
                   JOIN automation_routines r ON r.id=s.routine_id
                   LEFT JOIN automation_rules ar ON ar.id=s.rule_id
                   WHERE s.status=? ORDER BY s.id DESC LIMIT ?""",
                (state, bounded),
            ).fetchall()
        else:
            rows = connection.execute(
                """SELECT s.*,r.routine_key,r.name AS routine_name,ar.rule_key
                   FROM automation_app_suggestions s
                   JOIN automation_routines r ON r.id=s.routine_id
                   LEFT JOIN automation_rules ar ON ar.id=s.rule_id
                   ORDER BY s.id DESC LIMIT ?""",
                (bounded,),
            ).fetchall()
    return [{
        **{k:v for k,v in dict(row).items() if k!="arguments_json"},
        "arguments": _decode(row["arguments_json"], {}),
        "arguments_exposed": True,
    } for row in rows]


def decide_app_suggestion(suggestion_id: int, decision: str) -> dict[str, Any]:
    choice = str(decision or "").strip().lower()
    if choice not in {"accept","dismiss"}:
        raise LocalAutomationError("App suggestion decision must be accept or dismiss.")
    with db() as connection:
        row = connection.execute(
            "SELECT * FROM automation_app_suggestions WHERE id=?",
            (int(suggestion_id),),
        ).fetchone()
    if row is None:
        raise LocalAutomationError("App automation suggestion not found.",404)
    if str(row["status"])!="suggested":
        raise LocalAutomationError("App automation suggestion has already been decided.",409)
    request_id = ""
    if choice=="accept":
        try:
            request = approvals.create_app_action_request(
                "automation:suggestion",
                "apps.invoke",
                {
                    "app_key":str(row["app_key"]),
                    "action":str(row["action_key"]),
                    "arguments":_decode(row["arguments_json"],{}),
                },
                owner=True,
            )
        except approvals.ApprovalError as exc:
            raise LocalAutomationError(str(exc),exc.status_code) from exc
        request_id=str(((request.get("result") or {}).get("request_id") or ""))
        if not request_id:
            raise LocalAutomationError("Accepted suggestion could not create an approval request.",500)
    with db() as connection:
        connection.execute(
            """UPDATE automation_app_suggestions
               SET status=?,decided_at=CURRENT_TIMESTAMP WHERE id=? AND status='suggested'""",
            ("accepted" if choice=="accept" else "dismissed",int(suggestion_id)),
        )
    return {
        "suggestion_id":int(suggestion_id),
        "decision":choice,
        "request_id":request_id or None,
        "owner_approval_required":bool(request_id),
        "executed":False,
    }


def brain_context(limit: int = 20) -> dict[str, Any]:
    bounded=max(1,min(int(limit),50))
    routines=list_routines()
    rules=list_rules()
    executions=list_executions(bounded)
    suggestions=list_app_suggestions("suggested",bounded)
    return {
        "contract":"vp3.homeserver.app-orchestration.brain-context.v1",
        "version":AUTOMATION_RULES_VERSION,
        "routines":[{
            "routine_key":row["routine_key"],
            "name":row["name"],
            "enabled":bool(row["enabled"]),
            "approval_mode":row["approval_mode"],
            "step_count":len(row.get("steps") or []),
            "app_action_steps":sum(1 for step in row.get("steps") or [] if step.get("step_kind")=="app_action"),
        } for row in routines[:bounded]],
        "rules":[{
            "rule_key":row["rule_key"],
            "name":row["name"],
            "enabled":bool(row["enabled"]),
            "trigger_kind":row["trigger_kind"],
            "routine_key":row["routine_key"],
            "last_fired_at":row.get("last_fired_at"),
        } for row in rules[:bounded]],
        "recent_executions":[{
            "id":row["id"],
            "routine_key":row["routine_key"],
            "rule_key":row.get("rule_key"),
            "trigger_kind":row["trigger_kind"],
            "status":row["status"],
            "action_count":row["action_count"],
            "created_at":row["created_at"],
        } for row in executions[:bounded]],
        "pending_app_suggestions":[{
            "id":row["id"],
            "routine_key":row["routine_key"],
            "app_key":row["app_key"],
            "action_key":row["action_key"],
            "risk":row["risk"],
            "created_at":row["created_at"],
        } for row in suggestions[:bounded]],
        "governance":{
            "homeserver_execution_authority":True,
            "app_actions_use_universal_control":True,
            "app_actions_use_owner_approval_ledger":True,
            "suggest_only_never_executes":True,
            "event_metadata_exposed_to_brain":False,
        },
    }


def list_executions(limit: int = 100) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 500))
    with db() as connection:
        rows = connection.execute(
            """
            SELECT e.*,r.rule_key,r.name AS rule_name,u.routine_key,u.name AS routine_name
            FROM automation_rule_executions e
            LEFT JOIN automation_rules r ON r.id=e.rule_id
            JOIN automation_routines u ON u.id=e.routine_id
            ORDER BY e.id DESC LIMIT ?
            """,
            (bounded,),
        ).fetchall()
    output = []
    for row in rows:
        item = dict(row)
        item["trigger_snapshot"] = _decode(item.pop("trigger_snapshot_json", "{}"), {})
        item["request_ids"] = _decode(item.pop("request_ids_json", "[]"), [])
        item["suggestion_ids"] = _decode(item.pop("suggestion_ids_json", "[]"), [])
        output.append(item)
    return output


def _worker() -> None:
    while not _STOP.is_set():
        try:
            settings = get_settings()
            if settings["enabled"]:
                evaluate_due_rules()
            wait = max(5, int(settings["poll_seconds"]))
        except Exception:
            wait = 15
        _STOP.wait(wait)


def start() -> None:
    global _THREAD
    with _LOCK:
        if _THREAD and _THREAD.is_alive():
            return
        _STOP.clear()
        _THREAD = threading.Thread(target=_worker, name="vp3-local-automation", daemon=True)
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
        "version": AUTOMATION_RULES_VERSION,
        "local_scheduler": True,
        "owner_defined_rules": True,
        "routines": True,
        "trigger_kinds": sorted(_ALLOWED_TRIGGER_KINDS),
        "approval_modes": sorted(_ALLOWED_APPROVAL_MODES),
        "direct_physical_execution": False,
        "device_commands_still_require_owner_approval": True,
        "app_action_steps": True,
        "app_event_triggers": True,
        "app_state_conditions": True,
        "universal_app_control_contract": "vp3.app.agent-control.v3",
        "app_actions_use_owner_approval_ledger": True,
        "homeserver_execution_authority": True,
        "max_routine_steps": MAX_ROUTINE_STEPS,
    }
