from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-agent-tools-failure-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import providers  # noqa: E402

    with TestClient(app) as client:
        assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200
        assert client.put(
            "/api/v1/control/provider",
            json={"base_url": "http://127.0.0.1:11434", "model": "llama-test", "enabled": True},
        ).status_code == 200
        assert client.put(
            "/api/v1/control/agent",
            json={"name": "Failure Audit Agent", "model": "llama-test", "instructions": "Use local read tools when useful."},
        ).status_code == 200
        assert client.put(
            "/api/v1/control/agent-tools",
            json={"enabled": True, "max_calls": 2},
        ).status_code == 200
        assert client.post(
            "/api/v1/control/knowledge",
            json={"title": "Failure audit knowledge", "kind": "note", "content": "Agent tool failure auditing stays local."},
        ).status_code == 200

        calls = 0

        def fail_after_tool(messages, *, tools=None, model_override=None):
            nonlocal_calls[0] += 1
            if nonlocal_calls[0] == 1:
                return {
                    "provider": "ollama",
                    "model": model_override or "llama-test",
                    "content": "",
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": "homeserver_knowledge_search",
                                "arguments": {"query": "failure audit", "limit": 3},
                            },
                        }
                    ],
                }
            raise providers.ProviderError("Synthetic Ollama failure after tool execution.")

        nonlocal_calls = [calls]
        providers.generate_ollama_step = fail_after_tool

        response = client.post(
            "/api/v1/control/chat",
            json={"message": "Use local knowledge, then fail the final model step."},
        )
        assert response.status_code == 503

        with db() as connection:
            run = connection.execute(
                "SELECT id, conversation_id, status, tool_call_count, metadata_json FROM agent_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
            assert run is not None
            assert run["status"] == "failed"
            assert run["tool_call_count"] == 1
            metadata = json.loads(run["metadata_json"])
            assert len(metadata["tool_run_ids"]) == 1

            tool_run = connection.execute(
                "SELECT id, tool_key, status FROM tool_runs WHERE id=?",
                (metadata["tool_run_ids"][0],),
            ).fetchone()
            assert tool_run is not None
            assert tool_run["tool_key"] == "knowledge.search"
            assert tool_run["status"] == "completed"

            messages = connection.execute(
                "SELECT role FROM conversation_messages WHERE conversation_id=? ORDER BY id",
                (run["conversation_id"],),
            ).fetchall()
            assert [row["role"] for row in messages] == ["user"]

print("HomeServer Agent Tool failure-audit test passed")
