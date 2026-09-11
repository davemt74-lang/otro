from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

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
    assert response.status_code == 200
    assert client.get("/api/v1/control/system").status_code == 200
    return client


def verify_agent_voice_profile(client: httpx.Client, *, expected_rate: float | None = None) -> int:
    agent_response = client.get("/api/v1/control/agent")
    assert agent_response.status_code == 200, agent_response.text
    agent_id = int(agent_response.json()["agent"]["id"])
    profile_response = client.get(f"/api/v1/control/voice/agents/{agent_id}/profile")
    assert profile_response.status_code == 200, profile_response.text
    profile = profile_response.json()
    assert profile["version"] == "v0.45"
    if expected_rate is not None:
        assert profile["overrides"]["speaking_rate"] == expected_rate
    return agent_id


def verify_secondary_persona(client: httpx.Client, agent_id: int) -> None:
    response = client.get(f"/api/v1/control/agents/{agent_id}")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["version"] == "v0.46"
    agent = payload["agent"]
    assert agent["id"] == agent_id
    assert agent["is_primary"] is False
    assert agent["name"] == "Packaged Research Agent"
    assert agent["model"] == "packaged-local-model"
    assert agent["instructions"] == "Verify packaged multi-Agent persona persistence."
    assert agent["voice_profile"]["version"] == "v0.45"
    assert agent["voice_profile"]["overrides"]["voice"] is None
    assert agent["voice_profile"]["overrides"]["speaking_rate"] == 1.2
    assert agent["voice_profile"]["overrides"]["sentence_silence"] == 0.25


def verify_agent_routing(client: httpx.Client, primary_id: int, secondary_id: int) -> None:
    response = client.get("/api/v1/control/agent-routing")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["version"] == "v0.47"
    assert payload["primary_implicit"] is True
    agents = {int(item["id"]): item for item in payload["items"]}
    assert agents[primary_id]["is_primary"] is True
    assert agents[secondary_id]["is_primary"] is False
    assert agents[secondary_id]["name"] == "Packaged Research Agent"


def verify_agent_workflow(
    client: httpx.Client,
    task_id: int,
    primary_id: int,
    secondary_id: int,
) -> None:
    policy = client.get("/api/v1/control/agent-workflows/policy")
    assert policy.status_code == 200, policy.text
    policy_payload = policy.json()
    assert policy_payload["version"] == "v0.48"
    assert policy_payload["enabled"] is True
    assert policy_payload["max_context_chars"] == 16000

    task = client.get(f"/api/v1/control/agent-workflows/delegations/{task_id}")
    assert task.status_code == 200, task.text
    payload = task.json()
    assert payload["version"] == "v0.48"
    assert payload["status"] == "queued"
    assert payload["parent_agent_id"] == primary_id
    assert payload["worker_agent_id"] == secondary_id
    assert payload["parent_agent_name"]
    assert payload["worker_agent_name"] == "Packaged Research Agent"
    assert payload["task"] == "Verify packaged v0.48 delegation workflow persistence."
    assert payload["metadata"]["created_via"] == "owner_api"


def verify_team_run(
    client: httpx.Client,
    team_run_id: int,
    primary_id: int,
    research_id: int,
    analysis_id: int,
    expected_task_ids: list[int],
) -> None:
    response = client.get(f"/api/v1/control/agent-workflows/team-runs/{team_run_id}")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["version"] == "v0.51"
    assert payload["id"] == team_run_id
    assert payload["status"] == "queued"
    assert payload["parent_agent_id"] == primary_id
    assert payload["objective"] == "Verify packaged v0.51 Team Run persistence across restart."
    assert payload["counts"]["members"] == 2
    assert payload["counts"]["queued"] == 2
    assert payload["task_ids"] == expected_task_ids
    assert payload["one_hop_only"] is True
    assert payload["synthesis_version"] == "v0.50"
    members = payload["members"]
    assert [int(item["worker_agent_id"]) for item in members] == [research_id, analysis_id]
    assert [item["worker_agent_name"] for item in members] == ["Packaged Research Agent", "Packaged Analysis Agent"]
    assert [item["status"] for item in members] == ["queued", "queued"]


def verify_team_plan(
    client: httpx.Client,
    plan_id: int,
    primary_id: int,
    research_id: int,
    analysis_id: int,
    conversation_id: str,
) -> None:
    response = client.get(f"/api/v1/control/agent-workflows/team-plans/{plan_id}")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["version"] == "v0.52"
    assert payload["id"] == plan_id
    assert payload["status"] == "proposed"
    assert payload["requires_approval"] is True
    assert payload["auto_executes"] is False
    assert payload["team_run_id"] is None
    assert payload["conversation_id"] == conversation_id
    assert payload["parent_agent_id"] == primary_id
    assert payload["objective"] == "Verify packaged v0.52 Team Plan persistence across restart."
    assert [int(item["worker_agent_id"]) for item in payload["members"]] == [research_id, analysis_id]
    assert [item["worker_agent_name"] for item in payload["members"]] == ["Packaged Research Agent", "Packaged Analysis Agent"]
    assert payload["context"]["max_context_chars"] == 12000
    assert payload["cloud_used"] is False


def main() -> None:
    assert os.environ.get("HOMESERVER_DATA_DIR"), "HOMESERVER_DATA_DIR is required"
    assert wait_health(True, 20), "packaged HomeServer is not healthy before lifecycle test"

    first = authorize()
    agent_id = verify_agent_voice_profile(first)
    saved = first.put(
        f"/api/v1/control/voice/agents/{agent_id}/profile",
        json={"voice": None, "speaking_rate": 1.1, "sentence_silence": None},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["overrides"]["speaking_rate"] == 1.1

    created = first.post(
        "/api/v1/control/agents",
        json={
            "name": "Packaged Research Agent",
            "instructions": "Initial packaged persona.",
            "model": "packaged-local-model",
        },
    )
    assert created.status_code == 200, created.text
    secondary_id = int(created.json()["agent"]["id"])
    assert secondary_id != agent_id
    persona = first.put(
        f"/api/v1/control/agents/{secondary_id}/persona",
        json={
            "name": "Packaged Research Agent",
            "instructions": "Verify packaged multi-Agent persona persistence.",
            "model": "packaged-local-model",
            "voice_profile": {
                "voice": None,
                "speaking_rate": 1.2,
                "sentence_silence": 0.25,
            },
        },
    )
    assert persona.status_code == 200, persona.text
    verify_secondary_persona(first, secondary_id)
    verify_agent_routing(first, agent_id, secondary_id)

    analysis = first.post(
        "/api/v1/control/agents",
        json={
            "name": "Packaged Analysis Agent",
            "instructions": "Verify packaged Team Run persistence.",
            "model": "packaged-analysis-model",
        },
    )
    assert analysis.status_code == 200, analysis.text
    analysis_id = int(analysis.json()["agent"]["id"])
    assert analysis_id not in {agent_id, secondary_id}

    workflow_policy = first.put(
        "/api/v1/control/agent-workflows/policy",
        json={"enabled": True, "max_context_chars": 16000},
    )
    assert workflow_policy.status_code == 200, workflow_policy.text
    queued = first.post(
        "/api/v1/control/agent-workflows/delegations",
        json={
            "parent_agent_id": agent_id,
            "worker_agent_id": secondary_id,
            "task": "Verify packaged v0.48 delegation workflow persistence.",
            "include_memory": True,
            "include_knowledge": True,
            "include_contacts": False,
            "cloud_allowed": True,
            "max_context_chars": 16000,
        },
    )
    assert queued.status_code == 200, queued.text
    workflow_task_id = int(queued.json()["id"])
    verify_agent_workflow(first, workflow_task_id, agent_id, secondary_id)

    team_conversation_id = "packaged-team-run-v051"
    with db() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO conversations(id, agent_id, source_app_key, title, status)
            VALUES (?, ?, 'owner', 'Packaged Team Run v0.51', 'active')
            """,
            (team_conversation_id, agent_id),
        )

    team_created = first.post(
        "/api/v1/control/agent-workflows/team-runs",
        json={
            "parent_agent_id": agent_id,
            "conversation_id": team_conversation_id,
            "objective": "Verify packaged v0.51 Team Run persistence across restart.",
            "members": [
                {
                    "worker_agent_id": secondary_id,
                    "task": "Preserve the packaged research specialist member.",
                    "include_memory": True,
                    "include_knowledge": True,
                    "include_contacts": False,
                    "cloud_allowed": True,
                    "max_context_chars": 12000,
                },
                {
                    "worker_agent_id": analysis_id,
                    "task": "Preserve the packaged analysis specialist member.",
                    "include_memory": True,
                    "include_knowledge": True,
                    "include_contacts": False,
                    "cloud_allowed": True,
                    "max_context_chars": 12000,
                },
            ],
        },
    )
    assert team_created.status_code == 200, team_created.text
    team_payload = team_created.json()
    team_run_id = int(team_payload["id"])
    team_task_ids = [int(value) for value in team_payload["task_ids"]]
    verify_team_run(first, team_run_id, agent_id, secondary_id, analysis_id, team_task_ids)

    # The packaged lifecycle deliberately persists a proposed plan without invoking
    # inference. This proves the feature schema and review state survive restart;
    # behavioral inference/approval is covered by the dedicated v0.52 regression.
    plan_members = [
        {
            "worker_agent_id": secondary_id,
            "worker_agent_name": "Packaged Research Agent",
            "task": "Preserve the packaged proposed research task.",
        },
        {
            "worker_agent_id": analysis_id,
            "worker_agent_name": "Packaged Analysis Agent",
            "task": "Preserve the packaged proposed analysis task.",
        },
    ]
    plan_context = {
        "include_memory": True,
        "include_knowledge": True,
        "include_contacts": False,
        "cloud_allowed": False,
        "max_context_chars": 12000,
    }
    with db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO agent_team_plans(
                source_app_key, conversation_id, parent_agent_id, parent_agent_name,
                objective, members_json, context_json, permission_snapshot_json,
                provider_key, model, cloud_used, status
            ) VALUES ('owner', ?, ?, 'HomeServer Agent', ?, ?, ?, '[]', 'ollama', 'packaged-local-model', 0, 'proposed')
            """,
            (
                team_conversation_id,
                agent_id,
                "Verify packaged v0.52 Team Plan persistence across restart.",
                json.dumps(plan_members, separators=(",", ":")),
                json.dumps(plan_context, separators=(",", ":")),
            ),
        )
        team_plan_id = int(cursor.lastrowid)
    verify_team_plan(first, team_plan_id, agent_id, secondary_id, analysis_id, team_conversation_id)

    old_cookie = first.cookies.get("homeserver_owner")
    assert old_cookie
    restart = first.post("/api/v1/control/system/restart")
    assert restart.status_code == 200 and restart.json()["accepted"] is True
    first.close()

    wait_health(False, 4)
    assert wait_health(True, 20), "HomeServer did not return after supervised restart"

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
    assert verify_agent_voice_profile(second, expected_rate=1.1) == agent_id
    verify_secondary_persona(second, secondary_id)
    verify_agent_routing(second, agent_id, secondary_id)
    verify_agent_workflow(second, workflow_task_id, agent_id, secondary_id)
    verify_team_run(second, team_run_id, agent_id, secondary_id, analysis_id, team_task_ids)
    verify_team_plan(second, team_plan_id, agent_id, secondary_id, analysis_id, team_conversation_id)
    listed_teams = second.get(
        f"/api/v1/control/agent-workflows/team-runs?conversation_id={team_conversation_id}"
    )
    assert listed_teams.status_code == 200, listed_teams.text
    assert any(int(item["id"]) == team_run_id for item in listed_teams.json()["items"])
    listed_plans = second.get(
        f"/api/v1/control/agent-workflows/team-plans?conversation_id={team_conversation_id}"
    )
    assert listed_plans.status_code == 200, listed_plans.text
    assert any(int(item["id"]) == team_plan_id and item["status"] == "proposed" for item in listed_plans.json()["items"])
    agents = second.get("/api/v1/control/agents")
    assert agents.status_code == 200, agents.text
    ids = {int(item["id"]) for item in agents.json()["items"]}
    assert secondary_id in ids and analysis_id in ids

    shutdown = second.post("/api/v1/control/system/shutdown")
    assert shutdown.status_code == 200 and shutdown.json()["accepted"] is True
    second.close()
    assert wait_health(False, 20), "HomeServer listener remained active after supervised shutdown"

    print("Packaged HomeServer restart/session-rotation/Agent Voice Profile/v0.46 persona/v0.47 routing/v0.48 delegation/v0.51 Team Run/v0.52 Team Plan persistence/shutdown test passed")


if __name__ == "__main__":
    main()
