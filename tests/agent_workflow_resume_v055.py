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


with tempfile.TemporaryDirectory(prefix="homeserver-agent-workflow-resume-v055-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import agent_workflow_continuation  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    snapshots: dict[str, dict | Exception] = {}

    def fake_continuation(
        source_app_key: str,
        conversation_id: str,
        *,
        owner: bool,
        current_permissions=None,
    ) -> dict:
        value = snapshots.get(f"{source_app_key}|{conversation_id}")
        if isinstance(value, Exception):
            raise value
        if value is None:
            return {
                "version": "v0.54",
                "conversation_id": conversation_id,
                "parent": {"id": 1, "name": "Agent"},
                "active_count": 0,
                "workflow_count": 0,
                "current": None,
                "latest_terminal": None,
                "read_only": True,
                "auto_executes": False,
                "actions_via": "v0.53",
                "explicit_actions_preserved": True,
            }
        return value

    agent_workflow_continuation.conversation_continuation = fake_continuation

    def insert_conversation(connection, conversation_id: str, source: str, agent_id: int, title: str, updated_at: str, status: str = "active") -> None:
        connection.execute(
            """
            INSERT INTO conversations(id, agent_id, source_app_key, title, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (conversation_id, agent_id, source, title, status, updated_at, updated_at),
        )

    def insert_plan(connection, conversation_id: str, source: str, agent_id: int, title: str) -> int:
        cursor = connection.execute(
            """
            INSERT INTO agent_team_plans(
                source_app_key, conversation_id, parent_agent_id, parent_agent_name,
                objective, members_json, context_json, permission_snapshot_json,
                cloud_used, status
            ) VALUES (?, ?, ?, 'HomeServer Agent', ?, '[]', '{}', '[]', 0, 'proposed')
            """,
            (source, conversation_id, agent_id, title),
        )
        return int(cursor.lastrowid)

    def snapshot(conversation_id: str, plan_id: int, agent_id: int, title: str, next_key: str, next_label: str) -> dict:
        return {
            "version": "v0.54",
            "conversation_id": conversation_id,
            "parent": {"id": agent_id, "name": "HomeServer Agent"},
            "active_count": 1,
            "workflow_count": 1,
            "current": {
                "plan_id": plan_id,
                "team_run_id": None,
                "parent_agent_id": agent_id,
                "parent_agent_name": "HomeServer Agent",
                "objective": title,
                "plan_status": "proposed",
                "status": "proposed",
                "next_action": {"key": next_key, "label": next_label},
                "requires_explicit_action": True,
                "counts": {"members": 0, "queued": 0, "working": 0, "completed": 0, "failed": 0, "cancelled": 0},
                "retryable_task_ids": [],
                "provider_key": "must-not-leak",
                "model": "must-not-leak-model",
                "result": "must-not-leak-result",
            },
            "latest_terminal": None,
            "read_only": True,
            "auto_executes": False,
            "actions_via": "v0.53",
            "explicit_actions_preserved": True,
        }

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post(
            "/__owner/session",
            headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN},
        ).status_code == 200

        primary = next(item for item in client.get("/api/v1/control/agents").json()["items"] if item["is_primary"])
        primary_id = int(primary["id"])

        with db() as connection:
            insert_conversation(connection, "resume-recent-chat", "owner", primary_id, "Recent ordinary workflow", "2026-09-10 12:00:00")
            recent_plan = insert_plan(connection, "resume-recent-chat", "owner", primary_id, "Older Plan in newer chat")
            insert_conversation(connection, "resume-newest-plan", "owner", primary_id, "Older chat with newest workflow", "2026-09-01 12:00:00")
            newest_plan = insert_plan(connection, "resume-newest-plan", "owner", primary_id, "Newest Plan wins recovery")
            insert_conversation(connection, "resume-plain-chat", "owner", primary_id, "Plain chat without workflow", "2026-09-11 12:00:00")
            insert_conversation(connection, "resume-archived", "owner", primary_id, "Archived workflow", "2026-09-11 13:00:00", status="archived")
            archived_plan = insert_plan(connection, "resume-archived", "owner", primary_id, "Archived Plan")

        assert newest_plan > recent_plan
        assert archived_plan > newest_plan
        snapshots["owner|resume-recent-chat"] = snapshot(
            "resume-recent-chat", recent_plan, primary_id, "Older Plan in newer chat", "review_plan", "Review the older proposed workflow."
        )
        snapshots["owner|resume-newest-plan"] = snapshot(
            "resume-newest-plan", newest_plan, primary_id, "Newest Plan wins recovery", "review_plan", "Review the newest proposed workflow."
        )
        snapshots["owner|resume-archived"] = snapshot(
            "resume-archived", archived_plan, primary_id, "Archived Plan", "review_plan", "Must never be resumable."
        )

        endpoint = "/api/v1/control/agent-workflows/resume?limit=50"
        response = client.get(endpoint)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["version"] == "v0.55"
        assert payload["read_only"] is True
        assert payload["auto_executes"] is False
        assert payload["navigation_only"] is True
        assert payload["continuation_version"] == "v0.54"
        assert payload["explicit_actions_preserved"] is True
        assert payload["resumable_count"] == 2
        assert [item["conversation_id"] for item in payload["items"]] == ["resume-newest-plan", "resume-recent-chat"]
        assert payload["suggested"]["conversation_id"] == "resume-newest-plan"
        assert payload["suggested"]["plan_id"] == newest_plan
        assert "resume-plain-chat" not in {item["conversation_id"] for item in payload["items"]}
        assert "resume-archived" not in {item["conversation_id"] for item in payload["items"]}
        serialized = json.dumps(payload, sort_keys=True)
        assert "must-not-leak" not in serialized
        assert "provider_key" not in serialized
        assert '"model"' not in serialized
        assert '"result"' not in serialized

        with db() as connection:
            before_activity = int(connection.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0])
            before_plans = [tuple(row) for row in connection.execute(
                "SELECT id, status, team_run_id, updated_at FROM agent_team_plans ORDER BY id"
            ).fetchall()]
        for _ in range(3):
            assert client.get(endpoint).status_code == 200
        with db() as connection:
            after_activity = int(connection.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0])
            after_plans = [tuple(row) for row in connection.execute(
                "SELECT id, status, team_run_id, updated_at FROM agent_team_plans ORDER BY id"
            ).fetchall()]
        assert before_activity == after_activity
        assert before_plans == after_plans
        assert client.post(endpoint).status_code == 405
        assert client.put(endpoint).status_code == 405
        assert client.patch(endpoint).status_code == 405
        assert client.delete(endpoint).status_code == 405

        app_key = "v055-wrapper"
        app_token = "v055-wrapper-token-with-sufficient-length"
        blocked_conversation = "v055-wrapper-blocked"
        allowed_conversation = "v055-wrapper-allowed"
        with db() as connection:
            cursor = connection.execute(
                "INSERT INTO paired_apps(app_key, name, status, token_hash) VALUES (?, 'v0.55 Wrapper', 'active', ?)",
                (app_key, hashlib.sha256(app_token.encode("utf-8")).hexdigest()),
            )
            app_id = int(cursor.lastrowid)
            connection.execute(
                "INSERT INTO app_permissions(paired_app_id, permission, allowed) VALUES (?, 'agent.chat', 1)",
                (app_id,),
            )
            insert_conversation(connection, blocked_conversation, f"app:{app_key}", primary_id, "Revoked wrapper thread", "2026-09-11 14:00:00")
            blocked_plan = insert_plan(connection, blocked_conversation, f"app:{app_key}", primary_id, "Blocked wrapper Plan")
            insert_conversation(connection, allowed_conversation, f"app:{app_key}", primary_id, "Allowed wrapper thread", "2026-09-11 13:00:00")
            allowed_plan = insert_plan(connection, allowed_conversation, f"app:{app_key}", primary_id, "Allowed wrapper Plan")

        snapshots[f"app:{app_key}|{blocked_conversation}"] = agent_workflow_continuation.AgentWorkflowContinuationError(
            "Agent is not authorized for this application.", 403
        )
        snapshots[f"app:{app_key}|{allowed_conversation}"] = snapshot(
            allowed_conversation, allowed_plan, primary_id, "Allowed wrapper Plan", "review_plan", "Review this wrapper workflow."
        )
        headers = {"Authorization": f"Bearer {app_token}"}
        app_response = client.get("/api/v1/agent-workflows/resume", headers=headers)
        assert app_response.status_code == 200, app_response.text
        app_payload = app_response.json()
        assert app_payload["resumable_count"] == 1
        assert app_payload["items"][0]["conversation_id"] == allowed_conversation
        assert blocked_conversation not in json.dumps(app_payload)
        assert "resume-newest-plan" not in json.dumps(app_payload)

        no_chat_token = "v055-no-chat-token-with-sufficient-length"
        with db() as connection:
            connection.execute(
                "INSERT INTO paired_apps(app_key, name, status, token_hash) VALUES ('v055-no-chat', 'No Chat', 'active', ?)",
                (hashlib.sha256(no_chat_token.encode("utf-8")).hexdigest(),),
            )
        assert client.get(
            "/api/v1/agent-workflows/resume",
            headers={"Authorization": f"Bearer {no_chat_token}"},
        ).status_code == 403
        assert client.get("/api/v1/agent-workflows/resume").status_code == 401

        integrity_conversation = "resume-integrity-failure"
        with db() as connection:
            insert_conversation(connection, integrity_conversation, "owner", primary_id, "Integrity failure", "2026-09-12 12:00:00")
            insert_plan(connection, integrity_conversation, "owner", primary_id, "Integrity failure Plan")
        snapshots[f"owner|{integrity_conversation}"] = agent_workflow_continuation.AgentWorkflowContinuationError(
            "Linked Team Run integrity check failed.", 409
        )
        integrity = client.get(endpoint)
        assert integrity.status_code == 409
        assert "integrity" in integrity.text.lower()

        caps = client.get("/api/v1/capabilities")
        assert caps.status_code == 200
        capability = caps.json()["agent_workflow_resume"]
        assert capability["version"] == "v0.55"
        assert capability["read_only"] is True
        assert capability["cross_conversation_index"] is True
        assert capability["explicit_navigation"] is True
        assert capability["restart_resumable"] is True
        assert capability["auto_execute"] is False
        assert capability["explicit_actions_preserved"] is True
        assert capability["paired_app_scoped"] is True
        assert capability["requires_continuation"] == "v0.54"
        assert "agent.workflows.resume.v055" in caps.json()["features"]
        assert "agent.chat.workflow_recovery.v055" in caps.json()["features"]

print("HomeServer v0.55 Workflow Resume & Recovery regression passed")
