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


with tempfile.TemporaryDirectory(prefix="homeserver-agent-team-runs-v051-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import agent_tools, providers  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    failed_b_once = False
    observed_parent_systems: list[str] = []

    def fake_inference_status() -> dict:
        return {
            "available": True,
            "selected_provider": "openai",
            "model": "team-parent-model",
            "compute_source": "user_provider",
            "cloud_fallback_required": False,
            "preferred_provider": "openai",
            "providers": [
                {"provider_key": "openai", "ready": True, "enabled": True, "model": "team-parent-model"}
            ],
        }

    def fake_generate(messages, model_override=None):
        nonlocal_failed = globals()
        system = str(messages[0].get("content") or "") if messages else ""
        if "bounded specialist worker" in system:
            if "Team Specialist A" in system:
                result = "TEAM RESULT A: bounded specialist A result."
            elif "Team Specialist B" in system:
                if not nonlocal_failed.get("failed_b_once", False):
                    nonlocal_failed["failed_b_once"] = True
                    raise providers.ProviderError("transient specialist B provider failure")
                result = "TEAM RESULT B: recovered specialist B result."
            elif "Team Specialist C" in system:
                result = "TEAM RESULT C: scoped wrapper specialist result."
            else:
                result = "TEAM RESULT D: scoped wrapper specialist result."
            return {
                "content": result,
                "provider": "openai",
                "model": model_override or "team-worker-model",
                "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
            }
        observed_parent_systems.append(system)
        if "Explicit specialist Agent result handoffs" in system:
            assert "TEAM RESULT A" in system
            assert "TEAM RESULT B" in system
            return {
                "content": "PARENT TEAM SYNTHESIS COMPLETE",
                "provider": "openai",
                "model": model_override or "team-parent-model",
                "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
            }
        return {
            "content": "PARENT TEAM BASELINE",
            "provider": "openai",
            "model": model_override or "team-parent-model",
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        }

    providers.inference_status = fake_inference_status
    providers.generate = fake_generate

    with TestClient(app) as client:
        scheduler.stop()
        agent_tools.save_policy(False, 3, False)

        assert client.get("/api/v1/control/agent-workflows/team-runs").status_code == 401
        assert client.post(
            "/__owner/session",
            headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN},
        ).status_code == 200

        agents = client.get("/api/v1/control/agents").json()["items"]
        primary = next(item for item in agents if item["is_primary"])
        primary_id = int(primary["id"])

        worker_ids: list[int] = []
        for name in ("Team Specialist A", "Team Specialist B", "Team Specialist C", "Team Specialist D"):
            response = client.post(
                "/api/v1/control/agents",
                json={
                    "name": name,
                    "instructions": f"Act only as {name} and return one bounded result.",
                    "model": "team-worker-model",
                },
            )
            assert response.status_code == 200, response.text
            worker_ids.append(int(response.json()["agent"]["id"]))

        bootstrap = client.post(
            "/api/v1/control/chat",
            json={"message": "Open the v0.51 Team Run thread", "agent_id": primary_id},
        )
        assert bootstrap.status_code == 200, bootstrap.text
        conversation_id = bootstrap.json()["conversation_id"]

        base_counts = {}
        with db() as connection:
            base_counts["teams"] = int(connection.execute("SELECT COUNT(*) FROM agent_team_runs").fetchone()[0])
            base_counts["tasks"] = int(connection.execute("SELECT COUNT(*) FROM agent_delegation_tasks").fetchone()[0])

        duplicate_worker = client.post(
            "/api/v1/control/agent-workflows/team-runs",
            json={
                "parent_agent_id": primary_id,
                "conversation_id": conversation_id,
                "objective": "This invalid run repeats the same specialist.",
                "members": [
                    {"worker_agent_id": worker_ids[0], "task": "Duplicate A one."},
                    {"worker_agent_id": worker_ids[0], "task": "Duplicate A two."},
                ],
            },
        )
        assert duplicate_worker.status_code == 409, duplicate_worker.text
        with db() as connection:
            assert int(connection.execute("SELECT COUNT(*) FROM agent_team_runs").fetchone()[0]) == base_counts["teams"]
            assert int(connection.execute("SELECT COUNT(*) FROM agent_delegation_tasks").fetchone()[0]) == base_counts["tasks"]

        created = client.post(
            "/api/v1/control/agent-workflows/team-runs",
            json={
                "parent_agent_id": primary_id,
                "conversation_id": conversation_id,
                "objective": "Have two specialists independently investigate bounded facts and return them for parent synthesis.",
                "members": [
                    {
                        "worker_agent_id": worker_ids[0],
                        "task": "Find bounded fact A for the parent.",
                        "include_memory": True,
                        "include_knowledge": True,
                        "max_context_chars": 12000,
                    },
                    {
                        "worker_agent_id": worker_ids[1],
                        "task": "Find bounded fact B for the parent.",
                        "include_memory": True,
                        "include_knowledge": True,
                        "max_context_chars": 12000,
                    },
                ],
            },
        )
        assert created.status_code == 200, created.text
        team = created.json()
        assert team["version"] == "v0.51"
        assert team["status"] == "queued"
        assert team["counts"]["members"] == 2
        assert team["one_hop_only"] is True
        assert team["synthesis_version"] == "v0.50"
        team_id = int(team["id"])
        original_task_ids = [int(value) for value in team["task_ids"]]
        assert len(set(original_task_ids)) == 2

        with db() as connection:
            members = connection.execute(
                "SELECT position, task_id FROM agent_team_run_members WHERE team_run_id=? ORDER BY position",
                (team_id,),
            ).fetchall()
            assert [int(row["task_id"]) for row in members] == original_task_ids
            task_rows = connection.execute(
                "SELECT id, metadata_json FROM agent_delegation_tasks WHERE id IN (?, ?) ORDER BY id",
                tuple(original_task_ids),
            ).fetchall()
            assert len(task_rows) == 2
            for row in task_rows:
                metadata = json.loads(row["metadata_json"])
                assert metadata["team_run_version"] == "v0.51"
                assert int(metadata["team_run_id"]) == team_id
                assert metadata["nested_delegation"] is False

        first_run = client.post(f"/api/v1/control/agent-workflows/team-runs/{team_id}/run")
        assert first_run.status_code == 200, first_run.text
        partial = first_run.json()
        assert partial["status"] == "partial"
        assert partial["counts"]["completed"] == 1
        assert partial["counts"]["failed"] == 1
        assert len(partial["outcomes"]) == 2
        failed_member = next(item for item in partial["members"] if item["status"] == "failed")
        failed_task_id = int(failed_member["id"])
        assert failed_task_id == original_task_ids[1]
        assert failed_member["error"] == "transient specialist B provider failure"
        assert partial["retryable_task_ids"] == [failed_task_id]

        retried = client.post(
            f"/api/v1/control/agent-workflows/team-runs/{team_id}/members/{failed_task_id}/retry"
        )
        assert retried.status_code == 200, retried.text
        completed = retried.json()
        assert completed["retry_outcome"]["ok"] is True
        assert completed["status"] == "completed"
        assert completed["counts"]["completed"] == 2
        assert completed["task_ids"] == original_task_ids, "Retry must preserve the original delegation task ids."
        by_worker = {item["worker_agent_name"]: item for item in completed["members"]}
        assert "TEAM RESULT A" in by_worker["Team Specialist A"]["result"]
        assert "TEAM RESULT B" in by_worker["Team Specialist B"]["result"]

        cannot_retry_success = client.post(
            f"/api/v1/control/agent-workflows/team-runs/{team_id}/members/{original_task_ids[0]}/retry"
        )
        assert cannot_retry_success.status_code == 409

        # An unrelated pending handoff must block exact Team Run synthesis preparation.
        unrelated = client.post(
            "/api/v1/control/agent-workflows/delegations",
            json={
                "parent_agent_id": primary_id,
                "worker_agent_id": worker_ids[2],
                "task": "Produce one unrelated completed result.",
                "conversation_id": conversation_id,
            },
        )
        unrelated_task_id = int(unrelated.json()["id"])
        assert client.post(
            f"/api/v1/control/agent-workflows/delegations/{unrelated_task_id}/run"
        ).status_code == 200
        unrelated_handoff = client.post(
            f"/api/v1/control/agent-workflows/delegations/{unrelated_task_id}/handoff"
        )
        assert unrelated_handoff.status_code == 200
        blocked_prepare = client.post(
            f"/api/v1/control/agent-workflows/team-runs/{team_id}/prepare"
        )
        assert blocked_prepare.status_code == 409, blocked_prepare.text
        current_handoffs = client.get(
            f"/api/v1/control/agent-workflows/handoffs?conversation_id={conversation_id}"
        ).json()["items"]
        assert not any(int(item["task_id"]) in original_task_ids for item in current_handoffs)

        assert client.post(
            f"/api/v1/control/agent-workflows/handoffs/{int(unrelated_handoff.json()['id'])}/revoke"
        ).status_code == 200
        prepared = client.post(f"/api/v1/control/agent-workflows/team-runs/{team_id}/prepare")
        assert prepared.status_code == 200, prepared.text
        prepared_team = prepared.json()
        assert prepared_team["status"] == "prepared"
        assert prepared_team["synthesis"]["version"] == "v0.50"
        assert prepared_team["synthesis"]["task_ids"] == original_task_ids
        assert prepared_team["synthesis"]["count"] == 2
        assert all(item["handoff"]["status"] == "pending" for item in prepared_team["members"])

        parent_turn = client.post(
            "/api/v1/control/chat",
            json={
                "message": "Synthesize the prepared Team Run specialist results.",
                "conversation_id": conversation_id,
                "agent_id": primary_id,
                "max_context_chars": 12000,
            },
        )
        assert parent_turn.status_code == 200, parent_turn.text
        parent_payload = parent_turn.json()
        assert parent_payload["reply"] == "PARENT TEAM SYNTHESIS COMPLETE"
        assert parent_payload["handoffs"]["consumed"] == 2

        synthesized = client.get(f"/api/v1/control/agent-workflows/team-runs/{team_id}")
        assert synthesized.status_code == 200
        synthesized_team = synthesized.json()
        assert synthesized_team["status"] == "synthesized"
        consumed_runs = {
            int(item["handoff"]["consumed_run_id"])
            for item in synthesized_team["members"]
        }
        assert consumed_runs == {int(parent_payload["run_id"])}
        assert client.post(f"/api/v1/control/agent-workflows/team-runs/{team_id}/prepare").status_code == 409
        assert client.post(f"/api/v1/control/agent-workflows/team-runs/{team_id}/cancel").status_code == 409

        # Paired wrappers remain behind agent.chat, exact Agent grants and source isolation.
        app_key = "v051-wrapper"
        app_token = "v051-wrapper-token-with-sufficient-length"
        with db() as connection:
            cursor = connection.execute(
                "INSERT INTO paired_apps(app_key, name, status, token_hash) VALUES (?, 'v0.51 Wrapper', 'active', ?)",
                (app_key, hashlib.sha256(app_token.encode("utf-8")).hexdigest()),
            )
            app_id = int(cursor.lastrowid)
            for permission in ("agent.chat", "knowledge.search"):
                connection.execute(
                    "INSERT INTO app_permissions(paired_app_id, permission, allowed) VALUES (?, ?, 1)",
                    (app_id, permission),
                )
        for worker_id in worker_ids[2:4]:
            granted = client.put(
                f"/api/v1/control/connected-apps/{app_id}/agents/{worker_id}",
                json={"allowed": True},
            )
            assert granted.status_code == 200, granted.text

        headers = {"Authorization": f"Bearer {app_token}"}
        wrapper_chat = client.post(
            "/api/v1/chat",
            headers=headers,
            json={"message": "Open a wrapper-owned Team Run conversation", "agent_id": primary_id},
        )
        assert wrapper_chat.status_code == 200, wrapper_chat.text
        wrapper_conversation = wrapper_chat.json()["conversation_id"]
        wrapper_created = client.post(
            "/api/v1/agent-workflows/team-runs",
            headers=headers,
            json={
                "parent_agent_id": primary_id,
                "conversation_id": wrapper_conversation,
                "objective": "Run two wrapper-scoped specialists.",
                "members": [
                    {
                        "worker_agent_id": worker_ids[2],
                        "task": "Wrapper specialist C.",
                        "include_memory": True,
                        "include_knowledge": True,
                    },
                    {
                        "worker_agent_id": worker_ids[3],
                        "task": "Wrapper specialist D.",
                        "include_memory": True,
                        "include_knowledge": True,
                    },
                ],
            },
        )
        assert wrapper_created.status_code == 200, wrapper_created.text
        wrapper_team = wrapper_created.json()
        wrapper_team_id = int(wrapper_team["id"])
        assert all(item["include_memory"] is False for item in wrapper_team["members"])
        assert all(item["include_knowledge"] is True for item in wrapper_team["members"])
        assert client.get(
            f"/api/v1/agent-workflows/team-runs/{team_id}", headers=headers
        ).status_code == 404

        # Live Agent revocation prevents that queued member from executing without destroying sibling progress.
        revoked = client.put(
            f"/api/v1/control/connected-apps/{app_id}/agents/{worker_ids[3]}",
            json={"allowed": False},
        )
        assert revoked.status_code == 200
        wrapper_partial = client.post(
            f"/api/v1/agent-workflows/team-runs/{wrapper_team_id}/run",
            headers=headers,
        )
        assert wrapper_partial.status_code == 200, wrapper_partial.text
        wrapper_partial_payload = wrapper_partial.json()
        assert wrapper_partial_payload["counts"]["completed"] == 1
        assert wrapper_partial_payload["counts"]["queued"] == 1
        assert any(not item["ok"] and item["status_code"] == 403 for item in wrapper_partial_payload["outcomes"])

        restored = client.put(
            f"/api/v1/control/connected-apps/{app_id}/agents/{worker_ids[3]}",
            json={"allowed": True},
        )
        assert restored.status_code == 200
        wrapper_complete = client.post(
            f"/api/v1/agent-workflows/team-runs/{wrapper_team_id}/run",
            headers=headers,
        )
        assert wrapper_complete.status_code == 200
        assert wrapper_complete.json()["status"] == "completed"

        # Revoking private knowledge access redacts persisted specialist results on subsequent Team Run reads.
        with db() as connection:
            connection.execute(
                "UPDATE app_permissions SET allowed=0 WHERE paired_app_id=? AND permission='knowledge.search'",
                (app_id,),
            )
        redacted = client.get(
            f"/api/v1/agent-workflows/team-runs/{wrapper_team_id}", headers=headers
        )
        assert redacted.status_code == 200, redacted.text
        assert all(item["result"] == "" for item in redacted.json()["members"])
        assert all(item["result_redacted"] is True for item in redacted.json()["members"])

        no_chat_token = "v051-no-chat-token-with-sufficient-length"
        with db() as connection:
            connection.execute(
                "INSERT INTO paired_apps(app_key, name, status, token_hash) VALUES ('v051-no-chat', 'No Chat', 'active', ?)",
                (hashlib.sha256(no_chat_token.encode("utf-8")).hexdigest(),),
            )
        no_chat_headers = {"Authorization": f"Bearer {no_chat_token}"}
        assert client.get("/api/v1/agent-workflows/team-runs", headers=no_chat_headers).status_code == 403

        caps = client.get("/api/v1/capabilities")
        assert caps.status_code == 200
        capability = caps.json()["agent_team_runs"]
        assert capability["version"] == "v0.51"
        assert capability["min_members"] == 2
        assert capability["max_members"] == 4
        assert capability["distinct_specialists"] is True
        assert capability["partial_failure_recovery"] is True
        assert capability["nested_delegation"] is False
        assert "agent.workflows.team_runs.v051" in caps.json()["features"]
        assert "agent.workflows.team_retry.v051" in caps.json()["features"]

        with db() as connection:
            actions = [
                row["action"]
                for row in connection.execute(
                    "SELECT action FROM activity_log WHERE resource_type IN ('agent_team_run','agent_delegation')"
                ).fetchall()
            ]
        assert "agent.team_run.created" in actions
        assert "agent.team_run.executed" in actions
        assert "agent.team_run.member_retried" in actions
        assert "agent.team_run.prepared" in actions
        assert "agent.delegation.requeued" in actions

print("HomeServer v0.51 bounded Agent Team Runs regression passed")
