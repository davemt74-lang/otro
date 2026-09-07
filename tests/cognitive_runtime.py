from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-cognition-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db, initialize_database  # noqa: E402
    from app.services import (  # noqa: E402
        agent_tools,
        awareness_context,
        cognitive_runtime,
        context_chat,
        plugins,
        providers,
    )

    initialize_database()

    with db() as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 15
        memory_columns = {row["name"] for row in connection.execute("PRAGMA table_info(agent_memory)").fetchall()}
        assert {
            "memory_type", "source_app_key", "source_event_id", "confidence",
            "reinforcement_count", "last_accessed_at", "expires_at", "entity_type", "entity_key",
        }.issubset(memory_columns)
        run_columns = {row["name"] for row in connection.execute("PRAGMA table_info(agent_runs)").fetchall()}
        assert "awareness_count" in run_columns
        for table in ("cognitive_events", "cognition_jobs", "awareness_items", "memory_candidates", "plugins", "plugin_event_subscriptions"):
            assert connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None

    event_calls: list[str] = []
    tool_calls: list[str] = []
    manifest = {
        "plugin_key": "demo.insights",
        "name": "Demo Insights",
        "version": "1.0.0",
        "description": "Regression plugin",
        "requested_permissions": ["events.read", "awareness.read"],
        "produces_events": ["demo.activity"],
        "subscriptions": [{"event_pattern": "demo.*", "handler_key": "observe"}],
        "tools": [
            {
                "key": "lookup",
                "name": "Demo Lookup",
                "description": "Return a bounded synthetic lookup result.",
                "mode": "read",
                "required_permissions": ["knowledge.search"],
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
                "handler_key": "lookup",
            }
        ],
    }
    registered = plugins.register_plugin(manifest, trusted=True)
    assert registered["plugin_key"] == "demo.insights"
    assert registered["trusted"] is True

    try:
        plugins.register_plugin(
            {
                "plugin_key": "unsafe.writer",
                "name": "Unsafe Writer",
                "version": "1",
                "tools": [{"key": "write", "mode": "write"}],
            }
        )
        raise AssertionError("Plugin registry accepted a direct write tool")
    except plugins.PluginError:
        pass

    def observe_event(event):
        event_calls.append(event["event_id"])
        return {"observed": event["event_type"]}

    def lookup_tool(arguments, context):
        tool_calls.append(str(arguments.get("query") or ""))
        return {"answer": f"local:{arguments.get('query', '')}", "plugin": context["plugin_key"]}

    plugins.register_event_handler("demo.insights", "observe", observe_event)
    plugins.register_tool_handler("demo.insights", "lookup", lookup_tool)

    schemas = agent_tools.model_tool_schemas(
        {"tools.execute", "knowledge.search"},
        owner=False,
    )
    plugin_schema = next(schema for schema in schemas if schema["function"]["name"].startswith("plugin__demo_insights__lookup"))
    plugin_result = agent_tools.execute_model_tool(
        "app:alpha",
        plugin_schema["function"]["name"],
        {"query": "phoenix"},
        {"tools.execute", "knowledge.search"},
        owner=False,
    )
    assert plugin_result["result"]["answer"] == "local:phoenix"
    assert tool_calls == ["phoenix"]

    first = cognitive_runtime.emit_event(
        source_app_key="app:alpha",
        source_kind="app",
        event_id="evt-1",
        event_type="demo.activity",
        summary="Phoenix campaign activity increased.",
        entity_type="campaign",
        entity_key="phoenix-launch",
        importance=0.8,
        payload={"count": 1},
    )
    assert first["duplicate"] is False
    duplicate = cognitive_runtime.emit_event(
        source_app_key="app:alpha",
        source_kind="app",
        event_id="evt-1",
        event_type="demo.activity",
        summary="This duplicate must not create a second row.",
        entity_type="campaign",
        entity_key="phoenix-launch",
        importance=0.8,
    )
    assert duplicate["duplicate"] is True

    cognitive_runtime.emit_event(
        source_app_key="app:beta",
        source_kind="app",
        event_id="evt-2",
        event_type="demo.activity",
        summary="Phoenix campaign activity increased again.",
        entity_type="campaign",
        entity_key="phoenix-launch",
        importance=0.85,
    )
    cognitive_runtime.emit_event(
        source_app_key="app:alpha",
        source_kind="app",
        event_id="evt-3",
        event_type="demo.activity",
        summary="Phoenix campaign activity continued across apps.",
        entity_type="campaign",
        entity_key="phoenix-launch",
        importance=0.9,
    )
    jobs = cognitive_runtime.process_pending_jobs(limit=50)
    assert jobs["failed"] == 0
    assert set(event_calls) >= {"evt-1", "evt-2", "evt-3"}

    awareness = cognitive_runtime.list_awareness(limit=20, status="open")
    campaign_awareness = next(item for item in awareness if item.get("entity_key") == "phoenix-launch")
    assert campaign_awareness["occurrence_count"] == 3
    assert set(campaign_awareness["source_apps"]) == {"app:alpha", "app:beta"}
    assert campaign_awareness["importance"] == 0.9

    cross_app_candidates = cognitive_runtime.list_memory_candidates(limit=20, status="pending")
    cross_candidate = next(item for item in cross_app_candidates if item.get("source_awareness_id") == campaign_awareness["id"])
    assert cross_candidate["source_app_key"] == "multi-app"
    assert cross_candidate["memory_type"] == "episodic"

    ignored_candidate = cognitive_runtime.emit_event(
        source_app_key="app:gamma",
        source_kind="app",
        event_id="evt-no-memory-permission",
        event_type="demo.note",
        summary="An app without memory.write asked to remember this.",
        importance=0.95,
        memory_candidate=True,
        allow_memory_candidate=False,
    )
    assert ignored_candidate["event"]["memory_candidate"] is False

    allowed_candidate = cognitive_runtime.emit_event(
        source_app_key="app:gamma",
        source_kind="app",
        event_id="evt-memory-permission",
        event_type="demo.preference",
        summary="The user prefers concise local-first status reports.",
        entity_type="preference",
        entity_key="status-style",
        importance=0.9,
        memory_candidate=True,
        memory_type="preference",
        memory_key="Status report preference",
        allow_memory_candidate=True,
    )
    assert allowed_candidate["event"]["memory_candidate"] is True
    cognitive_runtime.process_pending_jobs(limit=50)
    candidate = next(
        item for item in cognitive_runtime.list_memory_candidates(limit=50, status="pending")
        if item.get("source_event_id") == allowed_candidate["event"]["id"]
    )
    accepted = cognitive_runtime.decide_memory_candidate(candidate["id"], "accepted")
    assert accepted["status"] == "accepted"
    assert accepted["consolidated_memory_id"] is not None
    with db() as connection:
        memory = connection.execute("SELECT * FROM agent_memory WHERE id=?", (accepted["consolidated_memory_id"],)).fetchone()
        assert memory["memory_type"] == "preference"
        assert memory["source_app_key"] == "app:gamma"
        assert memory["entity_key"] == "status-style"
        assert float(memory["confidence"]) > 0.8

        cursor = connection.execute(
            "INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json) VALUES ('app', 'app:delta', 'campaign.updated', 'campaign', 'delta-campaign', '{}')"
        )
        activity_id = int(cursor.lastrowid)
    mirrored = cognitive_runtime.mirror_activity_log(limit=1000)
    assert mirrored["cursor"] >= activity_id
    mirrored_event = cognitive_runtime.list_events(source_app_key="app:delta", limit=20, include_payload=True)
    assert any(item["event_id"] == f"activity:{activity_id}" for item in mirrored_event)

    relevant_awareness = awareness_context.collect("What changed with the Phoenix campaign?", limit=10)
    assert any(item.get("entity_key") == "phoenix-launch" for item in relevant_awareness)
    fragment = awareness_context.prompt_fragment(relevant_awareness)
    assert "cross-application awareness" in fragment
    assert "Phoenix campaign" in fragment

    original_status = providers.inference_status
    original_generate = providers.generate

    def fake_generate(messages, model_override=None):
        assert any("cross-application awareness" in str(message.get("content") or "") for message in messages if message.get("role") == "system")
        return {
            "provider": "openai",
            "model": model_override or "test-model",
            "content": "Awareness-aware answer.",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    providers.inference_status = lambda: {
        "available": True,
        "selected_provider": "openai",
        "model": "test-model",
        "compute_source": "user_provider",
        "cloud_fallback_required": False,
        "providers": [{"provider_key": "openai", "model": "test-model", "ready": True}],
    }
    providers.generate = fake_generate
    try:
        aware_chat = context_chat.chat(
            "app:aware",
            "What changed with the Phoenix campaign?",
            include_memory=False,
            include_knowledge=False,
            include_contacts=False,
            tool_permissions={"awareness.read"},
        )
        assert aware_chat["context"]["awareness_count"] >= 1
        assert any(source["kind"] == "awareness" for source in aware_chat["context"]["sources"])
        assert aware_chat["context"]["settings"]["include_awareness"] is True

        providers.generate = lambda messages, model_override=None: {
            "provider": "openai",
            "model": model_override or "test-model",
            "content": "No shared awareness.",
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
        isolated_chat = context_chat.chat(
            "app:isolated",
            "What changed with the Phoenix campaign?",
            include_memory=False,
            include_knowledge=False,
            include_contacts=False,
            tool_permissions=set(),
        )
        assert isolated_chat["context"]["awareness_count"] == 0
        assert isolated_chat["context"]["settings"]["include_awareness"] is False
    finally:
        providers.inference_status = original_status
        providers.generate = original_generate

    cognitive_runtime.scheduler.start()
    assert cognitive_runtime.scheduler.running is True
    cognitive_runtime.scheduler.stop()
    assert cognitive_runtime.scheduler.running is False

print("HomeServer cognitive runtime and plugin bus regression passed")
