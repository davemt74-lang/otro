from __future__ import annotations

from typing import Any

from ..database import db
from . import agent_workflow_continuation

AGENT_WORKFLOW_RESUME_VERSION = "v0.55"
MAX_RESUME_CONVERSATIONS = 50
MAX_SCAN_CONVERSATIONS = 250
MAX_TITLE_CHARS = 120


class AgentWorkflowResumeError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _source(value: str | None) -> str:
    normalized = str(value or "").strip()
    return normalized or "owner"


def _bounded_limit(value: int) -> int:
    return max(1, min(int(value), MAX_RESUME_CONVERSATIONS))


def _candidate_conversations(source_app_key: str) -> tuple[list[dict[str, Any]], bool]:
    with db() as connection:
        rows = connection.execute(
            """
            SELECT c.id, c.title, c.updated_at, MAX(p.id) AS latest_plan_id
            FROM conversations c
            JOIN agent_team_plans p
              ON p.source_app_key=c.source_app_key
             AND p.conversation_id=c.id
             AND p.status IN ('proposed','approved')
            WHERE c.source_app_key=?
              AND c.status='active'
            GROUP BY c.id, c.title, c.updated_at
            ORDER BY latest_plan_id DESC, c.id DESC
            LIMIT ?
            """,
            (source_app_key, MAX_SCAN_CONVERSATIONS + 1),
        ).fetchall()
    truncated = len(rows) > MAX_SCAN_CONVERSATIONS
    return [dict(row) for row in rows[:MAX_SCAN_CONVERSATIONS]], truncated


def _retryable_ids(values: Any) -> list[int]:
    ids: list[int] = []
    for raw in values or []:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value > 0 and value not in ids:
            ids.append(value)
    return ids


def _resume_item(row: dict[str, Any], continuation: dict[str, Any]) -> dict[str, Any] | None:
    current = continuation.get("current")
    if not isinstance(current, dict):
        return None
    plan_id = int(current.get("plan_id") or 0)
    if plan_id < 1:
        return None
    next_action = current.get("next_action") if isinstance(current.get("next_action"), dict) else {}
    return {
        "conversation_id": str(row["id"]),
        "title": " ".join(str(row.get("title") or "Conversation").split())[:MAX_TITLE_CHARS],
        "conversation_updated_at": row.get("updated_at"),
        "parent": dict(continuation.get("parent") or {}),
        "plan_id": plan_id,
        "team_run_id": int(current["team_run_id"]) if current.get("team_run_id") is not None else None,
        "workflow_status": str(current.get("status") or "proposed"),
        "plan_status": str(current.get("plan_status") or "proposed"),
        "objective": str(current.get("objective") or "")[:500],
        "next_action": {
            "key": str(next_action.get("key") or "inspect")[:80],
            "label": " ".join(str(next_action.get("label") or "Review workflow").split())[:500],
        },
        "requires_explicit_action": bool(current.get("requires_explicit_action")),
        "counts": dict(current.get("counts") or {}),
        "retryable_task_ids": _retryable_ids(current.get("retryable_task_ids")),
        "resume_available": True,
    }


def resume_index(
    source_app_key: str,
    *,
    owner: bool,
    current_permissions: set[str] | None = None,
    limit: int = MAX_RESUME_CONVERSATIONS,
) -> dict[str, Any]:
    source = _source(source_app_key)
    result_limit = _bounded_limit(limit)
    items: list[dict[str, Any]] = []
    # Candidate scanning is intentionally independent from the requested result
    # count. Approved plans can remain in storage after their derived v0.53 state
    # becomes terminal, so filtering only the first N candidates could hide an
    # older still-resumable workflow behind newer completed threads. Candidates
    # are ranked by Plan id rather than conversation activity because creating a
    # v0.52 Team Plan does not mutate conversations.updated_at.
    candidates, scan_truncated = _candidate_conversations(source)
    for row in candidates:
        try:
            continuation = agent_workflow_continuation.conversation_continuation(
                source,
                str(row["id"]),
                owner=owner,
                current_permissions=current_permissions,
            )
        except agent_workflow_continuation.AgentWorkflowContinuationError as exc:
            # A paired app can retain a historical conversation after its secondary
            # Agent grant is revoked. Omit that thread rather than disclosing it.
            # Other integrity failures remain visible instead of being hidden by
            # the resume projection.
            if exc.status_code in {403, 404}:
                continue
            raise AgentWorkflowResumeError(str(exc), exc.status_code) from exc
        item = _resume_item(row, continuation)
        if item is not None:
            items.append(item)

    items.sort(
        key=lambda item: (
            int(item.get("plan_id") or 0),
            str(item.get("conversation_updated_at") or ""),
            str(item.get("conversation_id") or ""),
        ),
        reverse=True,
    )
    resumable_count = len(items)
    visible_items = items[:result_limit]
    return {
        "version": AGENT_WORKFLOW_RESUME_VERSION,
        "items": visible_items,
        "resumable_count": resumable_count,
        "suggested": dict(visible_items[0]) if visible_items else None,
        "scan_limit": MAX_SCAN_CONVERSATIONS,
        "scan_truncated": scan_truncated,
        "read_only": True,
        "auto_executes": False,
        "navigation_only": True,
        "continuation_version": agent_workflow_continuation.AGENT_WORKFLOW_CONTINUATION_VERSION,
        "explicit_actions_preserved": True,
    }
