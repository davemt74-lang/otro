from __future__ import annotations

import time
from typing import Any

from . import tools, vp3_scheduling_connector as connector

SCHEDULING_KEYS = {
    "vp3.schedule.overview",
    "vp3.schedule.availability",
    "vp3.booking.create",
    "vp3.booking.reschedule",
    "vp3.booking.cancel",
}
WRITE_KEYS = {"vp3.booking.create", "vp3.booking.reschedule", "vp3.booking.cancel"}

DEFINITIONS = {
    "vp3.schedule.overview": {
        "key": "vp3.schedule.overview",
        "name": "VP3 Calendar Overview",
        "description": "Read normalized personal and Team scheduling configuration and upcoming bookings from the paired VP3 cloud account.",
        "mode": "read",
        "required_permissions": ["scheduling.read"],
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "vp3.schedule.availability": {
        "key": "vp3.schedule.availability",
        "name": "VP3 Scheduling Availability",
        "description": "Read live personal or Team availability from VP3, including Google/Outlook busy-time conflicts without exposing provider credentials.",
        "mode": "read",
        "required_permissions": ["scheduling.read"],
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["personal", "team"]},
                "target_id": {"type": "integer", "minimum": 1},
                "date": {"type": "string", "pattern": "^20[0-9]{2}-[0-9]{2}-[0-9]{2}$"},
                "routing_answer": {"type": "string", "maxLength": 500},
            },
            "required": ["kind", "target_id", "date"],
            "additionalProperties": False,
        },
    },
    "vp3.booking.create": {
        "key": "vp3.booking.create",
        "name": "Create VP3 Booking",
        "description": "Create a personal or Team booking in VP3 after local HomeServer approval.",
        "mode": "write",
        "required_permissions": ["scheduling.write"],
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["personal", "team"]},
                "target_id": {"type": "integer", "minimum": 1},
                "start_at_utc": {"type": "string", "minLength": 16, "maxLength": 40},
                "guest_name": {"type": "string", "minLength": 1, "maxLength": 190},
                "guest_email": {"type": "string", "maxLength": 254},
                "guest_timezone": {"type": "string", "maxLength": 100},
                "routing_answer": {"type": "string", "maxLength": 500},
                "idempotency_key": {"type": "string", "maxLength": 160},
            },
            "required": ["kind", "target_id", "start_at_utc", "guest_name"],
            "additionalProperties": False,
        },
    },
    "vp3.booking.reschedule": {
        "key": "vp3.booking.reschedule",
        "name": "Reschedule VP3 Booking",
        "description": "Reschedule an active personal VP3 booking after local HomeServer approval.",
        "mode": "write",
        "required_permissions": ["scheduling.write"],
        "input_schema": {
            "type": "object",
            "properties": {
                "booking_id": {"type": "integer", "minimum": 1},
                "start_at_utc": {"type": "string", "minLength": 16, "maxLength": 40},
                "idempotency_key": {"type": "string", "maxLength": 160},
            },
            "required": ["booking_id", "start_at_utc"],
            "additionalProperties": False,
        },
    },
    "vp3.booking.cancel": {
        "key": "vp3.booking.cancel",
        "name": "Cancel VP3 Booking",
        "description": "Cancel an active personal or Team VP3 booking after local HomeServer approval.",
        "mode": "write",
        "required_permissions": ["scheduling.write"],
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["personal", "team"]},
                "booking_id": {"type": "integer", "minimum": 1},
                "target_id": {"type": "integer", "minimum": 1},
                "idempotency_key": {"type": "string", "maxLength": 160},
            },
            "required": ["kind", "booking_id"],
            "additionalProperties": False,
        },
    },
}

SKILLS = (
    {
        "key": "vp3.calendar-review",
        "name": "VP3 Calendar Review",
        "description": "Review upcoming personal and Team bookings and scheduling configuration.",
        "tools": ["vp3.schedule.overview"],
    },
    {
        "key": "vp3.find-time",
        "name": "VP3 Find Time",
        "description": "Find conflict-safe personal or Team appointment times using VP3 scheduling and connected calendars.",
        "tools": ["vp3.schedule.availability"],
    },
    {
        "key": "vp3.booking-management",
        "name": "VP3 Booking Management",
        "description": "Find time and propose creating, rescheduling, or cancelling VP3 bookings through HomeServer approvals.",
        "tools": [
            "vp3.schedule.overview",
            "vp3.schedule.availability",
            "vp3.booking.create",
            "vp3.booking.reschedule",
            "vp3.booking.cancel",
        ],
    },
)


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise tools.ToolError(f"{name} must be a positive integer.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise tools.ToolError(f"{name} must be a positive integer.") from exc
    if parsed < 1:
        raise tools.ToolError(f"{name} must be a positive integer.")
    return parsed


def _execute(key: str, args: dict[str, Any]) -> dict[str, Any]:
    if key == "vp3.schedule.overview":
        return connector.request("overview", {})
    if key == "vp3.schedule.availability":
        kind = str(args.get("kind") or "")
        if kind not in {"personal", "team"}:
            raise tools.ToolError("kind must be personal or team.")
        target_id = _positive_int(args.get("target_id"), "target_id")
        date = str(args.get("date") or "").strip()
        if len(date) != 10:
            raise tools.ToolError("date must be YYYY-MM-DD.")
        payload = {
            "target_id": target_id,
            "date": date,
            "routing_answer": str(args.get("routing_answer") or "")[:500],
        }
        if kind == "team":
            payload["pool_id"] = target_id
            return connector.request("team.availability", payload)
        payload["event_type_id"] = target_id
        return connector.request("availability", payload)
    if key == "vp3.booking.create":
        kind = str(args.get("kind") or "")
        if kind not in {"personal", "team"}:
            raise tools.ToolError("kind must be personal or team.")
        payload = dict(args)
        payload.pop("kind", None)
        idempotency_key = str(payload.pop("idempotency_key", "") or "")
        target_id = _positive_int(payload.pop("target_id", 0), "target_id")
        if kind == "team":
            payload["pool_id"] = target_id
            return connector.request("team.booking.create", payload, idempotency_key=idempotency_key)
        payload["event_type_id"] = target_id
        return connector.request("booking.create", payload, idempotency_key=idempotency_key)
    if key == "vp3.booking.reschedule":
        payload = dict(args)
        payload["booking_id"] = _positive_int(payload.get("booking_id"), "booking_id")
        idempotency_key = str(payload.pop("idempotency_key", "") or "")
        return connector.request("booking.reschedule", payload, idempotency_key=idempotency_key)
    if key == "vp3.booking.cancel":
        kind = str(args.get("kind") or "")
        if kind not in {"personal", "team"}:
            raise tools.ToolError("kind must be personal or team.")
        payload = dict(args)
        payload.pop("kind", None)
        payload["booking_id"] = _positive_int(payload.get("booking_id"), "booking_id")
        idempotency_key = str(payload.pop("idempotency_key", "") or "")
        target_raw = payload.pop("target_id", 0)
        if kind == "team":
            payload["pool_id"] = _positive_int(target_raw, "target_id")
            return connector.request("team.booking.cancel", payload, idempotency_key=idempotency_key)
        return connector.request("booking.cancel", payload, idempotency_key=idempotency_key)
    raise tools.ToolError("Unsupported VP3 scheduling tool.")


def install() -> None:
    if getattr(tools, "_vp3_scheduling_v059_installed", False):
        return
    for key, definition in DEFINITIONS.items():
        existing = tools.TOOL_DEFINITIONS.get(key)
        if existing not in (None, definition):
            raise RuntimeError(f"Tool definition collision: {key}")
        tools.TOOL_DEFINITIONS[key] = definition
    for skill in SKILLS:
        if not any(item.get("key") == skill["key"] for item in tools.SKILL_DEFINITIONS):
            tools.SKILL_DEFINITIONS = (*tools.SKILL_DEFINITIONS, skill)

    original_meta = tools._safe_argument_metadata

    def safe_meta(key: str, args: dict[str, Any]) -> dict[str, Any]:
        if key in SCHEDULING_KEYS:
            try:
                target_id = int(args.get("target_id") or 0)
            except (TypeError, ValueError):
                target_id = 0
            try:
                booking_id = int(args.get("booking_id") or 0)
            except (TypeError, ValueError):
                booking_id = 0
            return {
                "argument_keys": sorted(k for k in args if k != "idempotency_key"),
                "kind": str(args.get("kind") or ""),
                "target_id": target_id,
                "booking_id": booking_id,
            }
        return original_meta(key, args)

    tools._safe_argument_metadata = safe_meta
    original_execute = tools.execute_tool

    def execute_tool(
        source_app_key: str,
        tool_key: str,
        arguments: dict[str, Any] | None,
        granted_permissions: set[str] | None = None,
        *,
        owner: bool = False,
    ) -> dict[str, Any]:
        if tool_key not in DEFINITIONS:
            return original_execute(
                source_app_key,
                tool_key,
                arguments,
                granted_permissions,
                owner=owner,
            )
        tool = tools._tool_definition(tool_key)
        source = source_app_key.strip() or ("owner" if owner else "app:unknown")
        actor = "owner" if owner else "app"
        granted = set(granted_permissions or set())
        required = [] if owner else sorted({tools.TOOL_EXECUTE_PERMISSION, *tool["required_permissions"]})
        payload = dict(arguments or {})
        missing = tools._missing_permissions(tool, granted, owner)
        if missing:
            raise tools.ToolError(f"Missing tool permissions: {', '.join(missing)}.", 403)
        if not tools._policy_map().get(tool_key, True):
            raise tools.ToolError("Tool is disabled by the HomeServer owner.", 403)

        started = time.perf_counter()
        meta = tools._safe_argument_metadata(tool_key, payload)
        try:
            result = _execute(tool_key, payload)
        except tools.ToolError as exc:
            run_id = tools._record_run(
                tool_key=tool_key,
                source_app_key=source,
                actor_type=actor,
                status="failed",
                required_permissions=required,
                arguments_meta=meta,
                duration_ms=int((time.perf_counter() - started) * 1000),
                error=str(exc),
            )
            raise tools.ToolError(f"{exc} Run {run_id} was recorded.", exc.status_code) from exc
        except connector.VP3SchedulingConnectorError as exc:
            run_id = tools._record_run(
                tool_key=tool_key,
                source_app_key=source,
                actor_type=actor,
                status="failed",
                required_permissions=required,
                arguments_meta=meta,
                duration_ms=int((time.perf_counter() - started) * 1000),
                error=str(exc),
            )
            raise tools.ToolError(f"{exc} Run {run_id} was recorded.", exc.status_code) from exc
        except Exception as exc:
            run_id = tools._record_run(
                tool_key=tool_key,
                source_app_key=source,
                actor_type=actor,
                status="failed",
                required_permissions=required,
                arguments_meta=meta,
                duration_ms=int((time.perf_counter() - started) * 1000),
                error="Internal VP3 scheduling tool failure.",
            )
            raise tools.ToolError(f"VP3 scheduling tool failed safely. Run {run_id} was recorded.", 500) from exc

        run_id = tools._record_run(
            tool_key=tool_key,
            source_app_key=source,
            actor_type=actor,
            status="completed",
            required_permissions=required,
            arguments_meta=meta,
            result_meta={"keys": sorted(result.keys()), "version": connector.VERSION},
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        return {"run_id": run_id, "tool_key": tool_key, "status": "completed", "result": result}

    tools.execute_tool = execute_tool
    tools._vp3_scheduling_v059_installed = True
