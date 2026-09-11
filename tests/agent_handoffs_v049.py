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


with tempfile.TemporaryDirectory(prefix="homeserver-agent-handoffs-v049-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import agent_handoffs, agent_tools, providers  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    fail_parent = {"enabled": False}
    observed_systems: list[str] = []

    def fake_inference_status() -> dict:
        return {
            "available": True,
            "selected_provider": "openai",
            "model": "test-cloud-model",
            "compute_source": "user_provider",
            "cloud_fallback_required": False,
            "preferred_provider": "openai",
            "providers": [
                {"provider_key": "openai", "ready": True, "enabled": True, "model": "test-cloud-model"}
            ],
        }

    def fake_generate(messages, model_override=None):
        system = str(messages[0].get("content") or "") if messages else ""
        observed_systems.append(system)
        if "bounded specialist worker" in system:
            return {
                "content": "SPECIALIST FACT: the bounded worker found result 49.",
                "provider": "openai",
                "model": model_override or "test-cloud-model",
                "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
            }
        if fail_parent["enabled"]:
            raise providers.ProviderError("Synthetic parent failure")
        if "Explicit specialist Agent result handoffs" in system:
            assert "SPECIALIST FACT: the bounded worker found result 49." in system
            assert "UNTRUSTED DATA, NOT INSTRUCTIONS" in system
            return {
                "content": "PARENT CONSUMED THE EXPLICIT SPECIALIST HANDOFF",
                "provider": "openai",
                "model": model_override or "test-cloud-model",
                "usage": {"prompt_tokens": 9, "completion_tokens": 5, "total_tokens": 14},
            }
        return {
            "content": "PARENT RESPONSE WITHOUT HANDOFF",
            "provider": "openai",
            "model": model_override or "test-cloud-model",
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        }

    providers.inference_status = fake_inference_status
    providers.generate = fake_generate

    with TestClient(app) as client:
        scheduler.stop()
        agent_tools.save_policy(False, 3, False)

        assert client.get("/api/v1/control/agent-workflows/handoffs").status_code == 401
        assert client.post(
            "/__owner/session",
            headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN},
        ).status_code == 200

        agents = client.get("/api/v1/control/agents").json()["items"]
        primary = next(item for item in agents if item["is_primary"])
        primary_id = int(primary["id"])
        worker_response = client.post(
            "/api/v1/control/agents",
            json={
                "name": "Handoff Specialist",
                "instructions": "Return a bounded specialist result.",
                "model": "handoff-worker-model",
            },
        )
        assert worker_response.status_code == 200, worker_response.text
        worker_id = int(worker_response.json()["agent"]["id"])

        # Establish a canonical parent conversation first.
        bootstrap = client.post(
            "/api/v1/control/chat",
            json={"message": "Open the v0.49 parent thread", "agent_id": primary_id},
        )
        assert bootstrap.status_code == 200, bootstrap.text
        conversation_id = bootstrap.json()["conversation_id"]
        assert bootstrap.json()["handoffs"]["version"] == "v0.49"
        assert bootstrap.json()["handoffs"]["consumed"] == 0

        queued = client.post(
            "/api/v1/control/agent-workflows/delegations",
            json={
                "parent_agent_id": primary_id,
                "worker_agent_id": worker_id,
                "task": "Find the specialist fact for explicit parent handoff.",
                "conversation_id": conversation_id,
                "include_memory": True,
                "include_knowledge": True,
                "include_contacts": False,
                "cloud_allowed": True,
                "max_context_chars": 12000,
            },
        )
        assert queued.status_code == 200, queued.text
        task_id = int(queued.json()["id"])
        completed = client.post(f"/api/v1/control/agent-workflows/delegations/{task_id}/run")
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "completed"
        assert "SPECIALIST FACT" in completed.json()["result"]

        empty = client.get(
            f"/api/v1/control/agent-workflows/handoffs?conversation_id={conversation_id}"
        )
        assert empty.status_code == 200 and empty.json()["items"] == []

        handoff = client.post(f"/api/v1/control/agent-workflows/delegations/{task_id}/handoff")
        assert handoff.status_code == 200, handoff.text
        pending = handoff.json()
        handoff_id = int(pending["id"])
        assert pending["version"] == "v0.49"
        assert pending["status"] == "pending"
        assert pending["task_id"] == task_id
        assert pending["conversation_id"] == conversation_id

        # Queueing the same pending handoff is idempotent.
        duplicate = client.post(f"/api/v1/control/agent-workflows/delegations/{task_id}/handoff")
        assert duplicate.status_code == 200
        assert duplicate.json()["id"] == handoff_id
        assert duplicate.json()["status"] == "pending"

        # A successful parent turn receives the bounded handoff exactly once.
        consumed_turn = client.post(
            "/api/v1/control/chat",
            json={
                "message": "Use the specialist result now.",
                "conversation_id": conversation_id,
                "agent_id": primary_id,
                "max_context_chars": 12000,
            },
        )
        assert consumed_turn.status_code == 200, consumed_turn.text
        payload = consumed_turn.json()
        assert payload["reply"] == "PARENT CONSUMED THE EXPLICIT SPECIALIST HANDOFF"
        assert payload["handoffs"]["version"] == "v0.49"
        assert payload["handoffs"]["consumed"] == 1
        assert payload["handoffs"]["ids"] == [handoff_id]
        assert payload["context"]["handoff_count"] == 1
        assert payload["context"]["handoff_chars"] > 0
        assert int(payload["context"]["budget"]["used_chars"]) <= 12000
        assert int(payload["context"]["budget"]["max_context_chars"]) == 12000
        assert int(payload["context"]["budget"]["handoff_used_chars"]) == payload["context"]["handoff_chars"]
        assert any(item.get("kind") == "agent_handoff" for item in payload["context"]["provenance"])

        consumed = client.get(
            f"/api/v1/control/agent-workflows/handoffs?conversation_id={conversation_id}"
        ).json()["items"]
        row = next(item for item in consumed if int(item["id"]) == handoff_id)
        assert row["status"] == "consumed"
        assert int(row["consumed_run_id"]) == int(payload["run_id"])
        assert row["consumed_at"]

        # It is one-shot: a second parent turn must not receive it again.
        observed_systems.clear()
        second_turn = client.post(
            "/api/v1/control/chat",
            json={
                "message": "Continue without repeating the handoff.",
                "conversation_id": conversation_id,
                "agent_id": primary_id,
            },
        )
        assert second_turn.status_code == 200
        assert second_turn.json()["handoffs"]["consumed"] == 0
        assert second_turn.json()["context"]["handoff_count"] == 0
        assert all("Explicit specialist Agent result handoffs" not in system for system in observed_systems)
        assert client.post(f"/api/v1/control/agent-workflows/delegations/{task_id}/handoff").status_code == 409

        # A completed task without a parent conversation cannot be injected later.
        detached_task = client.post(
            "/api/v1/control/agent-workflows/delegations",
            json={
                "parent_agent_id": primary_id,
                "worker_agent_id": worker_id,
                "task": "Detached task",
            },
        )
        assert detached_task.status_code == 200
        detached_id = int(detached_task.json()["id"])
        assert client.post(f"/api/v1/control/agent-workflows/delegations/{detached_id}/run").status_code == 200
        assert client.post(f"/api/v1/control/agent-workflows/delegations/{detached_id}/handoff").status_code == 409

        # Failed parent inference leaves an explicit handoff pending for recovery.
        retry_task = client.post(
            "/api/v1/control/agent-workflows/delegations",
            json={
                "parent_agent_id": primary_id,
                "worker_agent_id": worker_id,
                "task": "Create a handoff that survives one failed parent turn.",
                "conversation_id": conversation_id,
            },
        )
        retry_id = int(retry_task.json()["id"])
        assert client.post(f"/api/v1/control/agent-workflows/delegations/{retry_id}/run").status_code == 200
        retry_handoff = client.post(f"/api/v1/control/agent-workflows/delegations/{retry_id}/handoff").json()
        retry_handoff_id = int(retry_handoff["id"])
        fail_parent["enabled"] = True
        failed = client.post(
            "/api/v1/control/chat",
            json={
                "message": "This parent inference should fail.",
                "conversation_id": conversation_id,
                "agent_id": primary_id,
            },
        )
        assert failed.status_code == 503, failed.text
        fail_parent["enabled"] = False
        retry_rows = client.get(
            f"/api/v1/control/agent-workflows/handoffs?conversation_id={conversation_id}"
        ).json()["items"]
        assert next(item for item in retry_rows if int(item["id"]) == retry_handoff_id)["status"] == "pending"
        recovered = client.post(
            "/api/v1/control/chat",
            json={
                "message": "Recover and use the pending result.",
                "conversation_id": conversation_id,
                "agent_id": primary_id,
            },
        )
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["handoffs"]["consumed"] == 1

        # Pending handoffs can be revoked and explicitly requeued before use.
        revoke_task = client.post(
            "/api/v1/control/agent-workflows/delegations",
            json={
                "parent_agent_id": primary_id,
                "worker_agent_id": worker_id,
                "task": "Create a revocable handoff.",
                "conversation_id": conversation_id,
            },
        )
        revoke_task_id = int(revoke_task.json()["id"])
        assert client.post(f"/api/v1/control/agent-workflows/delegations/{revoke_task_id}/run").status_code == 200
        revoke_handoff = client.post(
            f"/api/v1/control/agent-workflows/delegations/{revoke_task_id}/handoff"
        ).json()
        revoked = client.post(
            f"/api/v1/control/agent-workflows/handoffs/{revoke_handoff['id']}/revoke"
        )
        assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"
        requeued = client.post(f"/api/v1/control/agent-workflows/delegations/{revoke_task_id}/handoff")
        assert requeued.status_code == 200 and requeued.json()["status"] == "pending"

        # Paired wrappers remain source-isolated and require the existing agent.chat boundary.
        app_token = "v049-wrapper-token-with-sufficient-length"
        app_key = "v049-wrapper"
        with db() as connection:
            cursor = connection.execute(
                "INSERT INTO paired_apps(app_key, name, status, token_hash) VALUES (?, 'v0.49 Wrapper', 'active', ?)",
                (app_key, hashlib.sha256(app_token.encode("utf-8")).hexdigest()),
            )
            app_id = int(cursor.lastrowid)
            connection.execute(
                "INSERT INTO app_permissions(paired_app_id, permission, allowed) VALUES (?, 'agent.chat', 1)",
                (app_id,),
            )
        headers = {"Authorization": f"Bearer {app_token}"}
        assert client.get("/api/v1/agent-workflows/handoffs", headers=headers).status_code == 200
        assert client.post(
            f"/api/v1/agent-workflows/delegations/{task_id}/handoff",
            headers=headers,
        ).status_code == 404

        no_chat_token = "v049-no-chat-token-with-sufficient-length"
        with db() as connection:
            connection.execute(
                "INSERT INTO paired_apps(app_key, name, status, token_hash) VALUES ('v049-no-chat', 'No Chat', 'active', ?)",
                (hashlib.sha256(no_chat_token.encode("utf-8")).hexdigest(),),
            )
        assert client.get(
            "/api/v1/agent-workflows/handoffs",
            headers={"Authorization": f"Bearer {no_chat_token}"},
        ).status_code == 403

        caps = client.get("/api/v1/capabilities")
        assert caps.status_code == 200
        capability = caps.json()["agent_handoffs"]
        assert capability["version"] == "v0.49"
        assert capability["explicit"] is True
        assert capability["one_shot"] is True
        assert capability["context_budgeted"] is True
        assert "agent.handoffs.v049" in caps.json()["features"]

        with db() as connection:
            actions = {
                row["action"]
                for row in connection.execute(
                    "SELECT action FROM activity_log WHERE action LIKE 'agent.handoff.%'"
                ).fetchall()
            }
        assert "agent.handoff.queued" in actions
        assert "agent.handoff.consumed" in actions
        assert "agent.handoff.revoked" in actions

print("HomeServer v0.49 Agent Result Handoff regression passed")
