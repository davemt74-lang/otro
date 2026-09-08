from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-context-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db, initialize_database  # noqa: E402
    from app.services import brain, contacts, context_chat, context_engine, providers  # noqa: E402
    from app.services.knowledge import create_knowledge_item  # noqa: E402

    initialize_database()

    with db() as connection:
        agent = connection.execute("SELECT id FROM agents WHERE is_primary=1 LIMIT 1").fetchone()
        assert agent is not None
        agent_id = int(agent["id"])
        connection.execute(
            """
            INSERT INTO agent_memory(agent_id, memory_key, content, importance)
            VALUES (?, 'Agent Radar decision', 'Agent Radar belongs in VP3 and feeds the main Agent Brain context.', 0.9)
            """,
            (agent_id,),
        )
        connection.execute(
            """
            INSERT INTO conversations(id, agent_id, source_app_key, title)
            VALUES ('context-test-chat', ?, 'owner', 'Context test')
            """,
            (agent_id,),
        )

    create_knowledge_item(
        "Agent Radar architecture",
        "note",
        "Agent Radar events should feed the main VP3 agent, CRM entities and opportunity notifications.",
    )
    contacts.create_contact(
        {
            "display_name": "Alice Example",
            "organization": "Acme Systems",
            "relationship": "integration partner",
            "email": "alice@example.invalid",
            "phone": "555-0100",
            "notes": "Alice works on the Agent Radar integration planning.",
        }
    )

    settings = context_engine.ensure_settings("context-test-chat")
    assert settings["include_memory"] is True
    assert settings["include_knowledge"] is True
    assert settings["include_contacts"] is True
    assert settings["cloud_allowed"] is True
    assert settings["max_context_chars"] == 12000

    bundle = context_engine.collect_context(
        agent_id,
        "What did we decide about Agent Radar and Alice at Acme?",
        "context-test-chat",
        allow_memory=True,
        allow_knowledge=True,
        allow_contacts=True,
    )
    assert len(bundle.memory) >= 1
    assert len(bundle.knowledge) >= 1
    assert len(bundle.contacts) >= 1
    assert bundle.context_chars <= 12000
    kinds = {source["kind"] for source in bundle.sources}
    assert {"memory", "knowledge", "contact"}.issubset(kinds)
    for source in bundle.sources:
        assert set(source).issubset({"kind", "id", "title", "updated_at"})
        serialized = str(source).lower()
        assert "alice@example.invalid" not in serialized
        assert "555-0100" not in serialized
        assert "source_path" not in serialized
        assert "content" not in serialized

    prompt = context_engine.system_prompt({"name": "Agent", "instructions": "Be useful."}, bundle)
    assert "Relevant local memory" in prompt
    assert "Relevant local knowledge" in prompt
    assert "Relevant local contacts" in prompt
    assert "untrusted" in prompt

    event_id = context_engine.record_retrieval("context-test-chat", "owner", bundle)
    assert event_id > 0
    history = context_engine.recent_sources("context-test-chat")
    assert history and history[0]["sources"] == bundle.sources
    assert "source_refs_json" not in history[0]

    revoked_history = context_engine.recent_sources(
        "context-test-chat",
        allowed_kinds={"memory", "knowledge"},
    )
    assert revoked_history
    assert all(source["kind"] != "contact" for source in revoked_history[0]["sources"])
    assert revoked_history[0]["contact_count"] == 0
    assert "Alice Example" not in str(revoked_history)

    private_settings = context_engine.update_settings(
        "context-test-chat",
        include_memory=True,
        include_knowledge=True,
        include_contacts=False,
        cloud_allowed=False,
        max_context_chars=2000,
    )
    assert private_settings["cloud_allowed"] is False
    assert private_settings["include_contacts"] is False
    assert private_settings["max_context_chars"] == 2000

    private_bundle = context_engine.collect_context(
        agent_id,
        "Agent Radar Alice Acme",
        "context-test-chat",
        allow_memory=True,
        allow_knowledge=True,
        allow_contacts=True,
    )
    assert len(private_bundle.contacts) == 0
    assert private_bundle.context_chars <= 2000

    original_status = providers.inference_status
    original_generate_ollama = providers.generate_ollama
    providers.inference_status = lambda: {
        "available": True,
        "selected_provider": "openai",
        "model": "test-hosted-model",
        "compute_source": "user_provider",
        "cloud_fallback_required": False,
        "providers": [
            {
                "provider_key": "openai",
                "model": "test-hosted-model",
                "compute_source": "user_provider",
                "ready": True,
            }
        ],
    }
    try:
        try:
            context_chat.chat(
                "owner",
                "This must stay local",
                context_options={"cloud_allowed": False},
                include_memory=False,
                include_knowledge=False,
                include_contacts=False,
            )
            raise AssertionError("Private conversation unexpectedly allowed a hosted provider without local inference")
        except brain.BrainError as exc:
            assert exc.status_code == 409
            assert "Private" in str(exc)
    finally:
        providers.inference_status = original_status

    captured_local: dict = {}

    def fake_local_generate(messages, model_override=None):
        captured_local["model"] = model_override
        return {
            "provider": "ollama",
            "model": model_override or "local-private-model",
            "content": "Private local answer.",
            "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
        }

    providers.inference_status = lambda: {
        "available": True,
        "preferred_provider": "openai",
        "selected_provider": "openai",
        "model": "test-hosted-model",
        "compute_source": "user_provider",
        "cloud_fallback_required": False,
        "providers": [
            {
                "provider_key": "ollama",
                "model": "local-private-model",
                "compute_source": "homeserver_local",
                "ready": True,
            },
            {
                "provider_key": "openai",
                "model": "test-hosted-model",
                "compute_source": "user_provider",
                "ready": True,
            },
        ],
    }
    providers.generate_ollama = fake_local_generate
    try:
        local_private = context_chat.chat(
            "owner",
            "Use local private inference",
            context_options={"cloud_allowed": False},
            include_memory=False,
            include_knowledge=False,
            include_contacts=False,
        )
        assert local_private["provider"] == "ollama"
        assert local_private["model"] == "local-private-model"
        assert local_private["compute_source"] == "homeserver_local"
        assert local_private["cloud_tokens_debited"] == 0
        assert local_private["context"]["settings"]["cloud_allowed"] is False
        assert captured_local["model"] == "local-private-model"
    finally:
        providers.inference_status = original_status
        providers.generate_ollama = original_generate_ollama

    with db() as connection:
        run_columns = {row["name"] for row in connection.execute("PRAGMA table_info(agent_runs)").fetchall()}
        assert {"contact_count", "context_chars", "awareness_count"}.issubset(run_columns)
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 16

print("HomeServer Agent Brain context engine regression passed")