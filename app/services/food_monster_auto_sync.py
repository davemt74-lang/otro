from __future__ import annotations

"""KB5C: owner-opt-in, durable Food Monster synchronization.

No cloud dependency and no independent agent. The schedule/lease lives in
HomeServer SQLite, not in chat prompts or the browser. Only this HomeServer
makes outbound approved HTTPS reads.
"""
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ..database import db
from . import food_monster_connector as connector

INTERVALS = frozenset({3600, 21600, 86400})
LEASE_SECONDS = 3600
TICK_SECONDS = 60
MAX_PER_TICK = 4


def _now(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("Auto-sync time must be timezone aware.")
    return value.astimezone(timezone.utc).replace(microsecond=0)


def _stamp(value: datetime) -> str:
    return _now(value).isoformat()


def _row(local_id: str) -> dict[str, Any] | None:
    with db() as connection:
        row = connection.execute(
            "SELECT p.*, c.status AS connection_status FROM food_monster_sync_policy p "
            "JOIN food_monster_connections c ON c.id=p.connection_id WHERE p.connection_id=?",
            (local_id,),
        ).fetchone()
    return dict(row) if row else None


def get_policy(local_id: str) -> dict[str, Any]:
    if not connector._ID.fullmatch(local_id):
        raise connector.FoodMonsterLinkError("Invalid connection identifier.")
    with db() as connection:
        record = connection.execute("SELECT status FROM food_monster_connections WHERE id=?",
                                    (local_id,)).fetchone()
    if not record:
        raise connector.FoodMonsterLinkError("Food Monster connection not found.", 404)
    row = _row(local_id)
    return {
        "connectionId": local_id,
        "enabled": bool(row["enabled"]) if row else False,
        "intervalSeconds": int(row["interval_seconds"]) if row else 21600,
        "nextDueAt": row["next_due_at"] if row else None,
        "inProgress": bool(row and row["lease_id"] and row["lease_until"] and
                           row["lease_until"] > _stamp(_now())),
        "lastRunAt": row["last_run_at"] if row else None,
        "lastSuccessAt": row["last_success_at"] if row else None,
        "lastOutcome": row["last_outcome"] if row else "",
        "consecutiveFailures": int(row["failure_count"]) if row else 0,
        "connectionStatus": str(record["status"]),
    }


def set_policy(local_id: str, enabled: bool, interval_seconds: int, *,
               now: datetime | None = None) -> dict[str, Any]:
    if (not connector._ID.fullmatch(local_id) or type(enabled) is not bool
        or type(interval_seconds) is not int or interval_seconds not in INTERVALS):
        raise connector.FoodMonsterLinkError("Invalid auto-sync settings.")
    clock = _now(now)
    # Immediate eligibility on opt-in or changed cadence, never on ordinary
    # health/status reads. Disabling invalidates any running generation.
    with db() as connection:
        source = connection.execute(
            "SELECT status FROM food_monster_connections WHERE id=?", (local_id,)
        ).fetchone()
        if source is None:
            raise connector.FoodMonsterLinkError("Connection not found.", 404)
        if source["status"] != "active" and enabled:
            raise connector.FoodMonsterLinkError("Cannot schedule a disconnected source.", 403)
        previous = connection.execute(
            "SELECT enabled,interval_seconds FROM food_monster_sync_policy WHERE connection_id=?",
            (local_id,),
        ).fetchone()
        if (previous is not None and bool(previous["enabled"]) == enabled
            and int(previous["interval_seconds"]) == interval_seconds):
            return get_policy(local_id)
        connection.execute(
            """INSERT INTO food_monster_sync_policy
             (connection_id,enabled,interval_seconds,next_due_at,updated_at)
             VALUES (?,?,?,?,?)
             ON CONFLICT(connection_id) DO UPDATE SET
              enabled=excluded.enabled,interval_seconds=excluded.interval_seconds,
              next_due_at=excluded.next_due_at,lease_id=NULL,lease_until=NULL,
              generation=generation+1,updated_at=excluded.updated_at,
              last_outcome=CASE WHEN excluded.enabled=0 THEN 'disabled' ELSE 'scheduled' END""",
            (local_id, int(enabled), interval_seconds, _stamp(clock) if enabled else None,
             _stamp(clock)),
        )
    return get_policy(local_id)


def _claim(local_id: str, *, now: datetime | None = None) -> str | None:
    clock = _now(now)
    stamp = _stamp(clock)
    lease = uuid.uuid4().hex
    with db() as connection:
        updated = connection.execute(
            """UPDATE food_monster_sync_policy SET lease_id=?,lease_until=?,
                  last_run_at=?,generation=generation+1,last_outcome='running'
               WHERE connection_id=? AND enabled=1 AND next_due_at<=?
                 AND (lease_until IS NULL OR lease_until<=?)
                 AND EXISTS (SELECT 1 FROM food_monster_connections c
                            WHERE c.id=connection_id AND c.status='active')""",
            (lease,_stamp(clock+timedelta(seconds=LEASE_SECONDS)),stamp,local_id,stamp,stamp),
        )
        return lease if updated.rowcount == 1 else None


def assert_lease(local_id: str, lease_id: str, *, connection=None) -> None:
    """Called between pages AND before mutation inside the target transaction."""
    if connection is None:
        with db() as c:
            return assert_lease(local_id, lease_id, connection=c)
    row = connection.execute(
        "SELECT p.lease_id,p.lease_until,p.enabled,c.status "
        "FROM food_monster_sync_policy p JOIN food_monster_connections c "
        "ON c.id=p.connection_id WHERE p.connection_id=?",
        (local_id,),
    ).fetchone()
    if (not row or row["enabled"] != 1 or row["status"] != "active"
        or row["lease_id"] != lease_id or not row["lease_until"]
        or row["lease_until"] <= _stamp(_now())):
        from .food_monster_sync import FoodMonsterSyncCancelled
        raise FoodMonsterSyncCancelled("Automatic sync stopped or lease expired.", 409)


def _finish(local_id: str, lease_id: str, success: bool, *,
            now: datetime | None = None) -> None:
    clock = _now(now)
    with db() as connection:
        row = connection.execute(
            "SELECT interval_seconds,failure_count FROM food_monster_sync_policy "
            "WHERE connection_id=? AND lease_id=? AND enabled=1",
            (local_id,lease_id),
        ).fetchone()
        if not row:
            return  # Disabled or reconfigured; never resurrect an old lease.
        failures = 0 if success else int(row["failure_count"]) + 1
        # A failed attempt backs off rather than retrying continuously. Bounded
        # by the configured cadence and a minimum of five minutes.
        delay = int(row["interval_seconds"]) if success else min(
            int(row["interval_seconds"]), 300 * (2 ** min(failures - 1, 7))
        )
        connection.execute(
            """UPDATE food_monster_sync_policy SET lease_id=NULL,lease_until=NULL,
               next_due_at=?,failure_count=?,
               last_success_at=CASE WHEN ? THEN ? ELSE last_success_at END,
               last_outcome=? WHERE connection_id=? AND lease_id=?""",
            (_stamp(clock+timedelta(seconds=delay)),failures,int(success),
             _stamp(clock),"synced" if success else "retry_backoff",local_id,lease_id),
        )


def run_due(*, now: datetime | None = None,
            fetch: Callable[..., dict[str, Any]] | None = None,
            max_jobs: int = MAX_PER_TICK) -> dict[str, int]:
    """One bounded pass; safe to invoke after restart or from any worker."""
    clock = _now(now)
    if not 1 <= max_jobs <= MAX_PER_TICK:
        raise ValueError("Invalid background sync batch limit.")
    with db() as connection:
        rows = connection.execute(
            """SELECT p.connection_id FROM food_monster_sync_policy p
               JOIN food_monster_connections c ON c.id=p.connection_id
               WHERE p.enabled=1 AND c.status='active' AND p.next_due_at<=?
                 AND (p.lease_until IS NULL OR p.lease_until<=?)
               ORDER BY p.next_due_at,p.connection_id LIMIT ?""",
            (_stamp(clock),_stamp(clock),max_jobs),
        ).fetchall()
    outcome = {"claimed": 0, "succeeded": 0, "failed": 0}
    from . import food_monster_sync
    for record in rows:
        cid = str(record["connection_id"])
        lease = _claim(cid, now=clock)
        if not lease:
            continue
        outcome["claimed"] += 1
        success = False
        try:
            food_monster_sync.synchronize(cid, fetch=fetch, auto_lease=lease)
            success = True
            outcome["succeeded"] += 1
        except connector.FoodMonsterLinkError:
            outcome["failed"] += 1
        except Exception:
            # Never log tokens, returned recipe data or raw exception bodies.
            outcome["failed"] += 1
        finally:
            _finish(cid,lease,success,now=_now())
    return outcome


class FoodMonsterAutoScheduler:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="homeserver-food-monster-sync", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=8)
        # The lease prevents a second worker if the first has not finished.
        if not self._thread or not self._thread.is_alive():
            self._thread = None

    def _run(self) -> None:
        if self._stop.wait(5):
            return
        while not self._stop.is_set():
            try:
                run_due()
            except Exception:
                pass
            self._stop.wait(TICK_SECONDS)


scheduler = FoodMonsterAutoScheduler()
