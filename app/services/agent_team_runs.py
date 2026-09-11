from __future__ import annotations

import json
from typing import Any

from ..database import db
from . import agent_routing, agent_workflow_timeline, agent_workflow_tool, agent_workflows

AGENT_TEAM_RUN_VERSION = "v0.51"
MIN_TEAM_MEMBERS = 2
MAX_TEAM_MEMBERS = 4
MAX_OBJECTIVE_CHARS = 16000


class AgentTeamRunError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _source(value: str | None) -> str:
    normalized = str(value or "").strip()
    return normalized or "owner"


def _actor_type(source_app_key: str) -> str:
    return "owner" if source_app_key == "owner" else "app"


def _required_permissions(task: dict[str, Any]) -> set[str]:
    required: set[str] = set()
    if bool(task.get("include_memory")):
        required.add("memory.read")
    if bool(task.get("include_knowledge")):
        required.add("knowledge.search")
    if bool(task.get("include_contacts")):
        required.add("contacts.read")
    return required


def _result_authorized(
    task: dict[str, Any],
    source_app_key: str,
    *,
    owner: bool,
    current_permissions: set[str] | None,
) -> bool:
    if owner:
        return True
    granted = set(current_permissions or set())
    if not _required_permissions(task).issubset(granted):
        return False
    worker_id = task.get("worker_agent_id")
    if worker_id is None:
        return False
    try:
        agent_routing.resolve_agent(source_app_key, int(worker_id), owner=False)
    except agent_routing.AgentRoutingError:
        return False
    return True


def _team_row(team_run_id: int, source_app_key: str, connection) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT id, source_app_key, conversation_id, parent_agent_id, parent_agent_name,
               objective, cancelled_at, created_at, updated_at
        FROM agent_team_runs
        WHERE id=? AND source_app_key=?
        LIMIT 1
        """,
        (int(team_run_id), _source(source_app_key)),
    ).fetchone()
    if row is None:
        raise AgentTeamRunError("Team Run not found for this application.", 404)
    return dict(row)


def _matching_synthesis_audit(
    connection,
    source_app_key: str,
    conversation_id: str,
    task_ids: list[int],
) -> str | None:
    expected = sorted(int(value) for value in task_ids)
    rows = connection.execute(
        """
        SELECT metadata_json, created_at
        FROM activity_log
        WHERE action='agent.synthesis.prepared'
          AND resource_type='conversation'
          AND resource_key=?
          AND actor_key=?
        ORDER BY id DESC
        LIMIT 20
        """,
        (str(conversation_id), _source(source_app_key)),
    ).fetchall()
    for row in rows:
        try:
            metadata = json.loads(str(row["metadata_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        found = sorted(
            int(value)
            for value in metadata.get("task_ids", [])
            if str(value).isdigit()
        )
        if found == expected:
            return str(row["created_at"])
    return None


def _members(connection, team_run_id: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        f"""
        SELECT m.position, m.task_id,
               {agent_workflows._select_columns()}
        FROM agent_team_run_members m
        JOIN agent_delegation_tasks ON agent_delegation_tasks.id=m.task_id
        WHERE m.team_run_id=?
        ORDER BY m.position ASC
        """,
        (int(team_run_id),),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        raw = dict(row)
        position = int(raw.pop("position"))
        raw.pop("task_id", None)
        task = agent_workflows._decorate(raw)
        task["position"] = position
        items.append(task)
    return items


def _handoff_states(connection, source: str, task_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not task_ids:
        return {}
    placeholders = ",".join("?" for _ in task_ids)
    rows = connection.execute(
        f"""
        SELECT id, task_id, status, consumed_run_id, created_at, consumed_at, revoked_at
        FROM agent_result_handoffs
        WHERE source_app_key=? AND task_id IN ({placeholders})
        """,
        (source, *task_ids),
    ).fetchall()
    return {int(row["task_id"]): dict(row) for row in rows}


def _derive_status(
    run: dict[str, Any],
    members: list[dict[str, Any]],
    handoffs: dict[int, dict[str, Any]],
    prepared_at: str | None,
) -> str:
    if run.get("cancelled_at"):
        return "cancelled"
    statuses = [str(item.get("status") or "queued") for item in members]
    if statuses and all(value == "completed" for value in statuses):
        states = [str(handoffs.get(int(item["id"]), {}).get("status") or "") for item in members]
        consumed_runs = {
            int(handoffs[int(item["id"])]["consumed_run_id"])
            for item in members
            if handoffs.get(int(item["id"]), {}).get("status") == "consumed"
            and handoffs[int(item["id"])].get("consumed_run_id") is not None
        }
        if states and all(value == "consumed" for value in states) and len(consumed_runs) == 1:
            return "synthesized"
        if prepared_at and states and all(value in {"pending", "consumed"} for value in states):
            return "prepared"
        return "completed"
    if any(value == "working" for value in statuses):
        return "running"
    if any(value in {"failed", "cancelled", "completed"} for value in statuses):
        return "partial"
    return "queued"


def _decorate_team(
    run: dict[str, Any],
    members: list[dict[str, Any]],
    handoffs: dict[int, dict[str, Any]],
    prepared_at: str | None,
    *,
    owner: bool,
    current_permissions: set[str] | None,
) -> dict[str, Any]:
    source = str(run["source_app_key"])
    safe_members: list[dict[str, Any]] = []
    for item in members:
        member = dict(item)
        authorized = _result_authorized(
            member,
            source,
            owner=owner,
            current_permissions=current_permissions,
        )
        if member.get("result") and not authorized:
            member["result"] = ""
            member["result_redacted"] = True
        else:
            member["result_redacted"] = False
        handoff = handoffs.get(int(member["id"]))
        member["handoff"] = dict(handoff) if handoff else None
        member["result_authorized"] = authorized
        safe_members.append(member)

    statuses = [str(item.get("status") or "queued") for item in members]
    status = _derive_status(run, members, handoffs, prepared_at)
    return {
        "version": AGENT_TEAM_RUN_VERSION,
        "id": int(run["id"]),
        "source_app_key": source,
        "conversation_id": str(run["conversation_id"]),
        "parent_agent_id": run.get("parent_agent_id"),
        "parent_agent_name": str(run.get("parent_agent_name") or "Agent"),
        "objective": str(run.get("objective") or ""),
        "status": status,
        "prepared_at": prepared_at,
        "cancelled_at": run.get("cancelled_at"),
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "members": safe_members,
        "task_ids": [int(item["id"]) for item in members],
        "counts": {
            "members": len(members),
            "queued": statuses.count("queued"),
            "working": statuses.count("working"),
            "completed": statuses.count("completed"),
            "failed": statuses.count("failed"),
            "cancelled": statuses.count("cancelled"),
        },
        "can_run": status not in {"cancelled", "prepared", "synthesized"} and any(value == "queued" for value in statuses),
        "can_prepare": status == "completed",
        "retryable_task_ids": [int(item["id"]) for item in members if item.get("status") == "failed"],
        "one_hop_only": True,
        "synthesis_version": agent_workflow_timeline.AGENT_WORKFLOW_TIMELINE_VERSION,
    }


def get_team_run(
    team_run_id: int,
    source_app_key: str,
    *,
    owner: bool,
    current_permissions: set[str] | None = None,
) -> dict[str, Any]:
    source = _source(source_app_key)
    with db() as connection:
        run = _team_row(team_run_id, source, connection)
        members = _members(connection, int(run["id"]))
        task_ids = [int(item["id"]) for item in members]
        handoffs = _handoff_states(connection, source, task_ids)
        prepared_at = _matching_synthesis_audit(
            connection,
            source,
            str(run["conversation_id"]),
            task_ids,
        )
    return _decorate_team(
        run,
        members,
        handoffs,
        prepared_at,
        owner=owner,
        current_permissions=current_permissions,
    )


def list_team_runs(
    source_app_key: str,
    *,
    owner: bool,
    current_permissions: set[str] | None = None,
    conversation_id: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    source = _source(source_app_key)
    bounded = max(1, min(int(limit), 50))
    query = "SELECT id FROM agent_team_runs WHERE source_app_key=?"
    params: list[Any] = [source]
    if conversation_id:
        query += " AND conversation_id=?"
        params.append(str(conversation_id))
    query += " ORDER BY id DESC LIMIT ?"
    params.append(bounded)
    with db() as connection:
        rows = connection.execute(query, tuple(params)).fetchall()
    return {
        "version": AGENT_TEAM_RUN_VERSION,
        "items": [
            get_team_run(
                int(row["id"]),
                source,
                owner=owner,
                current_permissions=current_permissions,
            )
            for row in rows
        ],
    }


def create_team_run(
    source_app_key: str,
    *,
    parent_agent_id: int,
    conversation_id: str,
    objective: str,
    members: list[dict[str, Any]],
    owner: bool,
    current_permissions: set[str] | None = None,
    external_conversation_id: str | None = None,
) -> dict[str, Any]:
    source = _source(source_app_key)
    objective_text = str(objective or "").strip()
    if not objective_text:
        raise AgentTeamRunError("Team Run objective is required.")
    if len(objective_text) > MAX_OBJECTIVE_CHARS:
        raise AgentTeamRunError(f"Team Run objective exceeds the {MAX_OBJECTIVE_CHARS:,} character limit.")
    if len(members) < MIN_TEAM_MEMBERS or len(members) > MAX_TEAM_MEMBERS:
        raise AgentTeamRunError(f"Team Runs require between {MIN_TEAM_MEMBERS} and {MAX_TEAM_MEMBERS} specialists.")
    conversation = str(conversation_id or "").strip()
    if not conversation:
        raise AgentTeamRunError("Team Runs require an active parent conversation.")

    try:
        parent = agent_routing.resolve_agent(source, int(parent_agent_id), owner=owner)
        agent_routing.validate_conversation_agent(source, conversation, int(parent["id"]))
    except agent_routing.AgentRoutingError as exc:
        raise AgentTeamRunError(str(exc), exc.status_code) from exc

    granted = set(current_permissions or set())
    seen_workers: set[int] = set()
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(members, start=1):
        worker_id = int(raw.get("worker_agent_id") or 0)
        if worker_id <= 0:
            raise AgentTeamRunError(f"Team member {index} requires a worker Agent.")
        if worker_id in seen_workers:
            raise AgentTeamRunError("Each Team Run specialist must be a different Agent.", 409)
        seen_workers.add(worker_id)
        try:
            worker = agent_routing.resolve_agent(source, worker_id, owner=owner)
        except agent_routing.AgentRoutingError as exc:
            raise AgentTeamRunError(str(exc), exc.status_code) from exc
        if int(worker["id"]) == int(parent["id"]):
            raise AgentTeamRunError("A Team Run specialist must be different from the parent Agent.", 409)
        brief = str(raw.get("task") or "").strip()
        if not brief:
            raise AgentTeamRunError(f"Team member {index} requires a task brief.")
        if len(brief) > agent_workflows.MAX_TASK_CHARS:
            raise AgentTeamRunError(f"Team member {index} task exceeds the {agent_workflows.MAX_TASK_CHARS:,} character limit.")
        context_chars = int(raw.get("max_context_chars") or 12000)
        if context_chars < 2000 or context_chars > 24000:
            raise AgentTeamRunError("Delegation context limit must be between 2,000 and 24,000 characters.")
        include_memory = bool(raw.get("include_memory", True) and (owner or "memory.read" in granted))
        include_knowledge = bool(raw.get("include_knowledge", True) and (owner or "knowledge.search" in granted))
        include_contacts = bool(raw.get("include_contacts", False) and (owner or "contacts.read" in granted))
        normalized.append(
            {
                "position": index,
                "worker": worker,
                "task": brief,
                "include_memory": include_memory,
                "include_knowledge": include_knowledge,
                "include_contacts": include_contacts,
                "cloud_allowed": bool(raw.get("cloud_allowed", True)),
                "max_context_chars": context_chars,
            }
        )

    permission_snapshot = [] if owner else sorted(granted)
    external_id = " ".join(str(external_conversation_id or "").split())[:160] or None
    with db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO agent_team_runs(
                source_app_key, conversation_id, parent_agent_id, parent_agent_name, objective
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                source,
                conversation,
                int(parent["id"]),
                str(parent.get("name") or "Agent"),
                objective_text,
            ),
        )
        team_run_id = int(cursor.lastrowid)
        task_ids: list[int] = []
        for member in normalized:
            worker = member["worker"]
            metadata = {
                "workflow_version": agent_workflows.AGENT_WORKFLOW_VERSION,
                "team_run_version": AGENT_TEAM_RUN_VERSION,
                "team_run_id": team_run_id,
                "team_position": int(member["position"]),
                "created_via": "team_run_v051",
                "nested_delegation": False,
                "scope_enforced": not owner,
            }
            task_cursor = connection.execute(
                """
                INSERT INTO agent_delegation_tasks(
                    source_app_key, parent_agent_id, worker_agent_id,
                    parent_agent_name, worker_agent_name,
                    conversation_id, external_conversation_id, task,
                    include_memory, include_knowledge, include_contacts, cloud_allowed,
                    max_context_chars, permission_snapshot_json, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source,
                    int(parent["id"]),
                    int(worker["id"]),
                    str(parent.get("name") or "Agent"),
                    str(worker.get("name") or "Agent"),
                    conversation,
                    external_id,
                    member["task"],
                    1 if member["include_memory"] else 0,
                    1 if member["include_knowledge"] else 0,
                    1 if member["include_contacts"] else 0,
                    1 if member["cloud_allowed"] else 0,
                    int(member["max_context_chars"]),
                    json.dumps(permission_snapshot, separators=(",", ":")),
                    json.dumps(metadata, separators=(",", ":")),
                ),
            )
            task_id = int(task_cursor.lastrowid)
            task_ids.append(task_id)
            connection.execute(
                """
                INSERT INTO agent_team_run_members(
                    team_run_id, position, task_id, worker_agent_id, worker_agent_name
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    team_run_id,
                    int(member["position"]),
                    task_id,
                    int(worker["id"]),
                    str(worker.get("name") or "Agent"),
                ),
            )
            connection.execute(
                """
                INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
                VALUES (?, ?, 'agent.delegation.queued', 'agent_delegation', ?, ?)
                """,
                (
                    _actor_type(source),
                    source,
                    str(task_id),
                    json.dumps(
                        {
                            "version": agent_workflows.AGENT_WORKFLOW_VERSION,
                            "team_run_version": AGENT_TEAM_RUN_VERSION,
                            "team_run_id": team_run_id,
                            "parent_agent_id": int(parent["id"]),
                            "worker_agent_id": int(worker["id"]),
                            "conversation_id": conversation,
                            "external_conversation_id": external_id,
                            "scope_enforced": not owner,
                        },
                        separators=(",", ":"),
                    ),
                ),
            )
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES (?, ?, 'agent.team_run.created', 'agent_team_run', ?, ?)
            """,
            (
                _actor_type(source),
                source,
                str(team_run_id),
                json.dumps(
                    {
                        "version": AGENT_TEAM_RUN_VERSION,
                        "conversation_id": conversation,
                        "parent_agent_id": int(parent["id"]),
                        "task_ids": task_ids,
                        "member_count": len(task_ids),
                        "one_hop_only": True,
                    },
                    separators=(",", ":"),
                ),
            ),
        )
    return get_team_run(
        team_run_id,
        source,
        owner=owner,
        current_permissions=current_permissions,
    )


def run_team(
    team_run_id: int,
    source_app_key: str,
    *,
    owner: bool,
    current_permissions: set[str] | None = None,
) -> dict[str, Any]:
    source = _source(source_app_key)
    current = get_team_run(
        team_run_id,
        source,
        owner=owner,
        current_permissions=current_permissions,
    )
    if current["status"] in {"cancelled", "prepared", "synthesized"}:
        raise AgentTeamRunError(f"Team Run cannot execute while status is {current['status']}.", 409)

    outcomes: list[dict[str, Any]] = []
    for member in current["members"]:
        if str(member.get("status") or "") != "queued":
            continue
        task_id = int(member["id"])
        try:
            with agent_workflow_tool.worker_scope():
                result = agent_workflows.execute_task(
                    task_id,
                    source,
                    owner=owner,
                    current_permissions=set(current_permissions or set()),
                )
            outcomes.append({"task_id": task_id, "status": result.get("status"), "ok": True})
        except Exception as exc:
            outcomes.append(
                {
                    "task_id": task_id,
                    "status": "failed",
                    "ok": False,
                    "error": str(exc)[:500],
                    "status_code": int(getattr(exc, "status_code", 500)),
                }
            )

    with db() as connection:
        connection.execute(
            "UPDATE agent_team_runs SET updated_at=CURRENT_TIMESTAMP WHERE id=? AND source_app_key=?",
            (int(team_run_id), source),
        )
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES (?, ?, 'agent.team_run.executed', 'agent_team_run', ?, ?)
            """,
            (
                _actor_type(source),
                source,
                str(team_run_id),
                json.dumps(
                    {
                        "version": AGENT_TEAM_RUN_VERSION,
                        "outcomes": outcomes,
                    },
                    separators=(",", ":"),
                ),
            ),
        )
    team = get_team_run(
        team_run_id,
        source,
        owner=owner,
        current_permissions=current_permissions,
    )
    team["outcomes"] = outcomes
    return team


def retry_member(
    team_run_id: int,
    task_id: int,
    source_app_key: str,
    *,
    owner: bool,
    current_permissions: set[str] | None = None,
) -> dict[str, Any]:
    source = _source(source_app_key)
    current = get_team_run(
        team_run_id,
        source,
        owner=owner,
        current_permissions=current_permissions,
    )
    if current["status"] in {"cancelled", "prepared", "synthesized"}:
        raise AgentTeamRunError(f"Team Run member cannot be retried while status is {current['status']}.", 409)
    member = next((item for item in current["members"] if int(item["id"]) == int(task_id)), None)
    if member is None:
        raise AgentTeamRunError("Delegation task is not a member of this Team Run.", 404)
    if str(member.get("status") or "") != "failed":
        raise AgentTeamRunError("Only failed Team Run members can be retried.", 409)
    worker_id = member.get("worker_agent_id")
    if worker_id is None:
        raise AgentTeamRunError("The Team Run specialist no longer exists.", 409)
    try:
        agent_routing.resolve_agent(source, int(worker_id), owner=owner)
    except agent_routing.AgentRoutingError as exc:
        raise AgentTeamRunError(str(exc), exc.status_code) from exc

    with db() as connection:
        changed = connection.execute(
            """
            UPDATE agent_delegation_tasks
            SET status='queued', result='', error=NULL, provider_key=NULL, model=NULL,
                agent_run_id=NULL, started_at=NULL, completed_at=NULL, updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND source_app_key=? AND status='failed'
            """,
            (int(task_id), source),
        )
        if changed.rowcount <= 0:
            raise AgentTeamRunError("Team Run member is no longer failed and cannot be retried.", 409)
        connection.execute(
            "UPDATE agent_team_runs SET updated_at=CURRENT_TIMESTAMP WHERE id=? AND source_app_key=?",
            (int(team_run_id), source),
        )
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES (?, ?, 'agent.delegation.requeued', 'agent_delegation', ?, ?)
            """,
            (
                _actor_type(source),
                source,
                str(task_id),
                json.dumps(
                    {"version": AGENT_TEAM_RUN_VERSION, "team_run_id": int(team_run_id), "reason": "retry_failed_member"},
                    separators=(",", ":"),
                ),
            ),
        )
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES (?, ?, 'agent.team_run.member_retried', 'agent_team_run', ?, ?)
            """,
            (
                _actor_type(source),
                source,
                str(team_run_id),
                json.dumps(
                    {"version": AGENT_TEAM_RUN_VERSION, "task_id": int(task_id)},
                    separators=(",", ":"),
                ),
            ),
        )

    try:
        with agent_workflow_tool.worker_scope():
            result = agent_workflows.execute_task(
                int(task_id),
                source,
                owner=owner,
                current_permissions=set(current_permissions or set()),
            )
        outcome = {"task_id": int(task_id), "status": result.get("status"), "ok": True}
    except Exception as exc:
        outcome = {
            "task_id": int(task_id),
            "status": "failed",
            "ok": False,
            "error": str(exc)[:500],
            "status_code": int(getattr(exc, "status_code", 500)),
        }
    team = get_team_run(
        team_run_id,
        source,
        owner=owner,
        current_permissions=current_permissions,
    )
    team["retry_outcome"] = outcome
    return team


def prepare_team_synthesis(
    team_run_id: int,
    source_app_key: str,
    *,
    owner: bool,
    current_permissions: set[str] | None = None,
) -> dict[str, Any]:
    source = _source(source_app_key)
    current = get_team_run(
        team_run_id,
        source,
        owner=owner,
        current_permissions=current_permissions,
    )
    if current["status"] == "cancelled":
        raise AgentTeamRunError("Cancelled Team Runs cannot be prepared for synthesis.", 409)
    if current["status"] == "synthesized":
        raise AgentTeamRunError("This Team Run has already been synthesized by the parent Agent.", 409)
    if current["status"] == "prepared":
        return current
    if current["status"] != "completed":
        raise AgentTeamRunError("All Team Run specialists must complete successfully before synthesis can be prepared.", 409)
    try:
        synthesis = agent_workflow_timeline.prepare_synthesis(
            source,
            str(current["conversation_id"]),
            [int(value) for value in current["task_ids"]],
            owner=owner,
            current_permissions=set(current_permissions or set()),
        )
    except agent_workflow_timeline.AgentWorkflowTimelineError as exc:
        raise AgentTeamRunError(str(exc), exc.status_code) from exc
    with db() as connection:
        connection.execute(
            "UPDATE agent_team_runs SET updated_at=CURRENT_TIMESTAMP WHERE id=? AND source_app_key=?",
            (int(team_run_id), source),
        )
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES (?, ?, 'agent.team_run.prepared', 'agent_team_run', ?, ?)
            """,
            (
                _actor_type(source),
                source,
                str(team_run_id),
                json.dumps(
                    {
                        "version": AGENT_TEAM_RUN_VERSION,
                        "synthesis_version": agent_workflow_timeline.AGENT_WORKFLOW_TIMELINE_VERSION,
                        "task_ids": current["task_ids"],
                        "handoff_ids": synthesis.get("handoff_ids", []),
                    },
                    separators=(",", ":"),
                ),
            ),
        )
    refreshed = get_team_run(
        team_run_id,
        source,
        owner=owner,
        current_permissions=current_permissions,
    )
    refreshed["synthesis"] = synthesis
    return refreshed


def cancel_team_run(
    team_run_id: int,
    source_app_key: str,
    *,
    owner: bool,
    current_permissions: set[str] | None = None,
) -> dict[str, Any]:
    source = _source(source_app_key)
    current = get_team_run(
        team_run_id,
        source,
        owner=owner,
        current_permissions=current_permissions,
    )
    if current["status"] == "cancelled":
        return current
    if current["status"] in {"prepared", "synthesized"}:
        raise AgentTeamRunError("Prepared or synthesized Team Runs cannot be cancelled.", 409)
    if any(item.get("status") == "working" for item in current["members"]):
        raise AgentTeamRunError("A Team Run cannot be cancelled while a specialist is working.", 409)

    queued_ids = [int(item["id"]) for item in current["members"] if item.get("status") == "queued"]
    with db() as connection:
        connection.execute(
            "UPDATE agent_team_runs SET cancelled_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=? AND source_app_key=?",
            (int(team_run_id), source),
        )
        for task_id in queued_ids:
            connection.execute(
                """
                UPDATE agent_delegation_tasks
                SET status='cancelled', completed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND source_app_key=? AND status='queued'
                """,
                (task_id, source),
            )
            connection.execute(
                """
                INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
                VALUES (?, ?, 'agent.delegation.cancelled', 'agent_delegation', ?, ?)
                """,
                (
                    _actor_type(source),
                    source,
                    str(task_id),
                    json.dumps(
                        {"version": agent_workflows.AGENT_WORKFLOW_VERSION, "team_run_id": int(team_run_id)},
                        separators=(",", ":"),
                    ),
                ),
            )
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES (?, ?, 'agent.team_run.cancelled', 'agent_team_run', ?, ?)
            """,
            (
                _actor_type(source),
                source,
                str(team_run_id),
                json.dumps(
                    {"version": AGENT_TEAM_RUN_VERSION, "cancelled_task_ids": queued_ids},
                    separators=(",", ":"),
                ),
            ),
        )
    return get_team_run(
        team_run_id,
        source,
        owner=owner,
        current_permissions=current_permissions,
    )
