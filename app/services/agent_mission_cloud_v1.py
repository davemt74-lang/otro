"""Cloud-facing projection for HomeServer-owned read-only missions.

Only paired VP3 may use these operations; no owner token is minted or echoed.
The HomeServer mission database remains authoritative.
"""
from __future__ import annotations

import uuid
from typing import Any

from ..database import db
from . import agent_mission_runtime as runtime, agent_mission_control as control
from . import agent_routing

CONTRACT = "vp3.agent-missions.cloud.v1"


def _bounded_string(value: Any, label: str, length: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > length:
        raise runtime.MissionError(f"{label} must contain 1 to {length} characters.")
    return text


def cloud_conversation(thread: Any, request_id: str) -> str:
    """Map a Cloud thread to the existing paired-app conversation namespace.

    Thread=0 represents a not-yet-created Cloud chat. Its mission request ID
    supplies a stable identity for retries without merging unrelated missions.
    """
    if isinstance(thread, bool):
        raise runtime.MissionError("Cloud thread ID is invalid.")
    try:
        thread_id = int(thread)
    except (ValueError, TypeError) as exc:
        raise runtime.MissionError("Cloud thread ID is invalid.") from exc
    if not 0 <= thread_id <= 2147483647:
        raise runtime.MissionError("Cloud thread ID is out of range.")
    key = _bounded_string(request_id, "Mission request ID", 128)
    namespace = f"vp3-cloud-thread:{thread_id}" if thread_id else f"vp3-cloud-request:{key}"
    return uuid.uuid5(uuid.NAMESPACE_URL, "app:vp3:" + namespace).hex


def _ensure_conversation(thread: Any, request_id: str, objective: str) -> str:
    cid = cloud_conversation(thread, request_id)
    parent = agent_routing.resolve_agent("app:vp3", owner=False)
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT OR IGNORE INTO conversations(id,agent_id,source_app_key,title) VALUES(?,?,?,?)",
            (cid, int(parent["id"]), "app:vp3", "VP3 Cloud: " + objective[:65]),
        )
        row = connection.execute(
            "SELECT agent_id,source_app_key,status FROM conversations WHERE id=?",
            (cid,),
        ).fetchone()
        if (not row or row["source_app_key"] != "app:vp3"
            or int(row["agent_id"] or 0) != int(parent["id"])
            or row["status"] != "active"):
            raise runtime.MissionError("Paired Cloud conversation is not available.", 409)
    return cid


def _projection(raw: dict, *, detailed: bool = False) -> dict:
    tasks = []
    for task in raw.get("tasks", [])[:4]:
        item = {
            "id": str(task.get("id") or ""),
            "title": str(task.get("title") or "")[:160],
            "role": str(task.get("role") or "")[:80],
            "status": str(task.get("status") or "queued"),
            "attempt": int(task.get("attempt") or 0),
            "started_at": task.get("started_at"),
            "completed_at": task.get("completed_at"),
        }
        if detailed:
            item["result"] = str(task.get("result") or "")[:6000]
            item["error"] = str(task.get("error") or "")[:500]
            item["model"] = str(task.get("model") or "")[:160]
        tasks.append(item)
    result = {
        "id": str(raw.get("id") or ""),
        "objective": str(raw.get("objective") or "")[:2000],
        "status": str(raw.get("status") or ""),
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
        "completed_at": raw.get("completed_at"),
        "tasks": tasks,
        "read_only": True,
        "verified": False,
        "contract": CONTRACT,
    }
    if detailed:
        result["result"] = str(raw.get("result") or "")[:8000]
        result["events"] = [
            {"id": int(e.get("id") or 0), "task_id": e.get("task_id"),
             "kind": str(e.get("kind") or "")[:120],
             "created_at": e.get("created_at")}
            for e in raw.get("events", [])[-40:]
        ]
    return result


def execute(action: str, body: dict) -> dict:
    if action == "list":
        count = body.get("limit", 6)
        if isinstance(count, bool) or not str(count).isdigit() or not 1 <= int(count) <= 8:
            raise runtime.MissionError("Mission list limit must be between 1 and 8.")
        return {"ok": True, "contract": CONTRACT, "items": [
            _projection(row) for row in runtime.list_missions("app:vp3", int(count))
        ]}
    if action == "create":
        objective = _bounded_string(body.get("objective"), "Mission objective", 4000)
        request_id = _bounded_string(body.get("request_id"), "Mission request ID", 128)
        # Important: claim model/mission authority through the source-scoped
        # runtime instead of owner routes or arbitrary Cloud conversation IDs.
        cid = _ensure_conversation(body.get("thread_id", 0), request_id, objective)
        created = runtime.create_mission(
            "app:vp3", conversation_id=cid, objective=objective,
            client_request_id=request_id, owner=False,
        )
        return {"ok": True, "mission": _projection(created, detailed=True)}
    mid = _bounded_string(body.get("mission_id"), "Mission ID", 80)
    # Source-scoped lookup prevents a paired Cloud call from reading owner
    # missions or a different app's mission ID.
    runtime.get_mission("app:vp3", mid)
    if action == "get":
        return {"ok": True, "mission": _projection(runtime.get_mission("app:vp3", mid), detailed=True)}
    if action == "start":
        result = runtime.start_mission("app:vp3", mid)
        return {"ok": True, "mission": _projection(result, detailed=True)}
    if action == "cancel":
        result = runtime.cancel_mission("app:vp3", mid)
        return {"ok": True, "mission": _projection(result, detailed=True)}
    if action == "pause":
        result = control.pause("app:vp3", mid)
        return {"ok": True, "mission": _projection(result, detailed=True)}
    if action == "events":
        after, limit = body.get("after", 0), body.get("limit", 40)
        if isinstance(after, bool) or isinstance(limit, bool):
            raise runtime.MissionError("Event cursor is invalid.")
        try:
            after, limit = int(after), int(limit)
        except (TypeError, ValueError) as exc:
            raise runtime.MissionError("Event cursor is invalid.") from exc
        return {"ok": True, **control.events("app:vp3", mid, after=after, limit=min(60, limit))}
    raise runtime.MissionError("Mission operation is not allowed.", 404)
