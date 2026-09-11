from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient


with tempfile.TemporaryDirectory(prefix="homeserver-agent-routing-v047-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import brain, providers  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    captured_calls: list[dict] = []

    def fake_inference_status() -> dict:
        return {
            "available": True,
            "selected_provider": "ollama",
            "model": "default-local-model",
            "compute_source": "homeserver_local",
            "cloud_fallback_required": False,
            "preferred_provider": "auto",
            "providers": [
                {
                    "provider_key": "ollama",
                    "ready": True,
                    "enabled": True,
                    "model": "default-local-model",
                }
            ],
        }

    def fake_generate(
        messages,
        *,
        source_app_key,
        selected_model,
        granted_permissions,
        owner,
        state=None,
        provider_key=None,
    ):
        tool_state = state if isinstance(state, dict) else {}
        tool_state.setdefault("call_count", 0)
        tool_state.setdefault("run_ids", [])
        tool_state.setdefault("action_request_ids", [])
        tool_state.setdefault(
            "provider_usage",
            {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )
        captured_calls.append(
            {
                "messages": messages,
                "source_app_key": source_app_key,
                "selected_model": selected_model,
                "permissions": set(granted_permissions),
                "owner": owner,
                "provider_key": provider_key,
            }
        )
        return {
            "content": f"reply from {selected_model or 'default-local-model'}",
            "provider": "ollama",
            "model": selected_model or "default-local-model",
        }, tool_state

    providers.inference_status = fake_inference_status
    brain._generate_with_agent_tools = fake_generate

    with TestClient(app) as client:
        scheduler.stop()

        # Owner surfaces stay behind the local owner session boundary.
        assert client.get("/api/v1/control/agent-routing").status_code == 401
        assert client.post(
            "/__owner/session",
            headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN},
        ).status_code == 200

        agents = client.get("/api/v1/control/agents").json()
        primary = next(item for item in agents["items"] if item["is_primary"])
        primary_id = int(primary["id"])

        created = client.post(
            "/api/v1/control/agents",
            json={
                "name": "Research Agent",
                "instructions": "Use the Research Agent persona and prioritize source-grounded analysis.",
                "model": "research-local-model",
            },
        )
        assert created.status_code == 200, created.text
        secondary = created.json()["agent"]
        secondary_id = int(secondary["id"])

        another = client.post(
            "/api/v1/control/agents",
            json={"name": "Private Agent", "instructions": "Private-only persona.", "model": "private-model"},
        )
        assert another.status_code == 200
        private_agent_id = int(another.json()["agent"]["id"])

        with db() as connection:
            connection.execute(
                "INSERT INTO agent_memory(agent_id, memory_key, content, importance) VALUES (?, ?, ?, 1)",
                (primary_id, "v047.primary", "PRIMARY MEMORY MUST NOT ROUTE TO SECONDARY"),
            )
            connection.execute(
                "INSERT INTO agent_memory(agent_id, memory_key, content, importance) VALUES (?, ?, ?, 1)",
                (secondary_id, "v047.secondary", "SECONDARY RESEARCH MEMORY"),
            )

        # Owner-selected stateful chat uses the secondary identity, instructions,
        # model, and Agent-scoped memory through the canonical v4.30 context path.
        selected_chat = client.post(
            "/api/v1/control/chat",
            json={"message": "Research this locally", "agent_id": secondary_id},
        )
        assert selected_chat.status_code == 200, selected_chat.text
        selected_payload = selected_chat.json()
        assert selected_payload["agent_routing_version"] == "v0.47"
        assert selected_payload["agent"] == {
            "id": secondary_id,
            "name": "Research Agent",
            "is_primary": False,
        }
        assert selected_payload["model"] == "research-local-model"
        assert selected_payload["context"]["memory_count"] >= 1
        selected_conversation = selected_payload["conversation_id"]
        selected_system = captured_calls[-1]["messages"][0]["content"]
        assert "Research Agent" in selected_system
        assert "source-grounded analysis" in selected_system
        assert "SECONDARY RESEARCH MEMORY" in selected_system
        assert "PRIMARY MEMORY MUST NOT ROUTE TO SECONDARY" not in selected_system
        assert captured_calls[-1]["selected_model"] == "research-local-model"

        detail = client.get(f"/api/v1/control/conversations/{selected_conversation}")
        assert detail.status_code == 200, detail.text
        routing = detail.json()["routing"]
        assert routing["version"] == "v0.47"
        assert routing["agent"]["id"] == secondary_id
        assert routing["available"] is True

        # A conversation is permanently persona-bound. The caller must start a
        # new thread instead of silently changing Agent identity mid-history.
        switched = client.post(
            "/api/v1/control/chat",
            json={
                "message": "Try to switch this thread",
                "conversation_id": selected_conversation,
                "agent_id": primary_id,
            },
        )
        assert switched.status_code == 409, switched.text
        assert "bound to another Agent" in switched.json()["detail"]

        same_agent = client.post(
            "/api/v1/control/chat",
            json={
                "message": "Continue research",
                "conversation_id": selected_conversation,
                "agent_id": secondary_id,
            },
        )
        assert same_agent.status_code == 200, same_agent.text
        assert same_agent.json()["conversation_id"] == selected_conversation
        assert same_agent.json()["agent"]["id"] == secondary_id

        unknown = client.post(
            "/api/v1/control/chat",
            json={"message": "Unknown agent", "agent_id": 999999},
        )
        assert unknown.status_code == 404

        # Omitting agent_id is the unchanged legacy behavior: primary Agent.
        legacy = client.post("/api/v1/control/chat", json={"message": "Use the default"})
        assert legacy.status_code == 200, legacy.text
        assert legacy.json()["agent"]["id"] == primary_id
        assert legacy.json()["agent"]["is_primary"] is True

        # Create a paired wrapper with only agent.chat. Secondary selection must
        # be denied until the owner grants that exact Agent, and Agent selection
        # must never broaden memory permissions.
        app_key = "v047-wrapper"
        app_token = "v047-test-token-with-sufficient-length"
        token_hash = hashlib.sha256(app_token.encode("utf-8")).hexdigest()
        with db() as connection:
            cursor = connection.execute(
                "INSERT INTO paired_apps(app_key, name, status, token_hash) VALUES (?, ?, 'active', ?)",
                (app_key, "v0.47 Wrapper", token_hash),
            )
            app_id = int(cursor.lastrowid)
            connection.execute(
                "INSERT INTO app_permissions(paired_app_id, permission, allowed) VALUES (?, 'agent.chat', 1)",
                (app_id,),
            )

        headers = {"Authorization": f"Bearer {app_token}"}
        visible_before = client.get("/api/v1/agents", headers=headers)
        assert visible_before.status_code == 200, visible_before.text
        assert [item["id"] for item in visible_before.json()["items"]] == [primary_id]

        denied = client.post(
            "/api/v1/chat",
            headers=headers,
            json={"message": "Use research", "agent_id": secondary_id},
        )
        assert denied.status_code == 403, denied.text
        assert "not authorized" in denied.json()["detail"]

        access = client.get(f"/api/v1/control/connected-apps/{app_id}/agents")
        assert access.status_code == 200, access.text
        primary_access = next(item for item in access.json()["items"] if item["id"] == primary_id)
        assert primary_access["allowed"] is True
        assert primary_access["implicit"] is True
        assert client.put(
            f"/api/v1/control/connected-apps/{app_id}/agents/{primary_id}",
            json={"allowed": False},
        ).status_code == 409

        granted = client.put(
            f"/api/v1/control/connected-apps/{app_id}/agents/{secondary_id}",
            json={"allowed": True},
        )
        assert granted.status_code == 200, granted.text
        assert granted.json()["version"] == "v0.47"
        assert granted.json()["allowed"] is True

        visible_after = client.get("/api/v1/agents", headers=headers)
        assert visible_after.status_code == 200
        assert {item["id"] for item in visible_after.json()["items"]} == {primary_id, secondary_id}
        assert private_agent_id not in {item["id"] for item in visible_after.json()["items"]}

        app_selected = client.post(
            "/api/v1/chat",
            headers=headers,
            json={"message": "Run scoped research", "agent_id": secondary_id},
        )
        assert app_selected.status_code == 200, app_selected.text
        app_payload = app_selected.json()
        assert app_payload["agent"]["id"] == secondary_id
        assert app_payload["context"]["memory_count"] == 0, "Agent selection must not grant memory.read"
        assert "SECONDARY RESEARCH MEMORY" not in captured_calls[-1]["messages"][0]["content"]
        app_conversation = app_payload["conversation_id"]

        client_binding = client.get(
            f"/api/v1/conversations/{app_conversation}/routing",
            headers=headers,
        )
        assert client_binding.status_code == 200
        assert client_binding.json()["agent"]["id"] == secondary_id

        # Delegated VP3 execution uses the same selected HomeServer Agent and
        # the same explicit grant boundary; the wrapper overlay cannot expand it.
        delegated = client.post(
            "/api/v1/chat",
            headers=headers,
            json={
                "message": "Delegate research",
                "agent_id": secondary_id,
                "external_conversation_id": "vp3-v047-1",
                "delegation": {
                    "name": "VP3 Research UI Agent",
                    "role": "research",
                    "instructions": "Summarize clearly.",
                },
                "surface_context": {"page": "research"},
            },
        )
        assert delegated.status_code == 200, delegated.text
        delegated_payload = delegated.json()
        assert delegated_payload["agent"]["id"] == secondary_id
        assert delegated_payload["agent_routing_version"] == "v0.47"
        assert delegated_payload["model"] == "research-local-model"
        assert delegated_payload["context"]["memory_count"] == 0

        revoked = client.put(
            f"/api/v1/control/connected-apps/{app_id}/agents/{secondary_id}",
            json={"allowed": False},
        )
        assert revoked.status_code == 200
        assert revoked.json()["allowed"] is False
        assert client.post(
            "/api/v1/chat",
            headers=headers,
            json={"message": "Use research after revoke", "agent_id": secondary_id},
        ).status_code == 403
        assert client.post(
            "/api/v1/chat",
            headers=headers,
            json={
                "message": "Delegate after revoke",
                "agent_id": secondary_id,
                "external_conversation_id": "vp3-v047-2",
                "delegation": {"name": "VP3", "role": "research", "instructions": ""},
            },
        ).status_code == 403

        # Primary chat remains available through the long-standing agent.chat grant.
        app_primary = client.post("/api/v1/chat", headers=headers, json={"message": "Default wrapper chat"})
        assert app_primary.status_code == 200, app_primary.text
        assert app_primary.json()["agent"]["id"] == primary_id

        with db() as connection:
            grant_row = connection.execute(
                "SELECT allowed FROM app_agent_grants WHERE paired_app_id=? AND agent_id=?",
                (app_id, secondary_id),
            ).fetchone()
            assert grant_row is not None and int(grant_row["allowed"]) == 0
            audit = connection.execute(
                "SELECT COUNT(*) AS count FROM activity_log WHERE action='app.agent_access.updated' AND resource_key=?",
                (str(app_id),),
            ).fetchone()
            assert int(audit["count"]) >= 2

        capabilities = client.get("/api/v1/capabilities")
        assert capabilities.status_code == 200
        caps = capabilities.json()
        assert caps["agent_routing"]["version"] == "v0.47"
        assert caps["agent_routing"]["paired_app_scoped"] is True
        assert caps["agent_routing"]["conversation_bound"] is True
        assert "agent.routing.v047" in caps["features"]

print("HomeServer v0.47 Agent Selection & Persona Routing regression passed")
