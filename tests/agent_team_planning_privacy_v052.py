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


with tempfile.TemporaryDirectory(prefix="homeserver-agent-team-planning-privacy-v052-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import agent_tools, providers  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    worker_ids: list[int] = []

    def fake_inference_status() -> dict:
        return {
            "available": True,
            "selected_provider": "openai",
            "model": "planner-cloud-model",
            "compute_source": "user_provider",
            "cloud_fallback_required": False,
            "preferred_provider": "openai",
            "providers": [
                {
                    "provider_key": "ollama",
                    "ready": True,
                    "enabled": True,
                    "model": "planner-local-model",
                    "compute_source": "homeserver_local",
                },
                {
                    "provider_key": "openai",
                    "ready": True,
                    "enabled": True,
                    "model": "planner-cloud-model",
                    "compute_source": "user_provider",
                },
            ],
        }

    def fake_generate(messages, model_override=None):
        return {
            "content": json.dumps(
                {
                    "members": [
                        {"worker_agent_id": worker_ids[0], "task": "Handle privacy race specialist A."},
                        {"worker_agent_id": worker_ids[1], "task": "Handle privacy race specialist B."},
                    ]
                }
            ),
            "provider": "openai",
            "model": model_override or "planner-cloud-model",
            "usage": {"prompt_tokens": 6, "completion_tokens": 4, "total_tokens": 10},
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

        primary = next(
            item for item in client.get("/api/v1/control/agents").json()["items"] if item["is_primary"]
        )
        primary_id = int(primary["id"])
        for name in ("Privacy Specialist A", "Privacy Specialist B"):
            created = client.post(
                "/api/v1/control/agents",
                json={"name": name, "instructions": f"Act as {name}.", "model": "privacy-worker-model"},
            )
            assert created.status_code == 200, created.text
            worker_ids.append(int(created.json()["agent"]["id"]))

        chat = client.post(
            "/api/v1/control/chat",
            json={"message": "Open v0.52 privacy-race conversation", "agent_id": primary_id},
        )
        assert chat.status_code == 200, chat.text
        conversation_id = chat.json()["conversation_id"]

        proposed = client.post(
            "/api/v1/control/agent-workflows/team-plans",
            json={
                "parent_agent_id": primary_id,
                "conversation_id": conversation_id,
                "objective": "Propose while cloud is allowed, then make the conversation private before approval.",
                "context": {"cloud_allowed": True, "include_memory": True, "include_knowledge": True},
            },
        )
        assert proposed.status_code == 200, proposed.text
        plan = proposed.json()
        assert plan["status"] == "proposed"
        assert plan["cloud_used"] is True
        plan_id = int(plan["id"])

        with db() as connection:
            connection.execute(
                "UPDATE conversation_context_settings SET cloud_allowed=0, updated_at=CURRENT_TIMESTAMP WHERE conversation_id=?",
                (conversation_id,),
            )
            trigger = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND name='trg_agent_team_plan_current_privacy'"
            ).fetchone()
            assert trigger is not None

        approved = client.post(f"/api/v1/control/agent-workflows/team-plans/{plan_id}/approve")
        assert approved.status_code == 200, approved.text
        payload = approved.json()
        assert payload["executed"] is False
        team = payload["team_run"]
        assert team["status"] == "queued"
        assert len(team["members"]) == 2
        assert all(member["cloud_allowed"] is False for member in team["members"])

        with db() as connection:
            task_ids = [int(value) for value in team["task_ids"]]
            rows = connection.execute(
                "SELECT id, cloud_allowed, status FROM agent_delegation_tasks WHERE id IN (?, ?) ORDER BY id",
                tuple(task_ids),
            ).fetchall()
            assert len(rows) == 2
            assert all(row["status"] == "queued" for row in rows)
            assert all(bool(row["cloud_allowed"]) is False for row in rows)

print("HomeServer v0.52 Team Planning approval privacy-race regression passed")