from __future__ import annotations

import json
from typing import Any

from ..database import db
from . import agent_team_orchestration, agent_workflow_rehydration

AGENT_WORKFLOW_SUPERVISION_VERSION = "v0.57"
MAX_SUPERVISED_STEPS = 2
SAFE_ACTIONS = {"run", "prepare"}


class AgentWorkflowSupervisionError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


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


def _json_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        parsed = value
    else:
        try:
            parsed = json.loads(str(value or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = []
    return [dict(item) for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


def _clean_counts(value: Any) -> dict[str, int]:
    counts = value if isinstance(value, dict) else {}
    return {
        key: max(0, int(counts.get(key) or 0))
        for key in ("members", "queued", "working", "completed", "failed", "cancelled")
    }


def _existing_session(
    source: str,
    conversation_id: str,
    plan_id: int,
    rehydration_id: int,
) -> dict[str, Any] | None:
    with db() as connection:
        row = connection.execute(
            """
            SELECT id, source_app_key, conversation_id, plan_id, team_run_id,
                   rehydration_id, starting_state_fingerprint, final_rehydration_id,
                   final_state_fingerprint, max_steps, status, step_count,
                   steps_json, stop_boundary, result_json, created_at, updated_at
            FROM agent_workflow_supervisions
            WHERE source_app_key=? AND conversation_id=? AND plan_id=? AND rehydration_id=?
            LIMIT 1
            """,
            (source, conversation_id, int(plan_id), int(rehydration_id)),
        ).fetchone()
    return dict(row) if row is not None else None


def _stored_payload(row: dict[str, Any], *, reused: bool) -> dict[str, Any]:
    result = _json_object(row.get("result_json"))
    if result:
        result["reused"] = reused
        return result
    return {
        "version": AGENT_WORKFLOW_SUPERVISION_VERSION,
        "supervision_id": int(row["id"]),
        "reused": reused,
        "source_app_key": str(row["source_app_key"]),
        "conversation_id": str(row["conversation_id"]),
        "plan_id": int(row["plan_id"]),
        "team_run_id": int(row["team_run_id"]) if row.get("team_run_id") is not None else None,
        "starting_rehydration_id": int(row["rehydration_id"]),
        "starting_state_fingerprint": str(row["starting_state_fingerprint"]),
        "final_rehydration_id": int(row["final_rehydration_id"]) if row.get("final_rehydration_id") is not None else None,
        "final_state_fingerprint": str(row.get("final_state_fingerprint") or "") or None,
        "status": str(row.get("status") or "running"),
        "bounded": True,
        "max_steps": int(row.get("max_steps") or MAX_SUPERVISED_STEPS),
        "step_count": int(row.get("step_count") or 0),
        "actions_executed": int(row.get("step_count") or 0),
        "steps": _json_list(row.get("steps_json")),
        "stop_boundary": str(row.get("stop_boundary") or "") or None,
        "requires_explicit_start": True,
        "auto_approval": False,
        "auto_retry": False,
        "auto_parent_chat": False,
        "nested_delegation": False,
        "actions_via": agent_team_orchestration.AGENT_TEAM_ORCHESTRATION_VERSION,
        "in_progress": str(row.get("status") or "") == "running",
    }


def _rehydrate(
    source: str,
    conversation_id: str,
    plan_id: int,
    *,
    owner: bool,
    current_permissions: set[str],
) -> dict[str, Any]:
    try:
        return agent_workflow_rehydration.rehydrate_workflow(
            source,
            conversation_id,
            int(plan_id),
            owner=owner,
            current_permissions=current_permissions,
        )
    except agent_workflow_rehydration.AgentWorkflowRehydrationError as exc:
        raise AgentWorkflowSupervisionError(str(exc), exc.status_code) from exc


def _orchestration(
    source: str,
    plan_id: int,
    *,
    owner: bool,
    current_permissions: set[str],
) -> dict[str, Any]:
    try:
        return agent_team_orchestration.get_orchestration(
            int(plan_id),
            source,
            owner=owner,
            current_permissions=current_permissions,
        )
    except agent_team_orchestration.AgentTeamOrchestrationError as exc:
        raise AgentWorkflowSupervisionError(str(exc), exc.status_code) from exc


def _decision(orchestration: dict[str, Any]) -> tuple[str | None, str, str]:
    plan_status = str(orchestration.get("plan_status") or "")
    status = str(orchestration.get("status") or "")
    allowed = {str(value) for value in orchestration.get("allowed_actions") or []}
    team = orchestration.get("team_run") if isinstance(orchestration.get("team_run"), dict) else {}
    retryable = [int(value) for value in team.get("retryable_task_ids") or [] if int(value or 0) > 0]

    if plan_status == "proposed":
        return None, "plan_approval_required", "Review and explicitly approve or reject the Team Plan before supervised continuation."
    if plan_status == "rejected":
        return None, "plan_rejected", "The Team Plan was rejected; no supervised actions can run."
    if plan_status != "approved":
        return None, "plan_review_required", "The Team Plan state requires explicit review."
    if retryable:
        return None, "retry_required", "A failed specialist requires an explicit retry decision before supervised continuation."
    if status in {"queued", "partial"} and "run" in allowed:
        return "run", "", ""
    if status == "running":
        return None, "specialists_running", "Specialist work is already running; no duplicate execution was started."
    if status == "completed" and "prepare" in allowed:
        return "prepare", "", ""
    if status == "prepared":
        return None, "parent_synthesis_required", "Specialist results are prepared. Send the parent Agent an explicit message to synthesize them."
    if status == "synthesized":
        return None, "workflow_complete", "The parent Agent has already synthesized this workflow."
    if status == "cancelled":
        return None, "workflow_cancelled", "The Team Run was cancelled."
    return None, "workflow_review_required", f"Workflow status '{status or 'unknown'}' requires explicit review."


def _claim_session(
    source: str,
    conversation_id: str,
    plan_id: int,
    rehydration_id: int,
    state_fingerprint: str,
    max_steps: int,
) -> tuple[dict[str, Any], bool]:
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            """
            SELECT id, source_app_key, conversation_id, plan_id, team_run_id,
                   rehydration_id, starting_state_fingerprint, final_rehydration_id,
                   final_state_fingerprint, max_steps, status, step_count,
                   steps_json, stop_boundary, result_json, created_at, updated_at
            FROM agent_workflow_supervisions
            WHERE source_app_key=? AND conversation_id=? AND plan_id=? AND rehydration_id=?
            LIMIT 1
            """,
            (source, conversation_id, int(plan_id), int(rehydration_id)),
        ).fetchone()
        if existing is not None:
            return dict(existing), False
        checkpoint = connection.execute(
            """
            SELECT id, team_run_id, state_fingerprint, status
            FROM agent_workflow_rehydrations
            WHERE id=? AND source_app_key=? AND conversation_id=? AND plan_id=?
            LIMIT 1
            """,
            (int(rehydration_id), source, conversation_id, int(plan_id)),
        ).fetchone()
        if checkpoint is None:
            raise AgentWorkflowSupervisionError("Recovery checkpoint not found for this workflow.", 404)
        if str(checkpoint["state_fingerprint"]) != state_fingerprint:
            raise AgentWorkflowSupervisionError("Recovery checkpoint fingerprint does not match.", 409)
        if str(checkpoint["status"]) != "ready":
            raise AgentWorkflowSupervisionError("Recovery checkpoint has a conflict and cannot continue safely.", 409)
        cursor = connection.execute(
            """
            INSERT INTO agent_workflow_supervisions(
                source_app_key, conversation_id, plan_id, team_run_id, rehydration_id,
                starting_state_fingerprint, max_steps, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'running')
            """,
            (
                source,
                conversation_id,
                int(plan_id),
                int(checkpoint["team_run_id"]) if checkpoint["team_run_id"] is not None else None,
                int(rehydration_id),
                state_fingerprint,
                int(max_steps),
            ),
        )
        supervision_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES (?, ?, 'agent.workflow.supervision.started', 'agent_team_plan', ?, ?)
            """,
            (
                _actor_type(source),
                source,
                str(int(plan_id)),
                json.dumps(
                    {
                        "version": AGENT_WORKFLOW_SUPERVISION_VERSION,
                        "supervision_id": supervision_id,
                        "rehydration_id": int(rehydration_id),
                        "max_steps": int(max_steps),
                        "bounded": True,
                        "auto_approval": False,
                        "auto_retry": False,
                        "auto_parent_chat": False,
                    },
                    separators=(",", ":"),
                ),
            ),
        )
        row = connection.execute(
            "SELECT * FROM agent_workflow_supervisions WHERE id=?",
            (supervision_id,),
        ).fetchone()
    return dict(row), True


def _audit_step(source: str, plan_id: int, supervision_id: int, step: dict[str, Any]) -> None:
    with db() as connection:
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES (?, ?, 'agent.workflow.supervision.step', 'agent_team_plan', ?, ?)
            """,
            (
                _actor_type(source),
                source,
                str(int(plan_id)),
                json.dumps(
                    {
                        "version": AGENT_WORKFLOW_SUPERVISION_VERSION,
                        "supervision_id": int(supervision_id),
                        "sequence": int(step["sequence"]),
                        "action": str(step["action"]),
                        "before_status": str(step["before_status"]),
                        "after_status": str(step["after_status"]),
                        "counts": dict(step["counts"]),
                    },
                    separators=(",", ":"),
                ),
            ),
        )


def _finish(
    row: dict[str, Any],
    *,
    status: str,
    steps: list[dict[str, Any]],
    stop_boundary: str,
    stop_label: str,
    final_checkpoint: dict[str, Any] | None,
) -> dict[str, Any]:
    result = {
        "version": AGENT_WORKFLOW_SUPERVISION_VERSION,
        "supervision_id": int(row["id"]),
        "reused": False,
        "source_app_key": str(row["source_app_key"]),
        "conversation_id": str(row["conversation_id"]),
        "plan_id": int(row["plan_id"]),
        "team_run_id": int(row["team_run_id"]) if row.get("team_run_id") is not None else None,
        "starting_rehydration_id": int(row["rehydration_id"]),
        "starting_state_fingerprint": str(row["starting_state_fingerprint"]),
        "final_rehydration_id": int(final_checkpoint["rehydration_id"]) if final_checkpoint else None,
        "final_state_fingerprint": str(final_checkpoint["state_fingerprint"]) if final_checkpoint else None,
        "status": status,
        "bounded": True,
        "max_steps": int(row["max_steps"]),
        "step_count": len(steps),
        "actions_executed": len(steps),
        "steps": steps,
        "stop_boundary": stop_boundary,
        "stop_label": stop_label,
        "checkpoint": dict(final_checkpoint.get("checkpoint") or {}) if final_checkpoint else None,
        "safe_to_continue": bool(final_checkpoint.get("safe_to_continue")) if final_checkpoint else False,
        "requires_explicit_start": True,
        "auto_approval": False,
        "auto_retry": False,
        "auto_parent_chat": False,
        "nested_delegation": False,
        "actions_via": agent_team_orchestration.AGENT_TEAM_ORCHESTRATION_VERSION,
        "in_progress": False,
    }
    encoded_steps = json.dumps(steps, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    encoded_result = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        changed = connection.execute(
            """
            UPDATE agent_workflow_supervisions
            SET status=?, step_count=?, steps_json=?, stop_boundary=?,
                final_rehydration_id=?, final_state_fingerprint=?, result_json=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='running'
            """,
            (
                status,
                len(steps),
                encoded_steps,
                stop_boundary,
                int(final_checkpoint["rehydration_id"]) if final_checkpoint else None,
                str(final_checkpoint["state_fingerprint"]) if final_checkpoint else None,
                encoded_result,
                int(row["id"]),
            ),
        )
        if changed.rowcount <= 0:
            existing = connection.execute("SELECT * FROM agent_workflow_supervisions WHERE id=?", (int(row["id"]),)).fetchone()
            if existing is not None:
                return _stored_payload(dict(existing), reused=True)
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES (?, ?, 'agent.workflow.supervision.stopped', 'agent_team_plan', ?, ?)
            """,
            (
                _actor_type(str(row["source_app_key"])),
                str(row["source_app_key"]),
                str(int(row["plan_id"])),
                json.dumps(
                    {
                        "version": AGENT_WORKFLOW_SUPERVISION_VERSION,
                        "supervision_id": int(row["id"]),
                        "status": status,
                        "actions_executed": len(steps),
                        "stop_boundary": stop_boundary,
                        "final_rehydration_id": int(final_checkpoint["rehydration_id"]) if final_checkpoint else None,
                    },
                    separators=(",", ":"),
                ),
            ),
        )
    return result


def continue_workflow(
    source_app_key: str,
    conversation_id: str,
    plan_id: int,
    rehydration_id: int,
    state_fingerprint: str,
    *,
    owner: bool,
    current_permissions: set[str] | None = None,
    max_steps: int = MAX_SUPERVISED_STEPS,
) -> dict[str, Any]:
    source = _source(source_app_key)
    conversation = str(conversation_id or "").strip()
    fingerprint = str(state_fingerprint or "").strip().lower()
    bounded_steps = int(max_steps)
    if not conversation:
        raise AgentWorkflowSupervisionError("Conversation id is required.")
    if int(plan_id) < 1 or int(rehydration_id) < 1:
        raise AgentWorkflowSupervisionError("Plan id and recovery checkpoint id are required.")
    if len(fingerprint) != 64 or any(char not in "0123456789abcdef" for char in fingerprint):
        raise AgentWorkflowSupervisionError("Recovery checkpoint fingerprint is invalid.")
    if bounded_steps < 1 or bounded_steps > MAX_SUPERVISED_STEPS:
        raise AgentWorkflowSupervisionError(f"Supervised continuation allows between 1 and {MAX_SUPERVISED_STEPS} steps.")
    permissions = set(current_permissions or set())

    existing = _existing_session(source, conversation, int(plan_id), int(rehydration_id))
    if existing is not None:
        return _stored_payload(existing, reused=True)

    preflight = _rehydrate(
        source,
        conversation,
        int(plan_id),
        owner=owner,
        current_permissions=permissions,
    )
    if int(preflight["rehydration_id"]) != int(rehydration_id) or str(preflight["state_fingerprint"]) != fingerprint:
        raise AgentWorkflowSupervisionError("Workflow changed after recovery; create a fresh checkpoint before continuing.", 409)
    if not bool(preflight.get("safe_to_continue")) or str(preflight.get("status") or "") != "ready":
        raise AgentWorkflowSupervisionError("Recovery checkpoint has a conflict and cannot continue safely.", 409)

    session, claimed = _claim_session(
        source,
        conversation,
        int(plan_id),
        int(rehydration_id),
        fingerprint,
        bounded_steps,
    )
    if not claimed:
        return _stored_payload(session, reused=True)

    steps: list[dict[str, Any]] = []
    latest = preflight
    try:
        # Revalidate after the durable claim closes the recovery->execution race.
        live = _rehydrate(source, conversation, int(plan_id), owner=owner, current_permissions=permissions)
        if int(live["rehydration_id"]) != int(rehydration_id) or str(live["state_fingerprint"]) != fingerprint:
            return _finish(
                session,
                status="conflict",
                steps=steps,
                stop_boundary="checkpoint_changed",
                stop_label="Workflow state changed before the first supervised step; no action was executed.",
                final_checkpoint=live,
            )
        latest = live

        while len(steps) < bounded_steps:
            orchestration = _orchestration(source, int(plan_id), owner=owner, current_permissions=permissions)
            action, boundary, label = _decision(orchestration)
            if action is None:
                return _finish(
                    session,
                    status="stopped",
                    steps=steps,
                    stop_boundary=boundary,
                    stop_label=label,
                    final_checkpoint=latest,
                )
            if action not in SAFE_ACTIONS:
                return _finish(
                    session,
                    status="stopped",
                    steps=steps,
                    stop_boundary="unsafe_action_boundary",
                    stop_label="The next workflow action is outside the supervised continuation allow-list.",
                    final_checkpoint=latest,
                )

            # Every step gets a fresh canonical permission/access/drift check.
            checked = _rehydrate(source, conversation, int(plan_id), owner=owner, current_permissions=permissions)
            if not bool(checked.get("safe_to_continue")) or str(checked.get("status") or "") != "ready":
                return _finish(
                    session,
                    status="conflict",
                    steps=steps,
                    stop_boundary="access_or_state_conflict",
                    stop_label="Canonical access or workflow state changed; supervised continuation stopped.",
                    final_checkpoint=checked,
                )
            latest = checked
            before_status = str(orchestration.get("status") or "")
            if action == "run":
                try:
                    after = agent_team_orchestration.run_plan_team(
                        int(plan_id),
                        source,
                        owner=owner,
                        current_permissions=permissions,
                    )
                except agent_team_orchestration.AgentTeamOrchestrationError as exc:
                    raise AgentWorkflowSupervisionError(str(exc), exc.status_code) from exc
            else:
                try:
                    after = agent_team_orchestration.prepare_plan_synthesis(
                        int(plan_id),
                        source,
                        owner=owner,
                        current_permissions=permissions,
                    )
                except agent_team_orchestration.AgentTeamOrchestrationError as exc:
                    raise AgentWorkflowSupervisionError(str(exc), exc.status_code) from exc
            team = after.get("team_run") if isinstance(after.get("team_run"), dict) else {}
            step = {
                "sequence": len(steps) + 1,
                "action": action,
                "before_status": before_status,
                "after_status": str(after.get("status") or ""),
                "counts": _clean_counts(team.get("counts")),
            }
            steps.append(step)
            _audit_step(source, int(plan_id), int(session["id"]), step)

            latest = _rehydrate(source, conversation, int(plan_id), owner=owner, current_permissions=permissions)
            if not bool(latest.get("safe_to_continue")) or str(latest.get("status") or "") != "ready":
                return _finish(
                    session,
                    status="conflict",
                    steps=steps,
                    stop_boundary="access_or_state_conflict",
                    stop_label="Canonical access or workflow state changed after the supervised step; continuation stopped.",
                    final_checkpoint=latest,
                )

            post = _orchestration(source, int(plan_id), owner=owner, current_permissions=permissions)
            next_action, boundary, label = _decision(post)
            if next_action is None:
                return _finish(
                    session,
                    status="stopped",
                    steps=steps,
                    stop_boundary=boundary,
                    stop_label=label,
                    final_checkpoint=latest,
                )

        return _finish(
            session,
            status="stopped",
            steps=steps,
            stop_boundary="step_budget_exhausted",
            stop_label="The supervised step budget is exhausted. Review the current checkpoint before continuing again.",
            final_checkpoint=latest,
        )
    except AgentWorkflowSupervisionError:
        try:
            latest = _rehydrate(source, conversation, int(plan_id), owner=owner, current_permissions=permissions)
        except Exception:
            latest = None
        _finish(
            session,
            status="error",
            steps=steps,
            stop_boundary="action_failed",
            stop_label="A supervised workflow action failed; review the canonical workflow state before retrying.",
            final_checkpoint=latest,
        )
        raise
    except Exception as exc:
        try:
            latest = _rehydrate(source, conversation, int(plan_id), owner=owner, current_permissions=permissions)
        except Exception:
            latest = None
        _finish(
            session,
            status="error",
            steps=steps,
            stop_boundary="action_failed",
            stop_label="A supervised workflow action failed; review the canonical workflow state before retrying.",
            final_checkpoint=latest,
        )
        raise AgentWorkflowSupervisionError("Supervised workflow continuation failed.", 500) from exc
