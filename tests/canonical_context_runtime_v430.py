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


with tempfile.TemporaryDirectory(prefix="homeserver-context-v430-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import brain, canonical_context, providers  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    captured: list[list[dict]] = []

    def fake_generate(messages, model_override=None):
        captured.append(messages)
        return {
            "provider": "ollama",
            "model": model_override or "llama-context-v430-test",
            "content": "Canonical context answer.",
            "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
        }

    providers.generate_ollama = fake_generate

    def pair(client: TestClient, permissions: list[str]) -> tuple[int, dict[str, str]]:
        request = client.post(
            "/api/v1/pairing/request",
            json={"app_key": "vp3-v430-test", "app_name": "VP3 v4.30 Test", "permissions": permissions},
        ).json()
        approved = client.post("/api/v1/pairing/approve", json={"code": request["code"]})
        assert approved.status_code == 200, approved.text
        apps = client.get("/api/v1/control/connected-apps").json()["apps"]
        app_id = next(int(item["id"]) for item in apps if item["app_key"] == "vp3-v430-test")
        return app_id, {"Authorization": f"Bearer {request['claim_token']}"}

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200
        assert client.put(
            "/api/v1/control/provider",
            json={"base_url": "http://127.0.0.1:11434", "model": "llama-context-v430-test", "enabled": True},
        ).status_code == 200

        app_id, auth = pair(
            client,
            ["agent.chat", "memory.read", "knowledge.search", "contacts.read", "awareness.read"],
        )
        assert client.put(
            f"/api/v1/control/apps/{app_id}/scope",
            json={
                "cloud_allowed": True,
                "memory_key_prefixes": ["vp3:"],
                "knowledge_kinds": ["note"],
                "tool_names": [],
                "plugin_keys": [],
            },
        ).status_code == 200

        allowed_memory = "V430_ALLOWED_MEMORY_38172 canonical context trip"
        blocked_memory = "V430_BLOCKED_MEMORY_49261 canonical context trip"
        allowed_knowledge = "V430_ALLOWED_KNOWLEDGE_60351 canonical context trip"
        blocked_knowledge = "V430_BLOCKED_KNOWLEDGE_71442 canonical context trip"
        assert client.post(
            "/api/v1/control/memory",
            json={"memory_key": "vp3:trip", "content": allowed_memory, "importance": 1.0},
        ).status_code == 200
        assert client.post(
            "/api/v1/control/memory",
            json={"memory_key": "private:trip", "content": blocked_memory, "importance": 1.0},
        ).status_code == 200
        assert client.post(
            "/api/v1/control/knowledge",
            json={"title": "VP3 trip note", "kind": "note", "content": allowed_knowledge},
        ).status_code == 200
        assert client.post(
            "/api/v1/control/knowledge",
            json={"title": "Private trip document", "kind": "document", "content": blocked_knowledge},
        ).status_code == 200

        query = "canonical context trip"
        captured.clear()
        direct_response = client.post(
            "/api/v1/chat",
            headers=auth,
            json={
                "message": query,
                "include_memory": True,
                "include_knowledge": True,
                "include_contacts": True,
                "max_context_chars": 12000,
            },
        )
        assert direct_response.status_code == 200, direct_response.text
        direct = direct_response.json()
        direct_prompt = captured[-1][0]["content"]

        captured.clear()
        delegated_response = client.post(
            "/api/v1/chat",
            headers=auth,
            json={
                "message": query,
                "external_conversation_id": "vp3:canonical-parity",
                "delegation": {"name": "VP3 Agent", "role": "travel", "instructions": "Be concise."},
                "history": [],
                "surface_context": {},
                "include_memory": True,
                "include_knowledge": True,
                "include_contacts": True,
                "max_context_chars": 12000,
            },
        )
        assert delegated_response.status_code == 200, delegated_response.text
        delegated = delegated_response.json()
        delegated_prompt = captured[-1][0]["content"]

        assert direct["context"]["canonical_context_version"] == canonical_context.CANONICAL_CONTEXT_VERSION
        assert delegated["context"]["canonical_context_version"] == canonical_context.CANONICAL_CONTEXT_VERSION
        assert direct["context"]["provenance"] == delegated["context"]["provenance"]
        assert direct["context"]["budget"] == delegated["context"]["budget"]
        assert direct["context"]["memory_count"] == delegated["context"]["memory_count"]
        assert direct["context"]["knowledge_count"] == delegated["context"]["knowledge_count"]
        assert allowed_memory in direct_prompt and allowed_memory in delegated_prompt
        assert allowed_knowledge in direct_prompt and allowed_knowledge in delegated_prompt
        assert blocked_memory not in direct_prompt and blocked_memory not in delegated_prompt
        assert blocked_knowledge not in direct_prompt and blocked_knowledge not in delegated_prompt

        # Surface/workspace data is untrusted, budgeted and represented only by
        # a hash + size in provenance. The raw private payload must not leak to
        # run metadata or activity metadata.
        private_surface = "V430_PRIVATE_SURFACE_82533 " + ("x" * 5000)
        captured.clear()
        surfaced_response = client.post(
            "/api/v1/chat",
            headers=auth,
            json={
                "message": query,
                "external_conversation_id": "vp3:surface-budget",
                "delegation": {"name": "VP3 Agent", "role": "travel", "instructions": "Be concise."},
                "surface_context": {"workspace_note": private_surface},
                "include_memory": True,
                "include_knowledge": True,
                "include_contacts": True,
                "max_context_chars": 4000,
            },
        )
        assert surfaced_response.status_code == 200, surfaced_response.text
        surfaced = surfaced_response.json()
        budget = surfaced["context"]["budget"]
        assert budget["max_context_chars"] == 4000
        assert budget["used_chars"] == surfaced["context"]["context_chars"]
        assert budget["used_chars"] <= budget["max_context_chars"]
        assert budget["surface_used_chars"] > 0
        surface_provenance = [item for item in surfaced["context"]["provenance"] if item["layer"] == "surface_workspace"]
        assert len(surface_provenance) == 1
        assert "sha256" in surface_provenance[0]
        assert private_surface not in json.dumps(surfaced["context"]["provenance"])

        with db() as connection:
            run = connection.execute("SELECT metadata_json FROM agent_runs ORDER BY id DESC LIMIT 1").fetchone()
            activity = connection.execute(
                "SELECT metadata_json FROM activity_log WHERE action='agent.delegate' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert run is not None and activity is not None
        assert private_surface not in str(run["metadata_json"])
        assert private_surface not in str(activity["metadata_json"])

    scheduler.stop()

print("HomeServer v4.30 canonical context runtime parity regression passed")
