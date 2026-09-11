from __future__ import annotations

import json
from typing import Any

from ..database import db
from . import (
    agent_routing,
    agent_team_runs,
    agent_workflows,
    app_scopes,
    context_chat,
    context_engine,
    providers,
    usage as usage_service,
)

AGENT_TEAM_PLANNING_VERSION = "v0.52"
MIN_PLAN_MEMBERS = 2
MAX_PLAN_MEMBERS = 4
MAX_OBJECTIVE_CHARS = 16000
PLAN_STATUSES = {"proposed", "approved", "rejected"}


class AgentTeamPlanningError(RuntimeError):
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


def _strip_json_fence(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().lower() in {"```", "```json"}:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _validated_members(
    raw_members: Any,
    allowed_workers: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(raw_members, list):
        raise AgentTeamPlanningError("Team plan must contain a members array.")
    if len(raw_members) < MIN_PLAN_MEMBERS or len(raw_members) > MAX_PLAN_MEMBERS:
        raise AgentTeamPlanningError(
            f"Team plans require between {MIN_PLAN_MEMBERS} and {MAX_PLAN_MEMBERS} specialists."
        )
    seen: set[int] = set()
    members: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_members, start=1):
        if not isinstance(raw, dict) or set(raw) != {"worker_agent_id", "task"}:
            raise AgentTeamPlanningError(
                "Each planned specialist must contain only worker_agent_id and task."
            )
        try:
            worker_id = int(raw.get("worker_agent_id"))
        except (TypeError, ValueError):
            raise AgentTeamPlanningError(f"Team plan member {index} has an invalid Agent id.") from None
        if worker_id not in allowed_workers:
            raise AgentTeamPlanningError(
                f"Team plan member {index} selected an Agent that is not currently authorized.",
                403,
            )
        if worker_id in seen:
            raise AgentTeamPlanningError("Each planned specialist must be a different Agent.", 409)
        seen.add(worker_id)
        task = str(raw.get("task") or "").strip()
        if not task:
            raise AgentTeamPlanningError(f"Team plan member {index} requires a task brief.")
        if len(task) > agent_workflows.MAX_TASK_CHARS:
            raise AgentTeamPlanningError(
                f"Team plan member {index} task exceeds the {agent_workflows.MAX_TASK_CHARS:,} character limit."
            )
        worker = allowed_workers[worker_id]
        members.append(
            {
                "worker_agent_id": worker_id,
                "worker_agent_name": str(worker.get("name") or "Agent"),
                "task": task,
            }
        )
    return members


def _parse_model_plan(content: str, allowed_workers: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        payload = json.loads(_strip_json_fence(content))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AgentTeamPlanningError("The parent Agent returned an invalid Team Plan. Nothing was created.", 502) from exc
    if not isinstance(payload, dict) or set(payload) != {"members"}:
        raise AgentTeamPlanningError(
            "The parent Agent returned an invalid Team Plan structure. Nothing was created.",
            502,
        )
    return _validated_members(payload.get("members"), allowed_workers)


def _context_policy(
    *,
    include_memory: bool,
    include_knowledge: bool,
    include_contacts: bool,
    cloud_allowed: bool,
    max_context_chars: int,
) -> dict[str, Any]:
    budget = int(max_context_chars)
    if budget < 2000 or budget > 24000:
        raise AgentTeamPlanningError("Team context limit must be between 2,000 and 24,000 characters.")
    return {
        "include_memory": bool(include_memory),
        "include_knowledge": bool(include_knowledge),
        "include_contacts": bool(include_contacts),
        "cloud_allowed": bool(cloud_allowed),
        "max_context_chars": budget,
    }


def _planner_route(
    source: str,
    conversation_id: str,
    parent: dict[str, Any],
    *,
    owner: bool,
) -> tuple[str, str, str | None, bool]:
    settings = context_engine.ensure_settings(conversation_id)
    scope = dict(app_scopes.DEFAULT_SCOPE) if owner else app_scopes.get_scope_for_source(source)
    cloud_allowed = bool(settings.get("cloud_allowed", True) and (owner or scope.get("cloud_allowed", False)))
    inference = providers.inference_status()
    provider_key = str(inference.get("selected_provider") or "unavailable")
    provider_model = str(inference.get("model") or "")
    provider_override: str | None = None
    if not cloud_allowed:
        try:
            provider_key, provider_model, provider_override = context_chat._private_inference_route(inference)
        except Exception as exc:
            raise AgentTeamPlanningError(str(exc), int(getattr(exc, "status_code", 409))) from exc
    selected_model = (
        provider_model.strip()
        if provider_override == "ollama"
        else (str(parent.get("model") or "") or provider_model).strip()
    )
    if not selected_model:
        raise AgentTeamPlanningError("No inference model is available for Team Planning.", 503)
    return provider_key, selected_model, provider_override, cloud_allowed


def _planner_prompt(parent: dict[str, Any], objective: str, workers: list[dict[str, Any]]) -> list[dict[str, str]]:
    catalog = "\n".join(
        f"- id={int(item['id'])}: {str(item.get('name') or 'Agent')}"
        for item in workers
    )
    parent_instructions = str(parent.get("instructions") or "").strip()
    system = (
        "You are the parent HomeServer Agent creating a bounded specialist Team Plan.\n"
        "Planning only: do not claim work was executed, do not call tools, and do not delegate recursively.\n"
        f"Choose {MIN_PLAN_MEMBERS} to {MAX_PLAN_MEMBERS} DISTINCT specialists only from the exact allow-list below.\n"
        "Give each selected specialist one concrete, non-overlapping task that contributes to the objective.\n"
        "Return JSON only with exactly this shape and no extra keys: "
        '{"members":[{"worker_agent_id":123,"task":"bounded task"}]}.\n'
        "Never invent Agent ids and never include the parent Agent as a worker.\n"
        f"Parent Agent: {str(parent.get('name') or 'Agent')} (id {int(parent['id'])})\n"
        + (f"Parent instructions: {parent_instructions}\n" if parent_instructions else "")
        + "Authorized specialist allow-list:\n"
        + catalog
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Team objective:\n{objective}"},
    ]


def _decorate_plan(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["members"] = _json_list(item.pop("members_json", "[]"))
    item["context"] = _json_object(item.pop("context_json", "{}"))
    item["id"] = int(item["id"])
    item["parent_agent_id"] = int(item["parent_agent_id"]) if item.get("parent_agent_id") is not None else None
    item["team_run_id"] = int(item["team_run_id"]) if item.get("team_run_id") is not None else None
    item["cloud_used"] = bool(item.pop("cloud_used", 0))
    item["version"] = AGENT_TEAM_PLANNING_VERSION
    item["requires_approval"] = item.get("status") == "proposed"
    item["auto_executes"] = False
    return item


def get_plan(plan_id: int, source_app_key: str) -> dict[str, Any]:
    source = _source(source_app_key)
    with db() as connection:
        row = connection.execute(
            """
            SELECT id, source_app_key, conversation_id, parent_agent_id, parent_agent_name,
                   objective, members_json, context_json, provider_key, model, cloud_used,
                   status, team_run_id, created_at, updated_at, decided_at
            FROM agent_team_plans
            WHERE id=? AND source_app_key=?
            LIMIT 1
            """,
            (int(plan_id), source),
        ).fetchone()
    if row is None:
        raise AgentTeamPlanningError("Team Plan not found for this application.", 404)
    return _decorate_plan(row)


def list_plans(
    source_app_key: str,
    *,
    conversation_id: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    source = _source(source_app_key)
    bounded = max(1, min(int(limit), 50))
    query = """
        SELECT id, source_app_key, conversation_id, parent_agent_id, parent_agent_name,
               objective, members_json, context_json, provider_key, model, cloud_used,
               status, team_run_id, created_at, updated_at, decided_at
        FROM agent_team_plans WHERE source_app_key=?
    """
    params: list[Any] = [source]
    if conversation_id:
        query += " AND conversation_id=?"
        params.append(str(conversation_id))
    query += " ORDER BY id DESC LIMIT ?"
    params.append(bounded)
    with db() as connection:
        rows = connection.execute(query, tuple(params)).fetchall()
    return {"version": AGENT_TEAM_PLANNING_VERSION, "items": [_decorate_plan(row) for row in rows]}


def propose_plan(
    source_app_key: str,
    *,
    parent_agent_id: int,
    conversation_id: str,
    objective: str,
    owner: bool,
    current_permissions: set[str] | None = None,
    include_memory: bool = True,
    include_knowledge: bool = True,
    include_contacts: bool = False,
    cloud_allowed: bool = True,
    max_context_chars: int = 12000,
) -> dict[str, Any]:
    source = _source(source_app_key)
    objective_text = str(objective or "").strip()
    if not objective_text:
        raise AgentTeamPlanningError("Team Plan objective is required.")
    if len(objective_text) > MAX_OBJECTIVE_CHARS:
        raise AgentTeamPlanningError(f"Team Plan objective exceeds the {MAX_OBJECTIVE_CHARS:,} character limit.")
    policy = _context_policy(
        include_memory=include_memory,
        include_knowledge=include_knowledge,
        include_contacts=include_contacts,
        cloud_allowed=cloud_allowed,
        max_context_chars=max_context_chars,
    )
    try:
        parent = agent_routing.resolve_agent(source, int(parent_agent_id), owner=owner)
        agent_routing.validate_conversation_agent(source, str(conversation_id), int(parent["id"]))
        worker_payload = agent_workflows.available_workers(source, int(parent["id"]), owner=owner)
    except (agent_routing.AgentRoutingError, agent_workflows.AgentWorkflowError) as exc:
        raise AgentTeamPlanningError(str(exc), int(getattr(exc, "status_code", 422))) from exc
    workers = list(worker_payload.get("items") or [])
    if len(workers) < MIN_PLAN_MEMBERS:
        raise AgentTeamPlanningError("At least two authorized specialist Agents are required for Team Planning.", 409)
    allowed_workers = {int(item["id"]): dict(item) for item in workers}
    provider_key, selected_model, provider_override, conversation_cloud_allowed = _planner_route(
        source,
        str(conversation_id),
        parent,
        owner=owner,
    )
    effective_cloud = bool(policy["cloud_allowed"] and conversation_cloud_allowed)
    if not effective_cloud and provider_override != "ollama":
        # A request can explicitly tighten a cloud-enabled conversation to local-only.
        inference = providers.inference_status()
        try:
            provider_key, selected_model, provider_override = context_chat._private_inference_route(inference)
        except Exception as exc:
            raise AgentTeamPlanningError(str(exc), int(getattr(exc, "status_code", 409))) from exc
    messages = _planner_prompt(parent, objective_text, workers)
    try:
        generated = (
            providers.generate_ollama(messages, model_override=selected_model)
            if provider_override == "ollama" or not effective_cloud
            else providers.generate(messages, model_override=selected_model)
        )
    except providers.ProviderError as exc:
        raise AgentTeamPlanningError(str(exc), 503) from exc
    members = _parse_model_plan(str(generated.get("content") or ""), allowed_workers)
    policy["cloud_allowed"] = effective_cloud
    permission_snapshot = [] if owner else sorted(set(current_permissions or set()))
    with db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO agent_team_plans(
                source_app_key, conversation_id, parent_agent_id, parent_agent_name,
                objective, members_json, context_json, permission_snapshot_json,
                provider_key, model, cloud_used, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed')
            """,
            (
                source,
                str(conversation_id),
                int(parent["id"]),
                str(parent.get("name") or "Agent"),
                objective_text,
                json.dumps(members, ensure_ascii=False, separators=(",", ":")),
                json.dumps(policy, separators=(",", ":")),
                json.dumps(permission_snapshot, separators=(",", ":")),
                str(generated.get("provider") or provider_key),
                str(generated.get("model") or selected_model),
                0 if str(generated.get("provider") or provider_key) == "ollama" else 1,
            ),
        )
        plan_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES (?, ?, 'agent.team_plan.proposed', 'agent_team_plan', ?, ?)
            """,
            (
                _actor_type(source),
                source,
                str(plan_id),
                json.dumps(
                    {
                        "version": AGENT_TEAM_PLANNING_VERSION,
                        "conversation_id": str(conversation_id),
                        "parent_agent_id": int(parent["id"]),
                        "worker_agent_ids": [int(item["worker_agent_id"]) for item in members],
                        "member_count": len(members),
                        "provider": str(generated.get("provider") or provider_key),
                        "model": str(generated.get("model") or selected_model),
                        "cloud_used": str(generated.get("provider") or provider_key) != "ollama",
                        "requires_approval": True,
                        "auto_executes": False,
                    },
                    separators=(",", ":"),
                ),
            ),
        )
    usage = generated.get("usage") if isinstance(generated.get("usage"), dict) else {}
    try:
        usage_service.record_usage(
            event_id=f"agent-team-plan:{plan_id}",
            source_app_key=source,
            compute_source="homeserver_local" if str(generated.get("provider") or provider_key) == "ollama" else "user_provider",
            provider_key=str(generated.get("provider") or provider_key),
            model=str(generated.get("model") or selected_model),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
            billable_tokens=0,
            request_kind="team_plan",
            metadata={
                "plan_id": plan_id,
                "conversation_id": str(conversation_id),
                "parent_agent_id": int(parent["id"]),
                "version": AGENT_TEAM_PLANNING_VERSION,
            },
        )
    except usage_service.UsageError:
        pass
    return get_plan(plan_id, source)


def update_plan(
    plan_id: int,
    source_app_key: str,
    *,
    objective: str,
    members: list[dict[str, Any]],
    owner: bool,
) -> dict[str, Any]:
    source = _source(source_app_key)
    current = get_plan(plan_id, source)
    if current["status"] != "proposed":
        raise AgentTeamPlanningError("Only proposed Team Plans can be edited.", 409)
    objective_text = str(objective or "").strip()
    if not objective_text or len(objective_text) > MAX_OBJECTIVE_CHARS:
        raise AgentTeamPlanningError("Edited Team Plan objective is required and must fit the 16,000 character limit.")
    try:
        parent = agent_routing.resolve_agent(source, int(current["parent_agent_id"]), owner=owner)
        agent_routing.validate_conversation_agent(source, str(current["conversation_id"]), int(parent["id"]))
        workers = agent_workflows.available_workers(source, int(parent["id"]), owner=owner).get("items") or []
    except (agent_routing.AgentRoutingError, agent_workflows.AgentWorkflowError) as exc:
        raise AgentTeamPlanningError(str(exc), int(getattr(exc, "status_code", 422))) from exc
    allowed = {int(item["id"]): dict(item) for item in workers}
    normalized = _validated_members(members, allowed)
    with db() as connection:
        changed = connection.execute(
            """
            UPDATE agent_team_plans
            SET objective=?, members_json=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND source_app_key=? AND status='proposed'
            """,
            (
                objective_text,
                json.dumps(normalized, ensure_ascii=False, separators=(",", ":")),
                int(plan_id),
                source,
            ),
        )
        if changed.rowcount <= 0:
            raise AgentTeamPlanningError("Team Plan is no longer editable.", 409)
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES (?, ?, 'agent.team_plan.edited', 'agent_team_plan', ?, ?)
            """,
            (
                _actor_type(source),
                source,
                str(plan_id),
                json.dumps(
                    {"version": AGENT_TEAM_PLANNING_VERSION, "member_count": len(normalized)},
                    separators=(",", ":"),
                ),
            ),
        )
    return get_plan(plan_id, source)


def _paired_app_tx(connection, source: str) -> dict[str, Any]:
    if not source.startswith("app:") or not source[4:].strip():
        raise AgentTeamPlanningError("Application identity is invalid.", 403)
    row = connection.execute(
        "SELECT id, app_key, status FROM paired_apps WHERE app_key=? LIMIT 1",
        (source[4:].strip(),),
    ).fetchone()
    if row is None or str(row["status"]) != "active":
        raise AgentTeamPlanningError("Application is not authorized for Team Planning.", 403)
    return dict(row)


def _resolve_agent_tx(connection, source: str, agent_id: int, *, owner: bool, app: dict[str, Any] | None) -> dict[str, Any]:
    row = connection.execute(
        "SELECT id, name, instructions, model, is_primary FROM agents WHERE id=? LIMIT 1",
        (int(agent_id),),
    ).fetchone()
    if row is None:
        raise AgentTeamPlanningError("Agent not found.", 404)
    agent = dict(row)
    if owner or bool(agent["is_primary"]):
        return agent
    if app is None:
        raise AgentTeamPlanningError("Application identity is invalid.", 403)
    grant = connection.execute(
        "SELECT allowed FROM app_agent_grants WHERE paired_app_id=? AND agent_id=? LIMIT 1",
        (int(app["id"]), int(agent_id)),
    ).fetchone()
    if grant is None or not bool(grant["allowed"]):
        raise AgentTeamPlanningError("Agent is not authorized for this application.", 403)
    return agent


def approve_plan(
    plan_id: int,
    source_app_key: str,
    *,
    owner: bool,
    current_permissions: set[str] | None = None,
) -> dict[str, Any]:
    source = _source(source_app_key)
    granted = set(current_permissions or set())
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT id, source_app_key, conversation_id, parent_agent_id, parent_agent_name,
                   objective, members_json, context_json, status, team_run_id
            FROM agent_team_plans
            WHERE id=? AND source_app_key=? LIMIT 1
            """,
            (int(plan_id), source),
        ).fetchone()
        if row is None:
            raise AgentTeamPlanningError("Team Plan not found for this application.", 404)
        plan = dict(row)
        if str(plan["status"]) == "approved":
            team_run_id = plan.get("team_run_id")
            if team_run_id is None:
                raise AgentTeamPlanningError("Approved Team Plan is missing its Team Run linkage.", 500)
            existing_id = int(team_run_id)
            # Returning after the transaction closes keeps all v0.51 reads on their normal connection.
            already_approved = True
        else:
            already_approved = False
            if str(plan["status"]) != "proposed":
                raise AgentTeamPlanningError("Rejected Team Plans cannot be approved.", 409)
            if plan.get("parent_agent_id") is None:
                raise AgentTeamPlanningError("The Team Plan parent Agent no longer exists.", 409)
            app = None if owner else _paired_app_tx(connection, source)
            parent = _resolve_agent_tx(connection, source, int(plan["parent_agent_id"]), owner=owner, app=app)
            conversation = connection.execute(
                "SELECT id, agent_id, status FROM conversations WHERE id=? AND source_app_key=? LIMIT 1",
                (str(plan["conversation_id"]), source),
            ).fetchone()
            if conversation is None:
                raise AgentTeamPlanningError("Conversation not found for this application.", 404)
            if str(conversation["status"]) != "active" or conversation["agent_id"] is None:
                raise AgentTeamPlanningError("The Team Plan conversation is no longer active.", 409)
            if int(conversation["agent_id"]) != int(parent["id"]):
                raise AgentTeamPlanningError("The Team Plan conversation is bound to another Agent.", 409)
            raw_members = _json_list(plan.get("members_json"))
            if len(raw_members) < MIN_PLAN_MEMBERS or len(raw_members) > MAX_PLAN_MEMBERS:
                raise AgentTeamPlanningError("Stored Team Plan has an invalid specialist count.", 409)
            seen: set[int] = set()
            approved_members: list[dict[str, Any]] = []
            for index, raw in enumerate(raw_members, start=1):
                worker_id = int(raw.get("worker_agent_id") or 0)
                if worker_id <= 0 or worker_id in seen:
                    raise AgentTeamPlanningError("Stored Team Plan has invalid or duplicate specialist Agents.", 409)
                seen.add(worker_id)
                worker = _resolve_agent_tx(connection, source, worker_id, owner=owner, app=app)
                if int(worker["id"]) == int(parent["id"]):
                    raise AgentTeamPlanningError("The parent Agent cannot be a Team Run specialist.", 409)
                task = str(raw.get("task") or "").strip()
                if not task or len(task) > agent_workflows.MAX_TASK_CHARS:
                    raise AgentTeamPlanningError(f"Stored Team Plan member {index} has an invalid task.", 409)
                approved_members.append({"worker": worker, "task": task, "position": index})
            context = _json_object(plan.get("context_json"))
            context_chars = int(context.get("max_context_chars") or 12000)
            if context_chars < 2000 or context_chars > 24000:
                raise AgentTeamPlanningError("Stored Team Plan context limit is invalid.", 409)
            include_memory = bool(context.get("include_memory", True) and (owner or "memory.read" in granted))
            include_knowledge = bool(context.get("include_knowledge", True) and (owner or "knowledge.search" in granted))
            include_contacts = bool(context.get("include_contacts", False) and (owner or "contacts.read" in granted))
            app_cloud_allowed = True
            if not owner:
                scope_row = connection.execute(
                    "SELECT cloud_allowed FROM app_capability_scopes WHERE paired_app_id=? LIMIT 1",
                    (int(app["id"]),),
                ).fetchone()
                app_cloud_allowed = bool(scope_row["cloud_allowed"]) if scope_row is not None else True
            effective_cloud = bool(context.get("cloud_allowed", True) and app_cloud_allowed)
            permission_snapshot = [] if owner else sorted(granted)
            cursor = connection.execute(
                """
                INSERT INTO agent_team_runs(source_app_key, conversation_id, parent_agent_id, parent_agent_name, objective)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    source,
                    str(plan["conversation_id"]),
                    int(parent["id"]),
                    str(parent.get("name") or "Agent"),
                    str(plan.get("objective") or ""),
                ),
            )
            team_run_id = int(cursor.lastrowid)
            task_ids: list[int] = []
            for member in approved_members:
                worker = member["worker"]
                metadata = {
                    "workflow_version": agent_workflows.AGENT_WORKFLOW_VERSION,
                    "team_run_version": agent_team_runs.AGENT_TEAM_RUN_VERSION,
                    "team_plan_version": AGENT_TEAM_PLANNING_VERSION,
                    "team_plan_id": int(plan_id),
                    "team_run_id": team_run_id,
                    "team_position": int(member["position"]),
                    "created_via": "team_plan_approval_v052",
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
                        str(plan["conversation_id"]),
                        f"team-plan:{int(plan_id)}",
                        member["task"],
                        1 if include_memory else 0,
                        1 if include_knowledge else 0,
                        1 if include_contacts else 0,
                        1 if effective_cloud else 0,
                        context_chars,
                        json.dumps(permission_snapshot, separators=(",", ":")),
                        json.dumps(metadata, separators=(",", ":")),
                    ),
                )
                task_id = int(task_cursor.lastrowid)
                task_ids.append(task_id)
                connection.execute(
                    """
                    INSERT INTO agent_team_run_members(team_run_id, position, task_id, worker_agent_id, worker_agent_name)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (team_run_id, int(member["position"]), task_id, int(worker["id"]), str(worker.get("name") or "Agent")),
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
                                "team_run_version": agent_team_runs.AGENT_TEAM_RUN_VERSION,
                                "team_plan_version": AGENT_TEAM_PLANNING_VERSION,
                                "team_plan_id": int(plan_id),
                                "team_run_id": team_run_id,
                                "parent_agent_id": int(parent["id"]),
                                "worker_agent_id": int(worker["id"]),
                                "conversation_id": str(plan["conversation_id"]),
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
                            "version": agent_team_runs.AGENT_TEAM_RUN_VERSION,
                            "team_plan_version": AGENT_TEAM_PLANNING_VERSION,
                            "team_plan_id": int(plan_id),
                            "conversation_id": str(plan["conversation_id"]),
                            "parent_agent_id": int(parent["id"]),
                            "task_ids": task_ids,
                            "member_count": len(task_ids),
                            "one_hop_only": True,
                        },
                        separators=(",", ":"),
                    ),
                ),
            )
            changed = connection.execute(
                """
                UPDATE agent_team_plans
                SET status='approved', team_run_id=?, decided_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND source_app_key=? AND status='proposed'
                """,
                (team_run_id, int(plan_id), source),
            )
            if changed.rowcount <= 0:
                raise AgentTeamPlanningError("Team Plan changed before approval could complete.", 409)
            connection.execute(
                """
                INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
                VALUES (?, ?, 'agent.team_plan.approved', 'agent_team_plan', ?, ?)
                """,
                (
                    _actor_type(source),
                    source,
                    str(plan_id),
                    json.dumps(
                        {
                            "version": AGENT_TEAM_PLANNING_VERSION,
                            "team_run_id": team_run_id,
                            "task_ids": task_ids,
                            "member_count": len(task_ids),
                            "auto_executes": False,
                        },
                        separators=(",", ":"),
                    ),
                ),
            )
            existing_id = team_run_id
    plan_result = get_plan(plan_id, source)
    team = agent_team_runs.get_team_run(
        existing_id,
        source,
        owner=owner,
        current_permissions=current_permissions,
    )
    return {
        "version": AGENT_TEAM_PLANNING_VERSION,
        "plan": plan_result,
        "team_run": team,
        "already_approved": already_approved,
        "executed": False,
        "next_action": "Review the queued Team Run and explicitly run specialists when ready.",
    }


def reject_plan(plan_id: int, source_app_key: str) -> dict[str, Any]:
    source = _source(source_app_key)
    with db() as connection:
        row = connection.execute(
            "SELECT status FROM agent_team_plans WHERE id=? AND source_app_key=? LIMIT 1",
            (int(plan_id), source),
        ).fetchone()
        if row is None:
            raise AgentTeamPlanningError("Team Plan not found for this application.", 404)
        status = str(row["status"])
        if status == "rejected":
            return get_plan(plan_id, source)
        if status != "proposed":
            raise AgentTeamPlanningError("Approved Team Plans cannot be rejected.", 409)
        changed = connection.execute(
            """
            UPDATE agent_team_plans
            SET status='rejected', decided_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND source_app_key=? AND status='proposed'
            """,
            (int(plan_id), source),
        )
        if changed.rowcount <= 0:
            raise AgentTeamPlanningError("Team Plan changed before rejection could complete.", 409)
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES (?, ?, 'agent.team_plan.rejected', 'agent_team_plan', ?, ?)
            """,
            (
                _actor_type(source),
                source,
                str(plan_id),
                json.dumps({"version": AGENT_TEAM_PLANNING_VERSION}, separators=(",", ":")),
            ),
        )
    return get_plan(plan_id, source)
