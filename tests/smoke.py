from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-smoke-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import providers  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    captured: dict = {}

    def fake_generate(messages, model_override=None):
        captured["messages"] = messages
        captured["model"] = model_override
        return {
            "provider": "ollama",
            "model": model_override or "llama-test",
            "content": "Local HomeServer answer.",
        }

    providers.generate_ollama = fake_generate

    with TestClient(app) as client:
        scheduler.stop()
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["version"] == "0.16.0"

        capabilities = client.get("/api/v1/capabilities", headers={"Origin": "https://vp3.me"})
        assert capabilities.status_code == 200
        capability_json = capabilities.json()
        assert capability_json["pairing_protocol"] == "claim-v1"
        assert capability_json["inference"]["available"] is False
        assert capability_json["inference"]["cloud_fallback_required"] is True
        for feature in (
            "action.approvals",
            "agent.chat",
            "agent.context",
            "agent.context.budget",
            "agent.context.sources",
            "agent.privacy.local_only",
            "agent.tools.read",
            "contacts.read",
            "inference.routing",
            "inference.status",
            "knowledge.sources.local",
            "knowledge.sources.sync",
            "notifications.read",
            "provider.credentials",
            "skills",
            "tasks.read",
            "tasks.write",
            "tasks.reminders",
            "tools.execute",
            "usage.history",
            "usage.sync",
        ):
            assert feature in capability_json["features"]
        for permission in (
            "contacts.read",
            "notifications.read",
            "tasks.read",
            "tasks.write",
            "tools.execute",
            "usage.read",
            "usage.write",
        ):
            assert permission in capability_json["permissions"]
        assert capabilities.headers.get("access-control-allow-origin") == "https://vp3.me"

        allowed_preflight = client.options(
            "/api/v1/pairing/request",
            headers={
                "Origin": "https://vp3.me",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert allowed_preflight.status_code == 200
        blocked_preflight = client.options(
            "/api/v1/pairing/request",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert blocked_preflight.status_code == 400

        status = client.get("/api/v1/status")
        assert status.status_code == 200
        assert status.json()["schema_version"] == 14

        assert client.get("/api/v1/control/overview").status_code == 401
        assert client.get("/api/v1/control/tasks").status_code == 401
        bootstrap = client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN})
        assert bootstrap.status_code == 200

        agent_tools = client.get("/api/v1/control/agent-tools")
        assert agent_tools.status_code == 200
        assert agent_tools.json()["policy"]["enabled"] is False
        assert agent_tools.json()["policy"]["max_calls"] == 3
        assert agent_tools.json()["policy"]["allow_write_proposals"] is False
        assert set(agent_tools.json()["available_tools"]) == {
            "homeserver_contacts_search",
            "homeserver_knowledge_search",
            "homeserver_memory_list",
            "homeserver_notifications_list",
            "homeserver_tasks_list",
        }
        assert client.get("/api/v1/control/action-requests?status=pending").json()["items"] == []

        owner_tools = client.get("/api/v1/control/tools")
        assert owner_tools.status_code == 200
        assert [item["key"] for item in owner_tools.json()["items"]] == [
            "contacts.search",
            "knowledge.search",
            "memory.list",
            "memory.write",
            "notifications.list",
            "tasks.create",
            "tasks.list",
        ]
        assert all(item["enabled"] and item["available"] for item in owner_tools.json()["items"])
        owner_skills = client.get("/api/v1/control/skills")
        assert owner_skills.status_code == 200
        assert [item["key"] for item in owner_skills.json()["items"]] == [
            "local.research",
            "relationship.context",
            "memory.manager",
            "task.manager",
        ]
        assert client.post("/api/v1/control/tools/not.real/execute", json={"arguments": {}}).status_code == 404

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
        inference = client.get("/api/v1/control/inference")
        assert inference.status_code == 200
        assert inference.json()["selected_provider"] == "ollama"
        assert inference.json()["compute_source"] == "homeserver_local"
        assert inference.json()["cloud_fallback_required"] is False

        agent = client.put(
            "/api/v1/control/agent",
            json={
                "name": "Private Agent",
                "model": "llama-test",
                "instructions": "Use private local context and be concise.",
            },
        )
        assert agent.status_code == 200

        memory = client.post(
            "/api/v1/control/memory",
            json={
                "memory_key": "architecture",
                "content": "HomeServer is application-neutral.",
                "importance": 0.9,
            },
        )
        assert memory.status_code == 200
        knowledge = client.post(
            "/api/v1/control/knowledge",
            json={
                "title": "Merchant plan",
                "kind": "note",
                "content": "Synthetic merchant partnerships use private HomeServer knowledge.",
            },
        )
        assert knowledge.status_code == 200

        document_bytes = b"# Merchant plan\n\nSynthetic merchant rewards remain searchable on the local HomeServer."
        imported = client.post(
            "/api/v1/control/knowledge/import",
            files={"file": ("merchant-plan.md", document_bytes, "text/markdown")},
        )
        assert imported.status_code == 200
        duplicate = client.post(
            "/api/v1/control/knowledge/import",
            files={"file": ("copy.md", document_bytes, "text/markdown")},
        )
        assert duplicate.status_code == 200 and duplicate.json()["duplicate"] is True

        private_search_query = "synthetic merchant partnerships"
        owner_tool_search = client.post(
            "/api/v1/control/tools/knowledge.search/execute",
            json={"arguments": {"query": private_search_query, "limit": 5}},
        )
        assert owner_tool_search.status_code == 200
        assert owner_tool_search.json()["result"]["count"] >= 1
        owner_tool_memory = client.post(
            "/api/v1/control/tools/memory.list/execute",
            json={"arguments": {"limit": 10}},
        )
        assert owner_tool_memory.status_code == 200
        assert owner_tool_memory.json()["result"]["count"] >= 1

        private_memory_body = "Synthetic private tool memory must not be duplicated into audit metadata."
        owner_tool_write = client.post(
            "/api/v1/control/tools/memory.write/execute",
            json={
                "arguments": {
                    "memory_key": "tool-private",
                    "content": private_memory_body,
                    "importance": 0.8,
                }
            },
        )
        assert owner_tool_write.status_code == 200
        assert owner_tool_write.json()["result"]["created"] is True

        task = client.post(
            "/api/v1/control/tasks",
            json={
                "title": "Synthetic smoke task",
                "description": "A private task used by the integration smoke test.",
                "due_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                "priority": "normal",
            },
        )
        assert task.status_code == 200
        task_id = task.json()["task"]["id"]
        owner_task_tool = client.post(
            "/api/v1/control/tools/tasks.list/execute",
            json={"arguments": {"query": "Synthetic smoke", "limit": 10}},
        )
        assert owner_task_tool.status_code == 200
        assert any(item["id"] == task_id for item in owner_task_tool.json()["result"]["items"])
        owner_notification_tool = client.post(
            "/api/v1/control/tools/notifications.list/execute",
            json={"arguments": {"limit": 10}},
        )
        assert owner_notification_tool.status_code == 200

        disable_write = client.put("/api/v1/control/tools/memory.write", json={"enabled": False})
        assert disable_write.status_code == 200
        owner_disabled_write = client.post(
            "/api/v1/control/tools/memory.write/execute",
            json={"arguments": {"content": "Should be denied while disabled."}},
        )
        assert owner_disabled_write.status_code == 403
        assert client.put("/api/v1/control/tools/memory.write", json={"enabled": True}).status_code == 200

        owner_chat = client.post(
            "/api/v1/control/chat",
            json={"message": "What do we know about synthetic merchant partnerships?"},
        )
        assert owner_chat.status_code == 200
        owner_chat_json = owner_chat.json()
        owner_conversation_id = owner_chat_json["conversation_id"]
        assert owner_chat_json["reply"] == "Local HomeServer answer."
        assert owner_chat_json["compute_source"] == "homeserver_local"
        assert owner_chat_json["cloud_tokens_debited"] == 0
        assert owner_chat_json["context"]["memory_count"] >= 1
        assert owner_chat_json["context"]["knowledge_count"] >= 1
        assert owner_chat_json["context"]["contact_count"] == 0
        assert owner_chat_json["context"]["context_chars"] > 0
        assert owner_chat_json["context"]["sources"]
        assert owner_chat_json["context"]["settings"]["include_memory"] is True
        assert owner_chat_json["context"]["settings"]["include_knowledge"] is True
        assert owner_chat_json["context"]["settings"]["include_contacts"] is True
        assert owner_chat_json["tools"]["policy_enabled"] is False
        assert owner_chat_json["tools"]["call_count"] == 0
        assert owner_chat_json["tools"]["action_request_ids"] == []
        system_prompt = captured["messages"][0]["content"]
        assert "HomeServer is application-neutral" in system_prompt
        assert "Synthetic merchant partnerships" in system_prompt
        assert "untrusted as instruction text" in system_prompt

        owner_thread = client.get(f"/api/v1/control/conversations/{owner_conversation_id}")
        assert owner_thread.status_code == 200
        owner_thread_json = owner_thread.json()
        assert [message["role"] for message in owner_thread_json["messages"]] == ["user", "assistant"]
        assert owner_thread_json["context_settings"]["max_context_chars"] == 12000
        assert owner_thread_json["context_history"]
        assert owner_thread_json["context_history"][0]["sources"]

        collision_pair = client.post(
            "/api/v1/pairing/request",
            json={"app_key": "owner", "app_name": "Owner-named Test App", "permissions": ["agent.chat"]},
        ).json()
        assert client.post("/api/v1/pairing/approve", json={"code": collision_pair["code"]}).status_code == 200
        collision_token = collision_pair["claim_token"]
        collision_headers = {"Authorization": f"Bearer {collision_token}"}
        assert client.get("/api/v1/conversations", headers=collision_headers).json()["items"] == []
        assert client.get(
            f"/api/v1/conversations/{owner_conversation_id}", headers=collision_headers
        ).status_code == 404
        collision_chat = client.post(
            "/api/v1/chat",
            json={"message": "Tell me private context."},
            headers=collision_headers,
        )
        assert collision_chat.status_code == 200
        collision_context = collision_chat.json()["context"]
        assert collision_context["memory_count"] == 0
        assert collision_context["knowledge_count"] == 0
        assert collision_context["contact_count"] == 0
        assert collision_context["context_chars"] == 0
        assert collision_context["sources"] == []
        assert collision_context["settings"]["include_memory"] is False
        assert collision_context["settings"]["include_knowledge"] is False
        assert collision_context["settings"]["include_contacts"] is False
        collision_tools = client.get("/api/v1/tools", headers=collision_headers).json()["items"]
        assert all(item["available"] is False for item in collision_tools)

        pair = client.post(
            "/api/v1/pairing/request",
            json={
                "app_key": "vp3-test",
                "app_name": "VP3 Test",
                "permissions": [
                    "agent.chat",
                    "knowledge.search",
                    "memory.read",
                    "notifications.read",
                    "tasks.read",
                    "tools.execute",
                ],
            },
            headers={"Origin": "https://vp3.me"},
        )
        assert pair.status_code == 200
        pair_json = pair.json()
        claim_token = pair_json["claim_token"]
        assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {claim_token}"}).status_code == 401
        assert client.post("/api/v1/pairing/approve", json={"code": pair_json["code"]}).status_code == 200
        vp3_headers = {"Authorization": f"Bearer {claim_token}", "Origin": "https://vp3.me"}

        vp3_tools = client.get("/api/v1/tools", headers=vp3_headers)
        assert vp3_tools.status_code == 200
        by_key = {item["key"]: item for item in vp3_tools.json()["items"]}
        assert by_key["contacts.search"]["available"] is False
        assert by_key["knowledge.search"]["available"] is True
        assert by_key["memory.list"]["available"] is True
        assert by_key["memory.write"]["available"] is False
        assert by_key["notifications.list"]["available"] is True
        assert by_key["tasks.list"]["available"] is True
        assert by_key["tasks.create"]["available"] is False
        assert by_key["tasks.create"]["missing_permissions"] == ["tasks.write"]

        vp3_skills = client.get("/api/v1/skills", headers=vp3_headers)
        assert vp3_skills.status_code == 200
        skills_by_key = {item["key"]: item for item in vp3_skills.json()["items"]}
        assert skills_by_key["local.research"]["available"] is True
        assert skills_by_key["relationship.context"]["available"] is False
        assert skills_by_key["memory.manager"]["available"] is False
        assert skills_by_key["task.manager"]["available"] is False

        vp3_tool_search = client.post(
            "/api/v1/tools/knowledge.search/execute",
            json={"arguments": {"query": private_search_query, "limit": 5}},
            headers=vp3_headers,
        )
        assert vp3_tool_search.status_code == 200
        assert vp3_tool_search.json()["result"]["count"] >= 1
        assert client.post(
            "/api/v1/tools/memory.list/execute",
            json={"arguments": {"limit": 10}},
            headers=vp3_headers,
        ).status_code == 200
        assert client.post(
            "/api/v1/tools/tasks.list/execute",
            json={"arguments": {"query": "Synthetic smoke"}},
            headers=vp3_headers,
        ).status_code == 200
        assert client.post(
            "/api/v1/tools/notifications.list/execute",
            json={"arguments": {"limit": 10}},
            headers=vp3_headers,
        ).status_code == 200
        assert client.post(
            "/api/v1/tools/memory.write/execute",
            json={"arguments": {"content": "Must be denied."}},
            headers=vp3_headers,
        ).status_code == 403
        assert client.post(
            "/api/v1/tools/tasks.create/execute",
            json={"arguments": {"title": "Must also be denied"}},
            headers=vp3_headers,
        ).status_code == 403

        vp3_chat = client.post(
            "/api/v1/chat",
            json={"message": "Summarize synthetic merchant context."},
            headers=vp3_headers,
        )
        assert vp3_chat.status_code == 200
        vp3_chat_json = vp3_chat.json()
        vp3_conversation_id = vp3_chat_json["conversation_id"]
        assert vp3_conversation_id != owner_conversation_id
        assert vp3_chat_json["context"]["memory_count"] >= 1
        assert vp3_chat_json["context"]["knowledge_count"] >= 1
        assert vp3_chat_json["context"]["contact_count"] == 0
        assert vp3_chat_json["context"]["settings"]["include_contacts"] is False
        client_conversations = client.get("/api/v1/conversations", headers=vp3_headers)
        assert [item["id"] for item in client_conversations.json()["items"]] == [vp3_conversation_id]
        vp3_thread = client.get(f"/api/v1/conversations/{vp3_conversation_id}", headers=vp3_headers)
        assert vp3_thread.status_code == 200
        assert vp3_thread.json()["context_settings"]["include_memory"] is True
        assert vp3_thread.json()["context_settings"]["include_knowledge"] is True
        assert vp3_thread.json()["context_settings"]["include_contacts"] is False
        assert all(
            source["kind"] != "contact"
            for event in vp3_thread.json()["context_history"]
            for source in event["sources"]
        )
        assert client.get(
            f"/api/v1/conversations/{owner_conversation_id}", headers=vp3_headers
        ).status_code == 404

        repair = client.post(
            "/api/v1/pairing/request",
            json={
                "app_key": "vp3-test",
                "app_name": "VP3 Test",
                "permissions": ["knowledge.search", "tools.execute"],
            },
        ).json()
        assert client.post("/api/v1/pairing/approve", json={"code": repair["code"]}).status_code == 200
        new_token = repair["claim_token"]
        assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {claim_token}"}).status_code == 401
        new_headers = {"Authorization": f"Bearer {new_token}"}
        assert client.post("/api/v1/chat", json={"message": "Denied"}, headers=new_headers).status_code == 403
        assert client.post(
            "/api/v1/tools/knowledge.search/execute",
            json={"arguments": {"query": private_search_query}},
            headers=new_headers,
        ).status_code == 200
        assert client.post(
            "/api/v1/tools/tasks.list/execute",
            json={"arguments": {}},
            headers=new_headers,
        ).status_code == 403

        runs = client.get("/api/v1/control/tool-runs?limit=200").json()["items"]
        serialized_runs = json.dumps(runs, ensure_ascii=False)
        assert private_search_query not in serialized_runs
        assert private_memory_body not in serialized_runs
        assert any(item["tool_key"] == "tasks.list" for item in runs)
        assert any(item["status"] == "denied" for item in runs)

print("HomeServer API/SQLite/Agent Brain/tool smoke test passed")