from __future__ import annotations

import json
from typing import Any

from ..database import db
from . import agent_handoffs, agent_routing

AGENT_WORKFLOW_TIMELINE_VERSION = "v0.50"
MAX_TIMELINE_EVENTS = 200
MAX_TIMELINE_RESULT_CHARS = 2400
MIN_SYNTHESIS_TASKS = 2
MAX_SYNTHESIS_TASKS = 4


class AgentWorkflowTimelineError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _source(value: str | None) -> str:
    normalized = str(value or "").strip()
    return normalized or "owner"


def _actor_type(source_app_key: str) -> str:
    return "owner" if source_app_key == "owner" else "app"


def _parse_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def _event(
    event_type: str,
    sort_at: str | None,
    *,
    task_id: int | None = None,
    handoff_id: int | None = None,
    parent_agent_id: int | None = None,
    parent_agent_name: str | None = None,
    worker_agent_id: int | None = None,
    worker_agent_name: str | None = None,
    status: str | None = None,
    task: str | None = None,
    result: str | None = None,
    run_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "version": AGENT_WORKFLOW_TIMELINE_VERSION,
        "event_type": event_type,
        "sort_at": sort_at,
        "task_id": task_id,
        "handoff_id": handoff_id,
        "parent_agent_id": parent_agent_id,
        "parent_agent_name": parent_agent_name,
        "worker_agent_id": worker_agent_id,
        "worker_agent_name": worker_agent_name,
        "status": status,
        "task": task,
        "result": result,
        "run_id": run_id,
        "metadata": dict(metadata or {}),
    }


def _authorized_result(
    task: dict[str, Any],
    source_app_key: str,
    *,
    owner: bool,
    current_permissions: set[str] | None,
) -> bool:
    if owner:
        return True
    granted = set(current_permissions or set())
    required: set[str] = set()
    if bool(task.get("include_memory")):
        required.add("memory.read")
    if bool(task.get("include_knowledge")):
        required.add("knowledge.search")
    if bool(task.get("include_contacts")):
        required.add("contacts.read")
    if not required.issubset(granted):
        return False
    worker_agent_id = task.get("worker_agent_id")
    if worker_agent_id is None:
        return False
    try:
        agent_routing.resolve_agent(source_app_key, int(worker_agent_id), owner=False)
    except agent_routing.AgentRoutingError:
        return False
    return True


def _conversation(source_app_key: str, conversation_id: str) -> dict[str, Any]:
    try:
        binding = agent_routing.conversation_binding(source_app_key, conversation_id)
    except agent_routing.AgentRoutingError as exc:
        raise AgentWorkflowTimelineError(str(exc), exc.status_code) from exc
    if not binding.get("available") or not binding.get("agent"):
        raise AgentWorkflowTimelineError(
            "This conversation's parent Agent is no longer available.",
            409,
        )
    return binding


def timeline(
    source_app_key: str,
    conversation_id: str,
    *,
    owner: bool,
    current_permissions: set[str] | None = None,
    limit: int = MAX_TIMELINE_EVENTS,
) -> dict[str, Any]:
    source = _source(source_app_key)
    binding = _conversation(source, str(conversation_id))
    bounded = max(1, min(int(limit), MAX_TIMELINE_EVENTS))
    with db() as connection:
        rows = connection.execute(
            """
            SELECT
                t.id, t.parent_agent_id, t.worker_agent_id,
                t.parent_agent_name, t.worker_agent_name, t.task, t.status,
                t.result, t.error, t.include_memory, t.include_knowledge, t.include_contacts,
                t.provider_key, t.model, t.agent_run_id,
                t.created_at, t.started_at, t.completed_at, t.updated_at,
                h.id AS handoff_id, h.status AS handoff_status,
                h.created_at AS handoff_created_at, h.consumed_at, h.revoked_at,
                h.consumed_run_id
            FROM agent_delegation_tasks t
            LEFT JOIN agent_result_handoffs h
              ON h.task_id=t.id AND h.source_app_key=t.source_app_key
            WHERE t.source_app_key=? AND t.conversation_id=?
            ORDER BY t.id ASC
            """,
            (source, str(conversation_id)),
        ).fetchall()
        audit_rows = connection.execute(
            """
            SELECT id, metadata_json, created_at
            FROM activity_log
            WHERE action='agent.synthesis.prepared'
              AND resource_type='conversation'
              AND resource_key=?
              AND actor_key=?
            ORDER BY id ASC
            """,
            (str(conversation_id), source),
        ).fetchall()

    events: list[dict[str, Any]] = []
    consumed_groups: dict[int, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        authorized = _authorized_result(
            item,
            source,
            owner=owner,
            current_permissions=current_permissions,
        )
        result = str(item.get("result") or "")[:MAX_TIMELINE_RESULT_CHARS] if authorized else ""
        base = {
            "task_id": int(item["id"]),
            "parent_agent_id": item.get("parent_agent_id"),
            "parent_agent_name": item.get("parent_agent_name"),
            "worker_agent_id": item.get("worker_agent_id"),
            "worker_agent_name": item.get("worker_agent_name"),
            "task": str(item.get("task") or ""),
        }
        events.append(
            _event(
                "delegation_queued",
                item.get("created_at"),
                status="queued",
                metadata={"result_authorized": authorized},
                **base,
            )
        )
        if item.get("started_at"):
            events.append(
                _event(
                    "delegation_started",
                    item.get("started_at"),
                    status="working",
                    metadata={"result_authorized": authorized},
                    **base,
                )
            )
        terminal = str(item.get("status") or "")
        if terminal in {"completed", "failed", "cancelled"}:
            events.append(
                _event(
                    f"delegation_{terminal}",
                    item.get("completed_at") or item.get("updated_at"),
                    status=terminal,
                    result=result if terminal == "completed" else None,
                    run_id=item.get("agent_run_id"),
                    metadata={
                        "result_authorized": authorized,
                        "result_redacted": bool(terminal == "completed" and item.get("result") and not authorized),
                        "error": str(item.get("error") or "")[:1000] if terminal == "failed" else "",
                        "provider_key": item.get("provider_key"),
                        "model": item.get("model"),
                    },
                    **base,
                )
            )
        handoff_id = item.get("handoff_id")
        if handoff_id is not None:
            events.append(
                _event(
                    "handoff_queued",
                    item.get("handoff_created_at"),
                    handoff_id=int(handoff_id),
                    status="pending",
                    result=result or None,
                    metadata={"result_authorized": authorized},
                    **base,
                )
            )
            if item.get("revoked_at"):
                events.append(
                    _event(
                        "handoff_revoked",
                        item.get("revoked_at"),
                        handoff_id=int(handoff_id),
                        status="revoked",
                        metadata={"result_authorized": authorized},
                        **base,
                    )
                )
            if item.get("consumed_at"):
                consumed_run_id = int(item["consumed_run_id"]) if item.get("consumed_run_id") is not None else None
                events.append(
                    _event(
                        "handoff_consumed",
                        item.get("consumed_at"),
                        handoff_id=int(handoff_id),
                        status="consumed",
                        run_id=consumed_run_id,
                        metadata={"result_authorized": authorized},
                        **base,
                    )
                )
                if consumed_run_id is not None:
                    group = consumed_groups.setdefault(
                        consumed_run_id,
                        {
                            "sort_at": item.get("consumed_at"),
                            "handoff_ids": [],
                            "task_ids": [],
                            "worker_names": [],
                            "parent_agent_id": item.get("parent_agent_id"),
                            "parent_agent_name": item.get("parent_agent_name"),
                        },
                    )
                    group["handoff_ids"].append(int(handoff_id))
                    group["task_ids"].append(int(item["id"]))
                    group["worker_names"].append(str(item.get("worker_agent_name") or "Worker Agent"))
                    if str(item.get("consumed_at") or "") > str(group.get("sort_at") or ""):
                        group["sort_at"] = item.get("consumed_at")

    for row in audit_rows:
        metadata = _parse_json(row["metadata_json"])
        events.append(
            _event(
                "synthesis_prepared",
                row["created_at"],
                parent_agent_id=metadata.get("parent_agent_id"),
                parent_agent_name=metadata.get("parent_agent_name"),
                status="prepared",
                metadata={
                    "task_ids": [int(value) for value in metadata.get("task_ids", []) if str(value).isdigit()],
                    "handoff_ids": [int(value) for value in metadata.get("handoff_ids", []) if str(value).isdigit()],
                    "count": int(metadata.get("count") or 0),
                    "scope_rechecked": bool(metadata.get("scope_rechecked")),
                },
            )
        )

    for run_id, group in consumed_groups.items():
        events.append(
            _event(
                "parent_synthesis",
                group.get("sort_at"),
                parent_agent_id=group.get("parent_agent_id"),
                parent_agent_name=group.get("parent_agent_name"),
                status="completed",
                run_id=run_id,
                metadata={
                    "task_ids": sorted(set(group["task_ids"])),
                    "handoff_ids": sorted(set(group["handoff_ids"])),
                    "worker_names": group["worker_names"],
                    "count": len(set(group["handoff_ids"])),
                },
            )
        )

    priority = {
        "delegation_queued": 10,
        "delegation_started": 20,
        "delegation_completed": 30,
        "delegation_failed": 30,
        "delegation_cancelled": 30,
        "handoff_queued": 40,
        "synthesis_prepared": 50,
        "handoff_revoked": 60,
        "handoff_consumed": 70,
        "parent_synthesis": 80,
    }
    events.sort(
        key=lambda item: (
            str(item.get("sort_at") or ""),
            priority.get(str(item.get("event_type") or ""), 99),
            int(item.get("task_id") or 0),
            int(item.get("handoff_id") or 0),
        )
    )
    if len(events) > bounded:
        events = events[-bounded:]
    return {
        "version": AGENT_WORKFLOW_TIMELINE_VERSION,
        "conversation_id": str(conversation_id),
        "parent": binding.get("agent"),
        "items": events,
        "counts": {
            "events": len(events),
            "delegations": len(rows),
            "pending_handoffs": sum(1 for row in rows if row["handoff_status"] == "pending"),
        },
    }


def prepare_synthesis(
    source_app_key: str,
    conversation_id: str,
    task_ids: list[int],
    *,
    owner: bool,
    current_permissions: set[str] | None = None,
) -> dict[str, Any]:
    source = _source(source_app_key)
    ids = []
    for raw in task_ids:
        value = int(raw)
        if value > 0 and value not in ids:
            ids.append(value)
    if len(ids) < MIN_SYNTHESIS_TASKS or len(ids) > MAX_SYNTHESIS_TASKS:
        raise AgentWorkflowTimelineError(
            f"Select between {MIN_SYNTHESIS_TASKS} and {MAX_SYNTHESIS_TASKS} completed specialist results.",
            422,
        )
    binding = _conversation(source, str(conversation_id))
    parent = binding["agent"]
    placeholders = ",".join("?" for _ in ids)

    with db() as connection:
        rows = connection.execute(
            f"""
            SELECT id, source_app_key, parent_agent_id, worker_agent_id,
                   parent_agent_name, worker_agent_name, conversation_id,
                   task, status, result, include_memory, include_knowledge, include_contacts
            FROM agent_delegation_tasks
            WHERE source_app_key=? AND id IN ({placeholders})
            """,
            (source, *ids),
        ).fetchall()
        if len(rows) != len(ids):
            raise AgentWorkflowTimelineError("One or more selected delegation tasks were not found for this application.", 404)
        by_id = {int(row["id"]): dict(row) for row in rows}
        ordered = [by_id[value] for value in ids]

        for item in ordered:
            if str(item.get("conversation_id") or "") != str(conversation_id):
                raise AgentWorkflowTimelineError("All synthesis tasks must belong to the active parent conversation.", 409)
            if int(item.get("parent_agent_id") or 0) != int(parent["id"]):
                raise AgentWorkflowTimelineError("All synthesis tasks must share the active parent Agent.", 409)
            if str(item.get("status") or "") != "completed" or not str(item.get("result") or "").strip():
                raise AgentWorkflowTimelineError("Only completed delegation results can be prepared for synthesis.", 409)
            if not _authorized_result(
                item,
                source,
                owner=owner,
                current_permissions=current_permissions,
            ):
                raise AgentWorkflowTimelineError(
                    "Current application permissions or Agent access no longer authorize one of the selected specialist results.",
                    403,
                )

        existing_rows = connection.execute(
            f"""
            SELECT id, task_id, status
            FROM agent_result_handoffs
            WHERE source_app_key=? AND task_id IN ({placeholders})
            """,
            (source, *ids),
        ).fetchall()
        existing = {int(row["task_id"]): dict(row) for row in existing_rows}
        for task_id, handoff in existing.items():
            if str(handoff.get("status") or "") == "consumed":
                raise AgentWorkflowTimelineError(
                    f"Delegation task {task_id} has already been consumed by the parent Agent.",
                    409,
                )

        other_pending = connection.execute(
            f"""
            SELECT task_id
            FROM agent_result_handoffs
            WHERE source_app_key=? AND conversation_id=? AND status='pending'
              AND task_id NOT IN ({placeholders})
            LIMIT 1
            """,
            (source, str(conversation_id), *ids),
        ).fetchone()
        if other_pending is not None:
            raise AgentWorkflowTimelineError(
                "This conversation already has another pending specialist handoff. Include or resolve it before preparing an exact synthesis set.",
                409,
            )

        handoff_ids: list[int] = []
        for item in ordered:
            task_id = int(item["id"])
            handoff = existing.get(task_id)
            if handoff is not None:
                handoff_id = int(handoff["id"])
                if str(handoff.get("status") or "") == "revoked":
                    connection.execute(
                        """
                        UPDATE agent_result_handoffs
                        SET status='pending', revoked_at=NULL, updated_at=CURRENT_TIMESTAMP
                        WHERE id=? AND source_app_key=? AND status='revoked'
                        """,
                        (handoff_id, source),
                    )
            else:
                cursor = connection.execute(
                    """
                    INSERT INTO agent_result_handoffs(
                        task_id, source_app_key, conversation_id, parent_agent_id, worker_agent_id,
                        parent_agent_name, worker_agent_name, task_excerpt, result
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        source,
                        str(conversation_id),
                        int(parent["id"]),
                        int(item["worker_agent_id"]) if item.get("worker_agent_id") is not None else None,
                        str(item.get("parent_agent_name") or parent.get("name") or "Agent"),
                        str(item.get("worker_agent_name") or "Worker Agent"),
                        " ".join(str(item.get("task") or "").split())[: agent_handoffs.MAX_TASK_EXCERPT_CHARS],
                        str(item.get("result") or "").strip()[: agent_handoffs.MAX_STORED_RESULT_CHARS],
                    ),
                )
                handoff_id = int(cursor.lastrowid)
            handoff_ids.append(handoff_id)

        audit = {
            "version": AGENT_WORKFLOW_TIMELINE_VERSION,
            "conversation_id": str(conversation_id),
            "parent_agent_id": int(parent["id"]),
            "parent_agent_name": str(parent.get("name") or "Agent"),
            "task_ids": ids,
            "handoff_ids": handoff_ids,
            "count": len(ids),
            "scope_rechecked": not owner,
        }
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES (?, ?, 'agent.synthesis.prepared', 'conversation', ?, ?)
            """,
            (
                _actor_type(source),
                source,
                str(conversation_id),
                json.dumps(audit, separators=(",", ":")),
            ),
        )

    return {
        "version": AGENT_WORKFLOW_TIMELINE_VERSION,
        "conversation_id": str(conversation_id),
        "parent": parent,
        "task_ids": ids,
        "handoff_ids": handoff_ids,
        "count": len(ids),
        "items": [agent_handoffs.get_handoff(handoff_id, source) for handoff_id in handoff_ids],
        "next_action": "Send one normal parent-Agent chat message to synthesize the prepared specialist results.",
    }
