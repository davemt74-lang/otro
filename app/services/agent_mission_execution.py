"""A5: governed per-worker provider binding and a model-only execution boundary.

No arbitrary provider URL, tool schema, filesystem, browser context, process,
network client or API key is ever supplied to a temporary mission worker.
"""
from __future__ import annotations

from ..database import db
from . import agent_mission_runtime as mission
from . import providers

PROVIDERS = ("auto", "ollama", "anthropic", "openai", "openrouter")
MODE = "isolated_model"


def _choice(source: str, conversation: str, key: str) -> dict:
    current_key, current_model, private = mission._route(source, conversation)
    key = str(key or "").strip().lower()
    if key not in PROVIDERS:
        raise mission.MissionError("Provider is not in the supported worker allowlist.", 422)
    selected = current_key if key == "auto" else key
    if private and selected != "ollama":
        raise mission.MissionError("Local-only workers cannot use external inference.", 403)
    if selected == "ollama" and private:
        local = providers.get_ollama()
        if not local.get("enabled") or not str(local.get("model") or "").strip():
            raise mission.MissionError("A local Ollama model is required.", 409)
        model = str(local["model"])
    else:
        available = {
            str(p.get("provider_key")): p
            for p in providers.list_inference_providers()
            if p.get("ready")
        }
        if selected not in available:
            raise mission.MissionError("Selected worker provider is not ready.", 409)
        model = str(available[selected].get("model") or "").strip()
        if not model:
            raise mission.MissionError("Selected worker provider has no configured model.", 409)
    return {
        "provider_key": key, "effective_provider": selected,
        "model": model, "environment": MODE,
    }


def list_profiles(source: str, mid: str) -> dict:
    snapshot = mission.get_mission(source, mid)
    _choice(source, str(snapshot["conversation_id"]), "auto")
    with db() as conn:
        rows = conn.execute(
            "SELECT task_id,provider_key FROM agent_mission_execution_v1 "
            "WHERE mission_id=?", (mid,)
        ).fetchall()
    configured = {str(row["task_id"]): str(row["provider_key"]) for row in rows}
    tasks = []
    for task in snapshot["tasks"]:
        key = configured.get(str(task["id"]), "auto")
        try:
            resolved = _choice(source, str(snapshot["conversation_id"]), key)
            ready = True
        except mission.MissionError:
            resolved = {
                "provider_key": key, "effective_provider": "",
                "model": "", "environment": MODE,
            }
            ready = False
        tasks.append({
            "task_id": task["id"], "status": task["status"],
            "requested_provider": key, "effective_provider": resolved["effective_provider"],
            "model": resolved["model"], "environment": MODE, "ready": ready,
        })
    choices = []
    for key in PROVIDERS:
        try:
            row = _choice(source, str(snapshot["conversation_id"]), key)
        except mission.MissionError:
            continue
        choices.append({
            "provider_key": key, "effective_provider": row["effective_provider"],
            "model": row["model"],
        })
    return {"mission_id": mid, "tasks": tasks, "providers": choices,
            "tools_enabled": False, "environment": MODE}


def configure(source: str, mid: str, tid: str, provider_key: str) -> dict:
    snapshot = mission.get_mission(source, mid)
    current = next((t for t in snapshot["tasks"] if t["id"] == tid), None)
    if current is None:
        raise mission.MissionError("Worker task is not in this mission.", 404)
    selected = _choice(source, str(snapshot["conversation_id"]), provider_key)
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT t.status,m.status AS mission_status FROM agent_mission_tasks_v1 t "
            "JOIN agent_missions_v1 m ON m.id=t.mission_id "
            "WHERE t.id=? AND m.id=? AND m.source_app_key=?",
            (tid, mid, source)
        ).fetchone()
        if not row or row["status"] != "queued" or row["mission_status"] not in ("planned", "running"):
            raise mission.MissionError("Only queued workers may change inference provider.", 409)
        conn.execute(
            "INSERT INTO agent_mission_execution_v1(task_id,mission_id,provider_key) "
            "VALUES(?,?,?) ON CONFLICT(task_id) DO UPDATE SET "
            "provider_key=excluded.provider_key,model_override='',"
            "updated_at=CURRENT_TIMESTAMP",
            (tid, mid, selected["provider_key"])
        )
        mission._event(conn, mid, "worker.provider_configured", tid, {
            "provider": selected["provider_key"], "environment": MODE
        })
    return {
        "task_id": tid, "requested_provider": selected["provider_key"],
        "effective_provider": selected["effective_provider"],
        "model": selected["model"], "environment": MODE, "tools_enabled": False,
    }


def execute(source: str, conversation: str, tid: str, messages: list[dict]) -> tuple[str, str, str]:
    """Run one task in a fresh model context; dependencies are explicit inputs."""
    if len(messages) != 2 or [m.get("role") for m in messages] != ["system", "user"]:
        raise mission.MissionError("Isolated workers accept only system and user messages.", 422)
    if any(not isinstance(m.get("content"), str) or len(m["content"]) > 28000 for m in messages):
        raise mission.MissionError("Worker model context exceeded the bounded input.", 422)
    # Recheck paired-app grants and conversation privacy immediately before inference.
    _, _, private = mission._route(source, conversation)
    with db() as conn:
        row = conn.execute(
            "SELECT provider_key FROM agent_mission_execution_v1 WHERE task_id=?", (tid,)
        ).fetchone()
    key = str(row["provider_key"]) if row else "auto"
    binding = _choice(source, conversation, key)
    if private and binding["effective_provider"] != "ollama":
        raise mission.MissionError("Local-only mission provider conflict.", 403)
    if key == "auto":
        return mission._infer(source, conversation, messages)
    generated = providers.generate_for_provider(
        messages, binding["effective_provider"], model_override=binding["model"]
    )
    return (
        str(generated["content"]).strip(), binding["effective_provider"],
        str(generated.get("model") or binding["model"]),
    )
