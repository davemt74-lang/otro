from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

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
    agents = second.get("/api/v1/control/agents")
    assert agents.status_code == 200, agents.text
    assert any(item["id"] == secondary_id and not item["is_primary"] for item in agents.json()["items"])

    shutdown = second.post("/api/v1/control/system/shutdown")
    assert shutdown.status_code == 200 and shutdown.json()["accepted"] is True
    second.close()
    assert wait_health(False, 20), "HomeServer listener remained active after supervised shutdown"

    print("Packaged HomeServer restart/session-rotation/Agent Voice Profile/v0.46 persona/v0.47 routing persistence/shutdown test passed")


if __name__ == "__main__":
    main()
