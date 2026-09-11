from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient


with tempfile.TemporaryDirectory(prefix="homeserver-agent-team-orchestration-integrity-v053-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

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
        secondary = client.post(
            "/api/v1/control/agents",
            json={
                "name": "Integrity Specialist",
                "instructions": "Used only to prove fail-closed v0.53 linkage checks.",
                "model": "integrity-local-model",
            },
        )
        assert secondary.status_code == 200, secondary.text
        secondary_id = int(secondary.json()["agent"]["id"])

        plan_conversation = "v053-integrity-plan"
        wrong_conversation = "v053-integrity-wrong-run"
        with db() as connection:
            connection.execute(
                """
                INSERT INTO conversations(id, agent_id, source_app_key, title, status)
                VALUES (?, ?, 'owner', 'v0.53 Integrity Plan', 'active')
                """,
                (plan_conversation, primary_id),
            )
            connection.execute(
                """
                INSERT INTO conversations(id, agent_id, source_app_key, title, status)
                VALUES (?, ?, 'owner', 'v0.53 Wrong Team Run', 'active')
                """,
                (wrong_conversation, primary_id),
            )
            team_cursor = connection.execute(
                """
                INSERT INTO agent_team_runs(
                    source_app_key, conversation_id, parent_agent_id, parent_agent_name, objective
                ) VALUES ('owner', ?, ?, ?, 'Fail-closed linkage regression')
                """,
                (wrong_conversation, primary_id, str(primary.get("name") or "Agent")),
            )
            team_run_id = int(team_cursor.lastrowid)
            plan_cursor = connection.execute(
                """
                INSERT INTO agent_team_plans(
                    source_app_key, conversation_id, parent_agent_id, parent_agent_name,
                    objective, members_json, context_json, permission_snapshot_json,
                    provider_key, model, cloud_used, status, team_run_id
                ) VALUES ('owner', ?, ?, ?, ?, ?, ?, '[]', 'ollama', 'integrity-local-model', 0, 'approved', ?)
                """,
                (
                    plan_conversation,
                    primary_id,
                    str(primary.get("name") or "Agent"),
                    "Fail-closed linkage regression",
                    json.dumps([], separators=(",", ":")),
                    json.dumps({"max_context_chars": 12000}, separators=(",", ":")),
                    team_run_id,
                ),
            )
            plan_id = int(plan_cursor.lastrowid)

        inconsistent = client.get(
            f"/api/v1/control/agent-workflows/team-plans/{plan_id}/orchestration"
        )
        assert inconsistent.status_code == 409, inconsistent.text
        assert "conversation linkage is inconsistent" in inconsistent.json()["detail"]
        blocked_run = client.post(
            f"/api/v1/control/agent-workflows/team-plans/{plan_id}/run"
        )
        assert blocked_run.status_code == 409, blocked_run.text

        with db() as connection:
            connection.execute(
                "UPDATE agent_team_runs SET conversation_id=?, parent_agent_id=? WHERE id=?",
                (plan_conversation, secondary_id, team_run_id),
            )
        parent_mismatch = client.get(
            f"/api/v1/control/agent-workflows/team-plans/{plan_id}/orchestration"
        )
        assert parent_mismatch.status_code == 409, parent_mismatch.text
        assert "parent Agent linkage is inconsistent" in parent_mismatch.json()["detail"]

        with db() as connection:
            connection.execute(
                "UPDATE agent_team_runs SET parent_agent_id=?, parent_agent_name=? WHERE id=?",
                (primary_id, str(primary.get("name") or "Agent"), team_run_id),
            )
        restored = client.get(
            f"/api/v1/control/agent-workflows/team-plans/{plan_id}/orchestration"
        )
        assert restored.status_code == 200, restored.text
        payload = restored.json()
        assert payload["version"] == "v0.53"
        assert payload["plan_id"] == plan_id
        assert payload["team_run_id"] == team_run_id
        assert payload["conversation_id"] == plan_conversation
        assert payload["parent_agent_id"] == primary_id
        assert payload["status"] == "queued"
        assert payload["auto_executes"] is False

print("HomeServer v0.53 Team Orchestration fail-closed linkage regression passed")
