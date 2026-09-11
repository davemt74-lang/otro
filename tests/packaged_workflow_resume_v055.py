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
    client = httpx.Client(base_url=BASE, timeout=3.0, trust_env=False)
    response = client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN})
    assert response.status_code == 200, response.text
    assert client.get("/api/v1/control/system").status_code == 200
    return client


def verify_resume(client: httpx.Client, conversation_id: str, plan_id: int) -> None:
    response = client.get("/api/v1/control/agent-workflows/resume?limit=50")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["version"] == "v0.55"
    assert payload["read_only"] is True
    assert payload["auto_executes"] is False
    assert payload["navigation_only"] is True
    assert payload["continuation_version"] == "v0.54"
    assert payload["explicit_actions_preserved"] is True
    assert payload["resumable_count"] >= 1
    suggested = payload["suggested"]
    assert suggested["conversation_id"] == conversation_id
    assert int(suggested["plan_id"]) == plan_id
    assert suggested["workflow_status"] == "proposed"
    assert suggested["next_action"]["key"] == "review_plan"
    assert suggested["resume_available"] is True
    serialized = json.dumps(payload, sort_keys=True)
    assert "provider_key" not in serialized
    assert '"model"' not in serialized
    assert '"result"' not in serialized


def main() -> None:
    assert os.environ.get("HOMESERVER_DATA_DIR"), "HOMESERVER_DATA_DIR is required"
    assert wait_health(True, 20), "packaged HomeServer is not healthy before v0.55 restart test"

    first = authorize()
    primary_response = first.get("/api/v1/control/agent")
    assert primary_response.status_code == 200, primary_response.text
    primary_id = int(primary_response.json()["agent"]["id"])

    workers: list[dict] = []
    for name in ("Packaged Resume Research", "Packaged Resume Analysis"):
        response = first.post(
            "/api/v1/control/agents",
            json={
                "name": name,
                "instructions": f"Persist {name} for the v0.55 packaged recovery test.",
                "model": "packaged-resume-model",
            },
        )
        assert response.status_code == 200, response.text
        workers.append(response.json()["agent"])

    conversation_id = "packaged-workflow-resume-v055"
    members = [
        {
            "worker_agent_id": int(workers[0]["id"]),
            "worker_agent_name": str(workers[0]["name"]),
            "task": "Preserve the packaged resume research task.",
        },
        {
            "worker_agent_id": int(workers[1]["id"]),
            "worker_agent_name": str(workers[1]["name"]),
            "task": "Preserve the packaged resume analysis task.",
        },
    ]
    context = {
        "include_memory": True,
        "include_knowledge": True,
        "include_contacts": False,
        "cloud_allowed": False,
        "max_context_chars": 12000,
    }
    with db() as connection:
        connection.execute(
            """
            INSERT INTO conversations(id, agent_id, source_app_key, title, status, created_at, updated_at)
            VALUES (?, ?, 'owner', 'Packaged v0.55 resumable workflow', 'active', '2026-09-10 10:00:00', '2026-09-10 10:00:00')
            """,
            (conversation_id, primary_id),
        )
        cursor = connection.execute(
            """
            INSERT INTO agent_team_plans(
                source_app_key, conversation_id, parent_agent_id, parent_agent_name,
                objective, members_json, context_json, permission_snapshot_json,
                provider_key, model, cloud_used, status
            ) VALUES ('owner', ?, ?, 'HomeServer Agent', ?, ?, ?, '[]', 'ollama', 'packaged-resume-model', 0, 'proposed')
            """,
            (
                conversation_id,
                primary_id,
                "Verify v0.55 packaged workflow resume across restart.",
                json.dumps(members, separators=(",", ":")),
                json.dumps(context, separators=(",", ":")),
            ),
        )
        plan_id = int(cursor.lastrowid)
        # A newer ordinary conversation proves recovery is workflow-aware rather
        # than just reopening whichever chat was updated most recently.
        connection.execute(
            """
            INSERT INTO conversations(id, agent_id, source_app_key, title, status, created_at, updated_at)
            VALUES ('packaged-newer-plain-chat-v055', ?, 'owner', 'Newer plain chat', 'active', '2026-09-11 12:00:00', '2026-09-11 12:00:00')
            """,
            (primary_id,),
        )

    verify_resume(first, conversation_id, plan_id)
    old_cookie = first.cookies.get("homeserver_owner")
    assert old_cookie
    restart = first.post("/api/v1/control/system/restart")
    assert restart.status_code == 200 and restart.json()["accepted"] is True
    first.close()

    wait_health(False, 4)
    assert wait_health(True, 20), "HomeServer did not return after v0.55 supervised restart"

    old_session = httpx.Client(
        base_url=BASE,
        cookies={"homeserver_owner": old_cookie},
        timeout=3.0,
        trust_env=False,
    )
    try:
        assert old_session.get("/api/v1/control/system").status_code == 401
    finally:
        old_session.close()

    second = authorize()
    verify_resume(second, conversation_id, plan_id)
    shutdown = second.post("/api/v1/control/system/shutdown")
    assert shutdown.status_code == 200 and shutdown.json()["accepted"] is True
    second.close()
    assert wait_health(False, 20), "HomeServer listener remained active after v0.55 packaged shutdown"

    print("Packaged HomeServer v0.55 workflow resume/restart/session-rotation recovery test passed")


if __name__ == "__main__":
    main()
