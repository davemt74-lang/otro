from __future__ import annotations

import json
import time
from typing import Any

from ..database import db
from . import brain, context_engine, providers, usage as usage_service


def _apply_context_options(conversation_id: str, options: dict[str, Any] | None) -> dict[str, Any]:
    current = context_engine.ensure_settings(conversation_id)
    if not options:
        return current
    values = {
        "include_memory": current["include_memory"],
        "include_knowledge": current["include_knowledge"],
        "include_contacts": current["include_contacts"],
        "cloud_allowed": current["cloud_allowed"],
        "max_context_chars": current["max_context_chars"],
    }
    for key in values:
        value = options.get(key)
        if value is not None:
            values[key] = value
    return context_engine.update_settings(conversation_id, **values)


def chat(
    source_app_key: str,
    message: str,
    conversation_id: str | None = None,
    *,
    include_memory: bool = True,
    include_knowledge: bool = True,
    include_contacts: bool = False,
    context_options: dict[str, Any] | None = None,
    tool_permissions: set[str] | None = None,
    owner_tools: bool = False,
) -> dict[str, Any]:
    text = message.strip()
    if not text:
        raise brain.BrainError("Message is required.")
    if len(text) > 32000:
        raise brain.BrainError("Message exceeds the 32,000 character limit.")

    agent = brain._primary_agent()
    conversation_id = brain._conversation_for_source(
        source_app_key, conversation_id, int(agent["id"]), text
    )
    settings = _apply_context_options(conversation_id, context_options)

    inference = providers.inference_status()
    provider_key = str(inference.get("selected_provider") or "unavailable")
    provider_model = str(inference.get("model") or "")
    compute_source = str(inference.get("compute_source") or "unavailable")
    if compute_source == "user_provider" and not settings["cloud_allowed"]:
        raise brain.BrainError(
            "This conversation is set to Private. Enable cloud providers for this chat or configure a local Ollama model.",
            409,
        )

    with db() as connection:
        connection.execute(
            "INSERT INTO conversation_messages(conversation_id, role, content, source_app_key) VALUES (?, 'user', ?, ?)",
            (conversation_id, text, source_app_key),
        )
        connection.execute(
            "UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (conversation_id,)
        )

    bundle = context_engine.collect_context(
        int(agent["id"]),
        text,
        conversation_id,
        allow_memory=include_memory,
        allow_knowledge=include_knowledge,
        allow_contacts=include_contacts,
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": context_engine.system_prompt(agent, bundle)},
        *brain._history(conversation_id),
    ]

    selected_model = (agent.get("model") or provider_model).strip()
    with db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO agent_runs(
                conversation_id, source_app_key, provider_key, model,
                memory_count, knowledge_count, contact_count, context_chars
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                conversation_id,
                source_app_key,
                provider_key,
                selected_model,
                len(bundle.memory),
                len(bundle.knowledge),
                len(bundle.contacts),
                bundle.context_chars,
            ),
        )
        run_id = int(cursor.lastrowid)

    started = time.perf_counter()
    tool_state: dict[str, Any] = {
        "policy_enabled": False,
        "allow_write_proposals": False,
        "available": False,
        "max_calls": 3,
        "call_count": 0,
        "run_ids": [],
        "action_request_ids": [],
        "provider_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
    try:
        generated, tool_state = brain._generate_with_agent_tools(
            messages,
            source_app_key=source_app_key,
            selected_model=selected_model,
            granted_permissions=set(tool_permissions or set()),
            owner=owner_tools,
            state=tool_state,
        )
    except providers.ProviderError as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        with db() as connection:
            connection.execute(
                """
                UPDATE agent_runs
                SET status='failed', duration_ms=?, error=?, tool_call_count=?, metadata_json=?, completed_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (duration_ms, str(exc)[:1000], int(tool_state["call_count"]), brain._run_metadata(tool_state), run_id),
            )
        raise brain.BrainError(str(exc), 503) from exc

    duration_ms = int((time.perf_counter() - started) * 1000)
    reply = generated["content"].strip()
    context_event_id = context_engine.record_retrieval(conversation_id, source_app_key, bundle)
    metadata = {
        "provider": generated["provider"],
        "run_id": run_id,
        "tool_call_count": int(tool_state["call_count"]),
        "tool_run_ids": tool_state["run_ids"],
        "action_request_ids": tool_state["action_request_ids"],
        "provider_usage": tool_state["provider_usage"],
        "context_event_id": context_event_id,
        "context_sources": bundle.sources,
    }
    run_metadata = json.loads(brain._run_metadata(tool_state))
    run_metadata.update(
        {
            "context_event_id": context_event_id,
            "context_source_refs": bundle.sources,
            "cloud_allowed": bool(bundle.settings["cloud_allowed"]),
        }
    )

    with db() as connection:
        connection.execute(
            """
            INSERT INTO conversation_messages(conversation_id, role, content, source_app_key, model, metadata_json)
            VALUES (?, 'assistant', ?, ?, ?, ?)
            """,
            (
                conversation_id,
                reply,
                source_app_key,
                generated["model"],
                json.dumps(metadata, separators=(",", ":")),
            ),
        )
        connection.execute(
            "UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (conversation_id,)
        )
        connection.execute(
            """
            UPDATE agent_runs
            SET status='completed', provider_key=?, model=?, duration_ms=?, tool_call_count=?,
                metadata_json=?, completed_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                generated["provider"],
                generated["model"],
                duration_ms,
                int(tool_state["call_count"]),
                json.dumps(run_metadata, separators=(",", ":")),
                run_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json)
            VALUES (?, ?, 'agent.chat', 'conversation', ?, ?)
            """,
            (
                "owner" if source_app_key == "owner" else "app",
                source_app_key,
                conversation_id,
                json.dumps(
                    {
                        "provider": generated["provider"],
                        "model": generated["model"],
                        "memory_count": len(bundle.memory),
                        "knowledge_count": len(bundle.knowledge),
                        "contact_count": len(bundle.contacts),
                        "context_chars": bundle.context_chars,
                        "context_event_id": context_event_id,
                        "tool_call_count": int(tool_state["call_count"]),
                        "action_request_count": len(tool_state["action_request_ids"]),
                        "duration_ms": duration_ms,
                    },
                    separators=(",", ":"),
                ),
            ),
        )

    actual_compute_source = "homeserver_local" if generated["provider"] == "ollama" else "user_provider"
    provider_usage = tool_state.get("provider_usage", {})
    try:
        usage_service.record_usage(
            event_id=f"agent-run:{run_id}",
            source_app_key=source_app_key,
            compute_source=actual_compute_source,
            provider_key=generated["provider"],
            model=generated["model"],
            prompt_tokens=int(provider_usage.get("prompt_tokens", 0)),
            completion_tokens=int(provider_usage.get("completion_tokens", 0)),
            total_tokens=int(provider_usage.get("total_tokens", 0)),
            billable_tokens=0,
            request_kind="chat",
            metadata={
                "conversation_id": conversation_id,
                "run_id": run_id,
                "context_event_id": context_event_id,
                "context_chars": bundle.context_chars,
                "context_source_counts": bundle.counts,
            },
        )
    except usage_service.UsageError:
        pass

    return {
        "conversation_id": conversation_id,
        "reply": reply,
        "provider": generated["provider"],
        "model": generated["model"],
        "compute_source": actual_compute_source,
        "cloud_tokens_debited": 0,
        "usage": provider_usage,
        "run_id": run_id,
        "context": {
            **bundle.counts,
            "context_chars": bundle.context_chars,
            "sources": bundle.sources,
            "settings": bundle.settings,
        },
        "tools": tool_state,
    }
