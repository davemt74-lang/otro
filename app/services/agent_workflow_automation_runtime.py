from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from ..database import db
from . import agent_workflow_automation as automation
from . import agent_workflow_supervision

RUN_LEASE_SECONDS = 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _reserve_run(run_id: int, current: datetime, *, allow_stale_running: bool) -> dict[str, Any] | None:
    cutoff = current - timedelta(seconds=RUN_LEASE_SECONDS)
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT r.*, a.source_app_key, a.conversation_id, a.plan_id,
                   a.trigger_config_json, a.next_run_at, a.last_activity_id,
                   a.max_steps, a.enabled, a.last_fired_at, a.last_status,
                   a.last_result_json, a.last_error, a.created_at AS automation_created_at,
                   a.updated_at AS automation_updated_at
            FROM agent_workflow_automation_runs r
            JOIN agent_workflow_automations a ON a.id=r.automation_id
            WHERE r.id=? LIMIT 1
            """,
            (int(run_id),),
        ).fetchone()
        if row is None:
            return None
        status = str(row["status"])
        if status == "claimed":
            changed = connection.execute(
                """
                UPDATE agent_workflow_automation_runs
                SET status='running', updated_at=?
                WHERE id=? AND status='claimed'
                """,
                (_iso(current), int(run_id)),
            )
        elif status == "running" and allow_stale_running:
            changed = connection.execute(
                """
                UPDATE agent_workflow_automation_runs
                SET updated_at=?
                WHERE id=? AND status='running' AND datetime(updated_at) <= datetime(?)
                """,
                (_iso(current), int(run_id), _iso(cutoff)),
            )
        else:
            return None
        if changed.rowcount != 1:
            return None
        refreshed = connection.execute(
            "SELECT * FROM agent_workflow_automation_runs WHERE id=?",
            (int(run_id),),
        ).fetchone()
        automation_row = connection.execute(
            "SELECT * FROM agent_workflow_automations WHERE id=?",
            (int(row["automation_id"]),),
        ).fetchone()
    return {
        "run_id": int(run_id),
        "automation": dict(automation_row),
        "trigger_key": str(refreshed["trigger_key"]),
        "trigger_value": str(refreshed["trigger_value"] or ""),
        "rehydration_id": int(refreshed["rehydration_id"]) if refreshed["rehydration_id"] is not None else None,
        "state_fingerprint": str(refreshed["state_fingerprint"] or "") or None,
        "supervision_id": int(refreshed["supervision_id"]) if refreshed["supervision_id"] is not None else None,
    }


def _persist_checkpoint(run_id: int, checkpoint: dict[str, Any]) -> bool:
    rehydration_id = int(checkpoint["rehydration_id"])
    fingerprint = str(checkpoint["state_fingerprint"])
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT rehydration_id, state_fingerprint, status FROM agent_workflow_automation_runs WHERE id=? LIMIT 1",
            (int(run_id),),
        ).fetchone()
        if row is None or str(row["status"]) != "running":
            return False
        if row["rehydration_id"] is not None:
            return int(row["rehydration_id"]) == rehydration_id and str(row["state_fingerprint"] or "") == fingerprint
        changed = connection.execute(
            """
            UPDATE agent_workflow_automation_runs
            SET rehydration_id=?, state_fingerprint=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='running' AND rehydration_id IS NULL
            """,
            (rehydration_id, fingerprint, int(run_id)),
        )
        return changed.rowcount == 1


def _update_supervision(run_id: int, supervision_id: int | None) -> None:
    if supervision_id is None:
        return
    with db() as connection:
        connection.execute(
            """
            UPDATE agent_workflow_automation_runs
            SET supervision_id=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='running'
            """,
            (int(supervision_id), int(run_id)),
        )


def _execute_reserved(claim: dict[str, Any]) -> str:
    item = claim["automation"]
    source = str(item["source_app_key"])
    owner, permissions, active = automation._live_access(source)
    if not active or (not owner and "agent.chat" not in permissions):
        automation._finish_run(
            claim,
            status="conflict",
            error="The paired application is inactive or no longer has agent.chat permission.",
            disable=True,
        )
        return "conflict"

    try:
        rehydration_id = claim.get("rehydration_id")
        fingerprint = claim.get("state_fingerprint")
        if rehydration_id is None or not fingerprint:
            checkpoint = automation._rehydrate(
                source,
                str(item["conversation_id"]),
                int(item["plan_id"]),
                owner=owner,
                permissions=permissions,
            )
            if checkpoint.get("safe_to_continue") is not True or str(checkpoint.get("status") or "") != "ready":
                automation._finish_run(
                    claim,
                    status="conflict",
                    result=checkpoint,
                    error="Canonical workflow state or access changed before the trigger fired.",
                    disable=True,
                )
                return "conflict"
            if not _persist_checkpoint(int(claim["run_id"]), checkpoint):
                automation._finish_run(
                    claim,
                    status="conflict",
                    result=checkpoint,
                    error="The automation checkpoint claim changed before supervision could start.",
                    disable=True,
                )
                return "conflict"
            rehydration_id = int(checkpoint["rehydration_id"])
            fingerprint = str(checkpoint["state_fingerprint"])
            claim["rehydration_id"] = rehydration_id
            claim["state_fingerprint"] = fingerprint

        try:
            result = agent_workflow_supervision.continue_workflow(
                source,
                str(item["conversation_id"]),
                int(item["plan_id"]),
                int(rehydration_id),
                str(fingerprint),
                owner=owner,
                current_permissions=permissions,
                max_steps=int(item["max_steps"]),
            )
        except agent_workflow_supervision.AgentWorkflowSupervisionError as exc:
            raise automation.AgentWorkflowAutomationError(str(exc), exc.status_code) from exc

        _update_supervision(
            int(claim["run_id"]),
            int(result["supervision_id"]) if result.get("supervision_id") is not None else None,
        )
        if result.get("in_progress") is True or str(result.get("status") or "") == "running":
            automation._finish_run(
                claim,
                status="conflict",
                result=result,
                error="The recovered v0.57 supervision session is still in progress and requires review.",
                disable=True,
            )
            return "conflict"

        boundary = str(result.get("stop_boundary") or "")
        result_status = str(result.get("status") or "stopped")
        status = "conflict" if result_status == "conflict" else "error" if result_status == "error" else (
            "completed" if int(result.get("actions_executed") or 0) > 0 else "stopped"
        )
        automation._finish_run(
            claim,
            status=status,
            result=result,
            disable=boundary in automation.REVIEW_BOUNDARIES,
        )
        return status
    except automation.AgentWorkflowAutomationError as exc:
        automation._finish_run(claim, status="error", error=str(exc), disable=True)
        return "error"
    except Exception:
        automation._finish_run(
            claim,
            status="error",
            error="Scheduled workflow continuation failed and was disabled for review.",
            disable=True,
        )
        return "error"


def _recover_inflight(current: datetime, limit: int) -> list[dict[str, Any]]:
    cutoff = current - timedelta(seconds=RUN_LEASE_SECONDS)
    with db() as connection:
        rows = connection.execute(
            """
            SELECT id, status
            FROM agent_workflow_automation_runs
            WHERE status='claimed'
               OR (status='running' AND datetime(updated_at) <= datetime(?))
            ORDER BY CASE status WHEN 'claimed' THEN 0 ELSE 1 END, id ASC
            LIMIT ?
            """,
            (_iso(cutoff), max(1, min(int(limit), 200))),
        ).fetchall()
    claims: list[dict[str, Any]] = []
    for row in rows:
        claim = _reserve_run(
            int(row["id"]),
            current,
            allow_stale_running=str(row["status"]) == "running",
        )
        if claim is not None:
            claims.append(claim)
    return claims


def _reserve_new_claim(claim: dict[str, Any], current: datetime) -> dict[str, Any] | None:
    reserved = _reserve_run(int(claim["run_id"]), current, allow_stale_running=False)
    if reserved is None:
        return None
    reserved["automation"] = claim["automation"]
    reserved["trigger_key"] = claim["trigger_key"]
    reserved["trigger_value"] = claim["trigger_value"]
    return reserved


def run_due_automations(*, now: datetime | None = None, limit: int = 50) -> dict[str, int]:
    current = (now or _now()).astimezone(timezone.utc)
    bounded = max(1, min(int(limit), 200))
    outcomes = {"recovered": 0, "claimed": 0, "completed": 0, "stopped": 0, "conflicts": 0, "errors": 0}

    recovered = _recover_inflight(current, bounded)
    for claim in recovered:
        outcomes["recovered"] += 1
        outcome = _execute_reserved(claim)
        outcomes["completed"] += int(outcome == "completed")
        outcomes["stopped"] += int(outcome == "stopped")
        outcomes["conflicts"] += int(outcome == "conflict")
        outcomes["errors"] += int(outcome == "error")
    remaining = max(0, bounded - len(recovered))
    if remaining == 0:
        return outcomes

    with db() as connection:
        watermark = automation._current_activity_id(connection)
        time_rows = connection.execute(
            """
            SELECT id FROM agent_workflow_automations
            WHERE enabled=1 AND trigger_type IN ('once','interval')
              AND next_run_at IS NOT NULL AND datetime(next_run_at) <= datetime(?)
            ORDER BY datetime(next_run_at) ASC, id ASC LIMIT ?
            """,
            (_iso(current), remaining),
        ).fetchall()
        activity_limit = max(0, remaining - len(time_rows))
        activity_rows = connection.execute(
            """
            SELECT id FROM agent_workflow_automations
            WHERE enabled=1 AND trigger_type='activity'
            ORDER BY id ASC LIMIT ?
            """,
            (activity_limit,),
        ).fetchall() if activity_limit else []

    for row in time_rows:
        raw = automation._claim_time(int(row["id"]), current)
        if raw is None:
            continue
        claim = _reserve_new_claim(raw, current)
        if claim is None:
            continue
        outcomes["claimed"] += 1
        outcome = _execute_reserved(claim)
        outcomes["completed"] += int(outcome == "completed")
        outcomes["stopped"] += int(outcome == "stopped")
        outcomes["conflicts"] += int(outcome == "conflict")
        outcomes["errors"] += int(outcome == "error")

    for row in activity_rows:
        raw = automation._claim_activity(int(row["id"]), current, watermark)
        if raw is None:
            continue
        claim = _reserve_new_claim(raw, current)
        if claim is None:
            continue
        outcomes["claimed"] += 1
        outcome = _execute_reserved(claim)
        outcomes["completed"] += int(outcome == "completed")
        outcomes["stopped"] += int(outcome == "stopped")
        outcomes["conflicts"] += int(outcome == "conflict")
        outcomes["errors"] += int(outcome == "error")
    return outcomes


class WorkflowAutomationRuntimeScheduler:
    def __init__(self, interval_seconds: float = 15.0) -> None:
        self.interval_seconds = max(1.0, float(interval_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="homeserver-workflow-automation-scheduler",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                run_due_automations()
            except Exception:
                pass
            self._stop.wait(self.interval_seconds)


scheduler = WorkflowAutomationRuntimeScheduler()