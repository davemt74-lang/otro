from __future__ import annotations

import hashlib
import json
from typing import Any

from ..database import db
from . import agent_routing, agent_team_orchestration, agent_workflow_continuation

AGENT_WORKFLOW_REHYDRATION_VERSION = "v0.56"
TERMINAL_STATUSES = {"rejected", "synthesized", "cancelled"}
MAX_TASK_EXCERPT_CHARS = 4000


class AgentWorkflowRehydrationError(RuntimeError):
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


def _json_strings(value: Any) -> list[str]:
    if isinstance(value, list):
        parsed = value
    else:
        try:
            parsed = json.loads(str(value or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = []
    if not isinstance(parsed, list):
        return []
    return sorted({str(item).strip() for item in parsed if str(item).strip()})


def _sha256(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _workflow_record(source: str, conversation_id: str, plan_id: int, connection=None) -> dict[str, Any]:
    if connection is None:
        with db() as owned:
            return _workflow_record(source, conversation_id, plan_id, owned)
    row = connection.execute(
        """
        SELECT p.id, p.source_app_key, p.conversation_id, p.parent_agent_id,
               p.objective, p.members_json, p.context_json, p.permission_snapshot_json,
               p.status, p.team_run_id, p.created_at, p.updated_at, p.decided_at,
               r.updated_at AS team_run_updated_at, r.cancelled_at AS team_run_cancelled_at
        FROM agent_team_plans p
        LEFT JOIN agent_team_runs r
          ON r.id=p.team_run_id AND r.source_app_key=p.source_app_key
        WHERE p.id=? AND p.source_app_key=? AND p.conversation_id=?
        LIMIT 1
        """,
        (int(plan_id), source, conversation_id),
    ).fetchone()
    if row is None:
        raise AgentWorkflowRehydrationError("Team Plan not found for this application and conversation.", 404)
    record = dict(row)
    team_run_id = record.get("team_run_id")
    tasks: list[dict[str, Any]] = []
    handoffs: list[dict[str, Any]] = []
    if team_run_id is not None:
        task_rows = connection.execute(
            """
            SELECT m.position, t.id, t.worker_agent_id, t.worker_agent_name, t.status,
                   t.include_memory, t.include_knowledge, t.include_contacts,
                   t.cloud_allowed, t.max_context_chars, t.updated_at
            FROM agent_team_run_members m
            JOIN agent_delegation_tasks t ON t.id=m.task_id
            WHERE m.team_run_id=?
            ORDER BY m.position ASC
            """,
            (int(team_run_id),),
        ).fetchall()
        tasks = [dict(item) for item in task_rows]
        task_ids = [int(item["id"]) for item in task_rows]
        if task_ids:
            placeholders = ",".join("?" for _ in task_ids)
            handoff_rows = connection.execute(
                f"""
                SELECT task_id, status, consumed_run_id, updated_at
                FROM agent_result_handoffs
                WHERE source_app_key=? AND task_id IN ({placeholders})
                ORDER BY task_id ASC
                """,
                (source, *task_ids),
            ).fetchall()
            handoffs = [dict(item) for item in handoff_rows]
    revision_material = {
        "plan": {
            "id": int(record["id"]),
            "parent_agent_id": record.get("parent_agent_id"),
            "objective": str(record.get("objective") or ""),
            "members_json": str(record.get("members_json") or "[]"),
            "context_json": str(record.get("context_json") or "{}"),
            "permission_snapshot_json": str(record.get("permission_snapshot_json") or "[]"),
            "status": str(record.get("status") or ""),
            "team_run_id": int(team_run_id) if team_run_id is not None else None,
            "updated_at": record.get("updated_at"),
            "decided_at": record.get("decided_at"),
            "team_run_updated_at": record.get("team_run_updated_at"),
            "team_run_cancelled_at": record.get("team_run_cancelled_at"),
        },
        "tasks": [
            {
                "position": int(item["position"]),
                "id": int(item["id"]),
                "worker_agent_id": int(item["worker_agent_id"]) if item.get("worker_agent_id") is not None else None,
                "status": str(item.get("status") or ""),
                "include_memory": bool(item.get("include_memory")),
                "include_knowledge": bool(item.get("include_knowledge")),
                "include_contacts": bool(item.get("include_contacts")),
                "cloud_allowed": bool(item.get("cloud_allowed")),
                "max_context_chars": int(item.get("max_context_chars") or 0),
                "updated_at": item.get("updated_at"),
            }
            for item in tasks
        ],
        "handoffs": [
            {
                "task_id": int(item["task_id"]),
                "status": str(item.get("status") or ""),
                "consumed_run_id": int(item["consumed_run_id"]) if item.get("consumed_run_id") is not None else None,
                "updated_at": item.get("updated_at"),
            }
            for item in handoffs
        ],
    }
    record["revision_token"] = _sha256(revision_material)
    return record


def _required_permissions(record: dict[str, Any], orchestration: dict[str, Any]) -> set[str]:
    required: set[str] = set()
    if str(orchestration.get("plan_status") or "") == "proposed":
        context = _json_object(record.get("context_json"))
        if bool(context.get("include_memory", True)):
            required.add("memory.read")
        if bool(context.get("include_knowledge", True)):
            required.add("knowledge.search")
        if bool(context.get("include_contacts", False)):
            required.add("contacts.read")
        return required
    team = orchestration.get("team_run") if isinstance(orchestration.get("team_run"), dict) else {}
    for member in team.get("members") or []:
        if bool(member.get("include_memory")):
            required.add("memory.read")
        if bool(member.get("include_knowledge")):
            required.add("knowledge.search")
        if bool(member.get("include_contacts")):
            required.add("contacts.read")
    return required


def _worker_ids(orchestration: dict[str, Any]) -> list[int]:
    team = orchestration.get("team_run") if isinstance(orchestration.get("team_run"), dict) else None
    if team is not None:
        values = [item.get("worker_agent_id") for item in team.get("members") or []]
    else:
        plan = orchestration.get("plan") if isinstance(orchestration.get("plan"), dict) else {}
        values = [item.get("worker_agent_id") for item in plan.get("members") or []]
    ids: list[int] = []
    for value in values:
        try:
            worker_id = int(value)
        except (TypeError, ValueError):
            continue
        if worker_id > 0 and worker_id not in ids:
            ids.append(worker_id)
    return ids


def _unavailable_workers(source: str, orchestration: dict[str, Any], *, owner: bool) -> tuple[list[int], list[int]]:
    unavailable: list[int] = []
    missing_task_worker: list[int] = []
    team = orchestration.get("team_run") if isinstance(orchestration.get("team_run"), dict) else None
    if team is not None:
        for member in team.get("members") or []:
            task_id = int(member.get("id") or 0)
            worker_id = member.get("worker_agent_id")
            if worker_id is None:
                if task_id > 0:
                    missing_task_worker.append(task_id)
                continue
            try:
                agent_routing.resolve_agent(source, int(worker_id), owner=owner)
            except agent_routing.AgentRoutingError:
                if int(worker_id) not in unavailable:
                    unavailable.append(int(worker_id))
    else:
        for worker_id in _worker_ids(orchestration):
            try:
                agent_routing.resolve_agent(source, worker_id, owner=owner)
            except agent_routing.AgentRoutingError:
                unavailable.append(worker_id)
    return sorted(set(unavailable)), sorted(set(missing_task_worker))


def _checkpoint(orchestration: dict[str, Any]) -> dict[str, Any]:
    next_action = orchestration.get("next_action") if isinstance(orchestration.get("next_action"), dict) else {}
    team = orchestration.get("team_run") if isinstance(orchestration.get("team_run"), dict) else None
    counts = (team or {}).get("counts") if isinstance((team or {}).get("counts"), dict) else {}
    members: list[dict[str, Any]] = []
    for item in (team or {}).get("members") or []:
        handoff = item.get("handoff") if isinstance(item.get("handoff"), dict) else {}
        member_status = str(item.get("status") or "queued")
        members.append(
            {
                "task_id": int(item.get("id") or 0),
                "position": int(item.get("position") or 0),
                "worker_agent_id": int(item["worker_agent_id"]) if item.get("worker_agent_id") is not None else None,
                "worker_agent_name": str(item.get("worker_agent_name") or "Agent")[:120],
                "task": " ".join(str(item.get("task") or "").split())[:MAX_TASK_EXCERPT_CHARS],
                "status": member_status,
                "result_available": member_status == "completed",
                "result_authorized": bool(item.get("result_authorized")),
                "handoff_status": str(handoff.get("status") or "") or None,
                "updated_at": item.get("updated_at"),
            }
        )
    retryable: list[int] = []
    for value in (team or {}).get("retryable_task_ids") or []:
        try:
            task_id = int(value)
        except (TypeError, ValueError):
            continue
        if task_id > 0 and task_id not in retryable:
            retryable.append(task_id)
    return {
        "conversation_id": str(orchestration.get("conversation_id") or ""),
        "plan_id": int(orchestration["plan_id"]),
        "team_run_id": int(orchestration["team_run_id"]) if orchestration.get("team_run_id") is not None else None,
        "parent": {
            "id": int(orchestration["parent_agent_id"]) if orchestration.get("parent_agent_id") is not None else None,
            "name": str(orchestration.get("parent_agent_name") or "Agent")[:120],
        },
        "objective": " ".join(str(orchestration.get("objective") or "").split())[:16000],
        "plan_status": str(orchestration.get("plan_status") or ""),
        "workflow_status": str(orchestration.get("status") or ""),
        "counts": {
            "members": max(0, int(counts.get("members") or len(members))),
            "queued": max(0, int(counts.get("queued") or 0)),
            "working": max(0, int(counts.get("working") or 0)),
            "completed": max(0, int(counts.get("completed") or 0)),
            "failed": max(0, int(counts.get("failed") or 0)),
            "cancelled": max(0, int(counts.get("cancelled") or 0)),
        },
        "members": members,
        "retryable_task_ids": retryable,
        "next_action": {
            "key": str(next_action.get("key") or "inspect")[:80],
            "label": " ".join(str(next_action.get("label") or "").split())[:500],
        },
        "requires_explicit_action": bool(orchestration.get("requires_explicit_action")),
    }


def rehydrate_workflow(
    source_app_key: str,
    conversation_id: str,
    plan_id: int,
    *,
    owner: bool,
    current_permissions: set[str] | None = None,
) -> dict[str, Any]:
    source = _source(source_app_key)
    conversation = str(conversation_id or "").strip()
    if not conversation:
        raise AgentWorkflowRehydrationError("Conversation id is required.")
    if int(plan_id) < 1:
        raise AgentWorkflowRehydrationError("Plan id is required.")
    granted = set(current_permissions or set())

    try:
        continuation = agent_workflow_continuation.conversation_continuation(
            source,
            conversation,
            owner=owner,
            current_permissions=granted,
        )
    except agent_workflow_continuation.AgentWorkflowContinuationError as exc:
        raise AgentWorkflowRehydrationError(str(exc), exc.status_code) from exc
    current = continuation.get("current") if isinstance(continuation.get("current"), dict) else None
    if current is None:
        raise AgentWorkflowRehydrationError("This conversation has no resumable workflow.", 409)
    if int(current.get("plan_id") or 0) != int(plan_id):
        raise AgentWorkflowRehydrationError("Workflow state changed; refresh recovery before resuming.", 409)

    before = _workflow_record(source, conversation, int(plan_id))
    try:
        orchestration = agent_team_orchestration.get_orchestration(
            int(plan_id),
            source,
            owner=owner,
            current_permissions=granted,
        )
    except agent_team_orchestration.AgentTeamOrchestrationError as exc:
        raise AgentWorkflowRehydrationError(str(exc), exc.status_code) from exc
    status = str(orchestration.get("status") or "")
    if status in TERMINAL_STATUSES:
        raise AgentWorkflowRehydrationError("This workflow is already terminal and cannot be rehydrated.", 409)

    checkpoint = _checkpoint(orchestration)
    required = set() if owner else _required_permissions(before, orchestration)
    snapshot_permissions = set() if owner else set(_json_strings(before.get("permission_snapshot_json")))
    missing_required = sorted(required - granted)
    snapshot_missing = sorted(snapshot_permissions - granted)
    unavailable_workers, unavailable_task_workers = _unavailable_workers(source, orchestration, owner=owner)
    redacted_task_ids = sorted(
        int(item.get("task_id") or 0)
        for item in checkpoint["members"]
        if int(item.get("task_id") or 0) > 0
        and bool(item.get("result_available"))
        and not bool(item.get("result_authorized"))
    )
    blocking: list[str] = []
    if missing_required:
        blocking.append("required_permissions_changed")
    if unavailable_workers or unavailable_task_workers:
        blocking.append("worker_access_changed")
    if redacted_task_ids:
        blocking.append("completed_results_no_longer_authorized")
    safe_to_continue = not blocking
    static_drift = {
        "detected": bool(snapshot_missing or blocking),
        "blocking": bool(blocking),
        "blocking_reasons": blocking,
        "required_missing_permissions": missing_required,
        "permission_snapshot_missing": snapshot_missing,
        "unavailable_worker_ids": unavailable_workers,
        "unavailable_task_worker_ids": unavailable_task_workers,
        "redacted_task_ids": redacted_task_ids,
    }
    state_fingerprint = _sha256(
        {
            "version": AGENT_WORKFLOW_REHYDRATION_VERSION,
            "source_app_key": source,
            "revision_token": before["revision_token"],
            "checkpoint": checkpoint,
            "drift": static_drift,
        }
    )
    snapshot_json = json.dumps(checkpoint, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    drift_json = json.dumps(static_drift, sort_keys=True, separators=(",", ":"))

    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        now = _workflow_record(source, conversation, int(plan_id), connection)
        if str(now["revision_token"]) != str(before["revision_token"]):
            raise AgentWorkflowRehydrationError("Workflow changed during recovery; retry from the latest state.", 409)
        latest = connection.execute(
            """
            SELECT id, state_fingerprint
            FROM agent_workflow_rehydrations
            WHERE source_app_key=? AND conversation_id=? AND plan_id=?
            ORDER BY id DESC LIMIT 1
            """,
            (source, conversation, int(plan_id)),
        ).fetchone()
        state_changed_since_last = bool(latest is not None and str(latest["state_fingerprint"]) != state_fingerprint)
        existing = connection.execute(
            """
            SELECT id, status, created_at
            FROM agent_workflow_rehydrations
            WHERE source_app_key=? AND conversation_id=? AND plan_id=? AND state_fingerprint=?
            LIMIT 1
            """,
            (source, conversation, int(plan_id), state_fingerprint),
        ).fetchone()
        if existing is not None:
            rehydration_id = int(existing["id"])
            created_at = existing["created_at"]
            reused = True
        else:
            row = connection.execute(
                """
                INSERT INTO agent_workflow_rehydrations(
                    source_app_key, conversation_id, plan_id, team_run_id,
                    state_fingerprint, status, snapshot_json, drift_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source,
                    conversation,
                    int(plan_id),
                    int(orchestration["team_run_id"]) if orchestration.get("team_run_id") is not None else None,
                    state_fingerprint,
                    "ready" if safe_to_continue else "conflict",
                    snapshot_json,
                    drift_json,
                ),
            )
            rehydration_id = int(row.lastrowid)
            created_at = connection.execute(
                "SELECT created_at FROM agent_workflow_rehydrations WHERE id=?",
                (rehydration_id,),
            ).fetchone()[0]
            reused = False
            connection.execute(
                """
                INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
                VALUES (?, ?, 'agent.workflow.rehydrated', 'agent_team_plan', ?, ?)
                """,
                (
                    _actor_type(source),
                    source,
                    str(int(plan_id)),
                    json.dumps(
                        {
                            "version": AGENT_WORKFLOW_REHYDRATION_VERSION,
                            "conversation_id": conversation,
                            "team_run_id": orchestration.get("team_run_id"),
                            "state_fingerprint": state_fingerprint,
                            "status": "ready" if safe_to_continue else "conflict",
                            "safe_to_continue": safe_to_continue,
                            "actions_executed": 0,
                            "auto_executes": False,
                        },
                        separators=(",", ":"),
                    ),
                ),
            )

    drift = dict(static_drift)
    drift["state_changed_since_last_rehydration"] = state_changed_since_last
    return {
        "version": AGENT_WORKFLOW_REHYDRATION_VERSION,
        "rehydration_id": rehydration_id,
        "reused": reused,
        "created_at": created_at,
        "source_app_key": source,
        "conversation_id": conversation,
        "plan_id": int(plan_id),
        "team_run_id": int(orchestration["team_run_id"]) if orchestration.get("team_run_id") is not None else None,
        "state_fingerprint": state_fingerprint,
        "status": "ready" if safe_to_continue else "conflict",
        "safe_to_continue": safe_to_continue,
        "checkpoint": checkpoint,
        "drift": drift,
        "recovered": True,
        "canonical_source": True,
        "auto_executes": False,
        "actions_executed": 0,
        "actions_via": agent_team_orchestration.AGENT_TEAM_ORCHESTRATION_VERSION,
        "requires_explicit_action": bool(checkpoint["requires_explicit_action"]),
    }
