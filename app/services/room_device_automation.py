from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from ..database import db

AUTOMATION_VERSION = "v0.60"

SAFE_CONTROL_CATEGORIES = {"light", "outlet", "fan", "thermostat", "scene"}
DISCOVERABLE_CATEGORIES = SAFE_CONTROL_CATEGORIES | {
    "sensor",
    "camera",
    "lock",
    "garage",
    "security",
    "appliance",
    "other",
}
BLOCKED_CONTROL_CATEGORIES = {"camera", "lock", "garage", "security", "appliance"}

_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,79}$")
_MAX_STATE_BYTES = 16 * 1024
_MAX_METADATA_BYTES = 16 * 1024
_MAX_CAPABILITIES_BYTES = 16 * 1024

Driver = Callable[[dict[str, Any], str, dict[str, Any]], dict[str, Any]]

_DRIVERS: dict[str, Driver] = {}
_DRIVER_LOCK = threading.RLock()


class RoomDeviceError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_key(value: Any, label: str) -> str:
    key = str(value or "").strip().lower()
    if not _KEY_RE.fullmatch(key):
        raise RoomDeviceError(f"{label} must use lowercase letters, numbers, dot, colon, underscore or dash.")
    return key


def _text(value: Any, maximum: int, *, required: bool = False, label: str = "value") -> str:
    text = " ".join(str(value or "").split())[:maximum]
    if required and not text:
        raise RoomDeviceError(f"{label} is required.")
    return text


def _json_object(value: Any, maximum_bytes: int, label: str) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise RoomDeviceError(f"{label} must be an object.")
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RoomDeviceError(f"{label} must be JSON serializable.") from exc
    if len(encoded) > maximum_bytes:
        raise RoomDeviceError(f"{label} exceeds the size limit.", 413)
    return json.loads(encoded.decode("utf-8"))


def _decode_json(value: Any) -> dict[str, Any]:
    try:
        decoded = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def register_driver(provider_key: str, driver: Driver) -> None:
    key = _safe_key(provider_key, "provider_key")
    if not callable(driver):
        raise RoomDeviceError("Provider driver must be callable.")
    with _DRIVER_LOCK:
        _DRIVERS[key] = driver


def unregister_driver(provider_key: str) -> None:
    key = str(provider_key or "").strip().lower()
    with _DRIVER_LOCK:
        _DRIVERS.pop(key, None)


def driver_registered(provider_key: str) -> bool:
    key = str(provider_key or "").strip().lower()
    with _DRIVER_LOCK:
        return key in _DRIVERS


def _driver(provider_key: str) -> Driver | None:
    key = str(provider_key or "").strip().lower()
    with _DRIVER_LOCK:
        return _DRIVERS.get(key)


def upsert_room(
    room_key: str,
    name: str,
    *,
    description: str = "",
    enabled: bool = True,
) -> dict[str, Any]:
    key = _safe_key(room_key, "room_key")
    safe_name = _text(name, 120, required=True, label="room name")
    safe_description = _text(description, 1000)
    with db() as connection:
        connection.execute(
            """
            INSERT INTO automation_rooms(room_key, name, description, enabled)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(room_key) DO UPDATE SET
                name=excluded.name,
                description=excluded.description,
                enabled=excluded.enabled,
                updated_at=CURRENT_TIMESTAMP
            """,
            (key, safe_name, safe_description or None, 1 if enabled else 0),
        )
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES ('owner','control-center','automation.room.upserted','automation_room',?,?)
            """,
            (key, json.dumps({"enabled": bool(enabled)}, separators=(",", ":"))),
        )
    return get_room(key)


def get_room(room_key: str) -> dict[str, Any]:
    key = _safe_key(room_key, "room_key")
    with db() as connection:
        row = connection.execute(
            """
            SELECT id,room_key,name,description,enabled,created_at,updated_at
            FROM automation_rooms WHERE room_key=? LIMIT 1
            """,
            (key,),
        ).fetchone()
    if row is None:
        raise RoomDeviceError("Room not found.", 404)
    return {**dict(row), "enabled": bool(row["enabled"])}


def list_rooms(*, enabled_only: bool = False) -> list[dict[str, Any]]:
    where = "WHERE r.enabled=1" if enabled_only else ""
    with db() as connection:
        rows = connection.execute(
            f"""
            SELECT r.id,r.room_key,r.name,r.description,r.enabled,r.created_at,r.updated_at,
                   COUNT(d.id) AS device_count
            FROM automation_rooms r
            LEFT JOIN automation_devices d ON d.room_id=r.id AND d.enabled=1
            {where}
            GROUP BY r.id
            ORDER BY r.name COLLATE NOCASE, r.id
            """
        ).fetchall()
    return [
        {**dict(row), "enabled": bool(row["enabled"]), "device_count": int(row["device_count"])}
        for row in rows
    ]


def delete_room(room_key: str) -> dict[str, Any]:
    room = get_room(room_key)
    with db() as connection:
        connection.execute("DELETE FROM automation_rooms WHERE id=?", (int(room["id"]),))
        connection.execute(
            """
            INSERT INTO activity_log(actor_type,actor_key,action,resource_type,resource_key,metadata_json)
            VALUES ('owner','control-center','automation.room.deleted','automation_room',?,'{}')
            """,
            (room["room_key"],),
        )
    return {"deleted": True, "room_key": room["room_key"]}


def upsert_provider(
    provider_key: str,
    name: str,
    provider_type: str,
    *,
    enabled: bool = True,
    executable: bool = False,
    status: str = "connected",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    key = _safe_key(provider_key, "provider_key")
    safe_name = _text(name, 120, required=True, label="provider name")
    safe_type = _safe_key(provider_type, "provider_type")
    safe_status = str(status or "disconnected").strip().lower()
    if safe_status not in {"connected", "disconnected", "degraded", "disabled"}:
        raise RoomDeviceError("Invalid provider status.")
    safe_meta = _json_object(metadata or {}, _MAX_METADATA_BYTES, "provider metadata")
    with db() as connection:
        connection.execute(
            """
            INSERT INTO automation_providers(
                provider_key,name,provider_type,enabled,executable,status,metadata_json,last_seen_at
            ) VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(provider_key) DO UPDATE SET
                name=excluded.name,
                provider_type=excluded.provider_type,
                enabled=excluded.enabled,
                executable=excluded.executable,
                status=excluded.status,
                metadata_json=excluded.metadata_json,
                last_seen_at=excluded.last_seen_at,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                key,
                safe_name,
                safe_type,
                1 if enabled else 0,
                1 if executable else 0,
                safe_status,
                json.dumps(safe_meta, separators=(",", ":")),
                _now_iso(),
            ),
        )
    return get_provider(key)


def get_provider(provider_key: str) -> dict[str, Any]:
    key = _safe_key(provider_key, "provider_key")
    with db() as connection:
        row = connection.execute(
            "SELECT * FROM automation_providers WHERE provider_key=? LIMIT 1",
            (key,),
        ).fetchone()
    if row is None:
        raise RoomDeviceError("Automation provider not found.", 404)
    item = dict(row)
    item["enabled"] = bool(item["enabled"])
    item["executable"] = bool(item["executable"])
    item["driver_registered"] = driver_registered(key)
    item["metadata"] = _decode_json(item.pop("metadata_json", "{}"))
    item["currently_executable"] = bool(
        item["enabled"]
        and item["executable"]
        and item["status"] == "connected"
        and item["driver_registered"]
    )
    return item


def list_providers() -> list[dict[str, Any]]:
    with db() as connection:
        rows = connection.execute(
            "SELECT provider_key FROM automation_providers ORDER BY name COLLATE NOCASE, id"
        ).fetchall()
    return [get_provider(str(row["provider_key"])) for row in rows]


def _room_id(room_key: str | None) -> int | None:
    if room_key in (None, ""):
        return None
    room = get_room(str(room_key))
    if not room["enabled"]:
        raise RoomDeviceError("Room is disabled.", 409)
    return int(room["id"])


def _normalize_category(value: Any) -> str:
    category = str(value or "other").strip().lower()
    if category not in DISCOVERABLE_CATEGORIES:
        category = "other"
    return category


def upsert_device(
    device_key: str,
    provider_key: str,
    provider_device_id: str,
    name: str,
    category: str,
    *,
    room_key: str | None = None,
    enabled: bool = True,
    controllable: bool = False,
    capabilities: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    key = _safe_key(device_key, "device_key")
    provider = get_provider(provider_key)
    provider_device = _text(provider_device_id, 240, required=True, label="provider_device_id")
    safe_name = _text(name, 160, required=True, label="device name")
    safe_category = _normalize_category(category)
    safe_capabilities = _json_object(capabilities or {}, _MAX_CAPABILITIES_BYTES, "capabilities")
    safe_state = _json_object(state or {}, _MAX_STATE_BYTES, "state")
    safe_metadata = _json_object(metadata or {}, _MAX_METADATA_BYTES, "device metadata")

    allowed_controllable = bool(controllable and safe_category in SAFE_CONTROL_CATEGORIES)
    with db() as connection:
        connection.execute(
            """
            INSERT INTO automation_devices(
                device_key,provider_key,provider_device_id,room_id,name,category,
                enabled,controllable,capabilities_json,state_json,metadata_json,last_seen_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(device_key) DO UPDATE SET
                provider_key=excluded.provider_key,
                provider_device_id=excluded.provider_device_id,
                room_id=excluded.room_id,
                name=excluded.name,
                category=excluded.category,
                enabled=excluded.enabled,
                controllable=excluded.controllable,
                capabilities_json=excluded.capabilities_json,
                state_json=excluded.state_json,
                metadata_json=excluded.metadata_json,
                last_seen_at=excluded.last_seen_at,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                key,
                provider["provider_key"],
                provider_device,
                _room_id(room_key),
                safe_name,
                safe_category,
                1 if enabled else 0,
                1 if allowed_controllable else 0,
                json.dumps(safe_capabilities, separators=(",", ":")),
                json.dumps(safe_state, separators=(",", ":")),
                json.dumps(safe_metadata, separators=(",", ":")),
                _now_iso(),
            ),
        )
    return get_device(key)


def _decode_device(row) -> dict[str, Any]:
    item = dict(row)
    item["enabled"] = bool(item["enabled"])
    item["controllable"] = bool(item["controllable"])
    item["capabilities"] = _decode_json(item.pop("capabilities_json", "{}"))
    item["state"] = _decode_json(item.pop("state_json", "{}"))
    item["metadata"] = _decode_json(item.pop("metadata_json", "{}"))
    provider = get_provider(str(item["provider_key"]))
    item["provider"] = {
        "provider_key": provider["provider_key"],
        "name": provider["name"],
        "provider_type": provider["provider_type"],
        "status": provider["status"],
        "currently_executable": provider["currently_executable"],
    }
    item["currently_executable"] = bool(
        item["enabled"]
        and item["controllable"]
        and item["category"] in SAFE_CONTROL_CATEGORIES
        and provider["currently_executable"]
    )
    return item


def get_device(device_key: str) -> dict[str, Any]:
    key = _safe_key(device_key, "device_key")
    with db() as connection:
        row = connection.execute(
            """
            SELECT d.*,r.room_key,r.name AS room_name
            FROM automation_devices d
            LEFT JOIN automation_rooms r ON r.id=d.room_id
            WHERE d.device_key=? LIMIT 1
            """,
            (key,),
        ).fetchone()
    if row is None:
        raise RoomDeviceError("Device not found.", 404)
    return _decode_device(row)


def list_devices(
    *,
    room_key: str | None = None,
    category: str | None = None,
    enabled_only: bool = False,
    controllable_only: bool = False,
    limit: int = 200,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if room_key:
        clauses.append("r.room_key=?")
        params.append(_safe_key(room_key, "room_key"))
    if category:
        clauses.append("d.category=?")
        params.append(_normalize_category(category))
    if enabled_only:
        clauses.append("d.enabled=1")
    if controllable_only:
        clauses.append("d.controllable=1")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    bounded = max(1, min(int(limit), 500))
    params.append(bounded)
    with db() as connection:
        rows = connection.execute(
            f"""
            SELECT d.*,r.room_key,r.name AS room_name
            FROM automation_devices d
            LEFT JOIN automation_rooms r ON r.id=d.room_id
            {where}
            ORDER BY COALESCE(r.name,''), d.name COLLATE NOCASE, d.id
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [_decode_device(row) for row in rows]


def _bool_arg(arguments: dict[str, Any], key: str) -> bool:
    value = arguments.get(key)
    if not isinstance(value, bool):
        raise RoomDeviceError(f"{key} must be boolean.")
    return value


def normalize_command(device: dict[str, Any], command: str, arguments: Any) -> tuple[str, dict[str, Any]]:
    category = str(device["category"])
    cmd = str(command or "").strip().lower()
    args = _json_object(arguments or {}, 4096, "command arguments")

    if category in BLOCKED_CONTROL_CATEGORIES or category not in SAFE_CONTROL_CATEGORIES:
        raise RoomDeviceError("This device category is discovery-only in VP3 OS v0.60.", 403)

    if category in {"light", "outlet", "fan"}:
        if cmd in {"on", "off", "toggle"}:
            if args:
                raise RoomDeviceError(f"{cmd} does not accept arguments.")
            return cmd, {}
        if category == "light" and cmd == "set_brightness":
            unknown = set(args) - {"brightness"}
            if unknown:
                raise RoomDeviceError(f"Unsupported command argument: {sorted(unknown)[0]}")
            value = args.get("brightness")
            if isinstance(value, bool):
                raise RoomDeviceError("brightness must be an integer.")
            try:
                brightness = int(value)
            except (TypeError, ValueError) as exc:
                raise RoomDeviceError("brightness must be an integer.") from exc
            if brightness < 0 or brightness > 100:
                raise RoomDeviceError("brightness must be between 0 and 100.")
            return cmd, {"brightness": brightness}

    if category == "thermostat":
        if cmd == "set_temperature":
            unknown = set(args) - {"temperature_f"}
            if unknown:
                raise RoomDeviceError(f"Unsupported command argument: {sorted(unknown)[0]}")
            raw = args.get("temperature_f")
            if isinstance(raw, bool):
                raise RoomDeviceError("temperature_f must be numeric.")
            try:
                temp = float(raw)
            except (TypeError, ValueError) as exc:
                raise RoomDeviceError("temperature_f must be numeric.") from exc
            if temp < 50 or temp > 90:
                raise RoomDeviceError("temperature_f must be between 50 and 90.")
            return cmd, {"temperature_f": round(temp, 1)}
        if cmd == "set_mode":
            unknown = set(args) - {"mode"}
            if unknown:
                raise RoomDeviceError(f"Unsupported command argument: {sorted(unknown)[0]}")
            mode = str(args.get("mode") or "").strip().lower()
            if mode not in {"off", "heat", "cool", "auto"}:
                raise RoomDeviceError("Unsupported thermostat mode.")
            return cmd, {"mode": mode}

    if category == "scene" and cmd == "activate":
        if args:
            raise RoomDeviceError("activate does not accept arguments.")
        return cmd, {}

    raise RoomDeviceError("Command is not supported for this device.", 422)


def command_metadata(device: dict[str, Any], command: str, arguments: dict[str, Any]) -> dict[str, Any]:
    safe = {
        "device_key": str(device["device_key"])[:80],
        "category": str(device["category"])[:40],
        "room_key": str(device.get("room_key") or "")[:80] or None,
        "command": str(command)[:40],
        "argument_count": len(arguments),
    }
    if command == "set_brightness":
        safe["brightness"] = int(arguments["brightness"])
    elif command == "set_temperature":
        safe["temperature_f"] = float(arguments["temperature_f"])
    elif command == "set_mode":
        safe["mode"] = str(arguments["mode"])[:20]
    return safe


def validate_command_request(
    device_key: str,
    command: str,
    arguments: dict[str, Any] | None,
) -> dict[str, Any]:
    device = get_device(device_key)
    if not device["enabled"]:
        raise RoomDeviceError("Device is disabled.", 409)
    if not device["controllable"]:
        raise RoomDeviceError("Device is not marked controllable.", 403)
    normalized_command, normalized_arguments = normalize_command(device, command, arguments or {})
    return {
        "device": device,
        "device_key": device["device_key"],
        "command": normalized_command,
        "arguments": normalized_arguments,
        "arguments_meta": command_metadata(device, normalized_command, normalized_arguments),
    }


def _assert_approved_execution_context(
    action_request_id: str | None,
    *,
    device_key: str,
    command: str,
    arguments: dict[str, Any],
) -> None:
    request_id = str(action_request_id or "").strip()
    if not request_id:
        raise RoomDeviceError(
            "Physical device commands require an approved action request.",
            403,
        )
    with db() as connection:
        row = connection.execute(
            """
            SELECT id, action_key, status, arguments_json
            FROM action_requests
            WHERE id=? LIMIT 1
            """,
            (request_id,),
        ).fetchone()
    if row is None:
        raise RoomDeviceError("Approved action request was not found.", 403)
    if str(row["action_key"]) != "devices.command" or str(row["status"]) != "executing":
        raise RoomDeviceError(
            "Device action request is not reserved for execution.",
            409,
        )
    try:
        request_arguments = json.loads(str(row["arguments_json"] or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RoomDeviceError("Approved action request arguments are invalid.", 409) from exc
    expected = {
        "device_key": str(device_key),
        "command": str(command),
        "arguments": dict(arguments),
    }
    if request_arguments != expected:
        raise RoomDeviceError(
            "Approved action request does not match this device command.",
            409,
        )


def execute_command(
    device_key: str,
    command: str,
    arguments: dict[str, Any] | None,
    *,
    source_app_key: str,
    action_request_id: str | None = None,
) -> dict[str, Any]:
    validated = validate_command_request(device_key, command, arguments)
    _assert_approved_execution_context(
        action_request_id,
        device_key=validated["device_key"],
        command=validated["command"],
        arguments=validated["arguments"],
    )
    device = validated["device"]
    provider = get_provider(str(device["provider_key"]))
    if not provider["currently_executable"] or not device["currently_executable"]:
        raise RoomDeviceError(
            "Device is discovered but no enabled local execution driver is available.",
            503,
        )
    driver = _driver(provider["provider_key"])
    if driver is None:
        raise RoomDeviceError("Local provider driver is unavailable.", 503)

    before_state = dict(device["state"])
    with db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO automation_device_actions(
                device_id,action_request_id,source_app_key,command,arguments_meta_json,
                before_state_json,status
            ) VALUES (?,?,?,?,?,?,'executing')
            """,
            (
                int(device["id"]),
                action_request_id,
                str(source_app_key or "owner")[:120],
                validated["command"],
                json.dumps(validated["arguments_meta"], separators=(",", ":")),
                json.dumps(before_state, separators=(",", ":")),
            ),
        )
        action_id = int(cursor.lastrowid)

    try:
        result = driver(device, validated["command"], dict(validated["arguments"]))
        if not isinstance(result, dict):
            raise RoomDeviceError("Provider driver returned an invalid result.", 502)
        new_state = _json_object(result.get("state", before_state), _MAX_STATE_BYTES, "provider state")
        with db() as connection:
            connection.execute(
                """
                UPDATE automation_devices
                SET state_json=?,last_seen_at=?,updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (json.dumps(new_state, separators=(",", ":")), _now_iso(), int(device["id"])),
            )
            connection.execute(
                """
                UPDATE automation_device_actions
                SET after_state_json=?,status='completed',completed_at=CURRENT_TIMESTAMP,error=NULL
                WHERE id=?
                """,
                (json.dumps(new_state, separators=(",", ":")), action_id),
            )
            connection.execute(
                """
                INSERT INTO activity_log(
                    actor_type,actor_key,action,resource_type,resource_key,metadata_json
                ) VALUES ('system','room-device-automation','automation.device.command.executed','automation_device',?,?)
                """,
                (
                    device["device_key"],
                    json.dumps(
                        {
                            "action_id": action_id,
                            "command": validated["command"],
                            "action_request_id": action_request_id,
                        },
                        separators=(",", ":"),
                    ),
                ),
            )
    except RoomDeviceError as exc:
        with db() as connection:
            connection.execute(
                """
                UPDATE automation_device_actions
                SET status='failed',error=?,completed_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (str(exc)[:1000], action_id),
            )
        raise
    except Exception as exc:
        with db() as connection:
            connection.execute(
                """
                UPDATE automation_device_actions
                SET status='failed',error='Provider command failed.',completed_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (action_id,),
            )
        raise RoomDeviceError("Provider command failed safely.", 502) from exc

    return {
        "executed": True,
        "action_id": action_id,
        "device_key": device["device_key"],
        "command": validated["command"],
        "state": new_state,
    }


def list_actions(limit: int = 100) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 500))
    with db() as connection:
        rows = connection.execute(
            """
            SELECT a.id,d.device_key,d.name AS device_name,a.action_request_id,
                   a.source_app_key,a.command,a.arguments_meta_json,a.status,a.error,
                   a.created_at,a.completed_at
            FROM automation_device_actions a
            JOIN automation_devices d ON d.id=a.device_id
            ORDER BY a.id DESC LIMIT ?
            """,
            (bounded,),
        ).fetchall()
    output: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["arguments_meta"] = _decode_json(item.pop("arguments_meta_json", "{}"))
        output.append(item)
    return output


def create_suggestion(
    *,
    source_kind: str,
    reason: str,
    device_key: str | None = None,
    command: str | None = None,
    arguments: dict[str, Any] | None = None,
    source_event_type: str | None = None,
) -> dict[str, Any]:
    safe_source = _text(source_kind, 80, required=True, label="source_kind")
    safe_reason = _text(reason, 1000, required=True, label="reason")
    device_id = None
    normalized_command = None
    normalized_args: dict[str, Any] = {}
    if device_key:
        validated = validate_command_request(str(device_key), str(command or ""), arguments or {})
        device_id = int(validated["device"]["id"])
        normalized_command = validated["command"]
        normalized_args = validated["arguments"]
    with db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO automation_suggestions(
                source_kind,source_event_type,device_id,command,arguments_json,reason
            ) VALUES (?,?,?,?,?,?)
            """,
            (
                safe_source,
                _text(source_event_type, 120) or None,
                device_id,
                normalized_command,
                json.dumps(normalized_args, separators=(",", ":")),
                safe_reason,
            ),
        )
        suggestion_id = int(cursor.lastrowid)
    return get_suggestion(suggestion_id)


def get_suggestion(suggestion_id: int) -> dict[str, Any]:
    with db() as connection:
        row = connection.execute(
            """
            SELECT s.*,d.device_key,d.name AS device_name
            FROM automation_suggestions s
            LEFT JOIN automation_devices d ON d.id=s.device_id
            WHERE s.id=? LIMIT 1
            """,
            (int(suggestion_id),),
        ).fetchone()
    if row is None:
        raise RoomDeviceError("Suggestion not found.", 404)
    item = dict(row)
    item["arguments"] = _decode_json(item.pop("arguments_json", "{}"))
    return item


def list_suggestions(status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 500))
    where = ""
    params: list[Any] = []
    if status:
        safe_status = str(status).strip().lower()
        if safe_status not in {"suggested", "requested", "dismissed", "expired"}:
            raise RoomDeviceError("Invalid suggestion status.")
        where = "WHERE s.status=?"
        params.append(safe_status)
    params.append(bounded)
    with db() as connection:
        rows = connection.execute(
            f"""
            SELECT s.id
            FROM automation_suggestions s
            {where}
            ORDER BY s.id DESC LIMIT ?
            """,
            params,
        ).fetchall()
    return [get_suggestion(int(row["id"])) for row in rows]


def dismiss_suggestion(suggestion_id: int) -> dict[str, Any]:
    item = get_suggestion(suggestion_id)
    if item["status"] != "suggested":
        raise RoomDeviceError("Suggestion is no longer pending.", 409)
    with db() as connection:
        connection.execute(
            "UPDATE automation_suggestions SET status='dismissed',updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (int(suggestion_id),),
        )
    return get_suggestion(suggestion_id)


def mark_suggestion_requested(suggestion_id: int, action_request_id: str) -> dict[str, Any]:
    item = get_suggestion(suggestion_id)
    if item["status"] != "suggested":
        raise RoomDeviceError("Suggestion is no longer pending.", 409)
    with db() as connection:
        connection.execute(
            """
            UPDATE automation_suggestions
            SET status='requested',action_request_id=?,updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (str(action_request_id)[:64], int(suggestion_id)),
        )
    return get_suggestion(suggestion_id)


def public_capability() -> dict[str, Any]:
    return {
        "version": AUTOMATION_VERSION,
        "provider_neutral": True,
        "room_registry": True,
        "device_registry": True,
        "device_state_read": True,
        "governed_device_actions": True,
        "ambient_direct_execution": False,
        "safe_control_categories": sorted(SAFE_CONTROL_CATEGORIES),
        "discovery_only_categories": sorted(DISCOVERABLE_CATEGORIES - SAFE_CONTROL_CATEGORIES),
    }


def paired_status() -> dict[str, Any]:
    with db() as connection:
        rooms = int(connection.execute("SELECT COUNT(*) FROM automation_rooms WHERE enabled=1").fetchone()[0])
        devices = int(connection.execute("SELECT COUNT(*) FROM automation_devices WHERE enabled=1").fetchone()[0])
        controllable = int(
            connection.execute(
                "SELECT COUNT(*) FROM automation_devices WHERE enabled=1 AND controllable=1"
            ).fetchone()[0]
        )
    return {
        "version": AUTOMATION_VERSION,
        "rooms": rooms,
        "devices": devices,
        "controllable_devices": controllable,
        "ambient_direct_execution": False,
    }
