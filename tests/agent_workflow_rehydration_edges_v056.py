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


with tempfile.TemporaryDirectory(prefix="homeserver-agent-workflow-rehydration-edges-v056-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import agent_workflow_rehydration  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    def workers(connection, prefix: str) -> list[int]:
        values: list[int] = []
        for suffix in ("Research", "Analysis"):
            cursor = connection.execute(
                "INSERT INTO agents(name, instructions, model, is_primary) VALUES (?, '', '', 0)",
                (f"{prefix} {suffix}",),
            )
            values.append(int(cursor.lastrowid))
        return values

    def proposed_plan(connection, source: str, conversation_id: str, parent_id: int, worker_ids: list[int], permissions: list[str]) -> int:
        connection.execute(
            "INSERT INTO conversations(id, agent_id, source_app_key, title, status) VALUES (?, ?, ?, ?, 'active')",
            (conversation_id, parent_id, source, f"{conversation_id} proposed plan"),
        )
        members = [
            {"worker_agent_id": worker_id, "worker_agent_name": f"Specialist {index}", "task": f"Planned specialist brief {index}"}
            for index, worker_id in enumerate(worker_ids, start=1)
        ]
        cursor = connection.execute(
            """
            INSERT INTO agent_team_plans(
                source_app_key, conversation_id, parent_agent_id, parent_agent_name,
                objective, members_json, context_json, permission_snapshot_json,
                cloud_used, status
            ) VALUES (?, ?, ?, 'HomeServer Agent', ?, ?, ?, ?, 0, 'proposed')
            """,
            (
                source,
                conversation_id,
                parent_id,
                "Restore the proposed specialist assignments before approval.",
                json.dumps(members, separators=(",", ":")),
                json.dumps({"include_memory": True, "include_knowledge": True, "include_contacts": False, "cloud_allowed": False, "max_context_chars": 12000}, separators=(",", ":")),
                json.dumps(permissions, separators=(",", ":")),
            ),
        )
        return int(cursor.lastrowid)

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200
        primary = next(item for item in client.get("/api/v1/control/agents").json()["items"] if item["is_primary"])
        primary_id = int(primary["id"])

        # Proposed plans rehydrate the assigned specialist briefs too, not just
        # an empty pre-run count.
        with db() as connection:
            owner_workers = workers(connection, "v0.56 Proposed")
            owner_plan = proposed_plan(connection, "owner", "v056-proposed-members", primary_id, owner_workers, [])
        proposed = client.post(
            "/api/v1/control/agent-workflows/rehydrate",
            json={"conversation_id": "v056-proposed-members", "plan_id": owner_plan},
        )
        assert proposed.status_code == 200, proposed.text
        checkpoint = proposed.json()["checkpoint"]
        assert checkpoint["plan_status"] == "proposed"
        assert checkpoint["workflow_status"] == "proposed"
        assert checkpoint["counts"]["members"] == 2
        assert [member["worker_agent_id"] for member in checkpoint["members"]] == owner_workers
        assert [member["status"] for member in checkpoint["members"]] == ["planned", "planned"]
        assert all(member["task_id"] is None for member in checkpoint["members"])

        # Paired-app permissions are re-read inside the BEGIN IMMEDIATE commit
        # boundary. Simulate a revocation after bearer authentication but before
        # checkpoint persistence; recovery must fail closed instead of marking
        # stale permissions ready.
        app_key = "v056-race-wrapper"
        app_token = "v056-race-wrapper-token-with-sufficient-length"
        with db() as connection:
            cursor = connection.execute(
                "INSERT INTO paired_apps(app_key, name, status, token_hash) VALUES (?, 'v0.56 Race Wrapper', 'active', ?)",
                (app_key, hashlib.sha256(app_token.encode("utf-8")).hexdigest()),
            )
            app_id = int(cursor.lastrowid)
            permissions = ["agent.chat", "memory.read", "knowledge.search"]
            for permission in permissions:
                connection.execute(
                    "INSERT INTO app_permissions(paired_app_id, permission, allowed) VALUES (?, ?, 1)",
                    (app_id, permission),
                )
            app_workers = workers(connection, "v0.56 Race")
            for worker_id in app_workers:
                connection.execute(
                    "INSERT INTO app_agent_grants(paired_app_id, agent_id, allowed) VALUES (?, ?, 1)",
                    (app_id, worker_id),
                )
            app_plan = proposed_plan(
                connection,
                f"app:{app_key}",
                "v056-permission-race",
                primary_id,
                app_workers,
                permissions,
            )

        original_live_permissions = agent_workflow_rehydration._live_app_permissions_tx
        try:
            agent_workflow_rehydration._live_app_permissions_tx = lambda source, connection: (
                app_id,
                {"agent.chat", "memory.read"},
            )
            race = client.post(
                "/api/v1/agent-workflows/rehydrate",
                headers={"Authorization": f"Bearer {app_token}"},
                json={"conversation_id": "v056-permission-race", "plan_id": app_plan},
            )
            assert race.status_code == 409, race.text
            assert "permissions changed during recovery" in race.text.lower()
            with db() as connection:
                assert connection.execute("SELECT COUNT(*) FROM agent_workflow_rehydrations WHERE plan_id=?", (app_plan,)).fetchone()[0] == 0
        finally:
            agent_workflow_rehydration._live_app_permissions_tx = original_live_permissions

        # Agent grants are also checked again inside the persistence boundary.
        # A specialist grant race cannot create a misleading ready checkpoint.
        original_agent_access = agent_workflow_rehydration._agent_access_tx
        try:
            revoked_worker = app_workers[0]

            def raced_access(agent_id: int, *, owner: bool, app_id: int | None, connection) -> bool:
                if int(agent_id) == int(revoked_worker):
                    return False
                return original_agent_access(agent_id, owner=owner, app_id=app_id, connection=connection)

            agent_workflow_rehydration._agent_access_tx = raced_access
            grant_race = client.post(
                "/api/v1/agent-workflows/rehydrate",
                headers={"Authorization": f"Bearer {app_token}"},
                json={"conversation_id": "v056-permission-race", "plan_id": app_plan},
            )
            assert grant_race.status_code == 409, grant_race.text
            assert "specialist agent access changed" in grant_race.text.lower()
            with db() as connection:
                assert connection.execute("SELECT COUNT(*) FROM agent_workflow_rehydrations WHERE plan_id=?", (app_plan,)).fetchone()[0] == 0
        finally:
            agent_workflow_rehydration._agent_access_tx = original_agent_access

print("HomeServer v0.56 Workflow Rehydration proposed-plan and race-boundary regression passed")
