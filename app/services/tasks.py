from __future__ import annotations

import calendar
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from ..database import db


class TaskError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


_STATUSES = {"pending", "in_progress", "completed", "cancelled"}
_PRIORITIES = {"low", "normal", "high", "urgent"}
_RECURRENCES = {"none", "daily", "weekly", "monthly"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value is not None else None


def _parse_datetime(value: Any, label: str) -> datetime | None:
    if value is None or str(value).strip() == "":
        return None
    raw = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise TaskError(f"{label} must be an ISO-8601 date/time.") from exc
    if parsed.tzinfo is None:
        raise TaskError(f"{label} must include a timezone offset.")
    return parsed.astimezone(timezone.utc)


def _normalize_task_input(payload: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
    allowed = {
        "title", "description", "status", "priority", "due_at", "remind_at",
        "recurrence", "recurrence_interval", "contact_id"
    }
    unknown = set(payload) - allowed
    if unknown:
        raise TaskError(f"Unsupported task field: {sorted(unknown)[0]}")
    result: dict[str, Any] = {}

    if not partial or "title" in payload:
        title = str(payload.get("title") or "").strip()
        if not title:
            raise TaskError("Task title is required.")
        if len(title) > 240:
            raise TaskError("Task title exceeds 240 characters.")
        result["title"] = title

    if not partial or "description" in payload:
        description = str(payload.get("description") or "").strip()
        if len(description) > 20000:
            raise TaskError("Task description exceeds 20,000 characters.")
        result["description"] = description

    if not partial or "status" in payload:
        status = str(payload.get("status") or "pending").strip().lower()
        if status not in _STATUSES:
            raise TaskError("Task status is invalid.")
        result["status"] = status

    if not partial or "priority" in payload:
        priority = str(payload.get("priority") or "normal").strip().lower()
        if priority not in _PRIORITIES:
            raise TaskError("Task priority is invalid.")
        result["priority"] = priority

    if not partial or "due_at" in payload:
        result["due_at"] = _iso(_parse_datetime(payload.get("due_at"), "due_at"))
    if not partial or "remind_at" in payload:
        result["remind_at"] = _iso(_parse_datetime(payload.get("remind_at"), "remind_at"))

    if not partial or "recurrence" in payload:
        recurrence = str(payload.get("recurrence") or "none").strip().lower()
        if recurrence not in _RECURRENCES:
            raise TaskError("Task recurrence is invalid.")
        result["recurrence"] = recurrence

    if not partial or "recurrence_interval" in payload:
        raw_interval = payload.get("recurrence_interval", 1)
        if isinstance(raw_interval, bool):
            raise TaskError("recurrence_interval must be an integer.")
        try:
            interval = int(raw_interval)
        except (TypeError, ValueError) as exc:
            raise TaskError("recurrence_interval must be an integer.") from exc
        if interval < 1 or interval > 365:
            raise TaskError("recurrence_interval must be between 1 and 365.")
        result["recurrence_interval"] = interval

    if not partial or "contact_id" in payload:
        raw_contact = payload.get("contact_id")
        if raw_contact in (None, ""):
            result["contact_id"] = None
        else:
            if isinstance(raw_contact, bool):
                raise TaskError("contact_id must be a positive integer.")
            try:
                contact_id = int(raw_contact)
            except (TypeError, ValueError) as exc:
                raise TaskError("contact_id must be a positive integer.") from exc
            if contact_id <= 0:
                raise TaskError("contact_id must be a positive integer.")
            result["contact_id"] = contact_id

    if not partial and result["recurrence"] != "none" and not result["remind_at"]:
        raise TaskError("Recurring tasks require remind_at.")

    return result


def _contact_exists(connection, contact_id: int | None) -> bool:
    if contact_id is None:
        return True
    return connection.execute("SELECT id FROM contacts WHERE id=? LIMIT 1", (contact_id,)).fetchone() is not None


def _task_row(connection, task_id: int):
    return connection.execute(
        """
        SELECT t.*, c.display_name AS contact_name
        FROM tasks t
        LEFT JOIN contacts c ON c.id=t.contact_id
        WHERE t.id=? LIMIT 1
        """,
        (task_id,),
    ).fetchone()


def get_task(task_id: int) -> dict[str, Any] | None:
    with db() as connection:
        row = _task_row(connection, int(task_id))
    return dict(row) if row else None


def create_task(payload: dict[str, Any], *, source_app_key: str | None = None, created_by_type: str = "owner") -> dict[str, Any]:
    normalized = _normalize_task_input(payload)
    source = str(source_app_key or "").strip() or None
    actor = created_by_type if created_by_type in {"owner", "app", "agent", "system"} else "app"
    with db() as connection:
        if not _contact_exists(connection, normalized["contact_id"]):
            raise TaskError("Linked contact was not found.", 404)
        cursor = connection.execute(
            """
            INSERT INTO tasks(
                title, description, status, priority, due_at, remind_at, recurrence,
                recurrence_interval, contact_id, source_app_key, created_by_type,
                completed_at, cancelled_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized["title"], normalized["description"], normalized["status"],
                normalized["priority"], normalized["due_at"], normalized["remind_at"],
                normalized["recurrence"], normalized["recurrence_interval"], normalized["contact_id"],
                source, actor,
                _iso(_now()) if normalized["status"] == "completed" else None,
                _iso(_now()) if normalized["status"] == "cancelled" else None,
            ),
        )
        task_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES (?, ?, 'task.created', 'task', ?, '{}')
            """,
            (actor, source or "control-center", str(task_id)),
        )
        row = _task_row(connection, task_id)
    return dict(row)


def update_task(task_id: int, payload: dict[str, Any], *, source_app_key: str | None = None, actor_type: str = "owner") -> dict[str, Any]:
    if not payload:
        raise TaskError("No task fields were supplied.")
    normalized = _normalize_task_input(payload, partial=True)
    with db() as connection:
        current = _task_row(connection, int(task_id))
        if current is None:
            raise TaskError("Task not found.", 404)
        if "contact_id" in normalized and not _contact_exists(connection, normalized["contact_id"]):
            raise TaskError("Linked contact was not found.", 404)

        effective_recurrence = normalized.get("recurrence", current["recurrence"])
        effective_remind_at = normalized.get("remind_at", current["remind_at"])
        if effective_recurrence != "none" and not effective_remind_at:
            raise TaskError("Recurring tasks require remind_at.")

        assignments: list[str] = []
        values: list[Any] = []
        for key in (
            "title", "description", "status", "priority", "due_at", "remind_at",
            "recurrence", "recurrence_interval", "contact_id"
        ):
            if key in normalized:
                assignments.append(f"{key}=?")
                values.append(normalized[key])

        new_status = normalized.get("status", current["status"])
        assignments.extend(["completed_at=?", "cancelled_at=?", "updated_at=CURRENT_TIMESTAMP"])
        values.extend([
            _iso(_now()) if new_status == "completed" and current["status"] != "completed" else current["completed_at"] if new_status == "completed" else None,
            _iso(_now()) if new_status == "cancelled" and current["status"] != "cancelled" else current["cancelled_at"] if new_status == "cancelled" else None,
        ])
        values.append(int(task_id))
        connection.execute(f"UPDATE tasks SET {', '.join(assignments)} WHERE id=?", values)
        connection.execute(
            "INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json) VALUES (?, ?, 'task.updated', 'task', ?, '{}')",
            (actor_type, str(source_app_key or "control-center"), str(task_id)),
        )
        row = _task_row(connection, int(task_id))
    return dict(row)


def delete_task(task_id: int) -> bool:
    deleted = False
    with db() as connection:
        cursor = connection.execute("DELETE FROM tasks WHERE id=?", (int(task_id),))
        deleted = bool(cursor.rowcount)
        if deleted:
            connection.execute(
                "INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json) VALUES ('owner', 'control-center', 'task.deleted', 'task', ?, '{}')",
                (str(task_id),),
            )
    return deleted


def list_tasks(*, status: str | None = None, q: str = "", limit: int = 250) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 500))
    where: list[str] = []
    params: list[Any] = []
    if status:
        if status not in _STATUSES:
            raise TaskError("Task status filter is invalid.")
        where.append("t.status=?")
        params.append(status)
    query = str(q or "").strip()
    if query:
        if len(query) > 240:
            raise TaskError("Task search exceeds 240 characters.")
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        where.append("(t.title LIKE ? ESCAPE '\\' OR t.description LIKE ? ESCAPE '\\' OR c.display_name LIKE ? ESCAPE '\\')")
        params.extend([like, like, like])
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(bounded)
    with db() as connection:
        rows = connection.execute(
            f"""
            SELECT t.*, c.display_name AS contact_name
            FROM tasks t LEFT JOIN contacts c ON c.id=t.contact_id
            {clause}
            ORDER BY
                CASE t.status WHEN 'pending' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END,
                CASE WHEN t.due_at IS NULL THEN 1 ELSE 0 END,
                t.due_at ASC,
                t.id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def list_notifications(*, unread_only: bool = False, include_dismissed: bool = False, limit: int = 200) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 500))
    where: list[str] = []
    if unread_only:
        where.append("n.read_at IS NULL")
    if not include_dismissed:
        where.append("n.dismissed_at IS NULL")
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with db() as connection:
        rows = connection.execute(
            f"""
            SELECT n.id, n.source, n.title, n.body, n.level, n.read_at, n.dismissed_at,
                   n.task_id, n.created_at, t.status AS task_status, t.due_at AS task_due_at
            FROM notifications n LEFT JOIN tasks t ON t.id=n.task_id
            {clause}
            ORDER BY n.id DESC LIMIT ?
            """,
            (bounded,),
        ).fetchall()
    return [dict(row) for row in rows]


def mark_notification(notification_id: int, *, read: bool | None = None, dismissed: bool | None = None) -> dict[str, Any]:
    assignments: list[str] = []
    values: list[Any] = []
    if read is not None:
        assignments.append("read_at=?")
        values.append(_iso(_now()) if read else None)
    if dismissed is not None:
        assignments.append("dismissed_at=?")
        values.append(_iso(_now()) if dismissed else None)
    if not assignments:
        raise TaskError("No notification change was supplied.")
    values.append(int(notification_id))
    with db() as connection:
        cursor = connection.execute(f"UPDATE notifications SET {', '.join(assignments)} WHERE id=?", values)
        if cursor.rowcount == 0:
            raise TaskError("Notification not found.", 404)
        row = connection.execute(
            "SELECT id, source, title, body, level, read_at, dismissed_at, task_id, created_at FROM notifications WHERE id=?",
            (int(notification_id),),
        ).fetchone()
    return dict(row)


def _advance_month(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _next_reminder(current: datetime, recurrence: str, interval: int, now: datetime) -> datetime | None:
    if recurrence == "none":
        return None
    candidate = current
    for _ in range(10000):
        if recurrence == "daily":
            candidate += timedelta(days=interval)
        elif recurrence == "weekly":
            candidate += timedelta(weeks=interval)
        elif recurrence == "monthly":
            candidate = _advance_month(candidate, interval)
        if candidate > now:
            return candidate
    raise TaskError("Could not advance recurring reminder safely.", 500)


def run_due_reminders(*, now: datetime | None = None, limit: int = 100) -> dict[str, int]:
    current = (now or _now()).astimezone(timezone.utc)
    current_iso = _iso(current)
    bounded = max(1, min(int(limit), 500))
    fired = 0
    with db() as connection:
        rows = connection.execute(
            """
            SELECT id, title, description, priority, remind_at, recurrence, recurrence_interval
            FROM tasks
            WHERE status IN ('pending','in_progress') AND remind_at IS NOT NULL AND remind_at <= ?
            ORDER BY remind_at ASC, id ASC LIMIT ?
            """,
            (current_iso, bounded),
        ).fetchall()
        for row in rows:
            remind_at = _parse_datetime(row["remind_at"], "remind_at")
            if remind_at is None:
                continue
            next_at = _next_reminder(remind_at, row["recurrence"], int(row["recurrence_interval"]), current)
            reserved = connection.execute(
                """
                UPDATE tasks
                SET remind_at=?, last_reminded_at=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND remind_at=? AND status IN ('pending','in_progress')
                """,
                (_iso(next_at), current_iso, row["id"], row["remind_at"]),
            )
            if reserved.rowcount != 1:
                continue
            level = "warning" if row["priority"] in {"high", "urgent"} else "info"
            connection.execute(
                """
                INSERT INTO notifications(source, title, body, level, task_id)
                VALUES ('tasks', ?, ?, ?, ?)
                """,
                (f"Reminder: {row['title']}", str(row["description"] or "")[:5000], level, row["id"]),
            )
            connection.execute(
                """
                INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
                VALUES ('system', 'task-scheduler', 'task.reminded', 'task', ?, '{}')
                """,
                (str(row["id"]),),
            )
            fired += 1
    return {"fired": fired}


class TaskScheduler:
    def __init__(self, interval_seconds: float = 15.0) -> None:
        self.interval_seconds = max(1.0, float(interval_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="homeserver-task-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                run_due_reminders()
            except Exception:
                pass
            self._stop.wait(self.interval_seconds)


scheduler = TaskScheduler()
