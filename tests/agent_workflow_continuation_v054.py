from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient


with tempfile.TemporaryDirectory(prefix="homeserver-agent-workflow-continuation-v054-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import agent_team_orchestration  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    snapshots: dict[str, list[dict]] = {}

    def fake_orchestrations(
        source_app_key: str,
        *,
        owner: bool,
        current_permissions=None,
        conversation_id=None,
        limit=20,
    ) -> dict:
        assert conversation_id
        items = list(snapshots.get(f"{source_app_key}|{conversation_id}", []))[: int(limit)]
        return {"version": "v0.53", "items": items}

    agent_team_orchestration.list_orchestrations = fake_orchestrations

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post(
            "/__owner/session",
            headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN},
        ).status_code == 200

        primary = next(
            item for item in client.get("/api/v1/control/agents").json()["items"] if item["is_primary"]
        )
        primary_id = int(primary["id"])
        owner_conversation = "v054-owner-conversation"
        with db() as connection:
            connection.execute(
                """
                INSERT INTO conversations(id, agent_id, source_app_key, title, status)
                VALUES (?, ?, 'owner', 'v0.54 Continuation', 'active')
                """,
                (owner_conversation, primary_id),
            )

        endpoint = f"/api/v1/control/agent-workflows/continuation?conversation_id={owner_conversation}"
        empty = client.get(endpoint)
        assert empty.status_code == 200, empty.text
        empty_payload = empty.json()
        assert empty_payload["version"] == "v0.54"
        assert empty_payload["active_count"] == 0
        assert empty_payload["current"] is None
        assert empty_payload["read_only"] is True
        assert empty_payload["auto_executes"] is False
        assert empty_payload["actions_via"] == "v0.53"
        assert empty_payload["explicit_actions_preserved"] is True

        proposed = {
            "plan_id": 41,
            "team_run_id": None,
            "parent_agent_id": primary_id,
            "parent_agent_name": primary["name"],
            "objective": "Review the newest proposed team before anything executes.",
            "plan_status": "proposed",
            "status": "proposed",
            "next_action": {
                "key": "review_plan",
                "label": "Review the proposed specialists and explicitly approve or reject the plan.",
                "requires_explicit_action": True,
            },
            "requires_explicit_action": True,
            "team_run": None,
            "provider_key": "must-never-leak",
            "model": "must-never-leak-model",
        }
        snapshots[f"owner|{owner_conversation}"] = [proposed]
        response = client.get(endpoint)
        assert response.status_code == 200
        current = response.json()["current"]
        assert current["status"] == "proposed"
        assert current["next_action"]["key"] == "review_plan"
        assert response.json()["active_count"] == 1

        queued = {
            "plan_id": 42,
            "team_run_id": 77,
            "parent_agent_id": primary_id,
            "parent_agent_name": primary["name"],
            "objective": "Run the approved specialist team explicitly.",
            "plan_status": "approved",
            "status": "queued",
            "next_action": {
                "key": "run_specialists",
                "label": "Explicitly run the queued specialists when ready.",
                "requires_explicit_action": True,
            },
            "requires_explicit_action": True,
            "team_run": {
                "counts": {"members": 2, "queued": 2, "working": 0, "completed": 0, "failed": 0, "cancelled": 0},
                "retryable_task_ids": [],
                "members": [
                    {
                        "id": 901,
                        "result": "SECRET WORKER RESULT MUST NEVER LEAK",
                        "provider_key": "openai",
                        "model": "private-worker-model",
                    }
                ],
            },
        }
        snapshots[f"owner|{owner_conversation}"] = [queued, proposed]
        response = client.get(endpoint)
        payload = response.json()
        assert payload["active_count"] == 2
        assert payload["current"]["plan_id"] == 42, "newest nonterminal workflow must win deterministically"
        assert payload["current"]["status"] == "queued"
        assert payload["current"]["next_action"]["key"] == "run_specialists"
        assert payload["current"]["counts"]["queued"] == 2
        serialized = json.dumps(payload, sort_keys=True)
        assert "SECRET WORKER RESULT MUST NEVER LEAK" not in serialized
        assert "private-worker-model" not in serialized
        assert "must-never-leak" not in serialized
        assert "provider_key" not in serialized
        assert "model" not in serialized

        queued["status"] = "partial"
        queued["next_action"] = {
            "key": "retry_failed",
            "label": "Explicitly retry the failed specialist tasks before synthesis.",
            "requires_explicit_action": True,
            "task_ids": [901],
        }
        queued["team_run"]["counts"] = {
            "members": 2, "queued": 1, "working": 0, "completed": 0, "failed": 1, "cancelled": 0
        }
        queued["team_run"]["retryable_task_ids"] = [901]
        partial = client.get(endpoint).json()["current"]
        assert partial["status"] == "partial"
        assert partial["next_action"]["key"] == "retry_failed"
        assert partial["retryable_task_ids"] == [901]

        queued["status"] = "completed"
        queued["next_action"] = {
            "key": "prepare_synthesis",
            "label": "Explicitly prepare the exact specialist result set for the parent Agent.",
            "requires_explicit_action": True,
        }
        queued["team_run"]["counts"] = {
            "members": 2, "queued": 0, "working": 0, "completed": 2, "failed": 0, "cancelled": 0
        }
        queued["team_run"]["retryable_task_ids"] = []
        completed = client.get(endpoint).json()["current"]
        assert completed["status"] == "completed"
        assert completed["next_action"]["key"] == "prepare_synthesis"
        assert completed["counts"]["completed"] == 2

        queued["status"] = "prepared"
        queued["next_action"] = {
            "key": "parent_chat",
            "label": "Send the parent Agent a message to synthesize the prepared specialist results.",
            "requires_explicit_action": True,
        }
        prepared = client.get(endpoint).json()["current"]
        assert prepared["status"] == "prepared"
        assert prepared["next_action"]["key"] == "parent_chat"

        queued["status"] = "synthesized"
        queued["next_action"] = {
            "key": "complete",
            "label": "The parent Agent has synthesized this Team Run.",
            "requires_explicit_action": False,
        }
        proposed["status"] = "rejected"
        proposed["plan_status"] = "rejected"
        proposed["next_action"] = {
            "key": "none",
            "label": "This Team Plan was rejected and created no Team Run.",
            "requires_explicit_action": False,
        }
        proposed["requires_explicit_action"] = False
        terminal = client.get(endpoint).json()
        assert terminal["active_count"] == 0
        assert terminal["current"] is None
        assert terminal["latest_terminal"]["plan_id"] == 42
        assert terminal["latest_terminal"]["status"] == "synthesized"

        with db() as connection:
            before = dict(connection.execute(
                "SELECT id, title, status, updated_at FROM conversations WHERE id=?", (owner_conversation,)
            ).fetchone())
            before_activity = int(connection.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0])
        for _ in range(3):
            assert client.get(endpoint).status_code == 200
        with db() as connection:
            after = dict(connection.execute(
                "SELECT id, title, status, updated_at FROM conversations WHERE id=?", (owner_conversation,)
            ).fetchone())
            after_activity = int(connection.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0])
        assert before == after
        assert before_activity == after_activity, "continuation GET must not create audit/action state"

        assert client.post(endpoint).status_code == 405
        assert client.put(endpoint).status_code == 405
        assert client.delete(endpoint).status_code == 405

        app_key = "v054-wrapper"
        app_token = "v054-wrapper-token-with-sufficient-length"
        app_conversation = "v054-wrapper-conversation"
        with db() as connection:
            cursor = connection.execute(
                "INSERT INTO paired_apps(app_key, name, status, token_hash) VALUES (?, 'v0.54 Wrapper', 'active', ?)",
                (app_key, hashlib.sha256(app_token.encode("utf-8")).hexdigest()),
            )
            app_id = int(cursor.lastrowid)
            connection.execute(
                "INSERT INTO app_permissions(paired_app_id, permission, allowed) VALUES (?, 'agent.chat', 1)",
                (app_id,),
            )
            connection.execute(
                """
                INSERT INTO conversations(id, agent_id, source_app_key, title, status)
                VALUES (?, ?, ?, 'v0.54 Wrapper Conversation', 'active')
                """,
                (app_conversation, primary_id, f"app:{app_key}"),
            )
        snapshots[f"app:{app_key}|{app_conversation}"] = [
            {
                **queued,
                "plan_id": 88,
                "team_run_id": 99,
                "status": "prepared",
                "plan_status": "approved",
                "next_action": {
                    "key": "parent_chat",
                    "label": "Send the parent Agent a message.",
                    "requires_explicit_action": True,
                },
            }
        ]
        headers = {"Authorization": f"Bearer {app_token}"}
        own = client.get(
            f"/api/v1/agent-workflows/continuation?conversation_id={app_conversation}",
            headers=headers,
        )
        assert own.status_code == 200, own.text
        assert own.json()["current"]["plan_id"] == 88
        assert client.get(
            f"/api/v1/agent-workflows/continuation?conversation_id={owner_conversation}",
            headers=headers,
        ).status_code == 404

        no_chat_token = "v054-no-chat-token-with-sufficient-length"
        with db() as connection:
            connection.execute(
                "INSERT INTO paired_apps(app_key, name, status, token_hash) VALUES ('v054-no-chat', 'No Chat', 'active', ?)",
                (hashlib.sha256(no_chat_token.encode("utf-8")).hexdigest(),),
            )
        assert client.get(
            f"/api/v1/agent-workflows/continuation?conversation_id={app_conversation}",
            headers={"Authorization": f"Bearer {no_chat_token}"},
        ).status_code == 403

        caps = client.get("/api/v1/capabilities")
        assert caps.status_code == 200
        capability = caps.json()["agent_workflow_continuation"]
        assert capability["version"] == "v0.54"
        assert capability["read_only"] is True
        assert capability["conversation_bound"] is True
        assert capability["derived_from"] == "v0.53"
        assert capability["auto_execute"] is False
        assert capability["explicit_actions_preserved"] is True
        assert capability["paired_app_scoped"] is True
        assert "agent.workflows.continuation.v054" in caps.json()["features"]
        assert "agent.chat.workflow_awareness.v054" in caps.json()["features"]

print("HomeServer v0.54 Workflow Continuation Awareness regression passed")
