"""Haiku 5.5 upgrade, owner overrides and the real Anthropic tool adapter."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-haiku55-") as data:
    os.environ["HOMESERVER_DATA_DIR"] = data
    from app.database import initialize_database, db
    from app.services import providers

    initialize_database()
    assert providers.get_inference_settings()["preferred_provider"] == "anthropic"
    assert providers._provider_row("anthropic")["model"] == "claude-haiku-5-5"
    assert providers.inference_status()["available"] is False

    # Simulate an existing enabled Claude installation upgrading from schema 80.
    with db() as conn:
        conn.execute("DELETE FROM schema_migrations WHERE version=81")
        conn.execute("UPDATE model_providers SET model='claude-haiku-4-5',enabled=1 WHERE provider_key='anthropic'")
        conn.execute("UPDATE inference_settings SET preferred_provider='auto' WHERE id=1")
    initialize_database()
    assert providers.get_inference_settings()["preferred_provider"] == "anthropic"
    assert providers._provider_row("anthropic")["model"] == "claude-haiku-5-5"

    ready = [dict(provider_key="ollama", ready=True, model="local", compute_source="homeserver_local"),
             dict(provider_key="anthropic", ready=True, model="claude-haiku-5-5", compute_source="user_provider")]
    with patch.object(providers, "list_inference_providers", return_value=ready):
        assert providers.inference_status()["selected_provider"] == "anthropic"
        providers.save_inference_settings("auto")
        initialize_database()
        assert providers.inference_status()["selected_provider"] == "ollama"

    captured = []
    answer = {"model": "claude-haiku-5-5", "content": [
        {"type": "thinking", "thinking": "not user-visible"},
        {"type": "text", "text": "Checking your record."},
        {"type": "tool_use", "id": "toolu_1", "name": "knowledge.search", "input": {"query": "notes"}},
    ], "usage": {"input_tokens": 12, "output_tokens": 20}}

    class Client:
        def __init__(self, **kwargs): pass
        def post(self, url, *, json, headers):
            captured.append(json)
            import httpx
            return httpx.Response(200, json=answer, request=httpx.Request("POST", url))
        def close(self): pass

    with patch.object(providers.httpx, "Client", Client), patch.object(providers.provider_secrets, "get_api_key", return_value="fixture-key"):
        provider = {"base_url": "https://api.anthropic.com"}
        tools = [{"type": "function", "function": {"name": "knowledge.search", "parameters": {"type": "object"}}}]
        step = providers._generate_anthropic_step(provider, "claude-haiku-5-5", [{"role": "user", "content": "Find my notes"}], tools)
        assert step["content"] == "Checking your record."
        assert step["tool_calls"][0]["function"]["arguments"] == {"query": "notes"}
        assert step["usage"]["total_tokens"] == 32
        request = captured[-1]
        assert request["thinking"] == {"type": "disabled"}
        assert request["output_config"] == {"effort": "medium"}
        assert not {"temperature", "top_p", "top_k"}.intersection(request)
        assert request["max_tokens"] == 1024
        assert request["tools"][0]["name"] == "knowledge.search"
        providers._generate_anthropic_step(provider, "claude-haiku-4-5", [{"role": "user", "content": "Hello"}], None)
        assert "thinking" not in captured[-1] and "output_config" not in captured[-1]

print("Haiku 5.5 upgrade, routing, bounded output and Anthropic tool adapter PASS")
