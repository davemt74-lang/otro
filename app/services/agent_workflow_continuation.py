from __future__ import annotations

from typing import Any

from . import agent_routing, agent_team_orchestration

AGENT_WORKFLOW_CONTINUATION_VERSION = "v0.54"
MAX_OBJECTIVE_EXCERPT_CHARS = 500
TERMINAL_STATUSES = {"rejected", "synthesized", "cancelled"}


class AgentWorkflowContinuationError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _source(value: str | None) -> str:
    normalized = str(value or "").strip()
    return normalized or "owner"


def _safe_counts(item: dict[str, Any]) -> dict[str, int]:
    raw = (item.get("team_run") or {}).get("counts") or {}
    return {
        "members": max(0, int(raw.get("members") or 0)),
        "queued": max(0, int(raw.get("queued") or 0)),
        "working": max(0, int(raw.get("working") or 0)),
        "completed": max(0, int(raw.get("completed") or 0)),
        "failed": max(0, int(raw.get("failed") or 0)),
        "cancelled": max(0, int(raw.get("cancelled") or 0)),
    }


def _safe_item(item: dict[str, Any]) -> dict[str, Any]:
    next_action = item.get("next_action") if isinstance(item.get("next_action"), dict) else {}
    retry_ids = [
        int(value)
        for value in ((item.get("team_run") or {}).get("retryable_task_ids") or [])
        if int(value) > 0
    ]
    return {
        "plan_id": int(item["plan_id"]),
        "team_run_id": int(item["team_run_id"]) if item.get("team_run_id") is not None else None,
        "parent_agent_id": int(item["parent_agent_id"]) if item.get("parent_agent_id") is not None else None,
        "parent_agent_name": str(item.get("parent_agent_name") or "Agent")[:120],
        "objective": " ".join(str(item.get("objective") or "").split())[:MAX_OBJECTIVE_EXCERPT_CHARS],
        "plan_status": str(item.get("plan_status") or "proposed"),
        "status": str(item.get("status") or "proposed"),
        "next_action": {
            "key": str(next_action.get("key") or "inspect")[:80],
            "label": " ".join(str(next_action.get("label") or "").split())[:500],
        },
        "requires_explicit_action": bool(item.get("requires_explicit_action")),
        "counts": _safe_counts(item),
        "retryable_task_ids": retry_ids,
    }


def conversation_continuation(
    source_app_key: str,
    conversation_id: str,
    *,
    owner: bool,
    current_permissions: set[str] | None = None,
) -> dict[str, Any]:
    source = _source(source_app_key)
    conversation = str(conversation_id or "").strip()
    if not conversation:
        raise AgentWorkflowContinuationError("Conversation id is required.")
    try:
        binding = agent_routing.conversation_binding(source, conversation)
    except agent_routing.AgentRoutingError as exc:
        raise AgentWorkflowContinuationError(str(exc), exc.status_code) from exc
    if not binding.get("available") or not binding.get("agent"):
        raise AgentWorkflowContinuationError("This conversation's parent Agent is no longer available.", 409)

    try:
        payload = agent_team_orchestration.list_orchestrations(
            source,
            owner=owner,
            current_permissions=current_permissions,
            conversation_id=conversation,
            limit=50,
        )
    except agent_team_orchestration.AgentTeamOrchestrationError as exc:
        raise AgentWorkflowContinuationError(str(exc), exc.status_code) from exc

    items = list(payload.get("items") or [])
    safe_items = [_safe_item(item) for item in items]
    active = [item for item in safe_items if str(item.get("status") or "") not in TERMINAL_STATUSES]
    terminal = [item for item in safe_items if str(item.get("status") or "") in TERMINAL_STATUSES]
    current = active[0] if active else None
    latest_terminal = terminal[0] if terminal else None

    return {
        "version": AGENT_WORKFLOW_CONTINUATION_VERSION,
        "conversation_id": conversation,
        "parent": {
            "id": int(binding["agent"]["id"]),
            "name": str(binding["agent"].get("name") or "Agent")[:120],
        },
        "active_count": len(active),
        "workflow_count": len(safe_items),
        "current": current,
        "latest_terminal": latest_terminal,
        "read_only": True,
        "auto_executes": False,
        "actions_via": agent_team_orchestration.AGENT_TEAM_ORCHESTRATION_VERSION,
        "explicit_actions_preserved": True,
    }
