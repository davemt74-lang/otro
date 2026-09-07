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


def tool_call(name: str, arguments: dict) -> dict:
    return {"type": "function", "function": {"name": name, "arguments": arguments}}


with tempfile.TemporaryDirectory(prefix="homeserver-agent-tools-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import providers  # noqa: E402

    normal_calls: list[list[dict]] = []
    step_calls: list[dict] = []

    def fake_normal(messages, model_override=None):
        normal_calls.append(messages)
        return {"provider": "ollama", "model": model_override or "llama-test", "content": "Normal local answer."}

    providers.generate_ollama = fake_normal

    with TestClient(app) as client:
        assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200
        assert client.put(
            "/api/v1/control/provider",
            json={"base_url": "http://127.0.0.1:11434", "model": "llama-test", "enabled": True},
        ).status_code == 200
        assert client.put(
            "/api/v1/control/agent",
            json={"name": "Tool Agent", "model": "llama-test", "instructions": "Use local tools only when useful."},
        ).status_code == 200
        assert client.post(
            "/api/v1/control/memory",
            json={"memory_key": "private", "content": "Private memory visible only with memory.read.", "importance": 0.9},
        ).status_code == 200
        assert client.post(
            "/api/v1/control/knowledge",
            json={"title": "Merchant context", "kind": "note", "content": "Merchant partnership context is stored locally."},
        ).status_code == 200

        default_policy = client.get("/api/v1/control/agent-tools")
        assert default_policy.status_code == 200
        assert default_policy.json()["policy"] == {
            "enabled": False,
            "max_calls": 3,
            "allow_write_proposals": False,
            "created_at": default_policy.json()["policy"]["created_at"],
            "updated_at": default_policy.json()["policy"]["updated_at"],
        }
        assert set(default_policy.json()["available_tools"]) == {
            "homeserver_contacts_search",
            "homeserver_knowledge_search",
            "homeserver_memory_list",
        }

        baseline = client.post("/api/v1/control/chat", json={"message": "Baseline before autonomous tools."})
        assert baseline.status_code == 200
        assert baseline.json()["tools"]["policy_enabled"] is False
        assert baseline.json()["tools"]["call_count"] == 0
        assert baseline.json()["tools"]["action_request_ids"] == []
        assert len(normal_calls) == 1

        assert client.put("/api/v1/control/agent-tools", json={"enabled": True, "max_calls": 2}).status_code == 200
        assert client.put("/api/v1/control/agent-tools", json={"enabled": True, "max_calls": 4}).status_code == 422

        owner_sequence = [
            {"provider":"ollama","model":"llama-test","content":"","tool_calls":[tool_call("homeserver_knowledge_search", {"query":"merchant partnership","limit":3})]},
            {"provider":"ollama","model":"llama-test","content":"I used the local knowledge search.","tool_calls":[]},
        ]

        def fake_owner_step(messages, *, tools=None, model_override=None):
            step_calls.append({"messages": [dict(item) for item in messages], "tools": tools})
            return owner_sequence.pop(0)

        providers.generate_ollama_step = fake_owner_step
        owner = client.post("/api/v1/control/chat", json={"message": "Search our merchant partnership knowledge."})
        assert owner.status_code == 200
        owner_json = owner.json()
        assert owner_json["reply"] == "I used the local knowledge search."
        assert owner_json["tools"]["policy_enabled"] is True
        assert owner_json["tools"]["available"] is True
        assert owner_json["tools"]["call_count"] == 1
        assert owner_json["tools"]["action_request_ids"] == []
        assert len(owner_json["tools"]["run_ids"]) == 1
        offered = {item["function"]["name"] for item in step_calls[0]["tools"]}
        assert offered == {
            "homeserver_contacts_search",
            "homeserver_knowledge_search",
            "homeserver_memory_list",
        }
        assert "homeserver_memory_write_request" not in offered
        second_messages = step_calls[1]["messages"]
        assert any(item.get("role") == "tool" and item.get("tool_name") == "homeserver_knowledge_search" for item in second_messages)

        thread = client.get(f"/api/v1/control/conversations/{owner_json['conversation_id']}")
        assert thread.status_code == 200
        assert [item["role"] for item in thread.json()["messages"]] == ["user", "assistant"]
        assert all("tool_name" not in item for item in thread.json()["messages"])

        with db() as connection:
            owner_run = connection.execute("SELECT tool_call_count, metadata_json FROM agent_runs WHERE id=?", (owner_json["run_id"],)).fetchone()
            assert owner_run is not None
            assert owner_run["tool_call_count"] == 1
            metadata = json.loads(owner_run["metadata_json"])
            assert metadata["tool_run_ids"] == owner_json["tools"]["run_ids"]
            assert metadata["action_request_ids"] == []

        pair = client.post(
            "/api/v1/pairing/request",
            json={"app_key":"knowledge-agent","app_name":"Knowledge Agent","permissions":["agent.chat","knowledge.search","tools.execute"]},
        )
        pair_json = pair.json()
        assert client.post("/api/v1/pairing/approve", json={"code": pair_json["code"]}).status_code == 200
        token = pair_json["claim_token"]

        app_steps: list[dict] = []
        app_sequence = [
            {"provider":"ollama","model":"llama-test","content":"","tool_calls":[tool_call("homeserver_memory_list", {"limit":10})]},
            {"provider":"ollama","model":"llama-test","content":"Memory was not available to this app.","tool_calls":[]},
        ]

        def fake_app_step(messages, *, tools=None, model_override=None):
            app_steps.append({"messages": [dict(item) for item in messages], "tools": tools})
            return app_sequence.pop(0)

        providers.generate_ollama_step = fake_app_step
        app_chat = client.post("/api/v1/chat", json={"message":"Try to inspect memory."}, headers={"Authorization":f"Bearer {token}"})
        assert app_chat.status_code == 200
        assert app_chat.json()["context"]["memory_count"] == 0
        app_offered = {item["function"]["name"] for item in app_steps[0]["tools"]}
        assert app_offered == {"homeserver_knowledge_search"}
        assert app_chat.json()["tools"]["call_count"] == 1
        assert len(app_chat.json()["tools"]["run_ids"]) == 1
        assert any(item.get("role") == "tool" and "denied" in item.get("content", "").lower() for item in app_steps[1]["messages"])

        with db() as connection:
            denied = connection.execute("SELECT tool_key, status FROM tool_runs WHERE id=?", (app_chat.json()["tools"]["run_ids"][0],)).fetchone()
            assert denied is not None
            assert denied["tool_key"] == "agent.unknown_tool"
            assert denied["status"] == "denied"

        no_tools_pair = client.post(
            "/api/v1/pairing/request",
            json={"app_key":"no-tools","app_name":"No Tools","permissions":["agent.chat","knowledge.search"]},
        ).json()
        assert client.post("/api/v1/pairing/approve", json={"code": no_tools_pair["code"]}).status_code == 200
        no_tools_token = no_tools_pair["claim_token"]
        normal_before = len(normal_calls)
        no_tools_chat = client.post("/api/v1/chat", json={"message":"Can you use a tool?"}, headers={"Authorization":f"Bearer {no_tools_token}"})
        assert no_tools_chat.status_code == 200
        assert no_tools_chat.json()["tools"]["policy_enabled"] is True
        assert no_tools_chat.json()["tools"]["available"] is False
        assert no_tools_chat.json()["tools"]["call_count"] == 0
        assert len(normal_calls) == normal_before + 1

        assert client.put("/api/v1/control/agent-tools", json={"enabled":True,"max_calls":1}).status_code == 200
        budget_steps: list[dict] = []
        budget_sequence = [
            {"provider":"ollama","model":"llama-test","content":"","tool_calls":[tool_call("homeserver_knowledge_search", {"query":"merchant"}), tool_call("homeserver_memory_list", {"limit":5})]},
            {"provider":"ollama","model":"llama-test","content":"One tool call was enough.","tool_calls":[]},
        ]

        def fake_budget_step(messages, *, tools=None, model_override=None):
            budget_steps.append({"messages": [dict(item) for item in messages], "tools": tools})
            return budget_sequence.pop(0)

        providers.generate_ollama_step = fake_budget_step
        budget_chat = client.post("/api/v1/control/chat", json={"message":"Use both local sources."})
        assert budget_chat.status_code == 200
        assert budget_chat.json()["tools"]["max_calls"] == 1
        assert budget_chat.json()["tools"]["call_count"] == 1
        assert len(budget_chat.json()["tools"]["run_ids"]) == 1
        assert budget_steps[1]["tools"] is None
        tool_messages = [item for item in budget_steps[1]["messages"] if item.get("role") == "tool"]
        assert len(tool_messages) == 2
        assert any("budget was exhausted" in item["content"] for item in tool_messages)

        memories_before = client.get("/api/v1/control/memory").json()["items"]
        write_sequence = [
            {"provider":"ollama","model":"llama-test","content":"","tool_calls":[tool_call("homeserver_memory_write", {"content":"The model must not write this."})]},
            {"provider":"ollama","model":"llama-test","content":"Write access was not available.","tool_calls":[]},
        ]

        def fake_write_step(messages, *, tools=None, model_override=None):
            return write_sequence.pop(0)

        providers.generate_ollama_step = fake_write_step
        write_attempt = client.post("/api/v1/control/chat", json={"message":"Try to store a memory automatically."})
        assert write_attempt.status_code == 200
        assert write_attempt.json()["tools"]["call_count"] == 1
        memories_after = client.get("/api/v1/control/memory").json()["items"]
        assert len(memories_after) == len(memories_before)
        with db() as connection:
            last_unknown = connection.execute("SELECT tool_key, status FROM tool_runs ORDER BY id DESC LIMIT 1").fetchone()
            assert last_unknown is not None
            assert last_unknown["tool_key"] == "agent.unknown_tool"
            assert last_unknown["status"] == "denied"

        assert client.put("/api/v1/control/agent-tools", json={"enabled":False,"max_calls":3}).status_code == 200
        normal_before = len(normal_calls)
        disabled_again = client.post("/api/v1/control/chat", json={"message":"Agent tools are off again."})
        assert disabled_again.status_code == 200
        assert disabled_again.json()["tools"]["policy_enabled"] is False
        assert disabled_again.json()["tools"]["call_count"] == 0
        assert len(normal_calls) == normal_before + 1

print("HomeServer Agent Tool test passed")