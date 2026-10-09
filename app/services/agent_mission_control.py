"""Mission A2: reviewed pause/resume, bounded repair, and paged event inspection.

This additive controller never invents new authority. Worker tool execution
continues to be disabled; interrupted work is never replayed automatically.
"""
from __future__ import annotations

import json

from ..database import db
from . import agent_mission_runtime as mission

MAX_ATTEMPTS = 3


def events(source: str, mission_id: str, *, after: int = 0, limit: int = 50) -> dict:
    mission.get_mission(source, mission_id)
    if after < 0 or limit < 1 or limit > 100:
        raise mission.MissionError("Invalid event cursor or page size.", 422)
    with db() as conn:
        rows = conn.execute(
            "SELECT id,task_id,kind,metadata_json,created_at "
            "FROM agent_mission_events_v1 WHERE mission_id=? AND id>? ORDER BY id ASC LIMIT ?",
            (mission_id, after, limit + 1),
        ).fetchall()
    page = rows[:limit]
    return {
        "mission_id": mission_id,
        "items": [{
            "id": int(row["id"]), "task_id": row["task_id"], "kind": row["kind"],
            "created_at": row["created_at"], "metadata": json.loads(row["metadata_json"]),
        } for row in page],
        "next_cursor": int(page[-1]["id"]) if page else after,
        "has_more": len(rows) > limit,
    }


def pause(source: str, mission_id: str) -> dict:
    mission.get_mission(source, mission_id)
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        status = conn.execute(
            "SELECT status FROM agent_missions_v1 WHERE id=? AND source_app_key=?",
            (mission_id, source),
        ).fetchone()
        if status is None:
            raise mission.MissionError("Mission not found.", 404)
        if status["status"] == "waiting_review":
            return mission.get_mission(source, mission_id)
        if status["status"] != "running":
            raise mission.MissionError("Only a running mission can be paused.", 409)
        conn.execute(
            "UPDATE agent_missions_v1 SET status='waiting_review',updated_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND source_app_key=? AND status='running'", (mission_id, source),
        )
        rows = conn.execute(
            "SELECT id,worker_id FROM agent_mission_tasks_v1 WHERE mission_id=? AND status='running'",
            (mission_id,),
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE agent_mission_tasks_v1 SET status='interrupted',lease_id=NULL,"
                "updated_at=CURRENT_TIMESTAMP WHERE id=?", (row["id"],),
            )
            conn.execute(
                "UPDATE agent_mission_workers_v1 SET status='interrupted' WHERE id=?",
                (row["worker_id"],),
            )
            mission._event(conn, mission_id, "task.interrupted", str(row["id"]),
                           {"reason": "owner_paused"})
        mission._event(conn, mission_id, "mission.paused", detail={
            "interrupted": len(rows), "requires_explicit_resume": True,
        })
    # Workers already inside model inference may finish their external model call,
    # but their now-revoked lease prevents a late result from being committed.
    return mission.get_mission(source, mission_id)


def _reset(conn, mission_id: str, allowed: set[str], *, failed_only: bool = False) -> int:
    rows = conn.execute(
        "SELECT id,worker_id,status,attempt,error FROM agent_mission_tasks_v1 "
        "WHERE mission_id=? ORDER BY position", (mission_id,),
    ).fetchall()
    count = 0
    for row in rows:
        if row["status"] not in allowed:
            continue
        if failed_only and str(row["error"] or "") == "Dependency failed":
            # Dependents are reconsidered separately after their parent is reset.
            continue
        if int(row["attempt"]) >= MAX_ATTEMPTS:
            raise mission.MissionError("Task retry limit reached; inspect the mission.", 409)
        conn.execute(
            "UPDATE agent_mission_tasks_v1 SET status='queued',error=NULL,lease_id=NULL,"
            "result='',started_at=NULL,completed_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (row["id"],),
        )
        conn.execute(
            "UPDATE agent_mission_workers_v1 SET status='ready' WHERE id=?",
            (row["worker_id"],),
        )
        mission._event(conn, mission_id, "task.requeued", str(row["id"]))
        count += 1
    return count


def _reset_blocked(conn, mission_id: str) -> int:
    """Revisit automatically blocked descendants; successful tasks stay immutable."""
    rows = conn.execute(
        "SELECT id,worker_id,depends_on_json FROM agent_mission_tasks_v1 "
        "WHERE mission_id=? AND status='failed' AND error='Dependency failed' ORDER BY position",
        (mission_id,),
    ).fetchall()
    count = 0
    for row in rows:
        deps = json.loads(row["depends_on_json"])
        statuses = [conn.execute(
            "SELECT status FROM agent_mission_tasks_v1 WHERE id=? AND mission_id=?",
            (dep, mission_id),
        ).fetchone() for dep in deps]
        if all(p and p["status"] in ("queued", "running", "completed") for p in statuses):
            conn.execute(
                "UPDATE agent_mission_tasks_v1 SET status='queued',error=NULL,lease_id=NULL,"
                "updated_at=CURRENT_TIMESTAMP WHERE id=?", (row["id"],),
            )
            conn.execute("UPDATE agent_mission_workers_v1 SET status='ready' WHERE id=?",
                         (row["worker_id"],))
            mission._event(conn, mission_id, "task.unblocked", str(row["id"]))
            count += 1
    return count


def resume(source: str, mission_id: str, *, allow_reexecution: bool = False) -> dict:
    from . import agent_mission_orchestration as orchestration
    if orchestration.assigned(mission_id):
        orchestration.check(source, mission_id)
    current = mission.get_mission(source, mission_id)
    if current["status"] != "waiting_review":
        raise mission.MissionError("Only a review-waiting mission can be resumed.", 409)
    interrupted = [t for t in current["tasks"] if t["status"] == "interrupted"]
    if interrupted and not allow_reexecution:
        raise mission.MissionError(
            "Interrupted model calls require explicit reexecution approval.", 409,
        )
    mission._route(source, str(current["conversation_id"]))
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        changed = conn.execute(
            "UPDATE agent_missions_v1 SET status='running',updated_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND source_app_key=? AND status='waiting_review'",
            (mission_id, source),
        )
        if changed.rowcount != 1:
            raise mission.MissionError("Mission state changed during review.", 409)
        count = _reset(conn, mission_id, {"interrupted"})
        count += _reset_blocked(conn, mission_id)
        mission._event(conn, mission_id, "mission.resumed", detail={"requeued": count})
    mission._dispatch(mission_id)
    return mission.get_mission(source, mission_id)


def retry(source: str, mission_id: str, task_id: str) -> dict:
    from . import agent_mission_orchestration as orchestration
    if orchestration.assigned(mission_id):
        orchestration.check(source, mission_id)
    current = mission.get_mission(source, mission_id)
    if current["status"] not in ("failed", "partial", "waiting_review"):
        raise mission.MissionError("Only a stopped mission can be repaired.", 409)
    selected = next((t for t in current["tasks"] if t["id"] == task_id), None)
    if selected is None:
        raise mission.MissionError("Task not found in the mission.", 404)
    if selected["status"] not in ("failed", "interrupted"):
        raise mission.MissionError("Only failed or interrupted work can be retried.", 409)
    if selected["status"] == "failed" and selected["error"] == "Dependency failed":
        raise mission.MissionError("Retry the failing prerequisite task first.", 409)
    if int(selected["attempt"]) >= MAX_ATTEMPTS:
        raise mission.MissionError("Task retry limit reached.", 409)
    mission._route(source, str(current["conversation_id"]))
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        status = conn.execute(
            "SELECT status FROM agent_missions_v1 WHERE id=? AND source_app_key=?",
            (mission_id, source),
        ).fetchone()
        if status is None or status["status"] != current["status"]:
            raise mission.MissionError("Mission state changed during retry.", 409)
        row = conn.execute(
            "SELECT status,attempt,error,worker_id FROM agent_mission_tasks_v1 "
            "WHERE id=? AND mission_id=?", (task_id, mission_id),
        ).fetchone()
        if (row is None or row["status"] != selected["status"] or
            int(row["attempt"]) >= MAX_ATTEMPTS):
            raise mission.MissionError("Task state changed during retry.", 409)
        conn.execute(
            "UPDATE agent_mission_tasks_v1 SET status='queued',error=NULL,lease_id=NULL,"
            "result='',started_at=NULL,completed_at=NULL,updated_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND mission_id=?", (task_id, mission_id),
        )
        conn.execute(
            "UPDATE agent_mission_workers_v1 SET status='ready' WHERE id=?",
            (row["worker_id"],),
        )
        descendants = _reset_blocked(conn, mission_id)
        conn.execute(
            "UPDATE agent_missions_v1 SET status='running',result='',completed_at=NULL,"
            "updated_at=CURRENT_TIMESTAMP WHERE id=? AND source_app_key=?",
            (mission_id, source),
        )
        mission._event(conn, mission_id, "mission.retry_authorized", task_id,
                       {"reopened_dependents": descendants})
    mission._dispatch(mission_id)
    return mission.get_mission(source, mission_id)
