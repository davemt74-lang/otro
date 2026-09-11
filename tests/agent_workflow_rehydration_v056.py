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


with tempfile.TemporaryDirectory(prefix="homeserver-agent-workflow-rehydration-v056-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    def create_workers(connection, prefix: str) -> list[int]:
        ids: list[int] = []
        for suffix in ("Research", "Analysis"):
            cursor = connection.execute(
                "INSERT INTO agents(name, instructions, model, is_primary) VALUES (?, '', '', 0)",
                (f"{prefix} {suffix}",),
            )
            ids.append(int(cursor.lastrowid))
        return ids

    def create_approved_completed_workflow(connection, conversation_id: str, source: str, parent_id: int, workers: list[int]) -> tuple[int, int, list[int]]:
        connection.execute(
            "INSERT INTO conversations(id, agent_id, source_app_key, title, status) VALUES (?, ?, ?, ?, 'active')",
            (conversation_id, parent_id, source, f"{conversation_id} workflow"),
        )
        run_cursor = connection.execute(
            """
            INSERT INTO agent_team_runs(source_app_key, conversation_id, parent_agent_id, parent_agent_name, objective)
            VALUES (?, ?, ?, 'HomeServer Agent', 'Recover the canonical completed team state.')
            """,
            (source, conversation_id, parent_id),
        )
        team_run_id = int(run_cursor.lastrowid)
        task_ids: list[int] = []
        members: list[dict] = []
        for position, worker_id in enumerate(workers, start=1):
            task = f"Specialist checkpoint task {position}"
            cursor = connection.execute(
                """
                INSERT INTO agent_delegation_tasks(
                    source_app_key, parent_agent_id, worker_agent_id, parent_agent_name,
                    worker_agent_name, conversation_id, task, status, result,
                    include_memory, include_knowledge, include_contacts, cloud_allowed,
                    max_context_chars, permission_snapshot_json
                ) VALUES (?, ?, ?, 'HomeServer Agent', ?, ?, ?, 'completed', ?, 1, 1, 0, 0, 12000, ?)
                """,
                (
                    source,
                    parent_id,
                    worker_id,
                    f"Worker {position}",
                    conversation_id,
                    task,
                    f"PRIVATE RESULT BODY {position}",
                    json.dumps(["agent.chat", "memory.read", "knowledge.search"] if source != "owner" else []),
                ),
            )
            task_id = int(cursor.lastrowid)
            task_ids.append(task_id)
            connection.execute(
                """
                INSERT INTO agent_team_run_members(team_run_id, position, task_id, worker_agent_id, worker_agent_name)
                VALUES (?, ?, ?, ?, ?)
                """,
                (team_run_id, position, task_id, worker_id, f"Worker {position}"),
            )
            members.append({"worker_agent_id": worker_id, "worker_agent_name": f"Worker {position}", "task": task})
        plan_cursor = connection.execute(
            """
            INSERT INTO agent_team_plans(
                source_app_key, conversation_id, parent_agent_id, parent_agent_name,
                objective, members_json, context_json, permission_snapshot_json,
                cloud_used, status, team_run_id, decided_at
            ) VALUES (?, ?, ?, 'HomeServer Agent', ?, ?, ?, ?, 0, 'approved', ?, CURRENT_TIMESTAMP)
            """,
            (
                source,
                conversation_id,
                parent_id,
                "Recover the canonical completed team state.",
                json.dumps(members, separators=(",", ":")),
                json.dumps({"include_memory": True, "include_knowledge": True, "include_contacts": False, "cloud_allowed": False, "max_context_chars": 12000}, separators=(",", ":")),
                json.dumps(["agent.chat", "memory.read", "knowledge.search"] if source != "owner" else []),
                team_run_id,
            ),
        )
        return int(plan_cursor.lastrowid), team_run_id, task_ids

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200
        primary = next(item for item in client.get("/api/v1/control/agents").json()["items"] if item["is_primary"])
        primary_id = int(primary["id"])

        with db() as connection:
            workers = create_workers(connection, "v0.56 Owner")
            plan_id, team_run_id, task_ids = create_approved_completed_workflow(
                connection, "v056-owner-recovery", "owner", primary_id, workers
            )

        endpoint = "/api/v1/control/agent-workflows/rehydrate"
        body = {"conversation_id": "v056-owner-recovery", "plan_id": plan_id}
        before = client.get(f"/api/v1/control/agent-workflows/team-plans/{plan_id}/orchestration").json()
        assert before["status"] == "completed"
        first = client.post(endpoint, json=body)
        assert first.status_code == 200, first.text
        payload = first.json()
        assert payload["version"] == "v0.56"
        assert payload["reused"] is False
        assert payload["recovered"] is True
        assert payload["canonical_source"] is True
        assert payload["safe_to_continue"] is True
        assert payload["status"] == "ready"
        assert payload["auto_executes"] is False
        assert payload["actions_executed"] == 0
        assert payload["actions_via"] == "v0.53"
        assert payload["checkpoint"]["workflow_status"] == "completed"
        assert payload["checkpoint"]["counts"]["completed"] == 2
        assert [item["task_id"] for item in payload["checkpoint"]["members"]] == task_ids
        serialized = json.dumps(payload, sort_keys=True)
        assert "PRIVATE RESULT BODY" not in serialized
        assert "provider_key" not in serialized
        assert '"model"' not in serialized

        second = client.post(endpoint, json=body)
        assert second.status_code == 200, second.text
        second_payload = second.json()
        assert second_payload["rehydration_id"] == payload["rehydration_id"]
        assert second_payload["state_fingerprint"] == payload["state_fingerprint"]
        assert second_payload["reused"] is True
        with db() as connection:
            assert connection.execute("SELECT COUNT(*) FROM agent_workflow_rehydrations WHERE plan_id=?", (plan_id,)).fetchone()[0] == 1
            assert connection.execute(
                "SELECT COUNT(*) FROM activity_log WHERE action='agent.workflow.rehydrated' AND resource_key=?",
                (str(plan_id),),
            ).fetchone()[0] == 1
            assert connection.execute("SELECT COUNT(*) FROM agent_result_handoffs WHERE task_id IN (?, ?)", tuple(task_ids)).fetchone()[0] == 0

        # A canonical workflow change creates a new checkpoint rather than
        # mutating/reusing the stale one.
        with db() as connection:
            connection.execute(
                "UPDATE agent_delegation_tasks SET updated_at='2026-09-11 13:05:00' WHERE id=?",
                (task_ids[0],),
            )
        changed = client.post(endpoint, json=body)
        assert changed.status_code == 200, changed.text
        changed_payload = changed.json()
        assert changed_payload["rehydration_id"] != payload["rehydration_id"]
        assert changed_payload["state_fingerprint"] != payload["state_fingerprint"]
        assert changed_payload["drift"]["state_changed_since_last_rehydration"] is True

        # A newer active plan invalidates a stale recovery click for the same conversation.
        with db() as connection:
            connection.execute(
                """
                INSERT INTO agent_team_plans(
                    source_app_key, conversation_id, parent_agent_id, parent_agent_name,
                    objective, members_json, context_json, permission_snapshot_json, cloud_used, status
                ) VALUES ('owner', 'v056-owner-recovery', ?, 'HomeServer Agent', 'Newer plan', '[]', '{}', '[]', 0, 'proposed')
                """,
                (primary_id,),
            )
        stale = client.post(endpoint, json=body)
        assert stale.status_code == 409
        assert "changed" in stale.text.lower()

        # Paired-app permission drift is visible and blocks safe continuation,
        # while the checkpoint remains scoped to that app and exposes no result body.
        app_key = "v056-wrapper"
        app_token = "v056-wrapper-token-with-sufficient-length"
        with db() as connection:
            cursor = connection.execute(
                "INSERT INTO paired_apps(app_key, name, status, token_hash) VALUES (?, 'v0.56 Wrapper', 'active', ?)",
                (app_key, hashlib.sha256(app_token.encode("utf-8")).hexdigest()),
            )
            app_id = int(cursor.lastrowid)
            for permission in ("agent.chat", "memory.read"):
                connection.execute(
                    "INSERT INTO app_permissions(paired_app_id, permission, allowed) VALUES (?, ?, 1)",
                    (app_id, permission),
                )
            app_workers = create_workers(connection, "v0.56 App")
            for worker_id in app_workers:
                connection.execute(
                    "INSERT INTO app_agent_grants(paired_app_id, agent_id, allowed) VALUES (?, ?, 1)",
                    (app_id, worker_id),
                )
            app_plan, _, _ = create_approved_completed_workflow(
                connection, "v056-app-recovery", f"app:{app_key}", primary_id, app_workers
            )

        headers = {"Authorization": f"Bearer {app_token}"}
        app_response = client.post(
            "/api/v1/agent-workflows/rehydrate",
            headers=headers,
            json={"conversation_id": "v056-app-recovery", "plan_id": app_plan},
        )
        assert app_response.status_code == 200, app_response.text
        app_payload = app_response.json()
        assert app_payload["source_app_key"] == f"app:{app_key}"
        assert app_payload["safe_to_continue"] is False
        assert app_payload["status"] == "conflict"
        assert "knowledge.search" in app_payload["drift"]["required_missing_permissions"]
        assert "required_permissions_changed" in app_payload["drift"]["blocking_reasons"]
        assert "PRIVATE RESULT BODY" not in json.dumps(app_payload)
        assert client.post(
            "/api/v1/agent-workflows/rehydrate",
            headers=headers,
            json={"conversation_id": "v056-owner-recovery", "plan_id": plan_id},
        ).status_code in {404, 409}

        no_chat_token = "v056-no-chat-token-with-sufficient-length"
        with db() as connection:
            connection.execute(
                "INSERT INTO paired_apps(app_key, name, status, token_hash) VALUES ('v056-no-chat', 'No Chat', 'active', ?)",
                (hashlib.sha256(no_chat_token.encode("utf-8")).hexdigest(),),
            )
        assert client.post(
            "/api/v1/agent-workflows/rehydrate",
            headers={"Authorization": f"Bearer {no_chat_token}"},
            json={"conversation_id": "x", "plan_id": 1},
        ).status_code == 403
        assert client.post("/api/v1/agent-workflows/rehydrate", json={"conversation_id": "x", "plan_id": 1}).status_code == 401

        capability = client.get("/api/v1/control/agent-workflows/rehydrate/capability")
        assert capability.status_code == 200
        assert capability.json() == {
            "version": "v0.56",
            "persistent_checkpoint": True,
            "idempotent": True,
            "drift_detection": True,
            "canonical_plan_run_state": True,
            "auto_execute": False,
            "explicit_actions_preserved": True,
            "paired_app_scoped": True,
        }

print("HomeServer v0.56 Durable Workflow Rehydration & Safe Resume regression passed")
