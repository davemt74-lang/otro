from __future__ import annotations

import os
import tempfile

from fastapi.testclient import TestClient


with tempfile.TemporaryDirectory(prefix="homeserver-smoke-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402

    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["version"] == "0.2.0"

        root = client.get("/")
        assert root.status_code == 200
        assert "HomeServer" in root.text

        unauthorized = client.get("/api/v1/control/overview")
        assert unauthorized.status_code == 401

        pair = client.post(
            "/api/v1/pairing/request",
            json={
                "app_key": "vp3-test",
                "app_name": "VP3 Test",
                "permissions": ["agent.chat", "knowledge.search", "memory.read", "memory.write", "not.real"],
            },
        )
        assert pair.status_code == 200
        code = pair.json()["code"]
        assert "not.real" not in pair.json()["permissions"]

        self_approval = client.post("/api/v1/pairing/approve", json={"code": code})
        assert self_approval.status_code == 401

        bad_bootstrap = client.post("/__owner/session", headers={"X-HomeServer-Owner": "wrong-token"})
        assert bad_bootstrap.status_code == 401

        bootstrap = client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN})
        assert bootstrap.status_code == 200
        assert client.cookies.get("homeserver_owner")

        overview = client.get("/api/v1/control/overview")
        assert overview.status_code == 200
        assert overview.json()["agent"]["name"] == "HomeServer Agent"

        update = client.put(
            "/api/v1/control/agent",
            json={"name": "Private Agent", "model": "local", "instructions": "Keep private data local."},
        )
        assert update.status_code == 200

        knowledge = client.post(
            "/api/v1/control/knowledge",
            json={"title": "VP3", "kind": "note", "content": "VP3 is an authorized HomeServer client."},
        )
        assert knowledge.status_code == 200

        memory = client.post(
            "/api/v1/control/memory",
            json={"memory_key": "architecture", "content": "HomeServer is application-neutral.", "importance": 0.9},
        )
        assert memory.status_code == 200

        approval = client.post("/api/v1/pairing/approve", json={"code": code})
        assert approval.status_code == 200
        token = approval.json()["token"]

        me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["app_key"] == "vp3-test"

        client_knowledge = client.get("/api/v1/knowledge?q=VP3", headers={"Authorization": f"Bearer {token}"})
        assert client_knowledge.status_code == 200
        assert len(client_knowledge.json()["items"]) == 1

        client_memory = client.get("/api/v1/memory", headers={"Authorization": f"Bearer {token}"})
        assert client_memory.status_code == 200
        assert len(client_memory.json()["items"]) == 1

        apps = client.get("/api/v1/control/apps")
        assert apps.status_code == 200
        assert apps.json()["apps"][0]["app_key"] == "vp3-test"

print("HomeServer smoke test passed")
