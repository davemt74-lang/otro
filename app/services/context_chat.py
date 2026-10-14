from __future__ import annotations

import json
import re
import time
from typing import Any

from ..database import db
from . import (
    agent_routing,
    app_scopes,
    approvals,
    homeserver_app_approvals,
    brain,
    canonical_context,
    context_engine,
    knowledge_collection_policy,
    providers,
    usage as usage_service,
)
from .inference_cancellation import CancellationToken, InferenceCancelled


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


def _model_tool_permissions(read_only: bool, permissions: set[str]) -> set[str]:
    """Keep private context authorization separate from model tool authorization."""
    return set() if read_only else set(permissions)


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


def _apply_collection_scope_to_bundle(
    source_app_key: str,
    scope: dict[str, Any],
    bundle: context_engine.ContextBundle,
    *,
    owner_tools: bool,
) -> None:
    """Compatibility shim for Section 8 consumers.

    v4.30 applies this policy inside canonical_context before prompt assembly.
    Keep the historical helper callable for older regression/integration code,
    but do not use it from the active chat path.
    """
    if owner_tools or not str(source_app_key or "").startswith("app:"):
        return
    app_id = knowledge_collection_policy.app_id_for_source(source_app_key)
    if app_id is None:
        bundle.knowledge = []
        bundle.sources = [ref for ref in bundle.sources if ref.get("kind") != "knowledge"]
    else:
        bundle.knowledge = knowledge_collection_policy.filter_items_for_app(
            app_id,
            bundle.knowledge,
            apply_kind_scope=False,
            scope=scope,
        )
        allowed_ids = {int(item["id"]) for item in bundle.knowledge}
        bundle.sources = [
            ref
            for ref in bundle.sources
            if ref.get("kind") != "knowledge" or int(ref.get("id") or 0) in allowed_ids
        ]
    bundle.context_chars = sum(
        len(str(item.get("content") or ""))
        for group in (bundle.memory, bundle.knowledge, bundle.contacts)
        for item in group
    )


def _safe_run_metadata(
    tool_state: dict[str, Any],
    context: canonical_context.CanonicalContext,
    *,
    agent: dict[str, Any],
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
        "interactive_context_version": context.interactive.get("version"),
        "interactive_transcript_count": int(context.interactive.get("transcript_count") or 0),
        "interactive_meeting_count": int(context.interactive.get("meeting_count") or 0),
        "agent_id": int(agent["id"]),
        "agent_name": str(agent.get("name") or "Agent"),
        "agent_routing_version": agent_routing.AGENT_ROUTING_VERSION,
    }


def chat(
    source_app_key: str,
    message: str,
    conversation_id: str | None = None,
    *,
    agent_id: int | None = None,
    include_memory: bool = True,
    include_knowledge: bool = True,
    include_contacts: bool = False,
    context_options: dict[str, Any] | None = None,
    tool_permissions: set[str] | None = None,
    owner_tools: bool = False,
    read_only: bool = False,
    cancellation_token: CancellationToken | None = None,
    project_knowledge_r6: dict | None = None,
) -> dict[str, Any]:
    if cancellation_token is not None:
        cancellation_token.raise_if_cancelled()
    text = message.strip()
    if not text:
        raise brain.BrainError("Message is required.")
    if len(text) > 32000:
        raise brain.BrainError("Message exceeds the 32,000 character limit.")

    granted_permissions = set(tool_permissions or set())
    agent = agent_routing.resolve_agent(source_app_key, agent_id, owner=owner_tools)
    original_conversation_id = conversation_id
    agent_routing.validate_conversation_agent(source_app_key, conversation_id, int(agent["id"]))
    conversation_id = brain._conversation_for_source(
        source_app_key, conversation_id, int(agent["id"]), text
    )
    settings = _apply_context_options(conversation_id, context_options)
    # Physical-context conversations are permanently local and read-only:
    # subsequent turns cannot export prior observations through model tools.
    read_only = bool(read_only or settings.get("agent_eyes_local_only"))

    from . import project_knowledge_r6 as r6, workspace_sync, knowledge_actions_r9 as r9
    knowledge_candidate=source_app_key=='owner' and owner_tools and r9.intent(text)
    knowledge_draft=None
    r6_packet=None;r6_args=None
    r6_selected_off=source_app_key=='owner' and owner_tools and r6.local_mode(conversation_id)=='off'
    if r6_selected_off:include_knowledge=False
    if source_app_key=='owner' and owner_tools:
        if include_knowledge and settings['include_knowledge'] and settings['cloud_allowed']:
            try:r6_packet,r6_args=r6.owner_packet(conversation_id,int(agent['id']),text,project_knowledge_r6)
            except workspace_sync.WorkspaceSyncError as exc:raise brain.BrainError(str(exc),exc.status_code) from exc
        elif include_knowledge and settings['include_knowledge'] and r6.requires_cloud(conversation_id):
            raise brain.BrainError('The selected Knowledge context requires current Cloud permission. Choose a different context to continue.',403)
        elif project_knowledge_r6 and project_knowledge_r6.get('attachments'):
            raise brain.BrainError('Knowledge is disabled for this conversation.',403)
    elif project_knowledge_r6:
        raise brain.BrainError('Project context selection requires the owner canvas.',403)
    r6_active=bool(r6_packet and r6_packet.get('active'))
    r6_reserve=min(6000,max(0,int(settings['max_context_chars'])-2000)) if r6_active else 0
    research_intent = bool(not r6_active and source_app_key == 'owner' and owner_tools and include_memory and include_knowledge
        and settings.get('include_memory') and settings.get('include_knowledge') and settings.get('cloud_allowed') and not read_only
        and re.search(r'\b(recall|remember|saved|previous|prior|earlier)\b', text, re.I)
        and re.search(r'\b(research|reports?|findings)\b', text, re.I))
    research_reserve = min(4000, max(0, int(settings['max_context_chars']) - 1000)) if research_intent else 0
    research_request = None
    research_result = None
    research_text = ''

    canonical = canonical_context.build_authorized_context(
        agent_id=int(agent["id"]),
        query=text,
        source_app_key=source_app_key,
        permissions=granted_permissions,
        owner=owner_tools,
        include_memory=include_memory,
        include_knowledge=include_knowledge and not r6_active,
        include_contacts=include_contacts,
        settings=settings,
        max_context_chars=int(settings["max_context_chars"]) - research_reserve - r6_reserve,
        cloud_allowed=bool(settings["cloud_allowed"]),
        surface_context=None,
        include_collaboration=not r6_active,
    )
    if r6_active:
        try:r6.apply_packet(canonical,r6_packet,int(settings['max_context_chars']))
        except workspace_sync.WorkspaceSyncError as exc:raise brain.BrainError(str(exc),exc.status_code) from exc
    bundle = canonical.bundle

    if research_intent and canonical.cloud_allowed and research_reserve >= 800:
        from . import chat_browser_research
        research_request = {'action': 'recall', 'local_conversation': conversation_id,
            'parent_agent_id': int(agent['id']), 'question': text[:1000]}
        try:
            research_result = chat_browser_research.request(research_request)
            candidate = str(research_result.get('context_text') or '')
            if len(candidate) <= research_reserve:
                research_text = candidate
        except chat_browser_research.ResearchError:
            research_request = None
        if not research_text:
            research_request = None

    if research_text:
        canonical.budget['research_limit_chars'] = research_reserve
        canonical.budget['research_used_chars'] = len(research_text)
        canonical.budget['used_chars'] = canonical.total_context_chars + len(research_text)
        canonical.budget['max_context_chars'] = int(settings['max_context_chars'])
        canonical.effective_settings['max_context_chars'] = int(settings['max_context_chars'])
        for report in research_result.get('reports', []):
            canonical.source_refs.append({'kind': 'saved_research', 'id': report['report']['id'], 'title': report['question']})
        canonical.provenance.append({'kind': 'saved_research', 'source': 'paired_cloud', 'snapshot_recheck': True})

    inference = providers.inference_status()
    provider_key = str(inference.get("selected_provider") or "unavailable")
    provider_model = str(inference.get("model") or "")
    provider_override: str | None = None
    if not canonical.cloud_allowed and not knowledge_candidate:
        provider_key, provider_model, provider_override = _private_inference_route(inference)

    with db() as connection:
        cursor = connection.execute(
            "INSERT INTO conversation_messages(conversation_id, role, content, source_app_key) VALUES (?, 'user', ?, ?)",
            (conversation_id, text, source_app_key),
        )
        user_message_id = int(cursor.lastrowid)
        connection.execute(
            "UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (conversation_id,)
        )

    history = brain._history(conversation_id)
    # Read history before rechecking the durable binding. A concurrent owner
    # opt-in must not let a turn with earlier settings send a newer physical
    # reply to a cloud route or export it through model tools.
    current_settings = context_engine.get_settings(conversation_id)
    if current_settings.get("agent_eyes_local_only"):
        read_only = True
        canonical.cloud_allowed = False
        canonical.effective_settings.update({"cloud_allowed": False, "agent_eyes_local_only": True})
        if not knowledge_candidate:provider_key, provider_model, provider_override = _private_inference_route(inference)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": canonical_context.system_prompt(agent, canonical)},
        *history,
    ]
    if research_text:
        messages[0]['content'] += '\n\n' + research_text
    if read_only:
        messages[0]["content"] += (
            "\n\nThis chat turn is server-enforced read-only. Do not invoke, propose, or claim any tool, task, memory write, external action, approval request, or side effect. Respond using only the conversation and authorized context supplied to you."
        )
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

    def cancel_turn(reason: str) -> None:
        duration_ms = int((time.perf_counter() - started) * 1000)
        request_ids=list(tool_state.get("action_request_ids") or [])
        cancelled_requests=approvals.cancel_pending_requests(
            request_ids,
            source_app_key=source_app_key,
            reason=f"Interrupted Agent turn: {reason[:180]}",
        )
        cancelled_requests+=homeserver_app_approvals.cancel_pending(
            request_ids,
            source_app_key=source_app_key,
            reason=f"Interrupted Agent turn: {reason[:180]}",
        )
        cancelled_metadata = _safe_run_metadata(tool_state, canonical, agent=agent)
        cancelled_metadata.update({
            "cancelled": True,
            "cancellation_reason": reason[:180],
            "cancelled_action_requests": cancelled_requests,
            "read_only": bool(read_only),
        })
        with db() as connection:
            connection.execute(
                """
                UPDATE agent_runs
                SET status='failed', duration_ms=?, error=?, tool_call_count=?, metadata_json=?, completed_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    duration_ms,
                    f"cancelled:{reason}"[:1000],
                    int(tool_state.get("call_count") or 0),
                    json.dumps(cancelled_metadata, separators=(",", ":")),
                    run_id,
                ),
            )
            connection.execute("DELETE FROM conversation_messages WHERE id=?", (user_message_id,))
            if original_conversation_id is None:
                connection.execute(
                    "DELETE FROM conversations WHERE id=? AND NOT EXISTS (SELECT 1 FROM conversation_messages WHERE conversation_id=?)",
                    (conversation_id, conversation_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE conversations
                    SET updated_at=COALESCE(
                        (SELECT MAX(created_at) FROM conversation_messages WHERE conversation_id=?),
                        created_at
                    )
                    WHERE id=?
                    """,
                    (conversation_id, conversation_id),
                )

    from . import workspace_sync
    workspace_datasets = tuple(
        name for name in workspace_sync.DATASETS
        if canonical.cloud_allowed
        and (name not in ('contacts', 'crm') or canonical.effective_settings['include_contacts'])
        and (name not in ('knowledge', 'transcriptions', 'meetings') or canonical.effective_settings['include_knowledge'])
        and (name not in ('agents', 'chats') or canonical.effective_settings['include_memory'])
    )
    try:
        if knowledge_candidate:
            try:knowledge_draft=r9.chat(text,conversation_id,int(agent['id']),current_settings,read_only,include_knowledge,project_knowledge_r6,user_message_id)
            except workspace_sync.WorkspaceSyncError as exc:
                cancel_turn('knowledge_action_authority_changed')
                raise brain.BrainError(str(exc),exc.status_code) from exc
            draft_execution=knowledge_draft.get('execution') or {}
            generated={'content':knowledge_draft['answer'],'provider':draft_execution.get('provider') or 'knowledge_review','model':draft_execution.get('model') or 'reviewed-knowledge-r9'}
            tool_state={'call_count':0,'run_ids':[],'action_request_ids':[],'provider_usage':draft_execution.get('usage') or {}}
        elif cancellation_token is None:
            generated, tool_state = brain._generate_with_agent_tools(
                messages,
                source_app_key=source_app_key,
                selected_model=selected_model,
                granted_permissions=_model_tool_permissions(read_only, canonical.model_tool_permissions),
                owner=bool(owner_tools and not read_only),
                state=tool_state,
                provider_key=provider_override,
                workspace_datasets=workspace_datasets,
                **({"scoped_knowledge":True} if r6_active or r6_selected_off else {}),
            )
        else:
            generated, tool_state = brain._generate_with_agent_tools(
                messages,
                source_app_key=source_app_key,
                selected_model=selected_model,
                granted_permissions=_model_tool_permissions(read_only, canonical.model_tool_permissions),
                owner=bool(owner_tools and not read_only),
                state=tool_state,
                provider_key=provider_override,
                cancellation_token=cancellation_token,
                workspace_datasets=workspace_datasets,
                **({"scoped_knowledge":True} if r6_active or r6_selected_off else {}),
            )
        tool_state["read_only"] = bool(read_only)
        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled()
    except InferenceCancelled as exc:
        cancel_turn(str(exc))
        raise brain.BrainError("Agent turn cancelled.", 409) from exc

    except providers.ProviderError as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        failed_metadata = _safe_run_metadata(tool_state, canonical, agent=agent)
        failed_metadata["read_only"] = bool(read_only)
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

    if cancellation_token is not None and cancellation_token.cancelled:
        reason = cancellation_token.reason
        cancel_turn(reason)
        raise brain.BrainError("Agent turn cancelled.", 409)
    if r6_active:
        try:r6.recheck(r6_packet,r6_args)
        except workspace_sync.WorkspaceSyncError as exc:
            cancel_turn('project_knowledge_authority_changed')
            raise brain.BrainError(str(exc),exc.status_code) from exc
    if research_request:
        try:
            fresh = chat_browser_research.request(research_request)
            prior_refs = [(r.get('id'), r.get('hash')) for r in research_result.get('reports', [])]
            fresh_refs = [(r.get('id'), r.get('hash')) for r in fresh.get('reports', [])]
            if prior_refs != fresh_refs or not context_engine.get_settings(conversation_id).get('include_knowledge') or not context_engine.get_settings(conversation_id).get('include_memory'):
                raise chat_browser_research.ResearchError('Saved research changed during the answer. Review current findings.', 409)
        except chat_browser_research.ResearchError as exc:
            cancel_turn('research_authority_changed')
            raise brain.BrainError(str(exc), exc.status_code) from exc
    duration_ms = int((time.perf_counter() - started) * 1000)
    reply = str(generated.get("content") or "").strip()
    if not reply:
        raise brain.BrainError("Inference provider returned no final response text.", 503)

    context_event_id = context_engine.record_retrieval(conversation_id, source_app_key, bundle)
    agent_summary = agent_routing.safe_summary(agent)
    metadata = {
        "knowledge_actions_r9": knowledge_draft,
        "project_knowledge_r6": r6.public(r6_packet),
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
        "read_only": bool(read_only),
        "agent": agent_summary,
        "agent_routing_version": agent_routing.AGENT_ROUTING_VERSION,
    }
    if research_text:
        metadata['research_reports'] = [{'id': r['report']['id'], 'url': research_result['cloud_base'] + r['report']['url'], 'title': r['question']} for r in research_result.get('reports', [])]
    run_metadata = _safe_run_metadata(tool_state, canonical, agent=agent, context_event_id=context_event_id)
    run_metadata["scope_enforced"] = not owner_tools
    run_metadata["read_only"] = bool(read_only)

    if cancellation_token is not None and cancellation_token.cancelled:
        reason = cancellation_token.reason
        cancel_turn(reason)
        raise brain.BrainError("Agent turn cancelled.", 409)

    if knowledge_draft and knowledge_draft.get('drafts'):
        try:r9.verify(knowledge_draft,conversation_id,int(agent['id']))
        except workspace_sync.WorkspaceSyncError as exc:
            cancel_turn('knowledge_action_authority_changed')
            raise brain.BrainError(str(exc),exc.status_code) from exc
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
                        "interactive_transcript_count": int(canonical.interactive.get("transcript_count") or 0),
                        "interactive_meeting_count": int(canonical.interactive.get("meeting_count") or 0),
                        "context_chars": canonical.total_context_chars,
                        "canonical_context_version": canonical_context.CANONICAL_CONTEXT_VERSION,
                        "context_event_id": context_event_id,
                        "tool_call_count": int(tool_state.get("call_count") or 0),
                        "action_request_count": len(tool_state.get("action_request_ids") or []),
                        "duration_ms": duration_ms,
                        "scope_enforced": not owner_tools,
                        "read_only": bool(read_only),
                        "agent_id": int(agent["id"]),
                        "agent_name": str(agent.get("name") or "Agent"),
                        "agent_routing_version": agent_routing.AGENT_ROUTING_VERSION,
                    },
                    separators=(",", ":"),
                ),
            ),
        )

    actual_compute_source = "vp3_cloud" if (knowledge_draft or {}).get('execution') and knowledge_draft['execution'].get('source')=='vp3_cloud' else ("homeserver_local" if generated["provider"] == "ollama" else "user_provider")
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
            billable_tokens=int((knowledge_draft or {}).get("execution",{}).get("cloud_tokens_debited",0)) if (knowledge_draft or {}).get("execution") else 0,
            request_kind="chat",
            metadata={
                "conversation_id": conversation_id,
                "run_id": run_id,
                "context_event_id": context_event_id,
                "context_chars": canonical.total_context_chars,
                "canonical_context_version": canonical_context.CANONICAL_CONTEXT_VERSION,
                "scope_enforced": not owner_tools,
                "read_only": bool(read_only),
                "agent_id": int(agent["id"]),
                "agent_routing_version": agent_routing.AGENT_ROUTING_VERSION,
                "context_source_counts": {
                    **bundle.counts,
                    "awareness_count": len(canonical.awareness_items),
                    "interactive_transcript_count": int(canonical.interactive.get("transcript_count") or 0),
                    "interactive_meeting_count": int(canonical.interactive.get("meeting_count") or 0),
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
        "cloud_tokens_debited": int((knowledge_draft or {}).get('execution',{}).get('cloud_tokens_debited',0)) if (knowledge_draft or {}).get('execution') else 0,
        "usage": provider_usage,
        "run_id": run_id,
        "read_only": bool(read_only),
        "agent": agent_summary,
        "agent_routing_version": agent_routing.AGENT_ROUTING_VERSION,
        "context": {
            **bundle.counts,
            "awareness_count": len(canonical.awareness_items),
            "interactive_transcript_count": int(canonical.interactive.get("transcript_count") or 0),
            "interactive_meeting_count": int(canonical.interactive.get("meeting_count") or 0),
            "collaboration_memory_count": canonical.collaboration_memory_count,
            "collaboration_knowledge_count": canonical.collaboration_knowledge_count,
            "context_chars": canonical.total_context_chars,
            "sources": canonical.source_refs,
            "provenance": canonical.provenance,
            "budget": canonical.budget,
            "canonical_context_version": canonical_context.CANONICAL_CONTEXT_VERSION,
            "settings": effective_settings,
        },
        "knowledge_actions_r9": knowledge_draft,
        "research_reports": metadata.get("research_reports", []),
        "project_knowledge_r6":r6.public(r6_packet),
        "annotation_citations": [r for r in canonical.source_refs if r.get("kind")=="browser_annotation"],
        "collaboration": {
            "version": canonical.collaboration.get("version"),
            "active": bool(canonical.collaboration_sources),
            "read_only_agent_context": True,
            "sources": canonical.collaboration_sources,
        },
        "tools": tool_state,
    }
