from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-smoke-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.config import settings  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import providers  # noqa: E402

    captured: dict = {}

    def fake_generate(messages, model_override=None):
        captured["messages"] = messages
        captured["model"] = model_override
        return {"provider": "ollama", "model": model_override or "llama-test", "content": "Local HomeServer answer."}

    providers.generate_ollama = fake_generate

    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["version"] == "0.5.0"

        capabilities = client.get("/api/v1/capabilities", headers={"Origin": "https://vp3.me"})
        assert capabilities.status_code == 200
        assert capabilities.json()["pairing_protocol"] == "claim-v1"
        assert "agent.chat" in capabilities.json()["features"]
        assert capabilities.headers.get("access-control-allow-origin") == "https://vp3.me"

        allowed_preflight = client.options(
            "/api/v1/pairing/request",
            headers={"Origin": "https://vp3.me", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"},
        )
        assert allowed_preflight.status_code == 200
        blocked_preflight = client.options(
            "/api/v1/pairing/request",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"},
        )
        assert blocked_preflight.status_code == 400

        status = client.get("/api/v1/status")
        assert status.status_code == 200
        assert status.json()["schema_version"] == 4

        assert client.get("/api/v1/control/overview").status_code == 401
        bootstrap = client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN})
        assert bootstrap.status_code == 200

        remote_provider = client.put(
            "/api/v1/control/provider",
            json={"base_url": "https://example.com", "model": "remote", "enabled": True},
        )
        assert remote_provider.status_code == 422

        provider = client.put(
            "/api/v1/control/provider",
            json={"base_url": "http://127.0.0.1:11434", "model": "llama-test", "enabled": True},
        )
        assert provider.status_code == 200
        assert provider.json()["provider"]["enabled"] is True

        agent = client.put(
            "/api/v1/control/agent",
            json={"name": "Private Agent", "model": "llama-test", "instructions": "Use private local context and be concise."},
        )
        assert agent.status_code == 200

        memory = client.post(
            "/api/v1/control/memory",
            json={"memory_key": "architecture", "content": "HomeServer is application-neutral.", "importance": 0.9},
        )
        assert memory.status_code == 200

        knowledge = client.post(
            "/api/v1/control/knowledge",
            json={"title": "Merchant plan", "kind": "note", "content": "North Mountain merchant partnerships use private HomeServer knowledge."},
        )
        assert knowledge.status_code == 200

        document_bytes = b"# Merchant plan\n\nPrivate merchant rewards should remain searchable on the local HomeServer."
        imported = client.post(
            "/api/v1/control/knowledge/import",
            files={"file": ("merchant-plan.md", document_bytes, "text/markdown")},
        )
        assert imported.status_code == 200
        document_id = imported.json()["id"]
        duplicate = client.post(
            "/api/v1/control/knowledge/import",
            files={"file": ("copy.md", document_bytes, "text/markdown")},
        )
        assert duplicate.status_code == 200 and duplicate.json()["duplicate"] is True

        owner_chat = client.post(
            "/api/v1/control/chat",
            json={"message": "What do we know about merchant partnerships?"},
        )
        assert owner_chat.status_code == 200
        owner_chat_json = owner_chat.json()
        owner_conversation_id = owner_chat_json["conversation_id"]
        assert owner_chat_json["reply"] == "Local HomeServer answer."
        assert owner_chat_json["context"]["memory_count"] >= 1
        assert owner_chat_json["context"]["knowledge_count"] >= 1
        system_prompt = captured["messages"][0]["content"]
        assert "HomeServer is application-neutral" in system_prompt
        assert "North Mountain merchant partnerships" in system_prompt

        owner_conversations = client.get("/api/v1/control/conversations")
        assert owner_conversations.status_code == 200
        assert owner_conversations.json()["items"][0]["id"] == owner_conversation_id
        owner_thread = client.get(f"/api/v1/control/conversations/{owner_conversation_id}")
        assert owner_thread.status_code == 200
        assert [m["role"] for m in owner_thread.json()["messages"]] == ["user", "assistant"]

        collision_pair = client.post(
            "/api/v1/pairing/request",
            json={"app_key": "owner", "app_name": "Owner-named Test App", "permissions": ["agent.chat"]},
        )
        assert collision_pair.status_code == 200
        collision_json = collision_pair.json()
        assert client.post("/api/v1/pairing/approve", json={"code": collision_json["code"]}).status_code == 200
        collision_token = collision_json["claim_token"]
        collision_conversations = client.get(
            "/api/v1/conversations", headers={"Authorization": f"Bearer {collision_token}"}
        )
        assert collision_conversations.status_code == 200
        assert collision_conversations.json()["items"] == []
        assert client.get(
            f"/api/v1/conversations/{owner_conversation_id}",
            headers={"Authorization": f"Bearer {collision_token}"},
        ).status_code == 404

        pair = client.post(
            "/api/v1/pairing/request",
            json={"app_key": "vp3-test", "app_name": "VP3 Test", "permissions": ["agent.chat", "knowledge.search", "memory.read"]},
            headers={"Origin": "https://vp3.me"},
        )
        assert pair.status_code == 200
        pair_json = pair.json()
        claim_token = pair_json["claim_token"]
        assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {claim_token}"}).status_code == 401
        approval = client.post("/api/v1/pairing/approve", json={"code": pair_json["code"]})
        assert approval.status_code == 200

        vp3_chat = client.post(
            "/api/v1/chat",
            json={"message": "Summarize merchant context."},
            headers={"Authorization": f"Bearer {claim_token}", "Origin": "https://vp3.me"},
        )
        assert vp3_chat.status_code == 200
        vp3_conversation_id = vp3_chat.json()["conversation_id"]
        assert vp3_conversation_id != owner_conversation_id

        client_conversations = client.get("/api/v1/conversations", headers={"Authorization": f"Bearer {claim_token}"})
        assert client_conversations.status_code == 200
        assert [item["id"] for item in client_conversations.json()["items"]] == [vp3_conversation_id]
        assert client.get(
            f"/api/v1/conversations/{owner_conversation_id}", headers={"Authorization": f"Bearer {claim_token}"}
        ).status_code == 404

        repair = client.post(
            "/api/v1/pairing/request",
            json={"app_key": "vp3-test", "app_name": "VP3 Test", "permissions": ["knowledge.search"]},
        )
        repair_json = repair.json()
        assert client.post("/api/v1/pairing/approve", json={"code": repair_json["code"]}).status_code == 200
        new_token = repair_json["claim_token"]
        assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {claim_token}"}).status_code == 401
        denied_chat = client.post(
            "/api/v1/chat", json={"message": "Should be denied"}, headers={"Authorization": f"Bearer {new_token}"}
        )
        assert denied_chat.status_code == 403

        deleted = client.delete(f"/api/v1/control/knowledge/{document_id}")
        assert deleted.status_code == 200
        assert not list(settings.knowledge_files_dir.glob("*"))

        deleted_chat = client.delete(f"/api/v1/control/conversations/{owner_conversation_id}")
        assert deleted_chat.status_code == 200
        assert client.get(f"/api/v1/control/conversations/{owner_conversation_id}").status_code == 404

print("HomeServer smoke test passed")
