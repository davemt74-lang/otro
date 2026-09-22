from __future__ import annotations

import json
from typing import Any

from ..database import db
from . import agent_routing, brain

MEETING_CARD_VERSION = "v0.40"
_MAX_ITEMS = 12


def _clean(value: Any, limit: int = 4000) -> str:
    return " ".join(str(value or "").split())[:limit]


def _safe_rows(value: Any, key: str, extras: tuple[str, ...] = ()) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, str]] = []
    for raw in value[:_MAX_ITEMS]:
        if not isinstance(raw, dict):
            continue
        primary = _clean(raw.get(key), 1200)
        if not primary:
            continue
        item = {key: primary}
        for extra in extras:
            text = _clean(raw.get(extra), 500)
            if text:
                item[extra] = text
        rows.append(item)
    return rows


def safe_card_payload(
    *,
    meeting_id: str,
    title: str,
    status: str,
    duration_ms: int,
    segment_count: int,
    snapshot: dict[str, Any] | None,
    source_hash: str = "",
) -> dict[str, Any]:
    data = snapshot if isinstance(snapshot, dict) else {}
    return {
        "card_type": "meeting",
        "version": MEETING_CARD_VERSION,
        "meeting_id": str(meeting_id or "")[:64],
        "title": _clean(title or "Meeting", 240) or "Meeting",
        "status": _clean(status or "completed", 40),
        "duration_ms": max(0, int(duration_ms or 0)),
        "segment_count": max(0, int(segment_count or 0)),
        "source_hash": str(source_hash or "")[:64],
        "summary": _clean(data.get("summary"), 6000),
        "key_points": _safe_rows(data.get("key_points"), "text"),
        "decisions": _safe_rows(data.get("decisions"), "decision"),
        "actions": _safe_rows(data.get("actions"), "action", ("owner", "due_date")),
        "questions": _safe_rows(data.get("questions"), "question"),
        "risks": _safe_rows(data.get("risks"), "risk"),
        "topics": _safe_rows(data.get("topics"), "topic"),
        "task_candidates": _safe_rows(data.get("task_candidates"), "title", ("owner", "due_date")),
        "crm_candidates": _safe_rows(data.get("crm_candidates"), "suggested_update", ("contact", "signal")),
        "follow_up_draft": _clean(data.get("follow_up_draft"), 6000),
        "agent_brief": _clean(data.get("agent_brief"), 6000),
    }


def create(
    *,
    meeting_id: str,
    title: str,
    status: str,
    duration_ms: int,
    segment_count: int,
    snapshot: dict[str, Any] | None,
    source_hash: str = "",
) -> dict[str, Any]:
    card = safe_card_payload(
        meeting_id=meeting_id,
        title=title,
        status=status,
        duration_ms=duration_ms,
        segment_count=segment_count,
        snapshot=snapshot,
        source_hash=source_hash,
    )
    agent = agent_routing.resolve_agent("owner", owner=True)
    conversation_id = brain._conversation_for_source(
        "owner",
        None,
        int(agent["id"]),
        f"Meeting: {card['title']}",
    )
    summary = card["summary"] or (
        "Meeting interrupted before intelligence could be finalized."
        if status == "interrupted"
        else "Meeting completed. No intelligence summary was available."
    )
    counts = []
    if card["decisions"]:
        counts.append(f"{len(card['decisions'])} decision{'s' if len(card['decisions']) != 1 else ''}")
    if card["actions"]:
        counts.append(f"{len(card['actions'])} action{'s' if len(card['actions']) != 1 else ''}")
    if card["questions"]:
        counts.append(f"{len(card['questions'])} question{'s' if len(card['questions']) != 1 else ''}")
    content = summary
    if counts:
        content += "\n\n" + " · ".join(counts)

    metadata = json.dumps(card, ensure_ascii=False, separators=(",", ":"))
    with db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO conversation_messages(
                conversation_id, role, content, source_app_key, model, metadata_json
            ) VALUES (?, 'assistant', ?, 'owner', 'vp3-meeting-intelligence', ?)
            """,
            (conversation_id, content, metadata),
        )
        message_id = int(cursor.lastrowid)
        connection.execute(
            "UPDATE conversations SET title=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (f"Meeting · {card['title']}"[:120], conversation_id),
        )
        connection.execute(
            """
            INSERT INTO activity_log(
                actor_type, actor_key, action, resource_type, resource_key, metadata_json
            ) VALUES ('system', 'physical-meeting', 'meeting.card.created', 'conversation', ?, ?)
            """,
            (
                conversation_id,
                json.dumps(
                    {
                        "meeting_id": card["meeting_id"],
                        "message_id": message_id,
                        "status": card["status"],
                        "segment_count": card["segment_count"],
                    },
                    separators=(",", ":"),
                ),
            ),
        )
    return {
        "conversation_id": conversation_id,
        "message_id": message_id,
        "card": card,
    }
