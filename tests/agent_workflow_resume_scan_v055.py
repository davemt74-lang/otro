from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient


with tempfile.TemporaryDirectory(prefix="homeserver-agent-workflow-resume-scan-v055-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import agent_workflow_continuation  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    resumable_conversation = "v055-deep-resumable"
    resumable_plan_id = 0

    def fake_continuation(
        source_app_key: str,
        conversation_id: str,
        *,
        owner: bool,
        current_permissions=None,
    ) -> dict:
        if source_app_key == "owner" and conversation_id == resumable_conversation:
            return {
                "version": "v0.54",
                "conversation_id": conversation_id,
                "parent": {"id": 1, "name": "HomeServer Agent"},
                "active_count": 1,
                "workflow_count": 1,
                "current": {
                    "plan_id": resumable_plan_id,
                    "team_run_id": None,
                    "parent_agent_id": 1,
                    "parent_agent_name": "HomeServer Agent",
                    "objective": "Deep resumable workflow survives terminal saturation.",
                    "plan_status": "proposed",
                    "status": "proposed",
                    "next_action": {"key": "review_plan", "label": "Review the deep resumable workflow."},
                    "requires_explicit_action": True,
                    "counts": {"members": 0, "queued": 0, "working": 0, "completed": 0, "failed": 0, "cancelled": 0},
                    "retryable_task_ids": ["901", 901, "bad", -2],
                },
                "latest_terminal": None,
                "read_only": True,
                "auto_executes": False,
                "actions_via": "v0.53",
                "explicit_actions_preserved": True,
            }
        return {
            "version": "v0.54",
            "conversation_id": conversation_id,
            "parent": {"id": 1, "name": "HomeServer Agent"},
            "active_count": 0,
            "workflow_count": 1,
            "current": None,
            "latest_terminal": {"status": "synthesized"},
            "read_only": True,
            "auto_executes": False,
            "actions_via": "v0.53",
            "explicit_actions_preserved": True,
        }

    agent_workflow_continuation.conversation_continuation = fake_continuation

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post(
            "/__owner/session",
            headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN},
        ).status_code == 200
        primary = next(item for item in client.get("/api/v1/control/agents").json()["items"] if item["is_primary"])
        primary_id = int(primary["id"])

        with db() as connection:
            connection.execute(
                """
                INSERT INTO conversations(id, agent_id, source_app_key, title, status, created_at, updated_at)
                VALUES (?, ?, 'owner', 'Deep resumable workflow', 'active', '2026-01-01 00:00:00', '2026-01-01 00:00:00')
                """,
                (resumable_conversation, primary_id),
            )
            cursor = connection.execute(
                """
                INSERT INTO agent_team_plans(
                    source_app_key, conversation_id, parent_agent_id, parent_agent_name,
                    objective, members_json, context_json, permission_snapshot_json,
                    cloud_used, status
                ) VALUES ('owner', ?, ?, 'HomeServer Agent', 'Deep resumable workflow', '[]', '{}', '[]', 0, 'proposed')
                """,
                (resumable_conversation, primary_id),
            )
            resumable_plan_id = int(cursor.lastrowid)

            # These conversations are all newer and still have proposed plan rows,
            # but v0.54 derives them as terminal. A candidate scan tied to limit=1
            # (or even the first 50 rows) would hide the real resumable workflow.
            for index in range(60):
                conversation_id = f"v055-terminal-{index:02d}"
                timestamp = f"2026-09-{(index % 9) + 1:02d} 12:{index % 60:02d}:00"
                connection.execute(
                    """
                    INSERT INTO conversations(id, agent_id, source_app_key, title, status, created_at, updated_at)
                    VALUES (?, ?, 'owner', ?, 'active', ?, ?)
                    """,
                    (conversation_id, primary_id, f"Terminal workflow {index}", timestamp, timestamp),
                )
                connection.execute(
                    """
                    INSERT INTO agent_team_plans(
                        source_app_key, conversation_id, parent_agent_id, parent_agent_name,
                        objective, members_json, context_json, permission_snapshot_json,
                        cloud_used, status
                    ) VALUES ('owner', ?, ?, 'HomeServer Agent', ?, '[]', '{}', '[]', 0, 'approved')
                    """,
                    (conversation_id, primary_id, f"Terminal workflow {index}"),
                )

        response = client.get("/api/v1/control/agent-workflows/resume?limit=1")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["version"] == "v0.55"
        assert payload["resumable_count"] == 1
        assert len(payload["items"]) == 1
        assert payload["suggested"]["conversation_id"] == resumable_conversation
        assert int(payload["suggested"]["plan_id"]) == resumable_plan_id
        assert payload["suggested"]["retryable_task_ids"] == [901]

print("HomeServer v0.55 Workflow Resume terminal-saturation scan regression passed")
