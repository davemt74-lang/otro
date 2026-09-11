from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
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


def restart(client: httpx.Client) -> None:
    cookie = client.cookies.get("homeserver_owner")
    assert cookie
    response = client.post("/api/v1/control/system/restart")
    assert response.status_code == 200 and response.json()["accepted"] is True
    client.close()
    wait_health(False, 4)
    assert wait_health(True, 20), "HomeServer did not return after packaged v0.58 restart"
    old = httpx.Client(base_url=BASE, cookies={"homeserver_owner": cookie}, timeout=3.0, trust_env=False)
    try:
        assert old.get("/api/v1/control/system").status_code == 401
    finally:
        old.close()


def wait_for_automation_run(client: httpx.Client, automation_id: int, timeout: float = 40.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(f"/api/v1/control/agent-workflows/automations/{automation_id}/runs")
        assert response.status_code == 200, response.text
        items = response.json().get("items") or []
        if items and items[0].get("status") not in {"claimed", "running"}:
            return items[0]
        time.sleep(0.4)
    raise AssertionError("Packaged v0.58 scheduled workflow did not fire")


def main() -> None:
    assert os.environ.get("HOMESERVER_DATA_DIR"), "HOMESERVER_DATA_DIR is required"
    assert wait_health(True, 20), "packaged HomeServer is not healthy before v0.58 automation test"

    first = authorize()
    capability = first.get("/api/v1/control/agent-workflows/automations/capability")
    assert capability.status_code == 200, capability.text
    assert capability.json()["version"] == "v0.58"
    primary = first.get("/api/v1/control/agent").json()["agent"]
    primary_id = int(primary["id"])
    conversation_id = "packaged-workflow-automation-v058"

    with db() as connection:
        workers: list[int] = []
        for name in ("Packaged Automation Research", "Packaged Automation Analysis"):
            cursor = connection.execute(
                "INSERT INTO agents(name, instructions, model, is_primary) VALUES (?, '', '', 0)",
                (name,),
            )
            workers.append(int(cursor.lastrowid))
        connection.execute(
            """
            INSERT INTO conversations(id, agent_id, source_app_key, title, status)
            VALUES (?, ?, 'owner', 'Packaged v0.58 scheduled workflow', 'active')
            """,
            (conversation_id, primary_id),
        )
        run_cursor = connection.execute(
            """
            INSERT INTO agent_team_runs(source_app_key, conversation_id, parent_agent_id, parent_agent_name, objective)
            VALUES ('owner', ?, ?, 'HomeServer Agent', 'Verify packaged scheduled continuation after restart.')
            """,
            (conversation_id, primary_id),
        )
        team_run_id = int(run_cursor.lastrowid)
        task_ids: list[int] = []
        members: list[dict] = []
        for position, worker_id in enumerate(workers, start=1):
            task = f"Packaged v0.58 completed specialist task {position}"
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
                    f"PACKAGED V058 PRIVATE RESULT {position}",
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
            ) VALUES ('owner', ?, ?, 'HomeServer Agent', 'Verify packaged scheduled continuation after restart.', ?, ?, '[]', 0, 'approved', ?, CURRENT_TIMESTAMP)
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

    # Schedule far enough ahead that the explicit restart happens before the
    # trigger is eligible. The restarted packaged scheduler must own the fire.
    run_at = datetime.now(timezone.utc) + timedelta(seconds=12)
    created = first.post(
        "/api/v1/control/agent-workflows/automations",
        json={
            "conversation_id": conversation_id,
            "plan_id": plan_id,
            "trigger_type": "once",
            "run_at": run_at.isoformat(),
            "max_steps": 2,
        },
    )
    assert created.status_code == 200, created.text
    automation = created.json()
    assert automation["version"] == "v0.58"
    assert automation["enabled"] is True
    assert automation["requires_explicit_creation"] is True
    assert automation["uses_supervision"] == "v0.57"
    automation_id = int(automation["automation_id"])
    with db() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM agent_workflow_automation_runs WHERE automation_id=?",
            (automation_id,),
        ).fetchone()[0] == 0

    restart(first)
    second = authorize()
    fired = wait_for_automation_run(second, automation_id)
    assert fired["status"] == "completed", fired
    result = fired["result"]
    assert result["version"] == "v0.57"
    assert result["actions_executed"] == 1
    assert [step["action"] for step in result["steps"]] == ["prepare"]
    assert result["stop_boundary"] == "parent_synthesis_required"
    assert "PACKAGED V058 PRIVATE RESULT" not in json.dumps(result)
    supervision_id = int(fired["supervision_id"])

    listed = second.get(
        f"/api/v1/control/agent-workflows/automations?conversation_id={conversation_id}"
    )
    assert listed.status_code == 200, listed.text
    item = next(item for item in listed.json()["items"] if int(item["automation_id"]) == automation_id)
    assert item["enabled"] is False
    assert item["last_status"] == "completed"
    with db() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM agent_workflow_automation_runs WHERE automation_id=?",
            (automation_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM agent_workflow_supervisions WHERE id=?",
            (supervision_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action='agent.synthesis.prepared' AND resource_key=?",
            (conversation_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM agent_result_handoffs WHERE task_id IN (?, ?)",
            tuple(task_ids),
        ).fetchone()[0] == 2

    # A second packaged restart must not replay the already-claimed one-time
    # trigger. The durable run key and disabled schedule survive process state.
    restart(second)
    third = authorize()
    time.sleep(3)
    runs = third.get(f"/api/v1/control/agent-workflows/automations/{automation_id}/runs")
    assert runs.status_code == 200, runs.text
    assert len(runs.json()["items"]) == 1
    with db() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM agent_workflow_automation_runs WHERE automation_id=?",
            (automation_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action='agent.synthesis.prepared' AND resource_key=?",
            (conversation_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action='agent.workflow.automation.finished' AND resource_key=?",
            (str(automation_id),),
        ).fetchone()[0] == 1

    shutdown = third.post("/api/v1/control/system/shutdown")
    assert shutdown.status_code == 200 and shutdown.json()["accepted"] is True
    third.close()
    assert wait_health(False, 20), "HomeServer listener remained active after v0.58 packaged shutdown"

    print("Packaged HomeServer v0.58 scheduled workflow restart/exactly-once test passed")


if __name__ == "__main__":
    main()