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


with tempfile.TemporaryDirectory(prefix="homeserver-agent-workflow-supervision-edges-v057-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    def create_app(connection, app_key: str, token: str, permissions: tuple[str, ...]) -> int:
        cursor = connection.execute(
            "INSERT INTO paired_apps(app_key, name, status, token_hash) VALUES (?, ?, 'active', ?)",
            (app_key, app_key, hashlib.sha256(token.encode("utf-8")).hexdigest()),
        )
        app_id = int(cursor.lastrowid)
        for permission in permissions:
            connection.execute(
                "INSERT INTO app_permissions(paired_app_id, permission, allowed) VALUES (?, ?, 1)",
                (app_id, permission),
            )
        return app_id

    def create_app_workflow(connection, app_key: str, app_id: int, parent_id: int) -> tuple[int, list[int]]:
        source = f"app:{app_key}"
        conversation_id = f"{app_key}-conversation"
        connection.execute(
            "INSERT INTO conversations(id, agent_id, source_app_key, title, status) VALUES (?, ?, ?, 'v0.57 app workflow', 'active')",
            (conversation_id, parent_id, source),
        )
        worker_ids: list[int] = []
        for index in (1, 2):
            cursor = connection.execute(
                "INSERT INTO agents(name, instructions, model, is_primary) VALUES (?, '', '', 0)",
                (f"{app_key} Worker {index}",),
            )
            worker_id = int(cursor.lastrowid)
            worker_ids.append(worker_id)
            connection.execute(
                "INSERT INTO app_agent_grants(paired_app_id, agent_id, allowed) VALUES (?, ?, 1)",
                (app_id, worker_id),
            )
        run_cursor = connection.execute(
            """
            INSERT INTO agent_team_runs(source_app_key, conversation_id, parent_agent_id, parent_agent_name, objective)
            VALUES (?, ?, ?, 'HomeServer Agent', 'Scoped v0.57 workflow')
            """,
            (source, conversation_id, parent_id),
        )
        team_run_id = int(run_cursor.lastrowid)
        task_ids: list[int] = []
        members: list[dict] = []
        permission_snapshot = ["agent.chat", "knowledge.search"]
        for position, worker_id in enumerate(worker_ids, start=1):
            task = f"Scoped specialist task {position}"
            cursor = connection.execute(
                """
                INSERT INTO agent_delegation_tasks(
                    source_app_key, parent_agent_id, worker_agent_id, parent_agent_name,
                    worker_agent_name, conversation_id, task, status,
                    include_memory, include_knowledge, include_contacts, cloud_allowed,
                    max_context_chars, permission_snapshot_json
                ) VALUES (?, ?, ?, 'HomeServer Agent', ?, ?, ?, 'queued', 0, 1, 0, 0, 12000, ?)
                """,
                (
                    source,
                    parent_id,
                    worker_id,
                    f"Worker {position}",
                    conversation_id,
                    task,
                    json.dumps(permission_snapshot),
                ),
            )
            task_id = int(cursor.lastrowid)
            task_ids.append(task_id)
            connection.execute(
                "INSERT INTO agent_team_run_members(team_run_id, position, task_id, worker_agent_id, worker_agent_name) VALUES (?, ?, ?, ?, ?)",
                (team_run_id, position, task_id, worker_id, f"Worker {position}"),
            )
            members.append({"worker_agent_id": worker_id, "worker_agent_name": f"Worker {position}", "task": task})
        plan_cursor = connection.execute(
            """
            INSERT INTO agent_team_plans(
                source_app_key, conversation_id, parent_agent_id, parent_agent_name,
                objective, members_json, context_json, permission_snapshot_json,
                cloud_used, status, team_run_id, decided_at
            ) VALUES (?, ?, ?, 'HomeServer Agent', 'Scoped v0.57 workflow', ?, ?, ?, 0, 'approved', ?, CURRENT_TIMESTAMP)
            """,
            (
                source,
                conversation_id,
                parent_id,
                json.dumps(members, separators=(",", ":")),
                json.dumps({"include_memory": False, "include_knowledge": True, "include_contacts": False, "cloud_allowed": False}, separators=(",", ":")),
                json.dumps(permission_snapshot),
                team_run_id,
            ),
        )
        return int(plan_cursor.lastrowid), task_ids

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200
        primary = next(item for item in client.get("/api/v1/control/agents").json()["items"] if item["is_primary"])
        primary_id = int(primary["id"])

        app_key = "v057-scoped-app"
        token = "v057-scoped-app-token-with-sufficient-length"
        with db() as connection:
            app_id = create_app(connection, app_key, token, ("agent.chat", "knowledge.search"))
            plan_id, task_ids = create_app_workflow(connection, app_key, app_id, primary_id)
        headers = {"Authorization": f"Bearer {token}"}
        conversation_id = f"{app_key}-conversation"

        recovery = client.post(
            "/api/v1/agent-workflows/rehydrate",
            headers=headers,
            json={"conversation_id": conversation_id, "plan_id": plan_id},
        )
        assert recovery.status_code == 200, recovery.text
        checkpoint = recovery.json()
        assert checkpoint["safe_to_continue"] is True
        body = {
            "conversation_id": conversation_id,
            "plan_id": plan_id,
            "rehydration_id": checkpoint["rehydration_id"],
            "state_fingerprint": checkpoint["state_fingerprint"],
            "max_steps": 2,
        }

        # Permission revocation after recovery invalidates the exact checkpoint.
        # v0.57 must not claim a session or execute even one queued task.
        with db() as connection:
            connection.execute(
                "UPDATE app_permissions SET allowed=0 WHERE paired_app_id=? AND permission='knowledge.search'",
                (app_id,),
            )
        revoked = client.post("/api/v1/agent-workflows/supervise", headers=headers, json=body)
        assert revoked.status_code == 409, revoked.text
        assert "fresh checkpoint" in revoked.text.lower() or "changed" in revoked.text.lower()
        with db() as connection:
            assert connection.execute("SELECT COUNT(*) FROM agent_workflow_supervisions WHERE rehydration_id=?", (checkpoint["rehydration_id"],)).fetchone()[0] == 0
            assert [row["status"] for row in connection.execute(
                "SELECT status FROM agent_delegation_tasks WHERE id IN (?, ?) ORDER BY id",
                tuple(task_ids),
            ).fetchall()] == ["queued", "queued"]

        # Restore permission, recover the new canonical checkpoint, then revoke a
        # specialist Agent grant. The worker-access change must also fail closed.
        with db() as connection:
            connection.execute(
                "UPDATE app_permissions SET allowed=1 WHERE paired_app_id=? AND permission='knowledge.search'",
                (app_id,),
            )
        current = client.post(
            "/api/v1/agent-workflows/rehydrate",
            headers=headers,
            json={"conversation_id": conversation_id, "plan_id": plan_id},
        )
        assert current.status_code == 200, current.text
        current_checkpoint = current.json()
        with db() as connection:
            worker_id = int(connection.execute(
                "SELECT worker_agent_id FROM agent_delegation_tasks WHERE id=?",
                (task_ids[0],),
            ).fetchone()[0])
            connection.execute(
                "UPDATE app_agent_grants SET allowed=0 WHERE paired_app_id=? AND agent_id=?",
                (app_id, worker_id),
            )
        worker_revoked = client.post(
            "/api/v1/agent-workflows/supervise",
            headers=headers,
            json={
                "conversation_id": conversation_id,
                "plan_id": plan_id,
                "rehydration_id": current_checkpoint["rehydration_id"],
                "state_fingerprint": current_checkpoint["state_fingerprint"],
                "max_steps": 2,
            },
        )
        assert worker_revoked.status_code in {403, 409}, worker_revoked.text
        with db() as connection:
            assert connection.execute("SELECT COUNT(*) FROM agent_workflow_supervisions WHERE rehydration_id=?", (current_checkpoint["rehydration_id"],)).fetchone()[0] == 0
            assert [row["status"] for row in connection.execute(
                "SELECT status FROM agent_delegation_tasks WHERE id IN (?, ?) ORDER BY id",
                tuple(task_ids),
            ).fetchall()] == ["queued", "queued"]

        # A valid-looking but wrong fingerprint cannot claim the checkpoint.
        with db() as connection:
            connection.execute(
                "UPDATE app_agent_grants SET allowed=1 WHERE paired_app_id=? AND agent_id=?",
                (app_id, worker_id),
            )
        refreshed = client.post(
            "/api/v1/agent-workflows/rehydrate",
            headers=headers,
            json={"conversation_id": conversation_id, "plan_id": plan_id},
        )
        assert refreshed.status_code == 200, refreshed.text
        refreshed_checkpoint = refreshed.json()
        wrong_fingerprint = "0" * 64 if refreshed_checkpoint["state_fingerprint"] != "0" * 64 else "1" * 64
        wrong = client.post(
            "/api/v1/agent-workflows/supervise",
            headers=headers,
            json={
                "conversation_id": conversation_id,
                "plan_id": plan_id,
                "rehydration_id": refreshed_checkpoint["rehydration_id"],
                "state_fingerprint": wrong_fingerprint,
                "max_steps": 2,
            },
        )
        assert wrong.status_code == 409, wrong.text
        with db() as connection:
            assert connection.execute("SELECT COUNT(*) FROM agent_workflow_supervisions WHERE rehydration_id=?", (refreshed_checkpoint["rehydration_id"],)).fetchone()[0] == 0

        # App source isolation prevents a paired app from supervising an owner checkpoint.
        owner_workers: list[int] = []
        with db() as connection:
            for index in (1, 2):
                cursor = connection.execute(
                    "INSERT INTO agents(name, instructions, model, is_primary) VALUES (?, '', '', 0)",
                    (f"Owner Edge Worker {index}",),
                )
                owner_workers.append(int(cursor.lastrowid))
            connection.execute(
                "INSERT INTO conversations(id, agent_id, source_app_key, title, status) VALUES ('v057-owner-edge', ?, 'owner', 'Owner edge', 'active')",
                (primary_id,),
            )
            members = [{"worker_agent_id": wid, "worker_agent_name": f"Owner {i}", "task": f"Owner task {i}"} for i, wid in enumerate(owner_workers, 1)]
            cursor = connection.execute(
                """
                INSERT INTO agent_team_plans(source_app_key, conversation_id, parent_agent_id, parent_agent_name, objective, members_json, context_json, permission_snapshot_json, cloud_used, status)
                VALUES ('owner', 'v057-owner-edge', ?, 'HomeServer Agent', 'Owner-only plan', ?, '{}', '[]', 0, 'proposed')
                """,
                (primary_id, json.dumps(members)),
            )
            owner_plan = int(cursor.lastrowid)
        owner_recovery = client.post(
            "/api/v1/control/agent-workflows/rehydrate",
            json={"conversation_id": "v057-owner-edge", "plan_id": owner_plan},
        )
        assert owner_recovery.status_code == 200, owner_recovery.text
        owner_checkpoint = owner_recovery.json()
        cross_source = client.post(
            "/api/v1/agent-workflows/supervise",
            headers=headers,
            json={
                "conversation_id": "v057-owner-edge",
                "plan_id": owner_plan,
                "rehydration_id": owner_checkpoint["rehydration_id"],
                "state_fingerprint": owner_checkpoint["state_fingerprint"],
                "max_steps": 2,
            },
        )
        assert cross_source.status_code in {404, 409}, cross_source.text

print("HomeServer v0.57 Supervised Workflow Continuation access/race boundaries passed")
