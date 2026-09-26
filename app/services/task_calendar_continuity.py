from __future__ import annotations

import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
import json
import re
from datetime import datetime, timezone
from typing import Any

from ..database import db
from . import federated_data
from . import tasks as task_service

_CANONICAL_ID = re.compile(r"^fd24_[0-9a-f]{40}$")
_REVISION = re.compile(r"^[0-9a-f]{64}$")
_MUTATION_ID = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_TASK_STATUSES = {"pending", "in_progress", "completed", "cancelled"}
_TASK_PRIORITIES = {"low", "normal", "high", "urgent"}
_TASK_RECURRENCES = {"none", "daily", "weekly", "monthly"}


class TaskCalendarContinuityError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


_TASK_CREATOR_PROVENANCE: ContextVar[str | None] = ContextVar(
    "homeserver_task_creator_provenance",
    default=None,
)


@contextmanager
def task_creator_provenance(value: str | None):
    creator = str(value or "").strip().lower()
    if creator not in {"owner", "app", "agent", "system"}:
        creator = None
    token = _TASK_CREATOR_PROVENANCE.set(creator)
    try:
        yield
    finally:
        _TASK_CREATOR_PROVENANCE.reset(token)


def current_task_creator_provenance(fallback: str = "app") -> str:
    creator = _TASK_CREATOR_PROVENANCE.get()
    if creator in {"owner", "app", "agent", "system"}:
        return creator
    safe = str(fallback or "app").strip().lower()
    return safe if safe in {"owner", "app", "agent", "system"} else "app"


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _iso_datetime(value: Any, label: str, *, required: bool = False) -> str | None:
    raw = _text(value, 80)
    if not raw:
        if required:
            raise TaskCalendarContinuityError(f"{label} is required.")
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TaskCalendarContinuityError(f"{label} must be ISO-8601.") from exc
    if parsed.tzinfo is None:
        raise TaskCalendarContinuityError(f"{label} must include a timezone offset.")
    return parsed.astimezone(timezone.utc).isoformat()


def _mutation_id(value: Any) -> str:
    mutation = _text(value, 128)
    if not _MUTATION_ID.fullmatch(mutation):
        raise TaskCalendarContinuityError("mutation_id must be 8 to 128 safe characters.")
    return mutation


def _expected_revision(value: Any) -> str:
    revision = _text(value, 64).lower()
    if not _REVISION.fullmatch(revision):
        raise TaskCalendarContinuityError("expected_revision must be a SHA-256 value.")
    return revision


def _task_key(task_id: int) -> str:
    return f"task:{int(task_id)}"


def _calendar_key(event_id: int) -> str:
    return f"calendar_event:{int(event_id)}"


def _task_state(task_id: int) -> dict[str, Any] | None:
    return task_service.get_task(int(task_id))


def _task_revision(row: dict[str, Any]) -> str:
    body = {
        "title": _text(row.get("title"), 240),
        "description": _text(row.get("description"), 20000),
        "status": _text(row.get("status"), 40),
        "priority": _text(row.get("priority"), 40),
        "due_at": _text(row.get("due_at"), 80),
        "remind_at": _text(row.get("remind_at"), 80),
        "recurrence": _text(row.get("recurrence"), 40),
        "recurrence_interval": int(row.get("recurrence_interval") or 1),
        "contact_id": int(row.get("contact_id") or 0),
        "updated_at": _text(row.get("updated_at"), 80),
    }
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def federated_task_item(row: dict[str, Any]) -> dict[str, Any]:
    task_id = int(row.get("id") or 0)
    key = _task_key(task_id)
    envelope = federated_data.envelope(
        "homeserver", "tasks", key,
        title=_text(row.get("title"), 240),
        content=_text(row.get("description"), 6000),
        updated_at=_text(row.get("updated_at"), 80),
    )
    revision = _task_revision(row)
    observed = dict(envelope)
    observed["record_revision"] = revision
    federated_data.observe(observed, observed_source="homeserver")
    return {
        **{k: row.get(k) for k in (
            "id", "title", "description", "status", "priority", "due_at", "remind_at",
            "recurrence", "recurrence_interval", "contact_id", "contact_name",
            "created_at", "updated_at", "completed_at", "cancelled_at",
        )},
        "authority_source": "homeserver",
        "authority_key": key,
        "canonical_id": envelope["canonical_id"],
        "record_revision": revision,
        "federation_version": federated_data.FEDERATED_DATA_VERSION,
        "mirror_only": False,
        "mutation_route": "homeserver_governed",
        "allowed_mutations": ["update", "delete"],
    }


def list_federated_tasks(*, status: str | None = None, q: str = "", limit: int = 250) -> list[dict[str, Any]]:
    return [federated_task_item(row) for row in task_service.list_tasks(status=status, q=q, limit=limit)]


def _task_id_from_canonical(canonical_id_value: str) -> int:
    canonical = _text(canonical_id_value, 45)
    if not _CANONICAL_ID.fullmatch(canonical):
        raise TaskCalendarContinuityError("Task canonical identity is invalid.")
    key = federated_data.resolve_authority_key(
        canonical, authority_source="homeserver", dataset="tasks", observed_source="homeserver"
    )
    if not key or not key.startswith("task:"):
        raise TaskCalendarContinuityError("HomeServer task not found.", 404)
    try:
        task_id = int(key.split(":", 1)[1])
    except ValueError as exc:
        raise TaskCalendarContinuityError("Task canonical identity is invalid.", 409) from exc
    if federated_data.canonical_id("homeserver", "tasks", _task_key(task_id)) != canonical:
        raise TaskCalendarContinuityError("Task canonical identity does not match its authority.", 409)
    return task_id


def get_federated_task_by_canonical(canonical_id_value: str) -> dict[str, Any] | None:
    try:
        task_id = _task_id_from_canonical(canonical_id_value)
    except TaskCalendarContinuityError as exc:
        if exc.status_code == 404:
            return None
        raise
    row = _task_state(task_id)
    return federated_task_item(row) if row else None


def normalize_task_create_arguments(payload: dict[str, Any] | None, *, require_mutation: bool = True) -> dict[str, Any]:
    raw = dict(payload or {})
    allowed = {
        "mutation_id", "title", "description", "status", "priority", "due_at", "remind_at",
        "recurrence", "recurrence_interval", "contact_id",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise TaskCalendarContinuityError(f"Unsupported tasks.create argument: {sorted(unknown)[0]}")
    title = _text(raw.get("title"), 240)
    if not title:
        raise TaskCalendarContinuityError("tasks.create requires title.")
    description = str(raw.get("description") or "").strip()
    if len(description) > 20000:
        raise TaskCalendarContinuityError("Task description exceeds 20,000 characters.")
    status = _text(raw.get("status") or "pending", 40).lower()
    priority = _text(raw.get("priority") or "normal", 40).lower()
    recurrence = _text(raw.get("recurrence") or "none", 40).lower()
    if status not in _TASK_STATUSES:
        raise TaskCalendarContinuityError("Task status is invalid.")
    if priority not in _TASK_PRIORITIES:
        raise TaskCalendarContinuityError("Task priority is invalid.")
    if recurrence not in _TASK_RECURRENCES:
        raise TaskCalendarContinuityError("Task recurrence is invalid.")
    interval = int(raw.get("recurrence_interval") or 1)
    if interval < 1 or interval > 365:
        raise TaskCalendarContinuityError("Task recurrence_interval must be between 1 and 365.")
    due_at = _iso_datetime(raw.get("due_at"), "due_at")
    remind_at = _iso_datetime(raw.get("remind_at"), "remind_at")
    if recurrence != "none" and not remind_at:
        raise TaskCalendarContinuityError("Recurring tasks require remind_at.")
    contact_id = raw.get("contact_id")
    if contact_id in (None, ""):
        contact_id = None
    else:
        contact_id = int(contact_id)
        if contact_id < 1:
            raise TaskCalendarContinuityError("contact_id must be a positive integer.")
    out = {
        "title": title, "description": description, "status": status, "priority": priority,
        "due_at": due_at, "remind_at": remind_at, "recurrence": recurrence,
        "recurrence_interval": interval, "contact_id": contact_id,
    }
    if require_mutation:
        out["mutation_id"] = _mutation_id(raw.get("mutation_id"))
    elif raw.get("mutation_id"):
        out["mutation_id"] = _mutation_id(raw.get("mutation_id"))
    return out


def normalize_task_update_arguments(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    allowed = {
        "canonical_id", "mutation_id", "expected_revision", "title", "description", "status",
        "priority", "due_at", "remind_at", "recurrence", "recurrence_interval", "contact_id",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise TaskCalendarContinuityError(f"Unsupported tasks.update argument: {sorted(unknown)[0]}")
    canonical = _text(raw.get("canonical_id"), 45)
    if not _CANONICAL_ID.fullmatch(canonical):
        raise TaskCalendarContinuityError("tasks.update requires a valid canonical_id.")
    fields: dict[str, Any] = {}
    if "title" in raw:
        title = _text(raw.get("title"), 240)
        if not title:
            raise TaskCalendarContinuityError("Task title cannot be empty.")
        fields["title"] = title
    if "description" in raw:
        description = str(raw.get("description") or "").strip()
        if len(description) > 20000:
            raise TaskCalendarContinuityError("Task description exceeds 20,000 characters.")
        fields["description"] = description
    if "status" in raw:
        status = _text(raw.get("status"), 40).lower()
        if status not in _TASK_STATUSES:
            raise TaskCalendarContinuityError("Task status is invalid.")
        fields["status"] = status
    if "priority" in raw:
        priority = _text(raw.get("priority"), 40).lower()
        if priority not in _TASK_PRIORITIES:
            raise TaskCalendarContinuityError("Task priority is invalid.")
        fields["priority"] = priority
    if "due_at" in raw:
        fields["due_at"] = _iso_datetime(raw.get("due_at"), "due_at")
    if "remind_at" in raw:
        fields["remind_at"] = _iso_datetime(raw.get("remind_at"), "remind_at")
    if "recurrence" in raw:
        recurrence = _text(raw.get("recurrence"), 40).lower()
        if recurrence not in _TASK_RECURRENCES:
            raise TaskCalendarContinuityError("Task recurrence is invalid.")
        fields["recurrence"] = recurrence
    if "recurrence_interval" in raw:
        interval = int(raw.get("recurrence_interval") or 1)
        if interval < 1 or interval > 365:
            raise TaskCalendarContinuityError("Task recurrence_interval must be between 1 and 365.")
        fields["recurrence_interval"] = interval
    if "contact_id" in raw:
        contact_id = raw.get("contact_id")
        fields["contact_id"] = None if contact_id in (None, "") else int(contact_id)
        if fields["contact_id"] is not None and fields["contact_id"] < 1:
            raise TaskCalendarContinuityError("contact_id must be a positive integer.")
    if not fields:
        raise TaskCalendarContinuityError("tasks.update requires at least one mutable field.")
    return {
        "canonical_id": canonical,
        "mutation_id": _mutation_id(raw.get("mutation_id")),
        "expected_revision": _expected_revision(raw.get("expected_revision")),
        **fields,
    }


def normalize_task_delete_arguments(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    if set(raw) - {"canonical_id", "mutation_id", "expected_revision"}:
        raise TaskCalendarContinuityError("Unsupported tasks.delete argument.")
    canonical = _text(raw.get("canonical_id"), 45)
    if not _CANONICAL_ID.fullmatch(canonical):
        raise TaskCalendarContinuityError("tasks.delete requires a valid canonical_id.")
    return {
        "canonical_id": canonical,
        "mutation_id": _mutation_id(raw.get("mutation_id")),
        "expected_revision": _expected_revision(raw.get("expected_revision")),
    }


def safe_task_mutation_meta(action: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    return {
        "action": _text(action, 32),
        "canonical_id_present": bool(_text(raw.get("canonical_id"), 45)),
        "mutation_id_present": bool(_text(raw.get("mutation_id"), 128)),
        "expected_revision_present": bool(_text(raw.get("expected_revision"), 64)),
        "title_length": len(str(raw.get("title") or "")),
        "description_length": len(str(raw.get("description") or "")),
        "status": _text(raw.get("status"), 40) or None,
        "priority": _text(raw.get("priority"), 40) or None,
        "has_due_at": bool(raw.get("due_at")),
        "has_remind_at": bool(raw.get("remind_at")),
        "argument_count": len(raw),
    }


def _mutation_hash(action_key: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps({"action": action_key, "payload": payload}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _replay(table: str, source_app_key: str, mutation_id: str, action_key: str, request_hash: str) -> dict[str, Any] | None:
    if table not in {"federated_task_mutations", "federated_calendar_mutations"}:
        raise TaskCalendarContinuityError("Mutation table is invalid.", 500)
    with db() as connection:
        row = connection.execute(
            f"SELECT action_key,request_hash,result_json FROM {table} WHERE source_app_key=? AND mutation_id=? LIMIT 1",
            (source_app_key, mutation_id),
        ).fetchone()
    if row is None:
        return None
    if str(row["action_key"]) != action_key or str(row["request_hash"]) != request_hash:
        raise TaskCalendarContinuityError("mutation_id was already used with different arguments.", 409)
    result = json.loads(row["result_json"] or "{}")
    if not isinstance(result, dict):
        result = {}
    result["idempotent_replay"] = True
    return result


def _record_mutation(table: str, source_app_key: str, mutation_id: str, action_key: str, request_hash: str, canonical_id_value: str | None, result: dict[str, Any]) -> None:
    if table not in {"federated_task_mutations", "federated_calendar_mutations"}:
        raise TaskCalendarContinuityError("Mutation table is invalid.", 500)
    with db() as connection:
        connection.execute(
            f"INSERT INTO {table}(source_app_key,mutation_id,action_key,request_hash,canonical_id,result_json) VALUES (?,?,?,?,?,?)",
            (source_app_key, mutation_id, action_key, request_hash, canonical_id_value, json.dumps(result, ensure_ascii=False, separators=(",", ":"))),
        )


def create_federated_task(
    payload: dict[str, Any],
    *,
    source_app_key: str,
    created_by_type: str = "app",
) -> dict[str, Any]:
    normalized = normalize_task_create_arguments(payload)
    mutation = str(normalized.pop("mutation_id"))
    request_hash = _mutation_hash("tasks.create", normalized)
    replay = _replay("federated_task_mutations", source_app_key, mutation, "tasks.create", request_hash)
    if replay is not None:
        item = replay.get("task")
        if isinstance(item, dict):
            return item
        raise TaskCalendarContinuityError("Stored task mutation result is unavailable.", 500)
    try:
        actor = str(created_by_type or "app").strip().lower()
        if actor not in {"owner", "app", "agent", "system"}:
            actor = "app"
        created = task_service.create_task(
            normalized,
            source_app_key=source_app_key.removeprefix("app:"),
            created_by_type=actor,
        )
    except task_service.TaskError as exc:
        raise TaskCalendarContinuityError(str(exc), exc.status_code) from exc
    item = federated_task_item(created)
    _record_mutation("federated_task_mutations", source_app_key, mutation, "tasks.create", request_hash, item["canonical_id"], {"task": item})
    return item


def update_federated_task(payload: dict[str, Any], *, source_app_key: str) -> dict[str, Any]:
    normalized = normalize_task_update_arguments(payload)
    canonical = str(normalized.pop("canonical_id"))
    mutation = str(normalized.pop("mutation_id"))
    expected = str(normalized.pop("expected_revision"))
    request_hash = _mutation_hash("tasks.update", {"canonical_id": canonical, "expected_revision": expected, **normalized})
    replay = _replay("federated_task_mutations", source_app_key, mutation, "tasks.update", request_hash)
    if replay is not None:
        item = replay.get("task")
        if isinstance(item, dict):
            return item
        raise TaskCalendarContinuityError("Stored task mutation result is unavailable.", 500)
    task_id = _task_id_from_canonical(canonical)
    current = get_federated_task_by_canonical(canonical)
    if current is None:
        raise TaskCalendarContinuityError("HomeServer task not found.", 404)
    if str(current["record_revision"]) != expected:
        raise TaskCalendarContinuityError("HomeServer task changed after this edit was prepared. Refresh and try again.", 409)
    try:
        updated = task_service.update_task(task_id, normalized, source_app_key=source_app_key.removeprefix("app:"), actor_type="app")
    except task_service.TaskError as exc:
        raise TaskCalendarContinuityError(str(exc), exc.status_code) from exc
    item = federated_task_item(updated)
    _record_mutation("federated_task_mutations", source_app_key, mutation, "tasks.update", request_hash, canonical, {"task": item})
    return item


def delete_federated_task(payload: dict[str, Any], *, source_app_key: str) -> bool:
    normalized = normalize_task_delete_arguments(payload)
    canonical = str(normalized["canonical_id"])
    mutation = str(normalized["mutation_id"])
    expected = str(normalized["expected_revision"])
    request_hash = _mutation_hash("tasks.delete", normalized)
    replay = _replay("federated_task_mutations", source_app_key, mutation, "tasks.delete", request_hash)
    if replay is not None:
        return bool(replay.get("deleted"))
    task_id = _task_id_from_canonical(canonical)
    current = get_federated_task_by_canonical(canonical)
    if current is None:
        raise TaskCalendarContinuityError("HomeServer task not found.", 404)
    if str(current["record_revision"]) != expected:
        raise TaskCalendarContinuityError("HomeServer task changed after this delete was prepared. Refresh and try again.", 409)
    if not task_service.delete_task(task_id):
        return False
    federated_data.mark_tombstone("homeserver", "tasks", _task_key(task_id), observed_source="homeserver")
    _record_mutation("federated_task_mutations", source_app_key, mutation, "tasks.delete", request_hash, canonical, {"deleted": True, "canonical_id": canonical})
    return True


def _calendar_row(event_id: int) -> dict[str, Any] | None:
    with db() as connection:
        row = connection.execute("SELECT * FROM local_calendar_events WHERE id=? LIMIT 1", (int(event_id),)).fetchone()
    return dict(row) if row else None


def _calendar_revision(row: dict[str, Any]) -> str:
    body = {k: row.get(k) for k in ("title", "description", "location", "start_at", "end_at", "timezone", "all_day", "status", "updated_at")}
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def federated_calendar_item(row: dict[str, Any]) -> dict[str, Any]:
    event_id = int(row.get("id") or 0)
    key = _calendar_key(event_id)
    envelope = federated_data.envelope(
        "homeserver", "calendar", key,
        title=_text(row.get("title"), 240),
        content=_text(row.get("description"), 6000),
        updated_at=_text(row.get("updated_at"), 80),
    )
    revision = _calendar_revision(row)
    observed = dict(envelope)
    observed["record_revision"] = revision
    federated_data.observe(observed, observed_source="homeserver")
    return {
        **{k: row.get(k) for k in ("id", "title", "description", "location", "start_at", "end_at", "timezone", "all_day", "status", "created_at", "updated_at")},
        "all_day": bool(row.get("all_day")),
        "authority_source": "homeserver",
        "authority_key": key,
        "canonical_id": envelope["canonical_id"],
        "record_revision": revision,
        "federation_version": federated_data.FEDERATED_DATA_VERSION,
        "mirror_only": False,
        "mutation_route": "homeserver_governed",
        "allowed_mutations": ["update", "delete"],
    }


def list_federated_calendar(*, from_at: str | None = None, to_at: str | None = None, q: str = "", limit: int = 250) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 500))
    where = ["status='active'"]
    params: list[Any] = []
    if from_at:
        where.append("end_at>?")
        params.append(_iso_datetime(from_at, "from_at", required=True))
    if to_at:
        where.append("start_at<?")
        params.append(_iso_datetime(to_at, "to_at", required=True))
    query = _text(q, 240)
    if query:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        where.append("(title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\' OR location LIKE ? ESCAPE '\\')")
        params.extend([like, like, like])
    params.append(bounded)
    with db() as connection:
        rows = connection.execute(
            f"SELECT * FROM local_calendar_events WHERE {' AND '.join(where)} ORDER BY start_at,id LIMIT ?",
            params,
        ).fetchall()
    return [federated_calendar_item(dict(row)) for row in rows]


def _calendar_id_from_canonical(canonical_id_value: str) -> int:
    canonical = _text(canonical_id_value, 45)
    if not _CANONICAL_ID.fullmatch(canonical):
        raise TaskCalendarContinuityError("Calendar canonical identity is invalid.")
    key = federated_data.resolve_authority_key(
        canonical, authority_source="homeserver", dataset="calendar", observed_source="homeserver"
    )
    if not key or not key.startswith("calendar_event:"):
        raise TaskCalendarContinuityError("HomeServer calendar event not found.", 404)
    event_id = int(key.split(":", 1)[1])
    if federated_data.canonical_id("homeserver", "calendar", _calendar_key(event_id)) != canonical:
        raise TaskCalendarContinuityError("Calendar canonical identity does not match its authority.", 409)
    return event_id


def normalize_calendar_create_arguments(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    allowed = {"mutation_id", "title", "description", "location", "start_at", "end_at", "timezone", "all_day"}
    unknown = set(raw) - allowed
    if unknown:
        raise TaskCalendarContinuityError(f"Unsupported calendar.create argument: {sorted(unknown)[0]}")
    title = _text(raw.get("title"), 240)
    if not title:
        raise TaskCalendarContinuityError("calendar.create requires title.")
    description = str(raw.get("description") or "").strip()
    location = _text(raw.get("location"), 500)
    if len(description) > 20000:
        raise TaskCalendarContinuityError("Calendar description exceeds 20,000 characters.")
    start_at = _iso_datetime(raw.get("start_at"), "start_at", required=True)
    end_at = _iso_datetime(raw.get("end_at"), "end_at", required=True)
    if datetime.fromisoformat(str(end_at)) <= datetime.fromisoformat(str(start_at)):
        raise TaskCalendarContinuityError("Calendar end must be after start.")
    timezone_name = _text(raw.get("timezone") or "UTC", 80)
    return {
        "mutation_id": _mutation_id(raw.get("mutation_id")),
        "title": title, "description": description, "location": location,
        "start_at": start_at, "end_at": end_at, "timezone": timezone_name,
        "all_day": 1 if bool(raw.get("all_day", False)) else 0,
    }


def normalize_calendar_update_arguments(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    allowed = {"canonical_id", "mutation_id", "expected_revision", "title", "description", "location", "start_at", "end_at", "timezone", "all_day"}
    unknown = set(raw) - allowed
    if unknown:
        raise TaskCalendarContinuityError(f"Unsupported calendar.update argument: {sorted(unknown)[0]}")
    canonical = _text(raw.get("canonical_id"), 45)
    if not _CANONICAL_ID.fullmatch(canonical):
        raise TaskCalendarContinuityError("calendar.update requires a valid canonical_id.")
    fields: dict[str, Any] = {}
    if "title" in raw:
        title = _text(raw.get("title"), 240)
        if not title:
            raise TaskCalendarContinuityError("Calendar title cannot be empty.")
        fields["title"] = title
    if "description" in raw:
        description = str(raw.get("description") or "").strip()
        if len(description) > 20000:
            raise TaskCalendarContinuityError("Calendar description exceeds 20,000 characters.")
        fields["description"] = description
    if "location" in raw:
        fields["location"] = _text(raw.get("location"), 500)
    if "start_at" in raw:
        fields["start_at"] = _iso_datetime(raw.get("start_at"), "start_at", required=True)
    if "end_at" in raw:
        fields["end_at"] = _iso_datetime(raw.get("end_at"), "end_at", required=True)
    if "timezone" in raw:
        fields["timezone"] = _text(raw.get("timezone") or "UTC", 80)
    if "all_day" in raw:
        fields["all_day"] = 1 if bool(raw.get("all_day")) else 0
    if not fields:
        raise TaskCalendarContinuityError("calendar.update requires at least one mutable field.")
    return {
        "canonical_id": canonical,
        "mutation_id": _mutation_id(raw.get("mutation_id")),
        "expected_revision": _expected_revision(raw.get("expected_revision")),
        **fields,
    }


def normalize_calendar_delete_arguments(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    if set(raw) - {"canonical_id", "mutation_id", "expected_revision"}:
        raise TaskCalendarContinuityError("Unsupported calendar.delete argument.")
    canonical = _text(raw.get("canonical_id"), 45)
    if not _CANONICAL_ID.fullmatch(canonical):
        raise TaskCalendarContinuityError("calendar.delete requires a valid canonical_id.")
    return {
        "canonical_id": canonical,
        "mutation_id": _mutation_id(raw.get("mutation_id")),
        "expected_revision": _expected_revision(raw.get("expected_revision")),
    }


def safe_calendar_mutation_meta(action: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    return {
        "action": _text(action, 32),
        "canonical_id_present": bool(_text(raw.get("canonical_id"), 45)),
        "mutation_id_present": bool(_text(raw.get("mutation_id"), 128)),
        "expected_revision_present": bool(_text(raw.get("expected_revision"), 64)),
        "title_length": len(str(raw.get("title") or "")),
        "description_length": len(str(raw.get("description") or "")),
        "location_length": len(str(raw.get("location") or "")),
        "has_start": bool(raw.get("start_at")),
        "has_end": bool(raw.get("end_at")),
        "all_day": bool(raw.get("all_day", False)),
        "argument_count": len(raw),
    }


def create_federated_calendar(payload: dict[str, Any], *, source_app_key: str) -> dict[str, Any]:
    normalized = normalize_calendar_create_arguments(payload)
    mutation = str(normalized.pop("mutation_id"))
    request_hash = _mutation_hash("calendar.create", normalized)
    replay = _replay("federated_calendar_mutations", source_app_key, mutation, "calendar.create", request_hash)
    if replay is not None:
        item = replay.get("event")
        if isinstance(item, dict):
            return item
        raise TaskCalendarContinuityError("Stored calendar mutation result is unavailable.", 500)
    with db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO local_calendar_events(title,description,location,start_at,end_at,timezone,all_day,status,source_app_key)
            VALUES (?,?,?,?,?,?,?,'active',?)
            """,
            (normalized["title"], normalized["description"], normalized["location"], normalized["start_at"], normalized["end_at"], normalized["timezone"], normalized["all_day"], source_app_key.removeprefix("app:")),
        )
        event_id = int(cursor.lastrowid)
    row = _calendar_row(event_id)
    if row is None:
        raise TaskCalendarContinuityError("Created calendar event is unavailable.", 500)
    item = federated_calendar_item(row)
    _record_mutation("federated_calendar_mutations", source_app_key, mutation, "calendar.create", request_hash, item["canonical_id"], {"event": item})
    return item


def update_federated_calendar(payload: dict[str, Any], *, source_app_key: str) -> dict[str, Any]:
    normalized = normalize_calendar_update_arguments(payload)
    canonical = str(normalized.pop("canonical_id"))
    mutation = str(normalized.pop("mutation_id"))
    expected = str(normalized.pop("expected_revision"))
    request_hash = _mutation_hash("calendar.update", {"canonical_id": canonical, "expected_revision": expected, **normalized})
    replay = _replay("federated_calendar_mutations", source_app_key, mutation, "calendar.update", request_hash)
    if replay is not None:
        item = replay.get("event")
        if isinstance(item, dict):
            return item
        raise TaskCalendarContinuityError("Stored calendar mutation result is unavailable.", 500)
    event_id = _calendar_id_from_canonical(canonical)
    row = _calendar_row(event_id)
    if row is None or str(row.get("status")) != "active":
        raise TaskCalendarContinuityError("HomeServer calendar event not found.", 404)
    current = federated_calendar_item(row)
    if str(current["record_revision"]) != expected:
        raise TaskCalendarContinuityError("HomeServer calendar event changed after this edit was prepared. Refresh and try again.", 409)
    start_at = str(normalized.get("start_at") or row["start_at"])
    end_at = str(normalized.get("end_at") or row["end_at"])
    if datetime.fromisoformat(end_at) <= datetime.fromisoformat(start_at):
        raise TaskCalendarContinuityError("Calendar end must be after start.")
    assignments = []
    values: list[Any] = []
    for key in ("title", "description", "location", "start_at", "end_at", "timezone", "all_day"):
        if key in normalized:
            assignments.append(f"{key}=?")
            values.append(normalized[key])
    assignments.append("updated_at=CURRENT_TIMESTAMP")
    values.append(event_id)
    with db() as connection:
        connection.execute(f"UPDATE local_calendar_events SET {', '.join(assignments)} WHERE id=?", values)
    saved = _calendar_row(event_id)
    if saved is None:
        raise TaskCalendarContinuityError("Updated calendar event is unavailable.", 500)
    item = federated_calendar_item(saved)
    _record_mutation("federated_calendar_mutations", source_app_key, mutation, "calendar.update", request_hash, canonical, {"event": item})
    return item


def delete_federated_calendar(payload: dict[str, Any], *, source_app_key: str) -> bool:
    normalized = normalize_calendar_delete_arguments(payload)
    canonical = str(normalized["canonical_id"])
    mutation = str(normalized["mutation_id"])
    expected = str(normalized["expected_revision"])
    request_hash = _mutation_hash("calendar.delete", normalized)
    replay = _replay("federated_calendar_mutations", source_app_key, mutation, "calendar.delete", request_hash)
    if replay is not None:
        return bool(replay.get("deleted"))
    event_id = _calendar_id_from_canonical(canonical)
    row = _calendar_row(event_id)
    if row is None or str(row.get("status")) != "active":
        raise TaskCalendarContinuityError("HomeServer calendar event not found.", 404)
    current = federated_calendar_item(row)
    if str(current["record_revision"]) != expected:
        raise TaskCalendarContinuityError("HomeServer calendar event changed after this delete was prepared. Refresh and try again.", 409)
    with db() as connection:
        cursor = connection.execute("UPDATE local_calendar_events SET status='cancelled',updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='active'", (event_id,))
    if cursor.rowcount != 1:
        return False
    federated_data.mark_tombstone("homeserver", "calendar", _calendar_key(event_id), observed_source="homeserver")
    _record_mutation("federated_calendar_mutations", source_app_key, mutation, "calendar.delete", request_hash, canonical, {"deleted": True, "canonical_id": canonical})
    return True
