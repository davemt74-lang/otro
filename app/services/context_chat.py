from __future__ import annotations

import json
import time
from typing import Any

from ..database import db
from . import brain, canonical_context, context_engine, providers, usage as usage_service


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


def _private_inference_route(inference: dict[str, Any]) -> tuple[str, str, str | None]:
    selected_provider = str(inference.get("selected_provider") or "")
    selected_model = str(inference.get("model") or "")
    compute_source = str(inference.get("compute_source") or "")
    if selected_provider == "ollama" and compute_source == "homeserver_local":
        return "ollama", selected_model, "ollama"

    for item in inference.get("providers") or []:
        if not isinstance(item, dict):
            continue
        if item.get("provider_key") != "ollama" or not item.get("ready"):
            continue
        model = str(item.get("model") or "").strip()
        if model:
            return "ollama", model, "ollama"

    raise brain.BrainError(
        "This conversation is set to Private. Configure and enable a local Ollama model to continue, or enable cloud providers for this chat.",
        409,
    )


def _safe_run_metadata(
    tool_state: dict[str, Any],
    context: canonical_context.CanonicalContext,
    *,
    context_event_id: int | None = None,
) -> dict[str, Any]:
    return {
        "tool_run_ids": list(tool_state.get("run_ids") or []),
        "action_request_ids": list(tool_state.get("action_request_ids") or []),
        "provider_usage": dict(tool_state.get("provider_usage") or {}),
        "context_event_id": context_event_id,
        "canonical_context_version": canonical_context.CANONICAL_CONTEXT_VERSION,
        "context_provenance": context.provenance,
        "context_budget": context.budget,
        "cloud_allowed": context.cloud_allowed,
        "scope_enforced": True,
        "collaboration_version": context.collaboration.get("version"),
        "collaboration_sources": context.collaboration_sources,
    }


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

    granted_permissions = set(tool_permissions or set())
    agent = brain._primary_agent()
    conversation_id = brain._conversation_for_source(
        source_app_key, conversation_id, int(agent["id"]), text
    )
    settings = _apply_context_options(conversation_id, context_options)

    canonical = canonical_context.build_authorized_context(
        agent_id=int(agent["id"]),
        query=text,
        source_app_key=source_app_key,
        permissions=granted_permissions,
        owner=owner_tools,
        include_memory=include_memory,
        include_knowledge=include_knowledge,
        include_contacts=include_contacts,
        settings=settings,
        max_context_chars=int(settings["max_context_chars"]),
        cloud_allowed=bool(settings["cloud_allowed"]),
        surface_context=None,
        include_collaboration=True,
    )
    bundle = canonical.bundle

    inference = providers.inference_status()
    provider_key = str(inference.get("selected_provider") or "unavailable")
    provider_model = str(inference.get("model") or "")
    provider_override: str | None = None
    if not canonical.cloud_allowed:
        provider_key, provider_model, provider_override = _private_inference_route(inference)

    with db() as connection:
        connection.execute(
            "INSERT INTO conversation_messages(conversation_id, role, content, source_app_key) VALUES (?, 'user', ?, ?)",
            (conversation_id, text, source_app_key),
        )
        connection.execute(
            "UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (conversation_id,)
        )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": canonical_context.system_prompt(agent, canonical)},
        *brain._history(conversation_id),
    ]
    selected_model = (
        provider_model.strip()
        if provider_override == "ollama"
        else (agent.get("model") or provider_model).strip()
    )

    with db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO agent_runs(
                conversation_id, source_app_key, provider_key, model,
                memory_count, knowledge_count, contact_count, awareness_count, context_chars
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                conversation_id,
                source_app_key,
                provider_key,
                selected_model,
                len(bundle.memory),
                len(bundle.knowledge),
                len(bundle.contacts),
                len(canonical.awareness_items),
                canonical.total_context_chars,
            ),
        )
        run_id = int(cursor.lastrowid)

    started = time.perf_counter()
    tool_state: dict[str, Any] = {}
    try:
        generated, tool_state = brain._generate_with_agent_tools(
            messages,
            source_app_key=source_app_key,
            selected_model=selected_model,
            granted_permissions=canonical.model_tool_permissions,
            owner=owner_tools,
            state=tool_state,
            provider_key=provider_override,
        )
    except providers.ProviderError as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        failed_metadata = _safe_run_metadata(tool_state, canonical)
        with db() as connection:
            connection.execute(
                """
                UPDATE agent_runs
                SET status='failed', duration_ms=?, error=?, tool_call_count=?, metadata_json=?, completed_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    duration_ms,
                    str(exc)[:1000],
                    int(tool_state.get("call_count") or 0),
                    json.dumps(failed_metadata, separators=(",", ":")),
                    run_id,
                ),
            )
        raise brain.BrainError(str(exc), 503) from exc

    duration_ms = int((time.perf_counter() - started) * 1000)
    reply = str(generated.get("content") or "").strip()
    if not reply:
        raise brain.BrainError("Inference provider returned no final response text.", 503)

    # Preserve the existing conversation-context history projection for UI
    # compatibility. v4.30 provenance for all context layers is stored beside
    # the run and returned separately without persisting private excerpts.
    context_event_id = context_engine.record_retrieval(conversation_id, source_app_key, bundle)
    metadata = {
        "provider": generated["provider"],
        "run_id": run_id,
        "tool_call_count": int(tool_state.get("call_count") or 0),
        "tool_run_ids": list(tool_state.get("run_ids") or []),
        "action_request_ids": list(tool_state.get("action_request_ids") or []),
        "provider_usage": dict(tool_state.get("provider_usage") or {}),
        "context_event_id": context_event_id,
        "context_sources": canonical.source_refs,
        "canonical_context_version": canonical_context.CANONICAL_CONTEXT_VERSION,
        "context_provenance": canonical.provenance,
        "context_budget": canonical.budget,
        "scope_enforced": not owner_tools,
    }
    run_metadata = _safe_run_metadata(tool_state, canonical, context_event_id=context_event_id)
    run_metadata["scope_enforced"] = not owner_tools

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
                int(tool_state.get("call_count") or 0),
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
                        "awareness_count": len(canonical.awareness_items),
                        "context_chars": canonical.total_context_chars,
                        "canonical_context_version": canonical_context.CANONICAL_CONTEXT_VERSION,
                        "context_event_id": context_event_id,
                        "tool_call_count": int(tool_state.get("call_count") or 0),
                        "action_request_count": len(tool_state.get("action_request_ids") or []),
                        "duration_ms": duration_ms,
                        "scope_enforced": not owner_tools,
                    },
                    separators=(",", ":"),
                ),
            ),
        )

    actual_compute_source = "homeserver_local" if generated["provider"] == "ollama" else "user_provider"
    provider_usage = dict(tool_state.get("provider_usage") or {})
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
                "context_chars": canonical.total_context_chars,
                "canonical_context_version": canonical_context.CANONICAL_CONTEXT_VERSION,
                "scope_enforced": not owner_tools,
                "context_source_counts": {
                    **bundle.counts,
                    "awareness_count": len(canonical.awareness_items),
                    "collaboration_memory_count": canonical.collaboration_memory_count,
                    "collaboration_knowledge_count": canonical.collaboration_knowledge_count,
                },
            },
        )
    except usage_service.UsageError:
        pass

    effective_settings = dict(canonical.effective_settings)
    effective_settings["include_awareness"] = bool(owner_tools or "awareness.read" in granted_permissions)
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
            "awareness_count": len(canonical.awareness_items),
            "collaboration_memory_count": canonical.collaboration_memory_count,
            "collaboration_knowledge_count": canonical.collaboration_knowledge_count,
            "context_chars": canonical.total_context_chars,
            "sources": canonical.source_refs,
            "provenance": canonical.provenance,
            "budget": canonical.budget,
            "canonical_context_version": canonical_context.CANONICAL_CONTEXT_VERSION,
            "settings": effective_settings,
        },
        "collaboration": {
            "version": canonical.collaboration.get("version"),
            "active": bool(canonical.collaboration_sources),
            "read_only_agent_context": True,
            "sources": canonical.collaboration_sources,
        },
        "tools": tool_state,
    }
