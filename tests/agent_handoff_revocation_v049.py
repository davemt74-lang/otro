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


with tempfile.TemporaryDirectory(prefix="homeserver-agent-handoff-revocation-v049-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import agent_tools, providers  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    observed_systems: list[str] = []

    def fake_inference_status() -> dict:
        return {
            "available": True,
            "selected_provider": "openai",
            "model": "revocation-test-model",
            "compute_source": "user_provider",
            "cloud_fallback_required": False,
            "preferred_provider": "openai",
            "providers": [
                {"provider_key": "openai", "ready": True, "enabled": True, "model": "revocation-test-model"}
            ],
        }

    def fake_generate(messages, model_override=None):
        system = str(messages[0].get("content") or "") if messages else ""
        observed_systems.append(system)
        if "PRIVATE DERIVED RESULT" in system:
            return {
                "content": "PARENT USED AUTHORIZED HANDOFF",
                "provider": "openai",
                "model": model_override or "revocation-test-model",
                "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
            }
        return {
            "content": "PARENT DID NOT RECEIVE HANDOFF",
            "provider": "openai",
            "model": model_override or "revocation-test-model",
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        }

    providers.inference_status = fake_inference_status
    providers.generate = fake_generate

    with TestClient(app) as client:
        scheduler.stop()
        agent_tools.save_policy(False, 3, False)
        assert client.post(
            "/__owner/session",
            headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN},
        ).status_code == 200

        agents = client.get("/api/v1/control/agents").json()["items"]
        primary_id = int(next(item for item in agents if item["is_primary"])["id"])
        worker_response = client.post(
            "/api/v1/control/agents",
            json={"name": "Revocable Worker", "instructions": "Scoped worker.", "model": ""},
        )
        assert worker_response.status_code == 200
        worker_id = int(worker_response.json()["agent"]["id"])

        app_key = "v049-revoke-wrapper"
        token = "v049-revoke-wrapper-token-with-sufficient-length"
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with db() as connection:
            cursor = connection.execute(
                "INSERT INTO paired_apps(app_key, name, status, token_hash) VALUES (?, 'v0.49 Revocation Wrapper', 'active', ?)",
                (app_key, token_hash),
            )
            app_id = int(cursor.lastrowid)
            for permission in ("agent.chat", "memory.read"):
                connection.execute(
                    "INSERT INTO app_permissions(paired_app_id, permission, allowed) VALUES (?, ?, 1)",
                    (app_id, permission),
                )
            connection.execute(
                "INSERT INTO app_agent_grants(paired_app_id, agent_id, allowed) VALUES (?, ?, 1)",
                (app_id, worker_id),
            )

        headers = {"Authorization": f"Bearer {token}"}
        parent_chat = client.post(
            "/api/v1/chat",
            headers=headers,
            json={"message": "Create scoped parent conversation", "agent_id": primary_id},
        )
        assert parent_chat.status_code == 200, parent_chat.text
        conversation_id = parent_chat.json()["conversation_id"]

        source = f"app:{app_key}"
        with db() as connection:
            cursor = connection.execute(
                """
                INSERT INTO agent_delegation_tasks(
                    source_app_key, parent_agent_id, worker_agent_id,
                    parent_agent_name, worker_agent_name, conversation_id,
                    task, status, result, include_memory, include_knowledge,
                    include_contacts, cloud_allowed, max_context_chars,
                    permission_snapshot_json, completed_at
                ) VALUES (?, ?, ?, 'HomeServer Agent', 'Revocable Worker', ?,
                          'Use private memory to derive the bounded result.', 'completed', ?,
                          1, 0, 0, 1, 12000, '["agent.chat","memory.read"]', CURRENT_TIMESTAMP)
                """,
                (source, primary_id, worker_id, conversation_id, "PRIVATE DERIVED RESULT"),
            )
            task_id = int(cursor.lastrowid)

        # Queue-time permission revocation must block preserving a result that was
        # derived while a broader private-data permission existed.
        with db() as connection:
            connection.execute(
                "UPDATE app_permissions SET allowed=0, updated_at=CURRENT_TIMESTAMP WHERE paired_app_id=? AND permission='memory.read'",
                (app_id,),
            )
        denied_permission = client.post(
            f"/api/v1/agent-workflows/delegations/{task_id}/handoff",
            headers=headers,
        )
        assert denied_permission.status_code == 403, denied_permission.text
        assert "no longer authorize" in denied_permission.json()["detail"]

        with db() as connection:
            connection.execute(
                "UPDATE app_permissions SET allowed=1, updated_at=CURRENT_TIMESTAMP WHERE paired_app_id=? AND permission='memory.read'",
                (app_id,),
            )
        queued = client.post(
            f"/api/v1/agent-workflows/delegations/{task_id}/handoff",
            headers=headers,
        )
        assert queued.status_code == 200, queued.text
        handoff_id = int(queued.json()["id"])
        assert queued.json()["status"] == "pending"

        # Revoking the exact secondary Worker after the handoff is queued must
        # also block injection on the next parent turn. The row stays pending so
        # access restoration can intentionally resume it later.
        with db() as connection:
            connection.execute(
                "UPDATE app_agent_grants SET allowed=0, updated_at=CURRENT_TIMESTAMP WHERE paired_app_id=? AND agent_id=?",
                (app_id, worker_id),
            )
        observed_systems.clear()
        blocked_turn = client.post(
            "/api/v1/chat",
            headers=headers,
            json={
                "message": "Do not use revoked specialist access.",
                "conversation_id": conversation_id,
                "agent_id": primary_id,
            },
        )
        assert blocked_turn.status_code == 200, blocked_turn.text
        blocked_payload = blocked_turn.json()
        assert blocked_payload["reply"] == "PARENT DID NOT RECEIVE HANDOFF"
        assert blocked_payload["handoffs"]["consumed"] == 0
        assert blocked_payload["context"]["handoff_count"] == 0
        assert all("PRIVATE DERIVED RESULT" not in system for system in observed_systems)
        row = client.get(
            f"/api/v1/agent-workflows/handoffs?conversation_id={conversation_id}",
            headers=headers,
        ).json()["items"]
        assert next(item for item in row if int(item["id"]) == handoff_id)["status"] == "pending"

        # Restoring the exact Worker grant makes the still-pending handoff
        # eligible again, and the next successful parent turn consumes it once.
        with db() as connection:
            connection.execute(
                "UPDATE app_agent_grants SET allowed=1, updated_at=CURRENT_TIMESTAMP WHERE paired_app_id=? AND agent_id=?",
                (app_id, worker_id),
            )
        observed_systems.clear()
        restored_turn = client.post(
            "/api/v1/chat",
            headers=headers,
            json={
                "message": "Use the restored specialist result.",
                "conversation_id": conversation_id,
                "agent_id": primary_id,
            },
        )
        assert restored_turn.status_code == 200, restored_turn.text
        restored_payload = restored_turn.json()
        assert restored_payload["reply"] == "PARENT USED AUTHORIZED HANDOFF"
        assert restored_payload["handoffs"]["consumed"] == 1
        assert restored_payload["handoffs"]["ids"] == [handoff_id]
        assert any("PRIVATE DERIVED RESULT" in system for system in observed_systems)

        # Repeat the consumption-time check for a private-data permission change.
        with db() as connection:
            cursor = connection.execute(
                """
                INSERT INTO agent_delegation_tasks(
                    source_app_key, parent_agent_id, worker_agent_id,
                    parent_agent_name, worker_agent_name, conversation_id,
                    task, status, result, include_memory, include_knowledge,
                    include_contacts, cloud_allowed, max_context_chars,
                    permission_snapshot_json, completed_at
                ) VALUES (?, ?, ?, 'HomeServer Agent', 'Revocable Worker', ?,
                          'Second private result.', 'completed', ?,
                          1, 0, 0, 1, 12000, '["agent.chat","memory.read"]', CURRENT_TIMESTAMP)
                """,
                (source, primary_id, worker_id, conversation_id, "PRIVATE DERIVED RESULT TWO"),
            )
            second_task_id = int(cursor.lastrowid)
        second_handoff = client.post(
            f"/api/v1/agent-workflows/delegations/{second_task_id}/handoff",
            headers=headers,
        )
        assert second_handoff.status_code == 200
        second_handoff_id = int(second_handoff.json()["id"])
        with db() as connection:
            connection.execute(
                "UPDATE app_permissions SET allowed=0, updated_at=CURRENT_TIMESTAMP WHERE paired_app_id=? AND permission='memory.read'",
                (app_id,),
            )
        blocked_permission_turn = client.post(
            "/api/v1/chat",
            headers=headers,
            json={
                "message": "Do not use permission-revoked handoff.",
                "conversation_id": conversation_id,
                "agent_id": primary_id,
            },
        )
        assert blocked_permission_turn.status_code == 200
        assert blocked_permission_turn.json()["handoffs"]["consumed"] == 0
        second_rows = client.get(
            f"/api/v1/agent-workflows/handoffs?conversation_id={conversation_id}",
            headers=headers,
        ).json()["items"]
        assert next(item for item in second_rows if int(item["id"]) == second_handoff_id)["status"] == "pending"
        with db() as connection:
            connection.execute(
                "UPDATE app_permissions SET allowed=1, updated_at=CURRENT_TIMESTAMP WHERE paired_app_id=? AND permission='memory.read'",
                (app_id,),
            )
        allowed_again = client.post(
            "/api/v1/chat",
            headers=headers,
            json={
                "message": "Now use the restored permission handoff.",
                "conversation_id": conversation_id,
                "agent_id": primary_id,
            },
        )
        assert allowed_again.status_code == 200
        assert allowed_again.json()["handoffs"]["consumed"] == 1

print("HomeServer v0.49 Agent Result Handoff revocation regression passed")
