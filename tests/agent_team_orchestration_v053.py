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


with tempfile.TemporaryDirectory(prefix="homeserver-agent-team-orchestration-v053-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import agent_tools, providers  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    worker_ids: list[int] = []
    failed_second_once = False

    def fake_inference_status() -> dict:
        return {
            "available": True,
            "selected_provider": "openai",
            "model": "orchestration-parent-model",
            "compute_source": "user_provider",
            "cloud_fallback_required": False,
            "preferred_provider": "openai",
            "providers": [
                {
                    "provider_key": "openai",
                    "ready": True,
                    "enabled": True,
                    "model": "orchestration-parent-model",
                    "compute_source": "user_provider",
                }
            ],
        }

    def fake_generate(messages, model_override=None):
        global failed_second_once
        system = str(messages[0].get("content") or "") if messages else ""
        if "bounded specialist Team Plan" in system:
            return {
                "content": json.dumps(
                    {
                        "members": [
                            {"worker_agent_id": worker_ids[0], "task": "Investigate orchestration part A."},
                            {"worker_agent_id": worker_ids[1], "task": "Investigate orchestration part B."},
                        ]
                    }
                ),
                "provider": "openai",
                "model": model_override or "orchestration-parent-model",
                "usage": {"prompt_tokens": 10, "completion_tokens": 7, "total_tokens": 17},
            }
        if "bounded specialist worker" in system:
            if "Orchestration Specialist A" in system:
                content = "ORCHESTRATION RESULT A"
            elif "Orchestration Specialist B" in system:
                if not failed_second_once:
                    failed_second_once = True
                    raise providers.ProviderError("transient orchestration specialist B failure")
                content = "ORCHESTRATION RESULT B"
            else:
                content = "ORCHESTRATION WRAPPER RESULT"
            return {
                "content": content,
                "provider": "openai",
                "model": model_override or "orchestration-worker-model",
                "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
            }
        if "Explicit specialist Agent result handoffs" in system:
            assert "ORCHESTRATION RESULT A" in system
            assert "ORCHESTRATION RESULT B" in system
            return {
                "content": "V0.53 PARENT SYNTHESIS COMPLETE",
                "provider": "openai",
                "model": model_override or "orchestration-parent-model",
                "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
            }
        return {
            "content": "ORCHESTRATION PARENT BASELINE",
            "provider": "openai",
            "model": model_override or "orchestration-parent-model",
            "usage": {"prompt_tokens": 6, "completion_tokens": 3, "total_tokens": 9},
        }

    providers.inference_status = fake_inference_status
    providers.generate = fake_generate

    with TestClient(app) as client:
        scheduler.stop()
        agent_tools.save_policy(False, 3, False)

        assert client.get("/api/v1/control/agent-workflows/team-orchestrations").status_code == 401
        assert client.post(
            "/__owner/session",
            headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN},
        ).status_code == 200

        primary = next(
            item for item in client.get("/api/v1/control/agents").json()["items"] if item["is_primary"]
        )
        primary_id = int(primary["id"])
        for name in ("Orchestration Specialist A", "Orchestration Specialist B"):
            created = client.post(
                "/api/v1/control/agents",
                json={
                    "name": name,
                    "instructions": f"Act only as {name} and return one bounded result.",
                    "model": "orchestration-worker-model",
                },
            )
            assert created.status_code == 200, created.text
            worker_ids.append(int(created.json()["agent"]["id"]))

        bootstrap = client.post(
            "/api/v1/control/chat",
            json={"message": "Open the v0.53 orchestration thread", "agent_id": primary_id},
        )
        assert bootstrap.status_code == 200, bootstrap.text
        conversation_id = bootstrap.json()["conversation_id"]

        proposed = client.post(
            "/api/v1/control/agent-workflows/team-plans",
            json={
                "parent_agent_id": primary_id,
                "conversation_id": conversation_id,
                "objective": "Exercise the entire explicit v0.53 orchestration lifecycle.",
            },
        )
        assert proposed.status_code == 200, proposed.text
        plan_id = int(proposed.json()["id"])

        proposed_lifecycle = client.get(
            f"/api/v1/control/agent-workflows/team-plans/{plan_id}/orchestration"
        )
        assert proposed_lifecycle.status_code == 200, proposed_lifecycle.text
        lifecycle = proposed_lifecycle.json()
        assert lifecycle["version"] == "v0.53"
        assert lifecycle["plan_status"] == "proposed"
        assert lifecycle["status"] == "proposed"
        assert lifecycle["team_run_id"] is None
        assert lifecycle["allowed_actions"] == ["edit", "approve", "reject"]
        assert lifecycle["auto_executes"] is False
        assert lifecycle["synthesis_via_parent_chat"] is True
        assert lifecycle["one_hop_only"] is True
        assert client.post(
            f"/api/v1/control/agent-workflows/team-plans/{plan_id}/run"
        ).status_code == 409

        before_approval_runs = 0
        with db() as connection:
            before_approval_runs = int(connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0])

        approved = client.post(
            f"/api/v1/control/agent-workflows/team-plans/{plan_id}/approve"
        )
        assert approved.status_code == 200, approved.text
        approved_payload = approved.json()
        assert approved_payload["executed"] is False
        team_run_id = int(approved_payload["team_run"]["id"])
        task_ids = [int(value) for value in approved_payload["team_run"]["task_ids"]]
        with db() as connection:
            assert int(connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0]) == before_approval_runs
            statuses = [row["status"] for row in connection.execute(
                "SELECT status FROM agent_delegation_tasks WHERE id IN (?, ?) ORDER BY id",
                tuple(task_ids),
            ).fetchall()]
            assert statuses == ["queued", "queued"]

        queued = client.get(
            f"/api/v1/control/agent-workflows/team-plans/{plan_id}/orchestration"
        ).json()
        assert queued["plan_status"] == "approved"
        assert queued["team_run_id"] == team_run_id
        assert queued["status"] == "queued"
        assert queued["next_action"]["key"] == "run_specialists"
        assert queued["allowed_actions"] == ["run"]

        first_run = client.post(
            f"/api/v1/control/agent-workflows/team-plans/{plan_id}/run"
        )
        assert first_run.status_code == 200, first_run.text
        partial = first_run.json()
        assert partial["status"] == "partial"
        assert partial["team_run"]["counts"]["completed"] == 1
        assert partial["team_run"]["counts"]["failed"] == 1
        failed_task_id = int(partial["team_run"]["retryable_task_ids"][0])
        assert failed_task_id == task_ids[1]
        assert "retry" in partial["allowed_actions"]
        assert partial["next_action"]["key"] == "retry_failed"

        retry = client.post(
            f"/api/v1/control/agent-workflows/team-plans/{plan_id}/members/{failed_task_id}/retry"
        )
        assert retry.status_code == 200, retry.text
        completed = retry.json()
        assert completed["status"] == "completed"
        assert completed["team_run"]["counts"]["completed"] == 2
        assert completed["allowed_actions"] == ["prepare"]
        assert completed["next_action"]["key"] == "prepare_synthesis"
        assert client.post(
            f"/api/v1/control/agent-workflows/team-plans/{plan_id}/members/{task_ids[0]}/retry"
        ).status_code == 409

        prepared = client.post(
            f"/api/v1/control/agent-workflows/team-plans/{plan_id}/prepare"
        )
        assert prepared.status_code == 200, prepared.text
        prepared_payload = prepared.json()
        assert prepared_payload["status"] == "prepared"
        assert prepared_payload["allowed_actions"] == ["parent_chat"]
        assert prepared_payload["next_action"]["key"] == "parent_chat"
        assert prepared_payload["requires_explicit_action"] is True
        assert client.post(
            f"/api/v1/control/agent-workflows/team-plans/{plan_id}/run"
        ).status_code == 409
        assert client.post(
            f"/api/v1/control/agent-workflows/team-plans/{plan_id}/synthesize"
        ).status_code == 404, "v0.53 must not add a hidden direct synthesis endpoint."

        parent_turn = client.post(
            "/api/v1/control/chat",
            json={
                "message": "Synthesize the prepared v0.53 specialist results.",
                "conversation_id": conversation_id,
                "agent_id": primary_id,
            },
        )
        assert parent_turn.status_code == 200, parent_turn.text
        assert parent_turn.json()["reply"] == "V0.53 PARENT SYNTHESIS COMPLETE"
        assert parent_turn.json()["handoffs"]["consumed"] == 2

        synthesized = client.get(
            f"/api/v1/control/agent-workflows/team-plans/{plan_id}/orchestration"
        )
        assert synthesized.status_code == 200, synthesized.text
        final = synthesized.json()
        assert final["status"] == "synthesized"
        assert final["allowed_actions"] == []
        assert final["next_action"]["key"] == "complete"
        assert final["requires_explicit_action"] is False

        listed = client.get(
            f"/api/v1/control/agent-workflows/team-orchestrations?conversation_id={conversation_id}"
        )
        assert listed.status_code == 200, listed.text
        assert any(int(item["plan_id"]) == plan_id and item["status"] == "synthesized" for item in listed.json()["items"])

        # Source isolation is inherited from the canonical v0.52 Plan ownership boundary.
        app_key = "v053-wrapper"
        app_token = "v053-wrapper-token-with-sufficient-length"
        with db() as connection:
            cursor = connection.execute(
                "INSERT INTO paired_apps(app_key, name, status, token_hash) VALUES (?, 'v0.53 Wrapper', 'active', ?)",
                (app_key, hashlib.sha256(app_token.encode("utf-8")).hexdigest()),
            )
            app_id = int(cursor.lastrowid)
            connection.execute(
                "INSERT INTO app_permissions(paired_app_id, permission, allowed) VALUES (?, 'agent.chat', 1)",
                (app_id,),
            )
        for worker_id in worker_ids:
            assert client.put(
                f"/api/v1/control/connected-apps/{app_id}/agents/{worker_id}",
                json={"allowed": True},
            ).status_code == 200
        headers = {"Authorization": f"Bearer {app_token}"}
        assert client.get(
            f"/api/v1/agent-workflows/team-plans/{plan_id}/orchestration",
            headers=headers,
        ).status_code == 404

        caps = client.get("/api/v1/capabilities")
        assert caps.status_code == 200
        capability = caps.json()["agent_team_orchestration"]
        assert capability["version"] == "v0.53"
        assert capability["derived_state"] is True
        assert capability["linked_plan_run_lifecycle"] is True
        assert capability["explicit_run"] is True
        assert capability["explicit_retry"] is True
        assert capability["explicit_prepare"] is True
        assert capability["synthesis_via_parent_chat"] is True
        assert capability["auto_execute"] is False
        assert "agent.workflows.team_orchestration.v053" in caps.json()["features"]
        assert "agent.workflows.team_lifecycle.v053" in caps.json()["features"]

print("HomeServer v0.53 Team Orchestration lifecycle regression passed")