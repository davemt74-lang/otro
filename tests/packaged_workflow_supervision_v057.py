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
    client = httpx.Client(base_url=BASE, timeout=5.0, trust_env=False)
    response = client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN})
    assert response.status_code == 200, response.text
    assert client.get("/api/v1/control/system").status_code == 200
    return client


def restart(client: httpx.Client) -> str:
    cookie = client.cookies.get("homeserver_owner")
    assert cookie
    response = client.post("/api/v1/control/system/restart")
    assert response.status_code == 200 and response.json()["accepted"] is True
    client.close()
    wait_health(False, 4)
    assert wait_health(True, 20), "HomeServer did not return after packaged v0.57 restart"
    old = httpx.Client(base_url=BASE, cookies={"homeserver_owner": cookie}, timeout=3.0, trust_env=False)
    try:
        assert old.get("/api/v1/control/system").status_code == 401
    finally:
        old.close()
    return cookie


def main() -> None:
    assert os.environ.get("HOMESERVER_DATA_DIR"), "HOMESERVER_DATA_DIR is required"
    assert wait_health(True, 20), "packaged HomeServer is not healthy before v0.57 supervision test"

    first = authorize()
    primary_response = first.get("/api/v1/control/agent")
    assert primary_response.status_code == 200, primary_response.text
    primary_id = int(primary_response.json()["agent"]["id"])
    conversation_id = "packaged-workflow-supervision-v057"

    with db() as connection:
        workers: list[int] = []
        for name in ("Packaged Supervision Research", "Packaged Supervision Analysis"):
            cursor = connection.execute(
                "INSERT INTO agents(name, instructions, model, is_primary) VALUES (?, '', '', 0)",
                (name,),
            )
            workers.append(int(cursor.lastrowid))
        connection.execute(
            """
            INSERT INTO conversations(id, agent_id, source_app_key, title, status, created_at, updated_at)
            VALUES (?, ?, 'owner', 'Packaged v0.57 supervised workflow', 'active', '2026-09-11 10:00:00', '2026-09-11 10:00:00')
            """,
            (conversation_id, primary_id),
        )
        run_cursor = connection.execute(
            """
            INSERT INTO agent_team_runs(source_app_key, conversation_id, parent_agent_id, parent_agent_name, objective)
            VALUES ('owner', ?, ?, 'HomeServer Agent', 'Verify packaged supervised continuation.')
            """,
            (conversation_id, primary_id),
        )
        team_run_id = int(run_cursor.lastrowid)
        task_ids: list[int] = []
        members: list[dict] = []
        for position, worker_id in enumerate(workers, start=1):
            task = f"Packaged v0.57 completed specialist task {position}"
            cursor = connection.execute(
                """
                INSERT INTO agent_delegation_tasks(
                    source_app_key, parent_agent_id, worker_agent_id, parent_agent_name,
                    worker_agent_name, conversation_id, task, status, result,
                    include_memory, include_knowledge, include_contacts, cloud_allowed,
                    max_context_chars, permission_snapshot_json, completed_at
                ) VALUES ('owner', ?, ?, 'HomeServer Agent', ?, ?, ?, 'completed', ?, 0, 0, 0, 0, 12000, '[]', CURRENT_TIMESTAMP)
                """,
                (
                    primary_id,
                    worker_id,
                    f"Worker {position}",
                    conversation_id,
                    task,
                    f"PACKAGED V057 PRIVATE RESULT {position}",
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
            ) VALUES ('owner', ?, ?, 'HomeServer Agent', 'Verify packaged supervised continuation.', ?, ?, '[]', 0, 'approved', ?, CURRENT_TIMESTAMP)
            """,
            (
                conversation_id,
                primary_id,
                json.dumps(members, separators=(",", ":")),
                json.dumps({"include_memory": False, "include_knowledge": False, "include_contacts": False, "cloud_allowed": False, "max_context_chars": 12000}, separators=(",", ":")),
                team_run_id,
            ),
        )
        plan_id = int(plan_cursor.lastrowid)

    recovered = first.post(
        "/api/v1/control/agent-workflows/rehydrate",
        json={"conversation_id": conversation_id, "plan_id": plan_id},
    )
    assert recovered.status_code == 200, recovered.text
    checkpoint = recovered.json()
    assert checkpoint["version"] == "v0.56"
    assert checkpoint["checkpoint"]["workflow_status"] == "completed"
    assert checkpoint["safe_to_continue"] is True
    assert "PACKAGED V057 PRIVATE RESULT" not in json.dumps(checkpoint)
    request_body = {
        "conversation_id": conversation_id,
        "plan_id": plan_id,
        "rehydration_id": checkpoint["rehydration_id"],
        "state_fingerprint": checkpoint["state_fingerprint"],
        "max_steps": 2,
    }

    # Prove the exact v0.56 checkpoint survives a packaged restart before v0.57 starts.
    restart(first)
    second = authorize()
    restored = second.post(
        "/api/v1/control/agent-workflows/rehydrate",
        json={"conversation_id": conversation_id, "plan_id": plan_id},
    )
    assert restored.status_code == 200, restored.text
    restored_payload = restored.json()
    assert restored_payload["rehydration_id"] == checkpoint["rehydration_id"]
    assert restored_payload["state_fingerprint"] == checkpoint["state_fingerprint"]
    assert restored_payload["reused"] is True

    supervised = second.post("/api/v1/control/agent-workflows/supervise", json=request_body)
    assert supervised.status_code == 200, supervised.text
    payload = supervised.json()
    assert payload["version"] == "v0.57"
    assert payload["actions_executed"] == 1
    assert [step["action"] for step in payload["steps"]] == ["prepare"]
    assert payload["stop_boundary"] == "parent_synthesis_required"
    assert payload["checkpoint"]["workflow_status"] == "prepared"
    assert payload["auto_approval"] is False
    assert payload["auto_retry"] is False
    assert payload["auto_parent_chat"] is False
    assert "PACKAGED V057 PRIVATE RESULT" not in json.dumps(payload)
    supervision_id = int(payload["supervision_id"])
    with db() as connection:
        assert connection.execute("SELECT COUNT(*) FROM agent_workflow_supervisions WHERE id=?", (supervision_id,)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM agent_result_handoffs WHERE task_id IN (?, ?)", tuple(task_ids)).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action='agent.synthesis.prepared' AND resource_key=?",
            (conversation_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action='agent.workflow.supervision.step' AND resource_key=?",
            (str(plan_id),),
        ).fetchone()[0] == 1

    # Restart again after the supervised action. Replaying the original explicit
    # request must return the durable session, not prepare or execute again.
    restart(second)
    third = authorize()
    duplicate = third.post("/api/v1/control/agent-workflows/supervise", json=request_body)
    assert duplicate.status_code == 200, duplicate.text
    duplicate_payload = duplicate.json()
    assert duplicate_payload["supervision_id"] == supervision_id
    assert duplicate_payload["reused"] is True
    assert duplicate_payload["actions_executed"] == 1
    assert [step["action"] for step in duplicate_payload["steps"]] == ["prepare"]
    with db() as connection:
        assert connection.execute("SELECT COUNT(*) FROM agent_workflow_supervisions WHERE id=?", (supervision_id,)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM agent_result_handoffs WHERE task_id IN (?, ?)", tuple(task_ids)).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action='agent.synthesis.prepared' AND resource_key=?",
            (conversation_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action='agent.workflow.supervision.started' AND resource_key=?",
            (str(plan_id),),
        ).fetchone()[0] == 1

    shutdown = third.post("/api/v1/control/system/shutdown")
    assert shutdown.status_code == 200 and shutdown.json()["accepted"] is True
    third.close()
    assert wait_health(False, 20), "HomeServer listener remained active after v0.57 packaged shutdown"

    print("Packaged HomeServer v0.57 supervised workflow continuation/restart/idempotency test passed")


if __name__ == "__main__":
    main()
