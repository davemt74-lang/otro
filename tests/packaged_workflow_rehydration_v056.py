from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import db  # noqa: E402
from app.security import OWNER_CONTROL_TOKEN  # noqa: E402

BASE = "http://127.0.0.1:4377"


def wait_health(expected_up: bool, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    with httpx.Client(base_url=BASE, timeout=0.8, trust_env=False) as client:
        while time.time() < deadline:
            up = False
            try:
                response = client.get("/api/v1/health")
                up = response.status_code == 200 and response.json().get("version") == "0.18.0"
            except Exception:
                up = False
            if up is expected_up:
                return True
            time.sleep(0.2)
    return False


def authorize() -> httpx.Client:
    client = httpx.Client(base_url=BASE, timeout=4.0, trust_env=False)
    response = client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN})
    assert response.status_code == 200, response.text
    assert client.get("/api/v1/control/system").status_code == 200
    return client


def verify_rehydration(client: httpx.Client, conversation_id: str, plan_id: int, expected_id: int | None = None) -> dict:
    resume = client.get("/api/v1/control/agent-workflows/resume?limit=50")
    assert resume.status_code == 200, resume.text
    resume_payload = resume.json()
    item = next(row for row in resume_payload["items"] if row["conversation_id"] == conversation_id)
    assert int(item["plan_id"]) == plan_id

    response = client.post(
        "/api/v1/control/agent-workflows/rehydrate",
        json={"conversation_id": conversation_id, "plan_id": plan_id},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["version"] == "v0.56"
    assert payload["recovered"] is True
    assert payload["canonical_source"] is True
    assert payload["safe_to_continue"] is True
    assert payload["auto_executes"] is False
    assert payload["actions_executed"] == 0
    assert payload["checkpoint"]["workflow_status"] == "completed"
    assert payload["checkpoint"]["counts"]["completed"] == 2
    assert "PACKAGED PRIVATE RESULT" not in json.dumps(payload)
    if expected_id is not None:
        assert payload["rehydration_id"] == expected_id
        assert payload["reused"] is True
    return payload


def main() -> None:
    assert os.environ.get("HOMESERVER_DATA_DIR"), "HOMESERVER_DATA_DIR is required"
    assert wait_health(True, 20), "packaged HomeServer is not healthy before v0.56 recovery test"

    first = authorize()
    primary_response = first.get("/api/v1/control/agent")
    assert primary_response.status_code == 200, primary_response.text
    primary_id = int(primary_response.json()["agent"]["id"])

    conversation_id = "packaged-workflow-rehydration-v056"
    with db() as connection:
        workers: list[int] = []
        for name in ("Packaged Rehydration Research", "Packaged Rehydration Analysis"):
            cursor = connection.execute(
                "INSERT INTO agents(name, instructions, model, is_primary) VALUES (?, '', '', 0)",
                (name,),
            )
            workers.append(int(cursor.lastrowid))
        connection.execute(
            """
            INSERT INTO conversations(id, agent_id, source_app_key, title, status, created_at, updated_at)
            VALUES (?, ?, 'owner', 'Packaged v0.56 recoverable workflow', 'active', '2026-09-11 10:00:00', '2026-09-11 10:00:00')
            """,
            (conversation_id, primary_id),
        )
        run_cursor = connection.execute(
            """
            INSERT INTO agent_team_runs(source_app_key, conversation_id, parent_agent_id, parent_agent_name, objective)
            VALUES ('owner', ?, ?, 'HomeServer Agent', 'Verify durable packaged workflow recovery.')
            """,
            (conversation_id, primary_id),
        )
        team_run_id = int(run_cursor.lastrowid)
        task_ids: list[int] = []
        members: list[dict] = []
        for position, worker_id in enumerate(workers, start=1):
            task = f"Packaged v0.56 completed specialist task {position}"
            cursor = connection.execute(
                """
                INSERT INTO agent_delegation_tasks(
                    source_app_key, parent_agent_id, worker_agent_id, parent_agent_name,
                    worker_agent_name, conversation_id, task, status, result,
                    include_memory, include_knowledge, include_contacts, cloud_allowed,
                    max_context_chars, permission_snapshot_json, completed_at
                ) VALUES ('owner', ?, ?, 'HomeServer Agent', ?, ?, ?, 'completed', ?, 1, 1, 0, 0, 12000, '[]', CURRENT_TIMESTAMP)
                """,
                (
                    primary_id,
                    worker_id,
                    f"Worker {position}",
                    conversation_id,
                    task,
                    f"PACKAGED PRIVATE RESULT {position}",
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
                provider_key, model, cloud_used, status, team_run_id, decided_at
            ) VALUES ('owner', ?, ?, 'HomeServer Agent', ?, ?, ?, '[]', 'ollama', 'packaged-v056-model', 0, 'approved', ?, CURRENT_TIMESTAMP)
            """,
            (
                conversation_id,
                primary_id,
                "Verify durable packaged workflow recovery.",
                json.dumps(members, separators=(",", ":")),
                json.dumps({"include_memory": True, "include_knowledge": True, "include_contacts": False, "cloud_allowed": False, "max_context_chars": 12000}, separators=(",", ":")),
                team_run_id,
            ),
        )
        plan_id = int(plan_cursor.lastrowid)

    original = verify_rehydration(first, conversation_id, plan_id)
    original_id = int(original["rehydration_id"])
    original_fingerprint = str(original["state_fingerprint"])
    with db() as connection:
        assert connection.execute("SELECT COUNT(*) FROM agent_result_handoffs WHERE task_id IN (?, ?)", tuple(task_ids)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM agent_workflow_rehydrations WHERE plan_id=?", (plan_id,)).fetchone()[0] == 1

    old_cookie = first.cookies.get("homeserver_owner")
    assert old_cookie
    restart = first.post("/api/v1/control/system/restart")
    assert restart.status_code == 200 and restart.json()["accepted"] is True
    first.close()

    wait_health(False, 4)
    assert wait_health(True, 20), "HomeServer did not return after v0.56 supervised restart"

    old_session = httpx.Client(base_url=BASE, cookies={"homeserver_owner": old_cookie}, timeout=3.0, trust_env=False)
    try:
        assert old_session.get("/api/v1/control/system").status_code == 401
    finally:
        old_session.close()

    second = authorize()
    restored = verify_rehydration(second, conversation_id, plan_id, expected_id=original_id)
    assert restored["state_fingerprint"] == original_fingerprint
    with db() as connection:
        assert connection.execute("SELECT COUNT(*) FROM agent_workflow_rehydrations WHERE plan_id=?", (plan_id,)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM agent_result_handoffs WHERE task_id IN (?, ?)", tuple(task_ids)).fetchone()[0] == 0

    # Continue exactly one explicit workflow action after recovery. Preparing
    # synthesis is local/idempotent and proves rehydration itself did not run it.
    prepare = second.post(f"/api/v1/control/agent-workflows/team-plans/{plan_id}/prepare")
    assert prepare.status_code == 200, prepare.text
    assert prepare.json()["status"] == "prepared"
    with db() as connection:
        assert connection.execute("SELECT COUNT(*) FROM agent_result_handoffs WHERE task_id IN (?, ?)", tuple(task_ids)).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action='agent.synthesis.prepared' AND resource_key=?",
            (conversation_id,),
        ).fetchone()[0] == 1

    # The existing v0.51/v0.50 prepare path is itself idempotent: repeating the
    # explicit action returns prepared state without duplicating handoffs/audit.
    prepare_again = second.post(f"/api/v1/control/agent-workflows/team-plans/{plan_id}/prepare")
    assert prepare_again.status_code == 200, prepare_again.text
    assert prepare_again.json()["status"] == "prepared"
    with db() as connection:
        assert connection.execute("SELECT COUNT(*) FROM agent_result_handoffs WHERE task_id IN (?, ?)", tuple(task_ids)).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action='agent.synthesis.prepared' AND resource_key=?",
            (conversation_id,),
        ).fetchone()[0] == 1

    updated = second.post(
        "/api/v1/control/agent-workflows/rehydrate",
        json={"conversation_id": conversation_id, "plan_id": plan_id},
    )
    assert updated.status_code == 200, updated.text
    updated_payload = updated.json()
    assert updated_payload["rehydration_id"] != original_id
    assert updated_payload["state_fingerprint"] != original_fingerprint
    assert updated_payload["checkpoint"]["workflow_status"] == "prepared"
    assert updated_payload["drift"]["state_changed_since_last_rehydration"] is True

    shutdown = second.post("/api/v1/control/system/shutdown")
    assert shutdown.status_code == 200 and shutdown.json()["accepted"] is True
    second.close()
    assert wait_health(False, 20), "HomeServer listener remained active after v0.56 packaged shutdown"

    print("Packaged HomeServer v0.56 durable workflow rehydration/restart/exactly-once continuation test passed")


if __name__ == "__main__":
    main()
