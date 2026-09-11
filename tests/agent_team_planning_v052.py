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


with tempfile.TemporaryDirectory(prefix="homeserver-agent-team-planning-v052-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import agent_tools, providers  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    worker_ids: list[int] = []
    planner_mode = "valid"
    planner_calls: list[dict] = []

    def fake_inference_status() -> dict:
        return {
            "available": True,
            "selected_provider": "openai",
            "model": "planner-parent-model",
            "compute_source": "user_provider",
            "cloud_fallback_required": False,
            "preferred_provider": "openai",
            "providers": [
                {"provider_key": "ollama", "ready": True, "enabled": True, "model": "planner-local-model", "compute_source": "homeserver_local"},
                {"provider_key": "openai", "ready": True, "enabled": True, "model": "planner-parent-model", "compute_source": "user_provider"},
            ],
        }

    def planner_response(messages, model_override=None, *, provider="openai"):
        system = str(messages[0].get("content") or "") if messages else ""
        if "bounded specialist Team Plan" in system:
            planner_calls.append({"system": system, "model": model_override, "provider": provider})
            if planner_mode == "malformed":
                content = "not-json"
            elif planner_mode == "unauthorized":
                content = json.dumps({
                    "members": [
                        {"worker_agent_id": worker_ids[0], "task": "Authorized first specialist."},
                        {"worker_agent_id": worker_ids[2], "task": "Unauthorized third specialist."},
                    ]
                })
            else:
                content = json.dumps({
                    "members": [
                        {"worker_agent_id": worker_ids[0], "task": "Investigate the first bounded part."},
                        {"worker_agent_id": worker_ids[1], "task": "Investigate the second bounded part."},
                    ]
                })
            return {
                "content": content,
                "provider": provider,
                "model": model_override or ("planner-local-model" if provider == "ollama" else "planner-parent-model"),
                "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
            }
        return {
            "content": "PARENT BASELINE RESPONSE",
            "provider": provider,
            "model": model_override or "planner-parent-model",
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }

    def fake_generate(messages, model_override=None):
        return planner_response(messages, model_override, provider="openai")

    def fake_generate_ollama(messages, model_override=None):
        return planner_response(messages, model_override, provider="ollama")

    providers.inference_status = fake_inference_status
    providers.generate = fake_generate
    providers.generate_ollama = fake_generate_ollama

    with TestClient(app) as client:
        scheduler.stop()
        agent_tools.save_policy(False, 3, False)

        assert client.get("/api/v1/control/agent-workflows/team-plans").status_code == 401
        assert client.post(
            "/__owner/session",
            headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN},
        ).status_code == 200

        primary = next(item for item in client.get("/api/v1/control/agents").json()["items"] if item["is_primary"])
        primary_id = int(primary["id"])
        for name in ("Planning Research Agent", "Planning Analysis Agent", "Planning Private Agent"):
            created = client.post(
                "/api/v1/control/agents",
                json={"name": name, "instructions": f"Act as {name}.", "model": "planner-worker-model"},
            )
            assert created.status_code == 200, created.text
            worker_ids.append(int(created.json()["agent"]["id"]))

        chat = client.post(
            "/api/v1/control/chat",
            json={"message": "Open v0.52 planning conversation", "agent_id": primary_id},
        )
        assert chat.status_code == 200, chat.text
        conversation_id = chat.json()["conversation_id"]

        def counts() -> tuple[int, int, int]:
            with db() as connection:
                return (
                    int(connection.execute("SELECT COUNT(*) FROM agent_team_plans").fetchone()[0]),
                    int(connection.execute("SELECT COUNT(*) FROM agent_team_runs").fetchone()[0]),
                    int(connection.execute("SELECT COUNT(*) FROM agent_delegation_tasks").fetchone()[0]),
                )

        baseline = counts()
        planner_mode = "malformed"
        malformed = client.post(
            "/api/v1/control/agent-workflows/team-plans",
            json={
                "parent_agent_id": primary_id,
                "conversation_id": conversation_id,
                "objective": "Malformed output must fail closed.",
            },
        )
        assert malformed.status_code == 502, malformed.text
        assert counts() == baseline, "Invalid planner output must create no plan, Team Run or delegation task."

        planner_mode = "valid"
        proposed = client.post(
            "/api/v1/control/agent-workflows/team-plans",
            json={
                "parent_agent_id": primary_id,
                "conversation_id": conversation_id,
                "objective": "Plan two bounded specialist investigations for explicit review.",
                "context": {
                    "include_memory": True,
                    "include_knowledge": True,
                    "include_contacts": False,
                    "cloud_allowed": True,
                    "max_context_chars": 16000,
                },
            },
        )
        assert proposed.status_code == 200, proposed.text
        plan = proposed.json()
        assert plan["version"] == "v0.52"
        assert plan["status"] == "proposed"
        assert plan["requires_approval"] is True
        assert plan["auto_executes"] is False
        assert plan["team_run_id"] is None
        assert [item["worker_agent_id"] for item in plan["members"]] == worker_ids[:2]
        plan_id = int(plan["id"])
        after_proposal = counts()
        assert after_proposal == (baseline[0] + 1, baseline[1], baseline[2]), "Proposal must not create execution state."

        edited = client.put(
            f"/api/v1/control/agent-workflows/team-plans/{plan_id}",
            json={
                "objective": "Edited objective still requires explicit approval.",
                "members": [
                    {"worker_agent_id": worker_ids[0], "task": "Edited research task."},
                    {"worker_agent_id": worker_ids[1], "task": "Edited analysis task."},
                ],
            },
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["members"][0]["task"] == "Edited research task."
        assert counts() == after_proposal

        approved = client.post(f"/api/v1/control/agent-workflows/team-plans/{plan_id}/approve")
        assert approved.status_code == 200, approved.text
        approved_payload = approved.json()
        assert approved_payload["version"] == "v0.52"
        assert approved_payload["executed"] is False
        assert approved_payload["already_approved"] is False
        assert approved_payload["plan"]["status"] == "approved"
        team = approved_payload["team_run"]
        assert team["version"] == "v0.51"
        assert team["status"] == "queued"
        assert team["counts"]["queued"] == 2
        assert team["counts"]["completed"] == 0
        team_id = int(team["id"])
        task_ids = [int(value) for value in team["task_ids"]]
        assert len(task_ids) == 2
        with db() as connection:
            rows = connection.execute(
                "SELECT id, status, metadata_json, include_memory, include_knowledge, include_contacts FROM agent_delegation_tasks WHERE id IN (?, ?) ORDER BY id",
                tuple(task_ids),
            ).fetchall()
            assert len(rows) == 2
            for row in rows:
                assert row["status"] == "queued"
                assert bool(row["include_memory"]) is True
                assert bool(row["include_knowledge"]) is True
                assert bool(row["include_contacts"]) is False
                metadata = json.loads(row["metadata_json"])
                assert metadata["team_plan_version"] == "v0.52"
                assert int(metadata["team_plan_id"]) == plan_id
                assert int(metadata["team_run_id"]) == team_id
            assert int(connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0]) >= 1

        approved_again = client.post(f"/api/v1/control/agent-workflows/team-plans/{plan_id}/approve")
        assert approved_again.status_code == 200, approved_again.text
        assert approved_again.json()["already_approved"] is True
        assert int(approved_again.json()["team_run"]["id"]) == team_id
        assert counts() == (baseline[0] + 1, baseline[1] + 1, baseline[2] + 2)
        assert client.put(
            f"/api/v1/control/agent-workflows/team-plans/{plan_id}",
            json={"objective": "No edits after approval", "members": [
                {"worker_agent_id": worker_ids[0], "task": "A"},
                {"worker_agent_id": worker_ids[1], "task": "B"},
            ]},
        ).status_code == 409

        rejected_plan = client.post(
            "/api/v1/control/agent-workflows/team-plans",
            json={
                "parent_agent_id": primary_id,
                "conversation_id": conversation_id,
                "objective": "This proposal will be rejected.",
            },
        ).json()
        rejected = client.post(
            f"/api/v1/control/agent-workflows/team-plans/{int(rejected_plan['id'])}/reject"
        )
        assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
        assert client.post(
            f"/api/v1/control/agent-workflows/team-plans/{int(rejected_plan['id'])}/approve"
        ).status_code == 409

        # Paired planning sees only authorized specialists and inherits local-only cloud scope.
        app_key = "v052-wrapper"
        app_token = "v052-wrapper-token-with-sufficient-length"
        with db() as connection:
            cursor = connection.execute(
                "INSERT INTO paired_apps(app_key, name, status, token_hash) VALUES (?, 'v0.52 Wrapper', 'active', ?)",
                (app_key, hashlib.sha256(app_token.encode("utf-8")).hexdigest()),
            )
            app_id = int(cursor.lastrowid)
            for permission in ("agent.chat", "knowledge.search"):
                connection.execute(
                    "INSERT INTO app_permissions(paired_app_id, permission, allowed) VALUES (?, ?, 1)",
                    (app_id, permission),
                )
            connection.execute(
                "INSERT INTO app_capability_scopes(paired_app_id, cloud_allowed) VALUES (?, 0)",
                (app_id,),
            )
        for worker_id in worker_ids[:2]:
            assert client.put(
                f"/api/v1/control/connected-apps/{app_id}/agents/{worker_id}",
                json={"allowed": True},
            ).status_code == 200
        headers = {"Authorization": f"Bearer {app_token}"}
        wrapper_chat = client.post(
            "/api/v1/chat",
            headers=headers,
            json={"message": "Open scoped planning conversation", "agent_id": primary_id},
        )
        assert wrapper_chat.status_code == 200, wrapper_chat.text
        wrapper_conversation = wrapper_chat.json()["conversation_id"]

        wrapper_before = counts()
        planner_mode = "unauthorized"
        unauthorized = client.post(
            "/api/v1/agent-workflows/team-plans",
            headers=headers,
            json={
                "parent_agent_id": primary_id,
                "conversation_id": wrapper_conversation,
                "objective": "The model must not escape the wrapper Agent allow-list.",
            },
        )
        assert unauthorized.status_code == 403, unauthorized.text
        assert counts() == wrapper_before
        assert str(worker_ids[2]) not in planner_calls[-1]["system"], "Unauthorized Agent must not appear in the planner allow-list."

        planner_mode = "valid"
        wrapper_plan_response = client.post(
            "/api/v1/agent-workflows/team-plans",
            headers=headers,
            json={
                "parent_agent_id": primary_id,
                "conversation_id": wrapper_conversation,
                "objective": "Create a local-only scoped wrapper plan.",
                "context": {"include_memory": True, "include_knowledge": True, "cloud_allowed": True},
            },
        )
        assert wrapper_plan_response.status_code == 200, wrapper_plan_response.text
        wrapper_plan = wrapper_plan_response.json()
        assert wrapper_plan["cloud_used"] is False
        assert planner_calls[-1]["provider"] == "ollama"
        wrapper_plan_id = int(wrapper_plan["id"])

        # Revocation after proposal is checked again inside the atomic approval transaction.
        assert client.put(
            f"/api/v1/control/connected-apps/{app_id}/agents/{worker_ids[1]}",
            json={"allowed": False},
        ).status_code == 200
        before_blocked_approval = counts()
        blocked = client.post(
            f"/api/v1/agent-workflows/team-plans/{wrapper_plan_id}/approve",
            headers=headers,
        )
        assert blocked.status_code == 403, blocked.text
        assert counts() == before_blocked_approval
        assert client.get(
            f"/api/v1/control/agent-workflows/team-plans/{wrapper_plan_id}"
        ).status_code == 404, "Owner source must not see wrapper-owned plans."

        assert client.put(
            f"/api/v1/control/connected-apps/{app_id}/agents/{worker_ids[1]}",
            json={"allowed": True},
        ).status_code == 200
        # Remove knowledge permission after proposal. Approval must narrow the queued v0.51 task snapshot.
        with db() as connection:
            connection.execute(
                "UPDATE app_permissions SET allowed=0 WHERE paired_app_id=? AND permission='knowledge.search'",
                (app_id,),
            )
        narrowed = client.post(
            f"/api/v1/agent-workflows/team-plans/{wrapper_plan_id}/approve",
            headers=headers,
        )
        assert narrowed.status_code == 200, narrowed.text
        narrowed_team = narrowed.json()["team_run"]
        assert narrowed_team["status"] == "queued"
        assert all(item["include_memory"] is False for item in narrowed_team["members"])
        assert all(item["include_knowledge"] is False for item in narrowed_team["members"])
        assert all(item["cloud_allowed"] is False for item in narrowed_team["members"])

        no_chat_token = "v052-no-chat-token-with-sufficient-length"
        with db() as connection:
            connection.execute(
                "INSERT INTO paired_apps(app_key, name, status, token_hash) VALUES ('v052-no-chat', 'No Chat', 'active', ?)",
                (hashlib.sha256(no_chat_token.encode("utf-8")).hexdigest(),),
            )
        assert client.get(
            "/api/v1/agent-workflows/team-plans",
            headers={"Authorization": f"Bearer {no_chat_token}"},
        ).status_code == 403

        caps = client.get("/api/v1/capabilities")
        assert caps.status_code == 200
        capability = caps.json()["agent_team_planning"]
        assert capability["version"] == "v0.52"
        assert capability["proposal_only"] is True
        assert capability["explicit_approval"] is True
        assert capability["atomic_approval"] is True
        assert capability["auto_execute"] is False
        assert capability["privacy_routed"] is True
        assert "agent.workflows.team_planning.v052" in caps.json()["features"]
        assert "agent.workflows.team_approval.v052" in caps.json()["features"]

        with db() as connection:
            actions = {row["action"] for row in connection.execute(
                "SELECT action FROM activity_log WHERE action LIKE 'agent.team_plan.%'"
            ).fetchall()}
        assert {"agent.team_plan.proposed", "agent.team_plan.edited", "agent.team_plan.approved", "agent.team_plan.rejected"}.issubset(actions)

print("HomeServer v0.52 Bounded Agent Team Planning regression passed")
