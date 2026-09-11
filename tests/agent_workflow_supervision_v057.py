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


with tempfile.TemporaryDirectory(prefix="homeserver-agent-workflow-supervision-v057-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import brain, providers  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    def fake_inference_status() -> dict:
        return {
            "available": True,
            "selected_provider": "openai",
            "model": "v057-worker-model",
            "compute_source": "user_provider",
            "cloud_fallback_required": False,
            "preferred_provider": "openai",
            "providers": [
                {
                    "provider_key": "openai",
                    "ready": True,
                    "enabled": True,
                    "model": "v057-worker-model",
                    "compute_source": "user_provider",
                }
            ],
        }

    def fake_agent_generate(messages, **kwargs):
        state = dict(kwargs.get("state") or {})
        state.setdefault("call_count", 0)
        state.setdefault("run_ids", [])
        state.setdefault("action_request_ids", [])
        state.setdefault("provider_usage", {})
        user_text = next((str(item.get("content") or "") for item in reversed(messages) if item.get("role") == "user"), "task")
        return {
            "content": f"SUPERVISED RESULT FOR {user_text}",
            "provider": "openai",
            "model": "v057-worker-model",
        }, state

    providers.inference_status = fake_inference_status
    brain._generate_with_agent_tools = fake_agent_generate

    def workers(connection, prefix: str) -> list[int]:
        ids: list[int] = []
        for suffix in ("Research", "Analysis"):
            cursor = connection.execute(
                "INSERT INTO agents(name, instructions, model, is_primary) VALUES (?, '', 'v057-worker-model', 0)",
                (f"{prefix} {suffix}",),
            )
            ids.append(int(cursor.lastrowid))
        return ids

    def approved_workflow(
        connection,
        conversation_id: str,
        parent_id: int,
        worker_ids: list[int],
        *,
        source: str = "owner",
        statuses: tuple[str, str] = ("queued", "queued"),
        permissions: list[str] | None = None,
    ) -> tuple[int, int, list[int]]:
        permission_snapshot = list(permissions or [])
        connection.execute(
            "INSERT INTO conversations(id, agent_id, source_app_key, title, status) VALUES (?, ?, ?, ?, 'active')",
            (conversation_id, parent_id, source, f"{conversation_id} v0.57 workflow"),
        )
        run_cursor = connection.execute(
            """
            INSERT INTO agent_team_runs(source_app_key, conversation_id, parent_agent_id, parent_agent_name, objective)
            VALUES (?, ?, ?, 'HomeServer Agent', 'Continue this approved workflow safely.')
            """,
            (source, conversation_id, parent_id),
        )
        team_run_id = int(run_cursor.lastrowid)
        task_ids: list[int] = []
        members: list[dict] = []
        for position, (worker_id, task_status) in enumerate(zip(worker_ids, statuses), start=1):
            task = f"v0.57 specialist task {position}"
            result = f"FAILED OLD RESULT {position}" if task_status == "failed" else ""
            error = "synthetic failed task" if task_status == "failed" else None
            cursor = connection.execute(
                """
                INSERT INTO agent_delegation_tasks(
                    source_app_key, parent_agent_id, worker_agent_id, parent_agent_name,
                    worker_agent_name, conversation_id, task, status, result, error,
                    include_memory, include_knowledge, include_contacts, cloud_allowed,
                    max_context_chars, permission_snapshot_json
                ) VALUES (?, ?, ?, 'HomeServer Agent', ?, ?, ?, ?, ?, ?, 0, 0, 0, 1, 12000, ?)
                """,
                (
                    source,
                    parent_id,
                    worker_id,
                    f"Worker {position}",
                    conversation_id,
                    task,
                    task_status,
                    result,
                    error,
                    json.dumps(permission_snapshot),
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
            ) VALUES (?, ?, ?, 'HomeServer Agent', 'Continue this approved workflow safely.', ?, ?, ?, 1, 'approved', ?, CURRENT_TIMESTAMP)
            """,
            (
                source,
                conversation_id,
                parent_id,
                json.dumps(members, separators=(",", ":")),
                json.dumps({"include_memory": False, "include_knowledge": False, "include_contacts": False, "cloud_allowed": True, "max_context_chars": 12000}, separators=(",", ":")),
                json.dumps(permission_snapshot),
                team_run_id,
            ),
        )
        return int(plan_cursor.lastrowid), team_run_id, task_ids

    def proposed_workflow(connection, conversation_id: str, parent_id: int, worker_ids: list[int]) -> int:
        connection.execute(
            "INSERT INTO conversations(id, agent_id, source_app_key, title, status) VALUES (?, ?, 'owner', ?, 'active')",
            (conversation_id, parent_id, f"{conversation_id} proposed"),
        )
        members = [
            {"worker_agent_id": worker_id, "worker_agent_name": f"Worker {index}", "task": f"Proposed task {index}"}
            for index, worker_id in enumerate(worker_ids, start=1)
        ]
        cursor = connection.execute(
            """
            INSERT INTO agent_team_plans(
                source_app_key, conversation_id, parent_agent_id, parent_agent_name,
                objective, members_json, context_json, permission_snapshot_json, cloud_used, status
            ) VALUES ('owner', ?, ?, 'HomeServer Agent', 'Review proposed plan', ?, ?, '[]', 1, 'proposed')
            """,
            (
                conversation_id,
                parent_id,
                json.dumps(members, separators=(",", ":")),
                json.dumps({"include_memory": False, "include_knowledge": False, "include_contacts": False, "cloud_allowed": True}, separators=(",", ":")),
            ),
        )
        return int(cursor.lastrowid)

    def rehydrate(client: TestClient, conversation_id: str, plan_id: int, *, headers: dict | None = None, control: bool = True) -> dict:
        path = "/api/v1/control/agent-workflows/rehydrate" if control else "/api/v1/agent-workflows/rehydrate"
        response = client.post(path, headers=headers or {}, json={"conversation_id": conversation_id, "plan_id": plan_id})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["version"] == "v0.56"
        return payload

    def supervise_body(checkpoint: dict, *, max_steps: int = 2) -> dict:
        return {
            "conversation_id": checkpoint["conversation_id"],
            "plan_id": checkpoint["plan_id"],
            "rehydration_id": checkpoint["rehydration_id"],
            "state_fingerprint": checkpoint["state_fingerprint"],
            "max_steps": max_steps,
        }

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200
        primary = next(item for item in client.get("/api/v1/control/agents").json()["items"] if item["is_primary"])
        primary_id = int(primary["id"])

        # One explicit v0.57 action runs already-approved queued specialists and,
        # only when all succeed, prepares synthesis. It never sends parent chat.
        with db() as connection:
            owner_workers = workers(connection, "v0.57 Owner")
            plan_id, team_run_id, task_ids = approved_workflow(
                connection, "v057-owner-two-step", primary_id, owner_workers
            )
        checkpoint = rehydrate(client, "v057-owner-two-step", plan_id)
        assert checkpoint["checkpoint"]["workflow_status"] == "queued"
        request_body = supervise_body(checkpoint)
        response = client.post("/api/v1/control/agent-workflows/supervise", json=request_body)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["version"] == "v0.57"
        assert payload["bounded"] is True
        assert payload["max_steps"] == 2
        assert payload["actions_executed"] == 2
        assert [step["action"] for step in payload["steps"]] == ["run", "prepare"]
        assert payload["steps"][0]["before_status"] == "queued"
        assert payload["steps"][0]["after_status"] == "completed"
        assert payload["steps"][1]["before_status"] == "completed"
        assert payload["steps"][1]["after_status"] == "prepared"
        assert payload["stop_boundary"] == "parent_synthesis_required"
        assert payload["checkpoint"]["workflow_status"] == "prepared"
        assert payload["requires_explicit_start"] is True
        assert payload["auto_approval"] is False
        assert payload["auto_retry"] is False
        assert payload["auto_parent_chat"] is False
        assert payload["nested_delegation"] is False
        assert payload["actions_via"] == "v0.53"
        assert "SUPERVISED RESULT" not in json.dumps(payload)
        with db() as connection:
            baseline_runs = int(connection.execute("SELECT COUNT(*) FROM agent_runs WHERE conversation_id=?", ("v057-owner-two-step",)).fetchone()[0])
            assert baseline_runs == 2
            assert connection.execute("SELECT COUNT(*) FROM agent_result_handoffs WHERE task_id IN (?, ?)", tuple(task_ids)).fetchone()[0] == 2
            assert connection.execute("SELECT COUNT(*) FROM agent_workflow_supervisions WHERE rehydration_id=?", (checkpoint["rehydration_id"],)).fetchone()[0] == 1
            assert connection.execute(
                "SELECT COUNT(*) FROM activity_log WHERE action='agent.workflow.supervision.step' AND resource_key=?",
                (str(plan_id),),
            ).fetchone()[0] == 2
            assert connection.execute(
                "SELECT COUNT(*) FROM activity_log WHERE action='agent.synthesis.prepared' AND resource_key=?",
                ("v057-owner-two-step",),
            ).fetchone()[0] == 1
            # v0.57 stops before parent chat, so no handoff is consumed.
            assert connection.execute(
                "SELECT COUNT(*) FROM agent_result_handoffs WHERE task_id IN (?, ?) AND status='consumed'",
                tuple(task_ids),
            ).fetchone()[0] == 0

        duplicate = client.post("/api/v1/control/agent-workflows/supervise", json=request_body)
        assert duplicate.status_code == 200, duplicate.text
        duplicate_payload = duplicate.json()
        assert duplicate_payload["supervision_id"] == payload["supervision_id"]
        assert duplicate_payload["reused"] is True
        assert [step["action"] for step in duplicate_payload["steps"]] == ["run", "prepare"]
        with db() as connection:
            assert int(connection.execute("SELECT COUNT(*) FROM agent_runs WHERE conversation_id=?", ("v057-owner-two-step",)).fetchone()[0]) == baseline_runs
            assert connection.execute("SELECT COUNT(*) FROM agent_result_handoffs WHERE task_id IN (?, ?)", tuple(task_ids)).fetchone()[0] == 2
            assert connection.execute(
                "SELECT COUNT(*) FROM activity_log WHERE action='agent.workflow.supervision.started' AND resource_key=?",
                (str(plan_id),),
            ).fetchone()[0] == 1

        # A one-step budget runs specialists but deliberately stops before prepare.
        with db() as connection:
            budget_workers = workers(connection, "v0.57 Budget")
            budget_plan, _, budget_tasks = approved_workflow(
                connection, "v057-budget", primary_id, budget_workers
            )
        budget_checkpoint = rehydrate(client, "v057-budget", budget_plan)
        budget_response = client.post(
            "/api/v1/control/agent-workflows/supervise",
            json=supervise_body(budget_checkpoint, max_steps=1),
        )
        assert budget_response.status_code == 200, budget_response.text
        budget_payload = budget_response.json()
        assert [step["action"] for step in budget_payload["steps"]] == ["run"]
        assert budget_payload["stop_boundary"] == "step_budget_exhausted"
        assert budget_payload["checkpoint"]["workflow_status"] == "completed"
        with db() as connection:
            assert connection.execute("SELECT COUNT(*) FROM agent_result_handoffs WHERE task_id IN (?, ?)", tuple(budget_tasks)).fetchone()[0] == 0
        fresh = rehydrate(client, "v057-budget", budget_plan)
        assert fresh["rehydration_id"] != budget_checkpoint["rehydration_id"]
        prepare_only = client.post(
            "/api/v1/control/agent-workflows/supervise",
            json=supervise_body(fresh, max_steps=1),
        )
        assert prepare_only.status_code == 200, prepare_only.text
        assert [step["action"] for step in prepare_only.json()["steps"]] == ["prepare"]
        assert prepare_only.json()["stop_boundary"] == "parent_synthesis_required"

        # A failed specialist is an explicit retry boundary. v0.57 does not run
        # another queued member while a retry decision is outstanding.
        with db() as connection:
            failed_workers = workers(connection, "v0.57 Failed")
            failed_plan, _, failed_tasks = approved_workflow(
                connection,
                "v057-retry-boundary",
                primary_id,
                failed_workers,
                statuses=("failed", "queued"),
            )
        failed_checkpoint = rehydrate(client, "v057-retry-boundary", failed_plan)
        failed_response = client.post(
            "/api/v1/control/agent-workflows/supervise",
            json=supervise_body(failed_checkpoint),
        )
        assert failed_response.status_code == 200, failed_response.text
        failed_payload = failed_response.json()
        assert failed_payload["actions_executed"] == 0
        assert failed_payload["stop_boundary"] == "retry_required"
        with db() as connection:
            statuses = [row["status"] for row in connection.execute(
                "SELECT status FROM agent_delegation_tasks WHERE id IN (?, ?) ORDER BY id",
                tuple(failed_tasks),
            ).fetchall()]
            assert statuses == ["failed", "queued"]

        # Proposed plans remain a hard explicit approval/rejection boundary.
        with db() as connection:
            proposal_workers = workers(connection, "v0.57 Proposed")
            proposal_plan = proposed_workflow(connection, "v057-proposed", primary_id, proposal_workers)
        proposal_checkpoint = rehydrate(client, "v057-proposed", proposal_plan)
        proposal_response = client.post(
            "/api/v1/control/agent-workflows/supervise",
            json=supervise_body(proposal_checkpoint),
        )
        assert proposal_response.status_code == 200, proposal_response.text
        proposal_payload = proposal_response.json()
        assert proposal_payload["actions_executed"] == 0
        assert proposal_payload["stop_boundary"] == "plan_approval_required"

        capability = client.get("/api/v1/control/agent-workflows/supervise/capability")
        assert capability.status_code == 200
        assert capability.json() == {
            "version": "v0.57",
            "requires_explicit_start": True,
            "bounded": True,
            "max_steps": 2,
            "safe_actions": ["run", "prepare"],
            "auto_approval": False,
            "auto_retry": False,
            "auto_parent_chat": False,
            "canonical_revalidation_each_step": True,
            "idempotent_checkpoint_claim": True,
            "paired_app_scoped": True,
            "requires_rehydration": "v0.56",
            "actions_via": "v0.53",
        }

        assert client.post("/api/v1/agent-workflows/supervise", json=request_body).status_code == 401
        no_chat_token = "v057-no-chat-token-with-sufficient-length"
        with db() as connection:
            connection.execute(
                "INSERT INTO paired_apps(app_key, name, status, token_hash) VALUES ('v057-no-chat', 'No Chat', 'active', ?)",
                (hashlib.sha256(no_chat_token.encode("utf-8")).hexdigest(),),
            )
        assert client.post(
            "/api/v1/agent-workflows/supervise",
            headers={"Authorization": f"Bearer {no_chat_token}"},
            json=request_body,
        ).status_code == 403

print("HomeServer v0.57 Supervised Workflow Continuation regression passed")
