from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-app-scope-handshake-v027-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200

        request = client.post(
            "/api/v1/pairing/request",
            json={
                "app_key": "vp3-handshake-test",
                "app_name": "VP3 Handshake Test",
                "permissions": ["tools.execute", "memory.read", "knowledge.search"],
            },
        ).json()
        assert client.post("/api/v1/pairing/approve", json={"code": request["code"]}).status_code == 200
        headers = {"Authorization": f"Bearer {request['claim_token']}"}

        apps = client.get("/api/v1/control/apps").json()["apps"]
        app_id = next(item["id"] for item in apps if item["app_key"] == "vp3-handshake-test")
        expected_scope = {
            "cloud_allowed": False,
            "memory_key_prefixes": ["vp3:"],
            "knowledge_kinds": ["note"],
            "tool_names": ["memory.list", "knowledge.search"],
            "plugin_keys": ["vp3.private"],
        }
        saved = client.put(f"/api/v1/control/apps/{app_id}/scope", json=expected_scope)
        assert saved.status_code == 200, saved.text

        tools = client.get("/api/v1/tools", headers=headers)
        assert tools.status_code == 200, tools.text
        payload = tools.json()
        assert payload["app"] == "vp3-handshake-test"
        assert payload["app_scope"] == expected_scope
        assert {item["key"] for item in payload["items"]} == {"memory.list", "knowledge.search"}

        assert client.get("/api/v1/tools").status_code == 401

print("HomeServer v0.27 authenticated app-scope handshake regression passed")
