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


with tempfile.TemporaryDirectory(prefix="homeserver-approvals-") as data_dir:
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
            json={"name": "Approval Agent", "model": "llama-test", "instructions": "Propose writes only when useful."},
        ).status_code == 200

        default_policy = client.get("/api/v1/control/agent-tools").json()
        assert default_policy["policy"]["allow_write_proposals"] is False
        assert "homeserver_memory_write_request" not in default_policy["available_tools"]

        enabled = client.put(
            "/api/v1/control/agent-tools",
            json={"enabled": True, "max_calls": 2, "allow_write_proposals": True},
        )
        assert enabled.status_code == 200
        assert enabled.json()["policy"]["allow_write_proposals"] is True
        available = client.get("/api/v1/control/agent-tools").json()["available_tools"]
        assert "homeserver_memory_write_request" in available

        owner_private = "OWNER_APPROVAL_PRIVATE_MEMORY_720941"
        owner_steps = []
        owner_sequence = [
            {
                "provider": "ollama",
                "model": "llama-test",
                "content": "",
                "tool_calls": [
                    tool_call(
                        "homeserver_memory_write_request",
                        {"memory_key": "owner-approved", "content": owner_private, "importance": 0.8},
                    )
                ],
            },
            {
                "provider": "ollama",
                "model": "llama-test",
                "content": "I proposed a memory write for your approval.",
                "tool_calls": [],
            },
        ]

        def owner_step(messages, *, tools=None, model_override=None):
            owner_steps.append({"messages": [dict(item) for item in messages], "tools": tools})
            return owner_sequence.pop(0)

        providers.generate_ollama_step = owner_step
        before = len(client.get("/api/v1/control/memory").json()["items"])
        proposed = client.post("/api/v1/control/chat", json={"message": "Remember this only after I approve it."})
        assert proposed.status_code == 200
        proposed_json = proposed.json()
        assert proposed_json["tools"]["call_count"] == 1
        assert len(proposed_json["tools"]["action_request_ids"]) == 1
        request_id = proposed_json["tools"]["action_request_ids"][0]
        assert len(client.get("/api/v1/control/memory").json()["items"]) == before
        offered = {item["function"]["name"] for item in owner_steps[0]["tools"]}
        assert "homeserver_memory_write_request" in offered
        assert "homeserver_memory_write" not in offered

        pending = client.get("/api/v1/control/action-requests?status=pending").json()["items"]
        assert len(pending) == 1
        assert pending[0]["id"] == request_id
        assert pending[0]["arguments"]["content"] == owner_private
        assert pending[0]["status"] == "pending"

        run_metadata = client.get("/api/v1/control/tool-runs?limit=100").json()["items"]
        activity = client.get("/api/v1/control/activity?limit=100").json()["items"]
        assert owner_private not in json.dumps(run_metadata, ensure_ascii=False)
        assert owner_private not in json.dumps(activity, ensure_ascii=False)
        assert any(item["tool_key"] == "memory.write.request" for item in run_metadata)

        approved = client.post(f"/api/v1/control/action-requests/{request_id}/approve")
        assert approved.status_code == 200
        approved_item = approved.json()["request"]
        assert approved_item["status"] == "executed"
        assert approved_item["execution_tool_run_id"] is not None
        memories = client.get("/api/v1/control/memory").json()["items"]
        assert len(memories) == before + 1
        assert any(item["content"] == owner_private for item in memories)
        assert client.post(f"/api/v1/control/action-requests/{request_id}/approve").status_code == 409

        deny_private = "DENIED_APPROVAL_MEMORY_881203"
        deny_sequence = [
            {
                "provider": "ollama",
                "model": "llama-test",
                "content": "",
                "tool_calls": [tool_call("homeserver_memory_write_request", {"content": deny_private})],
            },
            {"provider": "ollama", "model": "llama-test", "content": "Pending your approval.", "tool_calls": []},
        ]
        providers.generate_ollama_step = lambda messages, *, tools=None, model_override=None: deny_sequence.pop(0)
        denied_chat = client.post("/api/v1/control/chat", json={"message": "Propose another memory."})
        deny_id = denied_chat.json()["tools"]["action_request_ids"][0]
        assert client.post(f"/api/v1/control/action-requests/{deny_id}/deny").status_code == 200
        assert client.post(f"/api/v1/control/action-requests/{deny_id}/approve").status_code == 409
        assert not any(item["content"] == deny_private for item in client.get("/api/v1/control/memory").json()["items"])

        pair = client.post(
            "/api/v1/pairing/request",
            json={
                "app_key": "vp3-approval",
                "app_name": "VP3 Approval Test",
                "permissions": ["agent.chat", "memory.write", "tools.execute"],
            },
        ).json()
        assert client.post("/api/v1/pairing/approve", json={"code": pair["code"]}).status_code == 200
        token = pair["claim_token"]
        app_private = "APP_APPROVAL_PRIVATE_MEMORY_332109"
        app_sequence = [
            {
                "provider": "ollama",
                "model": "llama-test",
                "content": "",
                "tool_calls": [tool_call("homeserver_memory_write_request", {"content": app_private, "importance": 0.6})],
            },
            {"provider": "ollama", "model": "llama-test", "content": "I sent that to HomeServer for approval.", "tool_calls": []},
        ]
        app_tools_seen = []

        def app_step(messages, *, tools=None, model_override=None):
            app_tools_seen.append(tools)
            return app_sequence.pop(0)

        providers.generate_ollama_step = app_step
        app_chat = client.post(
            "/api/v1/chat",
            json={"message": "Ask the owner to remember this."},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert app_chat.status_code == 200
        app_id = app_chat.json()["tools"]["action_request_ids"][0]
        app_offered = {item["function"]["name"] for item in app_tools_seen[0]}
        assert app_offered == {"homeserver_memory_write_request"}
        assert not any(item["content"] == app_private for item in client.get("/api/v1/control/memory").json()["items"])

        app_status = client.get(
            f"/api/v1/action-requests/{app_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert app_status.status_code == 200
        status_payload = app_status.json()["request"]
        assert status_payload["status"] == "pending"
        assert "arguments" not in status_payload
        assert "arguments_meta" not in status_payload
        assert app_private not in json.dumps(status_payload, ensure_ascii=False)

        other = client.post(
            "/api/v1/pairing/request",
            json={"app_key": "other-app", "app_name": "Other App", "permissions": ["agent.chat", "memory.write", "tools.execute"]},
        ).json()
        assert client.post("/api/v1/pairing/approve", json={"code": other["code"]}).status_code == 200
        other_token = other["claim_token"]
        assert client.get(
            f"/api/v1/action-requests/{app_id}",
            headers={"Authorization": f"Bearer {other_token}"},
        ).status_code == 404

        assert client.post(f"/api/v1/control/action-requests/{app_id}/approve").status_code == 200
        assert any(item["content"] == app_private for item in client.get("/api/v1/control/memory").json()["items"])
        executed_status = client.get(
            f"/api/v1/action-requests/{app_id}",
            headers={"Authorization": f"Bearer {token}"},
        ).json()["request"]
        assert executed_status["status"] == "executed"
        assert executed_status["execution_tool_run_id"] is not None

        assert client.put("/api/v1/control/tools/memory.write", json={"enabled": False}).status_code == 200
        owner_policy = client.get("/api/v1/control/agent-tools").json()
        assert "homeserver_memory_write_request" not in owner_policy["available_tools"]

        action_count_before = len(client.get("/api/v1/control/action-requests").json()["items"])
        hallucinated_sequence = [
            {
                "provider": "ollama",
                "model": "llama-test",
                "content": "",
                "tool_calls": [tool_call("homeserver_memory_write_request", {"content": "Must not become pending."})],
            },
            {"provider": "ollama", "model": "llama-test", "content": "The proposal tool was unavailable.", "tool_calls": []},
        ]
        providers.generate_ollama_step = lambda messages, *, tools=None, model_override=None: hallucinated_sequence.pop(0)
        hallucinated = client.post("/api/v1/control/chat", json={"message": "Try the unavailable proposal tool."})
        assert hallucinated.status_code == 200
        assert hallucinated.json()["tools"]["action_request_ids"] == []
        assert len(client.get("/api/v1/control/action-requests").json()["items"]) == action_count_before
        assert client.put("/api/v1/control/tools/memory.write", json={"enabled": True}).status_code == 200

        with db() as connection:
            proposal_rows = connection.execute(
                "SELECT arguments_json, arguments_meta_json FROM action_requests ORDER BY created_at"
            ).fetchall()
            assert any(owner_private in row["arguments_json"] for row in proposal_rows)
            assert all(owner_private not in row["arguments_meta_json"] for row in proposal_rows)

print("HomeServer action approval test passed")