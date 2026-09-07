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


with tempfile.TemporaryDirectory(prefix="homeserver-contacts-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import providers  # noqa: E402

    with TestClient(app) as client:
        assert client.get("/api/v1/control/contacts").status_code == 401
        assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200

        notes_sentinel = "Synthetic relationship note sentinel-99173 for test-only local context."
        created = client.post(
            "/api/v1/control/contacts",
            json={
                "display_name": "Test Contact Alpha",
                "organization": "Example Organization",
                "email": "alpha@example.invalid",
                "relationship": "test relationship",
                "notes": notes_sentinel,
            },
        )
        assert created.status_code == 200
        contact_id = created.json()["contact"]["id"]

        owner_search = client.get("/api/v1/control/contacts?q=test%20relationship")
        assert owner_search.status_code == 200
        assert [item["id"] for item in owner_search.json()["items"]] == [contact_id]

        updated = client.put(
            f"/api/v1/control/contacts/{contact_id}",
            json={
                "display_name": "Test Contact Alpha",
                "organization": "Example Organization",
                "email": "alpha@example.invalid",
                "relationship": "test relationship",
                "notes": notes_sentinel + " Updated.",
            },
        )
        assert updated.status_code == 200
        assert updated.json()["contact"]["notes"].endswith("Updated.")

        no_contact_pair = client.post(
            "/api/v1/pairing/request",
            json={"app_key": "no-contacts", "app_name": "No Contacts", "permissions": ["agent.chat", "tools.execute"]},
        ).json()
        assert client.post("/api/v1/pairing/approve", json={"code": no_contact_pair["code"]}).status_code == 200
        no_contact_token = no_contact_pair["claim_token"]
        assert client.get("/api/v1/contacts", headers={"Authorization": f"Bearer {no_contact_token}"}).status_code == 403
        assert client.post(
            "/api/v1/tools/contacts.search/execute",
            json={"arguments": {"query": "Test Contact"}},
            headers={"Authorization": f"Bearer {no_contact_token}"},
        ).status_code == 403

        read_pair = client.post(
            "/api/v1/pairing/request",
            json={"app_key": "contact-reader", "app_name": "Contact Reader", "permissions": ["contacts.read"]},
        ).json()
        assert client.post("/api/v1/pairing/approve", json={"code": read_pair["code"]}).status_code == 200
        read_token = read_pair["claim_token"]
        contact_read = client.get(
            "/api/v1/contacts?q=Example",
            headers={"Authorization": f"Bearer {read_token}"},
        )
        assert contact_read.status_code == 200
        assert contact_read.json()["items"][0]["id"] == contact_id
        assert client.post(
            "/api/v1/tools/contacts.search/execute",
            json={"arguments": {"query": "Example"}},
            headers={"Authorization": f"Bearer {read_token}"},
        ).status_code == 403

        tool_pair = client.post(
            "/api/v1/pairing/request",
            json={
                "app_key": "relationship-agent",
                "app_name": "Relationship Agent",
                "permissions": ["agent.chat", "contacts.read", "tools.execute"],
            },
        ).json()
        assert client.post("/api/v1/pairing/approve", json={"code": tool_pair["code"]}).status_code == 200
        tool_token = tool_pair["claim_token"]

        private_query = "Test Contact relationship"
        direct_search = client.post(
            "/api/v1/tools/contacts.search/execute",
            json={"arguments": {"query": private_query, "limit": 5}},
            headers={"Authorization": f"Bearer {tool_token}"},
        )
        assert direct_search.status_code == 200
        assert direct_search.json()["result"]["count"] == 1

        assert client.put(
            "/api/v1/control/provider",
            json={"base_url": "http://127.0.0.1:11434", "model": "llama-test", "enabled": True},
        ).status_code == 200
        assert client.put(
            "/api/v1/control/agent",
            json={"name": "Relationship Agent", "model": "llama-test", "instructions": "Use permitted local relationship context."},
        ).status_code == 200
        assert client.put(
            "/api/v1/control/agent-tools",
            json={"enabled": True, "max_calls": 2, "allow_write_proposals": False},
        ).status_code == 200

        steps: list[dict] = []
        sequence = [
            {
                "provider": "ollama",
                "model": "llama-test",
                "content": "",
                "tool_calls": [tool_call("homeserver_contacts_search", {"query": "Test Contact", "limit": 3})],
            },
            {
                "provider": "ollama",
                "model": "llama-test",
                "content": "The synthetic contact is in Example Organization.",
                "tool_calls": [],
            },
        ]

        def fake_step(messages, *, tools=None, model_override=None):
            steps.append({"messages": [dict(item) for item in messages], "tools": tools})
            return sequence.pop(0)

        providers.generate_ollama_step = fake_step
        agent_chat = client.post(
            "/api/v1/chat",
            json={"message": "Find the synthetic contact."},
            headers={"Authorization": f"Bearer {tool_token}"},
        )
        assert agent_chat.status_code == 200
        offered = {item["function"]["name"] for item in steps[0]["tools"]}
        assert offered == {"homeserver_contacts_search"}
        assert agent_chat.json()["tools"]["call_count"] == 1
        assert len(agent_chat.json()["tools"]["run_ids"]) == 1
        assert any(item.get("role") == "tool" for item in steps[1]["messages"])

        with db() as connection:
            rows = connection.execute(
                "SELECT tool_key, arguments_meta_json, result_meta_json FROM tool_runs ORDER BY id"
            ).fetchall()
            audit_blob = json.dumps([dict(row) for row in rows], ensure_ascii=False)
            assert private_query not in audit_blob
            assert notes_sentinel not in audit_blob
            assert "query_length" in audit_blob

        assert client.put("/api/v1/control/tools/contacts.search", json={"enabled": False}).status_code == 200
        disabled = client.post(
            "/api/v1/tools/contacts.search/execute",
            json={"arguments": {"query": "Test Contact"}},
            headers={"Authorization": f"Bearer {tool_token}"},
        )
        assert disabled.status_code == 403
        assert client.put("/api/v1/control/tools/contacts.search", json={"enabled": True}).status_code == 200

        deleted = client.delete(f"/api/v1/control/contacts/{contact_id}")
        assert deleted.status_code == 200
        assert client.get("/api/v1/control/contacts?q=Test%20Contact").json()["items"] == []

print("HomeServer contacts test passed")
