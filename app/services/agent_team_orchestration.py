from __future__ import annotations

from typing import Any

from . import agent_team_planning, agent_team_runs

AGENT_TEAM_ORCHESTRATION_VERSION = "v0.53"


class AgentTeamOrchestrationError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _source(value: str | None) -> str:
    normalized = str(value or "").strip()
    return normalized or "owner"


def _team_for_plan(
    plan: dict[str, Any],
    source: str,
    *,
    owner: bool,
    current_permissions: set[str] | None,
) -> dict[str, Any] | None:
    status = str(plan.get("status") or "proposed")
    if status != "approved":
        return None
    team_run_id = plan.get("team_run_id")
    if team_run_id is None:
        raise AgentTeamOrchestrationError("Approved Team Plan is missing its Team Run linkage.", 500)
    try:
        return agent_team_runs.get_team_run(
            int(team_run_id),
            source,
            owner=owner,
            current_permissions=current_permissions,
        )
    except agent_team_runs.AgentTeamRunError as exc:
        raise AgentTeamOrchestrationError(str(exc), exc.status_code) from exc


def _allowed_actions(plan_status: str, team: dict[str, Any] | None) -> list[str]:
    if plan_status == "proposed":
        return ["edit", "approve", "reject"]
    if plan_status != "approved" or team is None:
        return []
    status = str(team.get("status") or "queued")
    actions: list[str] = []
    if bool(team.get("can_run")):
        actions.append("run")
    if team.get("retryable_task_ids"):
        actions.append("retry")
    if bool(team.get("can_prepare")):
        actions.append("prepare")
    if status == "prepared":
        actions.append("parent_chat")
    return actions


def _next_action(plan_status: str, team: dict[str, Any] | None) -> dict[str, Any]:
    if plan_status == "proposed":
        return {
            "key": "review_plan",
            "label": "Review the proposed specialists and explicitly approve or reject the plan.",
            "requires_explicit_action": True,
        }
    if plan_status == "rejected":
        return {
            "key": "none",
            "label": "This Team Plan was rejected and created no Team Run.",
            "requires_explicit_action": False,
        }
    if team is None:
        return {
            "key": "repair_linkage",
            "label": "The approved Team Plan is missing its linked Team Run.",
            "requires_explicit_action": False,
        }
    status = str(team.get("status") or "queued")
    if status == "queued":
        return {
            "key": "run_specialists",
            "label": "Explicitly run the queued specialists when ready.",
            "requires_explicit_action": True,
        }
    if status == "running":
        return {
            "key": "await_specialists",
            "label": "Specialist work is currently running.",
            "requires_explicit_action": False,
        }
    if status == "partial":
        retryable = [int(value) for value in team.get("retryable_task_ids") or []]
        if retryable:
            return {
                "key": "retry_failed",
                "label": "Explicitly retry the failed specialist tasks before synthesis.",
                "requires_explicit_action": True,
                "task_ids": retryable,
            }
        if bool(team.get("can_run")):
            return {
                "key": "run_remaining",
                "label": "Explicitly run the remaining queued specialists.",
                "requires_explicit_action": True,
            }
        return {
            "key": "resolve_partial",
            "label": "Resolve the partial Team Run before synthesis can continue.",
            "requires_explicit_action": True,
        }
    if status == "completed":
        return {
            "key": "prepare_synthesis",
            "label": "Explicitly prepare the exact specialist result set for the parent Agent.",
            "requires_explicit_action": True,
        }
    if status == "prepared":
        return {
            "key": "parent_chat",
            "label": "Send the parent Agent a message to synthesize the prepared specialist results.",
            "requires_explicit_action": True,
        }
    if status == "synthesized":
        return {
            "key": "complete",
            "label": "The parent Agent has synthesized this Team Run.",
            "requires_explicit_action": False,
        }
    if status == "cancelled":
        return {
            "key": "none",
            "label": "The linked Team Run was cancelled.",
            "requires_explicit_action": False,
        }
    return {
        "key": "inspect",
        "label": f"Inspect Team Run status: {status}.",
        "requires_explicit_action": False,
    }


def _decorate(plan: dict[str, Any], team: dict[str, Any] | None) -> dict[str, Any]:
    plan_status = str(plan.get("status") or "proposed")
    lifecycle_status = plan_status if plan_status != "approved" else str((team or {}).get("status") or "queued")
    next_action = _next_action(plan_status, team)
    return {
        "version": AGENT_TEAM_ORCHESTRATION_VERSION,
        "source_app_key": str(plan.get("source_app_key") or "owner"),
        "conversation_id": str(plan.get("conversation_id") or ""),
        "plan_id": int(plan["id"]),
        "team_run_id": int(plan["team_run_id"]) if plan.get("team_run_id") is not None else None,
        "parent_agent_id": plan.get("parent_agent_id"),
        "parent_agent_name": str(plan.get("parent_agent_name") or "Agent"),
        "objective": str(plan.get("objective") or ""),
        "plan_status": plan_status,
        "status": lifecycle_status,
        "allowed_actions": _allowed_actions(plan_status, team),
        "next_action": next_action,
        "requires_explicit_action": bool(next_action.get("requires_explicit_action")),
        "auto_executes": False,
        "one_hop_only": True,
        "synthesis_via_parent_chat": True,
        "plan": plan,
        "team_run": team,
    }


def get_orchestration(
    plan_id: int,
    source_app_key: str,
    *,
    owner: bool,
    current_permissions: set[str] | None = None,
) -> dict[str, Any]:
    source = _source(source_app_key)
    try:
        plan = agent_team_planning.get_plan(int(plan_id), source)
    except agent_team_planning.AgentTeamPlanningError as exc:
        raise AgentTeamOrchestrationError(str(exc), exc.status_code) from exc
    team = _team_for_plan(
        plan,
        source,
        owner=owner,
        current_permissions=current_permissions,
    )
    return _decorate(plan, team)


def list_orchestrations(
    source_app_key: str,
    *,
    owner: bool,
    current_permissions: set[str] | None = None,
    conversation_id: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    source = _source(source_app_key)
    try:
        plans = agent_team_planning.list_plans(
            source,
            conversation_id=conversation_id,
            limit=limit,
        ).get("items") or []
    except agent_team_planning.AgentTeamPlanningError as exc:
        raise AgentTeamOrchestrationError(str(exc), exc.status_code) from exc
    return {
        "version": AGENT_TEAM_ORCHESTRATION_VERSION,
        "items": [
            get_orchestration(
                int(plan["id"]),
                source,
                owner=owner,
                current_permissions=current_permissions,
            )
            for plan in plans
        ],
    }


def _approved_team_id(
    plan_id: int,
    source: str,
    *,
    owner: bool,
    current_permissions: set[str] | None,
) -> int:
    orchestration = get_orchestration(
        plan_id,
        source,
        owner=owner,
        current_permissions=current_permissions,
    )
    if orchestration["plan_status"] != "approved" or orchestration["team_run_id"] is None:
        raise AgentTeamOrchestrationError("Team Plan must be explicitly approved before Team Run actions are available.", 409)
    return int(orchestration["team_run_id"])


def run_plan_team(
    plan_id: int,
    source_app_key: str,
    *,
    owner: bool,
    current_permissions: set[str] | None = None,
) -> dict[str, Any]:
    source = _source(source_app_key)
    team_run_id = _approved_team_id(
        plan_id,
        source,
        owner=owner,
        current_permissions=current_permissions,
    )
    try:
        agent_team_runs.run_team(
            team_run_id,
            source,
            owner=owner,
            current_permissions=current_permissions,
        )
    except agent_team_runs.AgentTeamRunError as exc:
        raise AgentTeamOrchestrationError(str(exc), exc.status_code) from exc
    return get_orchestration(
        plan_id,
        source,
        owner=owner,
        current_permissions=current_permissions,
    )


def retry_plan_member(
    plan_id: int,
    task_id: int,
    source_app_key: str,
    *,
    owner: bool,
    current_permissions: set[str] | None = None,
) -> dict[str, Any]:
    source = _source(source_app_key)
    team_run_id = _approved_team_id(
        plan_id,
        source,
        owner=owner,
        current_permissions=current_permissions,
    )
    try:
        agent_team_runs.retry_member(
            team_run_id,
            int(task_id),
            source,
            owner=owner,
            current_permissions=current_permissions,
        )
    except agent_team_runs.AgentTeamRunError as exc:
        raise AgentTeamOrchestrationError(str(exc), exc.status_code) from exc
    return get_orchestration(
        plan_id,
        source,
        owner=owner,
        current_permissions=current_permissions,
    )


def prepare_plan_synthesis(
    plan_id: int,
    source_app_key: str,
    *,
    owner: bool,
    current_permissions: set[str] | None = None,
) -> dict[str, Any]:
    source = _source(source_app_key)
    team_run_id = _approved_team_id(
        plan_id,
        source,
        owner=owner,
        current_permissions=current_permissions,
    )
    try:
        agent_team_runs.prepare_team_synthesis(
            team_run_id,
            source,
            owner=owner,
            current_permissions=current_permissions,
        )
    except agent_team_runs.AgentTeamRunError as exc:
        raise AgentTeamOrchestrationError(str(exc), exc.status_code) from exc
    return get_orchestration(
        plan_id,
        source,
        owner=owner,
        current_permissions=current_permissions,
    )
