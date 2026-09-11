from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from ..database import db
from . import agent_workflow_rehydration, agent_workflow_supervision

AGENT_WORKFLOW_AUTOMATION_VERSION = "v0.58"
MIN_INTERVAL_SECONDS = 60
MAX_INTERVAL_SECONDS = 2_592_000
MAX_LOOKAHEAD_DAYS = 366
SAFE_ACTIVITY_PREFIXES = ("task.", "knowledge.", "contact.", "event.", "app.", "cognition.")
AUTOMATION_TRIGGER_TYPES = {"once", "interval", "activity"}
REVIEW_BOUNDARIES = {
    "plan_approval_required",
    "plan_rejected",
    "plan_review_required",
    "retry_required",
    "parent_synthesis_required",
    "workflow_complete",
    "workflow_cancelled",
    "workflow_review_required",
    "unsafe_action_boundary",
    "checkpoint_changed",
    "access_or_state_conflict",
    "action_failed",
}


class AgentWorkflowAutomationError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value is not None else None


def _parse_datetime(value: Any, label: str) -> datetime:
    raw = str(value or "").strip().replace("Z", "+00:00")
    if not raw:
        raise AgentWorkflowAutomationError(f"{label} is required.")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise AgentWorkflowAutomationError(f"{label} must be an ISO-8601 date/time.") from exc
    if parsed.tzinfo is None:
        raise AgentWorkflowAutomationError(f"{label} must include a timezone offset.")
    return parsed.astimezone(timezone.utc)


def _source(value: str | None) -> str:
    normalized = str(value or "").strip()
    return normalized or "owner"


def _actor_type(source: str) -> str:
    return "owner" if source == "owner" else "app"


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _current_activity_id(connection) -> int:
    row = connection.execute("SELECT COALESCE(MAX(id), 0) AS id FROM activity_log").fetchone()
    return int(row["id"] if row else 0)


def _live_access(source: str) -> tuple[bool, set[str], bool]:
    if source == "owner":
        return True, set(), True
    if not source.startswith("app:"):
        return False, set(), False
    app_key = source.removeprefix("app:").strip()
    if not app_key:
        return False, set(), False
    with db() as connection:
        app = connection.execute(
            "SELECT id FROM paired_apps WHERE app_key=? AND status='active' LIMIT 1",
            (app_key,),
        ).fetchone()
        if app is None:
            return False, set(), False
        rows = connection.execute(
            "SELECT permission FROM app_permissions WHERE paired_app_id=? AND allowed=1 ORDER BY permission",
            (int(app["id"]),),
        ).fetchall()
    return False, {str(row["permission"]) for row in rows}, True


def _rehydrate(
    source: str,
    conversation_id: str,
    plan_id: int,
    *,
    owner: bool,
    permissions: set[str],
) -> dict[str, Any]:
    try:
        return agent_workflow_rehydration.rehydrate_workflow(
            source,
            conversation_id,
            int(plan_id),
            owner=owner,
            current_permissions=permissions,
        )
    except agent_workflow_rehydration.AgentWorkflowRehydrationError as exc:
        raise AgentWorkflowAutomationError(str(exc), exc.status_code) from exc


def _ensure_schedulable(checkpoint: dict[str, Any]) -> None:
    item = checkpoint.get("checkpoint") if isinstance(checkpoint.get("checkpoint"), dict) else {}
    counts = item.get("counts") if isinstance(item.get("counts"), dict) else {}
    plan_status = str(item.get("plan_status") or "")
    workflow_status = str(item.get("workflow_status") or "")
    if checkpoint.get("safe_to_continue") is not True or str(checkpoint.get("status") or "") != "ready":
        raise AgentWorkflowAutomationError("Workflow recovery has a conflict and cannot be automated safely.", 409)
    if plan_status != "approved":
        raise AgentWorkflowAutomationError("Only an explicitly approved Team Plan can be automated.", 409)
    if int(counts.get("failed") or 0) > 0:
        raise AgentWorkflowAutomationError("A failed specialist requires an explicit retry decision before automation.", 409)
    if workflow_status not in {"queued", "partial", "completed"}:
        raise AgentWorkflowAutomationError(
            f"Workflow status '{workflow_status or 'unknown'}' is not eligible for scheduled continuation.",
            409,
        )


def _normalize_trigger(
    trigger_type: str,
    *,
    run_at: str | None,
    every_seconds: int | None,
    activity_action: str | None,
    resource_type: str | None,
    resource_key: str | None,
    current: datetime,
) -> tuple[dict[str, Any], str | None, int]:
    kind = str(trigger_type or "").strip().lower()
    if kind not in AUTOMATION_TRIGGER_TYPES:
        raise AgentWorkflowAutomationError("trigger_type must be once, interval, or activity.")

    if kind == "once":
        scheduled = _parse_datetime(run_at, "run_at")
        if scheduled <= current:
            raise AgentWorkflowAutomationError("run_at must be in the future.")
        if scheduled > current + timedelta(days=MAX_LOOKAHEAD_DAYS):
            raise AgentWorkflowAutomationError(f"run_at cannot be more than {MAX_LOOKAHEAD_DAYS} days ahead.")
        return {"run_at": _iso(scheduled)}, _iso(scheduled), 0

    if kind == "interval":
        if isinstance(every_seconds, bool):
            raise AgentWorkflowAutomationError("every_seconds must be an integer.")
        try:
            interval = int(every_seconds or 0)
        except (TypeError, ValueError) as exc:
            raise AgentWorkflowAutomationError("every_seconds must be an integer.") from exc
        if interval < MIN_INTERVAL_SECONDS or interval > MAX_INTERVAL_SECONDS:
            raise AgentWorkflowAutomationError(
                f"every_seconds must be between {MIN_INTERVAL_SECONDS} and {MAX_INTERVAL_SECONDS}."
            )
        scheduled = _parse_datetime(run_at, "run_at") if run_at else current + timedelta(seconds=interval)
        if scheduled <= current:
            raise AgentWorkflowAutomationError("run_at must be in the future when supplied.")
        if scheduled > current + timedelta(days=MAX_LOOKAHEAD_DAYS):
            raise AgentWorkflowAutomationError(f"run_at cannot be more than {MAX_LOOKAHEAD_DAYS} days ahead.")
        return {"every_seconds": interval, "start_at": _iso(scheduled)}, _iso(scheduled), 0

    action = str(activity_action or "").strip()
    if not action or len(action) > 160:
        raise AgentWorkflowAutomationError("activity_action must be an exact activity action up to 160 characters.")
    if not action.startswith(SAFE_ACTIVITY_PREFIXES):
        raise AgentWorkflowAutomationError(
            "Activity triggers are limited to task, knowledge, contact, event, app, or cognition activity."
        )
    rtype = str(resource_type or "").strip()
    rkey = str(resource_key or "").strip()
    if len(rtype) > 120 or len(rkey) > 240:
        raise AgentWorkflowAutomationError("Activity resource filters are too long.")
    config = {"action": action}
    if rtype:
        config["resource_type"] = rtype
    if rkey:
        config["resource_key"] = rkey
    return config, None, -1


def _automation_row(connection, automation_id: int):
    return connection.execute(
        "SELECT * FROM agent_workflow_automations WHERE id=? LIMIT 1",
        (int(automation_id),),
    ).fetchone()


def _automation_payload(row: dict[str, Any], *, reused: bool = False) -> dict[str, Any]:
    return {
        "version": AGENT_WORKFLOW_AUTOMATION_VERSION,
        "automation_id": int(row["id"]),
        "reused": reused,
        "source_app_key": str(row["source_app_key"]),
        "conversation_id": str(row["conversation_id"]),
        "plan_id": int(row["plan_id"]),
        "trigger_type": str(row["trigger_type"]),
        "trigger": _json_object(row.get("trigger_config_json")),
        "next_run_at": row.get("next_run_at"),
        "last_activity_id": int(row.get("last_activity_id") or 0),
        "max_steps": int(row.get("max_steps") or agent_workflow_supervision.MAX_SUPERVISED_STEPS),
        "enabled": bool(row.get("enabled")),
        "last_fired_at": row.get("last_fired_at"),
        "last_status": str(row.get("last_status") or "idle"),
        "last_result": _json_object(row.get("last_result_json")),
        "last_error": str(row.get("last_error") or ""),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "requires_explicit_creation": True,
        "uses_supervision": agent_workflow_supervision.AGENT_WORKFLOW_SUPERVISION_VERSION,
        "auto_approval": False,
        "auto_retry": False,
        "auto_parent_chat": False,
        "nested_delegation": False,
    }


def create_automation(
    source_app_key: str,
    conversation_id: str,
    plan_id: int,
    *,
    trigger_type: str,
    run_at: str | None = None,
    every_seconds: int | None = None,
    activity_action: str | None = None,
    resource_type: str | None = None,
    resource_key: str | None = None,
    max_steps: int = agent_workflow_supervision.MAX_SUPERVISED_STEPS,
    owner: bool,
    current_permissions: set[str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    source = _source(source_app_key)
    conversation = str(conversation_id or "").strip()
    if not conversation:
        raise AgentWorkflowAutomationError("Conversation id is required.")
    if int(plan_id) < 1:
        raise AgentWorkflowAutomationError("Plan id is required.")
    bounded_steps = int(max_steps)
    if bounded_steps < 1 or bounded_steps > agent_workflow_supervision.MAX_SUPERVISED_STEPS:
        raise AgentWorkflowAutomationError(
            f"Workflow automation allows between 1 and {agent_workflow_supervision.MAX_SUPERVISED_STEPS} supervised steps."
        )
    permissions = set(current_permissions or set())
    checkpoint = _rehydrate(
        source,
        conversation,
        int(plan_id),
        owner=owner,
        permissions=permissions,
    )
    _ensure_schedulable(checkpoint)
    current = (now or _now()).astimezone(timezone.utc)
    trigger, next_run_at, activity_cursor = _normalize_trigger(
        trigger_type,
        run_at=run_at,
        every_seconds=every_seconds,
        activity_action=activity_action,
        resource_type=resource_type,
        resource_key=resource_key,
        current=current,
    )
    encoded = _canonical_json(trigger)
    with db() as connection:
        if activity_cursor < 0:
            activity_cursor = _current_activity_id(connection)
        try:
            cursor = connection.execute(
                """
                INSERT INTO agent_workflow_automations(
                    source_app_key, conversation_id, plan_id, trigger_type,
                    trigger_config_json, next_run_at, last_activity_id, max_steps
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source,
                    conversation,
                    int(plan_id),
                    str(trigger_type).strip().lower(),
                    encoded,
                    next_run_at,
                    int(activity_cursor),
                    bounded_steps,
                ),
            )
            automation_id = int(cursor.lastrowid)
            reused = False
        except sqlite3.IntegrityError:
            existing = connection.execute(
                """
                SELECT * FROM agent_workflow_automations
                WHERE source_app_key=? AND conversation_id=? AND plan_id=?
                  AND trigger_type=? AND trigger_config_json=?
                LIMIT 1
                """,
                (source, conversation, int(plan_id), str(trigger_type).strip().lower(), encoded),
            ).fetchone()
            if existing is None:
                raise
            automation_id = int(existing["id"])
            reused = True
        if not reused:
            connection.execute(
                """
                INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
                VALUES (?, ?, 'agent.workflow.automation.created', 'agent_team_plan', ?, ?)
                """,
                (
                    _actor_type(source),
                    source,
                    str(int(plan_id)),
                    _canonical_json({
                        "version": AGENT_WORKFLOW_AUTOMATION_VERSION,
                        "automation_id": automation_id,
                        "trigger_type": str(trigger_type).strip().lower(),
                        "max_steps": bounded_steps,
                    }),
                ),
            )
        row = _automation_row(connection, automation_id)
    return _automation_payload(dict(row), reused=reused)


def list_automations(
    source_app_key: str,
    *,
    conversation_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    source = _source(source_app_key)
    bounded = max(1, min(int(limit), 250))
    params: list[Any] = [source]
    where = "source_app_key=?"
    conversation = str(conversation_id or "").strip()
    if conversation:
        where += " AND conversation_id=?"
        params.append(conversation)
    params.append(bounded)
    with db() as connection:
        rows = connection.execute(
            f"SELECT * FROM agent_workflow_automations WHERE {where} ORDER BY enabled DESC, id DESC LIMIT ?",
            params,
        ).fetchall()
    return [_automation_payload(dict(row)) for row in rows]


def list_automation_runs(
    source_app_key: str,
    automation_id: int,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    source = _source(source_app_key)
    bounded = max(1, min(int(limit), 200))
    with db() as connection:
        parent = connection.execute(
            "SELECT id FROM agent_workflow_automations WHERE id=? AND source_app_key=? LIMIT 1",
            (int(automation_id), source),
        ).fetchone()
        if parent is None:
            raise AgentWorkflowAutomationError("Workflow automation not found.", 404)
        rows = connection.execute(
            """
            SELECT id, automation_id, trigger_key, trigger_type, trigger_value,
                   rehydration_id, state_fingerprint, supervision_id, status,
                   result_json, error, created_at, updated_at
            FROM agent_workflow_automation_runs
            WHERE automation_id=? ORDER BY id DESC LIMIT ?
            """,
            (int(automation_id), bounded),
        ).fetchall()
    return [
        {
            **{key: row[key] for key in row.keys() if key != "result_json"},
            "result": _json_object(row["result_json"]),
        }
        for row in rows
    ]


def set_automation_enabled(
    source_app_key: str,
    automation_id: int,
    enabled: bool,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    source = _source(source_app_key)
    current = (now or _now()).astimezone(timezone.utc)
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = _automation_row(connection, int(automation_id))
        if row is None or str(row["source_app_key"]) != source:
            raise AgentWorkflowAutomationError("Workflow automation not found.", 404)
        config = _json_object(row["trigger_config_json"])
        next_run_at = row["next_run_at"]
        last_activity_id = int(row["last_activity_id"] or 0)
        if enabled:
            if str(row["trigger_type"]) == "once" and not next_run_at:
                raise AgentWorkflowAutomationError("A completed one-time automation cannot be re-enabled; create a new schedule.", 409)
            if str(row["trigger_type"]) == "interval" and not next_run_at:
                interval = int(config.get("every_seconds") or 0)
                next_run_at = _iso(current + timedelta(seconds=interval))
            if str(row["trigger_type"]) == "activity":
                last_activity_id = _current_activity_id(connection)
        connection.execute(
            """
            UPDATE agent_workflow_automations
            SET enabled=?, next_run_at=?, last_activity_id=?,
                last_status=?, last_error='', updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                1 if enabled else 0,
                next_run_at,
                last_activity_id,
                "idle" if enabled else "disabled",
                int(automation_id),
            ),
        )
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES (?, ?, ?, 'agent_workflow_automation', ?, '{}')
            """,
            (
                _actor_type(source),
                source,
                "agent.workflow.automation.enabled" if enabled else "agent.workflow.automation.disabled",
                str(int(automation_id)),
            ),
        )
        updated = _automation_row(connection, int(automation_id))
    return _automation_payload(dict(updated))


def delete_automation(source_app_key: str, automation_id: int) -> bool:
    source = _source(source_app_key)
    with db() as connection:
        row = connection.execute(
            "SELECT plan_id FROM agent_workflow_automations WHERE id=? AND source_app_key=? LIMIT 1",
            (int(automation_id), source),
        ).fetchone()
        if row is None:
            return False
        connection.execute("DELETE FROM agent_workflow_automations WHERE id=?", (int(automation_id),))
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES (?, ?, 'agent.workflow.automation.deleted', 'agent_team_plan', ?, ?)
            """,
            (
                _actor_type(source),
                source,
                str(int(row["plan_id"])),
                _canonical_json({"version": AGENT_WORKFLOW_AUTOMATION_VERSION, "automation_id": int(automation_id)}),
            ),
        )
    return True


def _advance_interval(due: datetime, interval_seconds: int, current: datetime) -> datetime:
    if due > current:
        return due
    elapsed = max(0.0, (current - due).total_seconds())
    jumps = int(elapsed // interval_seconds) + 1
    return due + timedelta(seconds=interval_seconds * jumps)


def _claim_time(automation_id: int, current: datetime) -> dict[str, Any] | None:
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = _automation_row(connection, int(automation_id))
        if row is None or not bool(row["enabled"]) or str(row["trigger_type"]) not in {"once", "interval"}:
            return None
        if not row["next_run_at"]:
            return None
        due = _parse_datetime(row["next_run_at"], "next_run_at")
        if due > current:
            return None
        config = _json_object(row["trigger_config_json"])
        trigger_key = f"time:{_iso(due)}"
        try:
            cursor = connection.execute(
                """
                INSERT INTO agent_workflow_automation_runs(
                    automation_id, trigger_key, trigger_type, trigger_value, status
                ) VALUES (?, ?, ?, ?, 'claimed')
                """,
                (int(row["id"]), trigger_key, str(row["trigger_type"]), _iso(due)),
            )
        except sqlite3.IntegrityError:
            return None
        run_id = int(cursor.lastrowid)
        if str(row["trigger_type"]) == "once":
            next_run_at = None
            enabled = 0
        else:
            interval = int(config.get("every_seconds") or 0)
            if interval < MIN_INTERVAL_SECONDS:
                raise AgentWorkflowAutomationError("Stored automation interval is invalid.", 500)
            next_run_at = _iso(_advance_interval(due, interval, current))
            enabled = 1
        connection.execute(
            """
            UPDATE agent_workflow_automations
            SET enabled=?, next_run_at=?, last_fired_at=?, last_status='claimed',
                last_error='', updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (enabled, next_run_at, _iso(current), int(row["id"])),
        )
        updated = _automation_row(connection, int(row["id"]))
    return {"run_id": run_id, "automation": dict(updated), "trigger_key": trigger_key, "trigger_value": _iso(due)}


def _claim_activity(automation_id: int, current: datetime, watermark: int) -> dict[str, Any] | None:
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = _automation_row(connection, int(automation_id))
        if row is None or not bool(row["enabled"]) or str(row["trigger_type"]) != "activity":
            return None
        config = _json_object(row["trigger_config_json"])
        action = str(config.get("action") or "")
        params: list[Any] = [int(row["last_activity_id"] or 0), int(watermark), action]
        where = "id > ? AND id <= ? AND action=?"
        if config.get("resource_type"):
            where += " AND resource_type=?"
            params.append(str(config["resource_type"]))
        if config.get("resource_key"):
            where += " AND resource_key=?"
            params.append(str(config["resource_key"]))
        event = connection.execute(
            f"SELECT id, action, resource_type, resource_key, created_at FROM activity_log WHERE {where} ORDER BY id ASC LIMIT 1",
            params,
        ).fetchone()
        if event is None:
            if watermark > int(row["last_activity_id"] or 0):
                connection.execute(
                    "UPDATE agent_workflow_automations SET last_activity_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (int(watermark), int(row["id"])),
                )
            return None
        trigger_key = f"activity:{int(event['id'])}"
        trigger_value = _canonical_json({
            "activity_id": int(event["id"]),
            "action": str(event["action"]),
            "resource_type": event["resource_type"],
            "resource_key": event["resource_key"],
        })
        try:
            cursor = connection.execute(
                """
                INSERT INTO agent_workflow_automation_runs(
                    automation_id, trigger_key, trigger_type, trigger_value, status
                ) VALUES (?, ?, 'activity', ?, 'claimed')
                """,
                (int(row["id"]), trigger_key, trigger_value),
            )
        except sqlite3.IntegrityError:
            connection.execute(
                "UPDATE agent_workflow_automations SET last_activity_id=? WHERE id=?",
                (int(event["id"]), int(row["id"])),
            )
            return None
        run_id = int(cursor.lastrowid)
        connection.execute(
            """
            UPDATE agent_workflow_automations
            SET last_activity_id=?, last_fired_at=?, last_status='claimed',
                last_error='', updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (int(event["id"]), _iso(current), int(row["id"])),
        )
        updated = _automation_row(connection, int(row["id"]))
    return {"run_id": run_id, "automation": dict(updated), "trigger_key": trigger_key, "trigger_value": trigger_value}


def _notification(automation: dict[str, Any], status: str, label: str) -> None:
    title = "Workflow automation ran" if status in {"completed", "stopped"} else "Workflow automation needs review"
    with db() as connection:
        connection.execute(
            """
            INSERT INTO notifications(source, title, body, level)
            VALUES ('workflow-automation', ?, ?, ?)
            """,
            (
                title,
                f"Plan {int(automation['plan_id'])}: {label}"[:5000],
                "info" if status in {"completed", "stopped"} else "warning",
            ),
        )


def _finish_run(
    claim: dict[str, Any],
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error: str = "",
    disable: bool = False,
) -> None:
    automation = claim["automation"]
    encoded = _canonical_json(result or {})
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE agent_workflow_automation_runs
            SET status=?, result_json=?, error=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND status IN ('claimed','running')
            """,
            (status, encoded, str(error or "")[:5000], int(claim["run_id"])),
        )
        connection.execute(
            """
            UPDATE agent_workflow_automations
            SET enabled=CASE WHEN ? THEN 0 ELSE enabled END,
                last_status=?, last_result_json=?, last_error=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (1 if disable else 0, status, encoded, str(error or "")[:5000], int(automation["id"])),
        )
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES ('system', 'workflow-automation-scheduler', 'agent.workflow.automation.finished', 'agent_workflow_automation', ?, ?)
            """,
            (
                str(int(automation["id"])),
                _canonical_json({
                    "version": AGENT_WORKFLOW_AUTOMATION_VERSION,
                    "run_id": int(claim["run_id"]),
                    "status": status,
                    "trigger_key": str(claim["trigger_key"]),
                    "disabled": bool(disable),
                }),
            ),
        )
    label = str((result or {}).get("stop_label") or error or "Scheduled continuation finished.")
    _notification(automation, status, label)


def _execute_claim(claim: dict[str, Any]) -> str:
    automation = claim["automation"]
    source = str(automation["source_app_key"])
    owner, permissions, active = _live_access(source)
    if not active or (not owner and "agent.chat" not in permissions):
        _finish_run(
            claim,
            status="conflict",
            error="The paired application is inactive or no longer has agent.chat permission.",
            disable=True,
        )
        return "conflict"
    with db() as connection:
        connection.execute(
            "UPDATE agent_workflow_automation_runs SET status='running', updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='claimed'",
            (int(claim["run_id"]),),
        )
    try:
        checkpoint = _rehydrate(
            source,
            str(automation["conversation_id"]),
            int(automation["plan_id"]),
            owner=owner,
            permissions=permissions,
        )
        if checkpoint.get("safe_to_continue") is not True or str(checkpoint.get("status") or "") != "ready":
            _finish_run(
                claim,
                status="conflict",
                result=checkpoint,
                error="Canonical workflow state or access changed before the trigger fired.",
                disable=True,
            )
            return "conflict"
        try:
            result = agent_workflow_supervision.continue_workflow(
                source,
                str(automation["conversation_id"]),
                int(automation["plan_id"]),
                int(checkpoint["rehydration_id"]),
                str(checkpoint["state_fingerprint"]),
                owner=owner,
                current_permissions=permissions,
                max_steps=int(automation["max_steps"]),
            )
        except agent_workflow_supervision.AgentWorkflowSupervisionError as exc:
            raise AgentWorkflowAutomationError(str(exc), exc.status_code) from exc
        boundary = str(result.get("stop_boundary") or "")
        result_status = str(result.get("status") or "stopped")
        status = "conflict" if result_status == "conflict" else "error" if result_status == "error" else (
            "completed" if int(result.get("actions_executed") or 0) > 0 else "stopped"
        )
        disable = boundary in REVIEW_BOUNDARIES
        with db() as connection:
            connection.execute(
                """
                UPDATE agent_workflow_automation_runs
                SET rehydration_id=?, state_fingerprint=?, supervision_id=?
                WHERE id=?
                """,
                (
                    int(checkpoint["rehydration_id"]),
                    str(checkpoint["state_fingerprint"]),
                    int(result["supervision_id"]) if result.get("supervision_id") is not None else None,
                    int(claim["run_id"]),
                ),
            )
        _finish_run(claim, status=status, result=result, disable=disable)
        return status
    except AgentWorkflowAutomationError as exc:
        _finish_run(claim, status="error", error=str(exc), disable=True)
        return "error"
    except Exception:
        _finish_run(
            claim,
            status="error",
            error="Scheduled workflow continuation failed and was disabled for review.",
            disable=True,
        )
        return "error"


def run_due_automations(*, now: datetime | None = None, limit: int = 50) -> dict[str, int]:
    current = (now or _now()).astimezone(timezone.utc)
    bounded = max(1, min(int(limit), 200))
    current_iso = _iso(current)
    with db() as connection:
        watermark = _current_activity_id(connection)
        time_rows = connection.execute(
            """
            SELECT id FROM agent_workflow_automations
            WHERE enabled=1 AND trigger_type IN ('once','interval')
              AND next_run_at IS NOT NULL AND datetime(next_run_at) <= datetime(?)
            ORDER BY datetime(next_run_at) ASC, id ASC LIMIT ?
            """,
            (current_iso, bounded),
        ).fetchall()
        remaining = max(0, bounded - len(time_rows))
        activity_rows = connection.execute(
            """
            SELECT id FROM agent_workflow_automations
            WHERE enabled=1 AND trigger_type='activity'
            ORDER BY id ASC LIMIT ?
            """,
            (remaining,),
        ).fetchall() if remaining else []

    claimed = 0
    completed = 0
    stopped = 0
    conflicts = 0
    errors = 0
    for row in time_rows:
        claim = _claim_time(int(row["id"]), current)
        if claim is None:
            continue
        claimed += 1
        outcome = _execute_claim(claim)
        completed += int(outcome == "completed")
        stopped += int(outcome == "stopped")
        conflicts += int(outcome == "conflict")
        errors += int(outcome == "error")
    for row in activity_rows:
        claim = _claim_activity(int(row["id"]), current, watermark)
        if claim is None:
            continue
        claimed += 1
        outcome = _execute_claim(claim)
        completed += int(outcome == "completed")
        stopped += int(outcome == "stopped")
        conflicts += int(outcome == "conflict")
        errors += int(outcome == "error")
    return {
        "claimed": claimed,
        "completed": completed,
        "stopped": stopped,
        "conflicts": conflicts,
        "errors": errors,
    }


class WorkflowAutomationScheduler:
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


scheduler = WorkflowAutomationScheduler()