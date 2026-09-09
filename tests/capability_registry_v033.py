from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-capability-registry-v033-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200

        public = client.get("/api/v1/capabilities")
        assert public.status_code == 200, public.text
        advertised = public.json()
        assert "capability.registry.v1" in advertised["features"]
        assert advertised["capability_registry"]["operation"] == "capability.registry"
        assert client.get("/api/v1/capability-registry").status_code == 401

        request = client.post(
            "/api/v1/pairing/request",
            json={
                "app_key": "registry-test",
                "app_name": "Registry Test",
                "permissions": [
                    "agent.chat",
                    "tools.execute",
                    "memory.read",
                    "knowledge.search",
                    "contacts.read",
                ],
            },
        ).json()
        assert client.post("/api/v1/pairing/approve", json={"code": request["code"]}).status_code == 200
        headers = {"Authorization": f"Bearer {request['claim_token']}"}

        apps = client.get("/api/v1/control/apps").json()["apps"]
        app_id = next(item["id"] for item in apps if item["app_key"] == "registry-test")
        scope = {
            "cloud_allowed": False,
            "memory_key_prefixes": ["vp3:"],
            "knowledge_kinds": ["note"],
            "tool_names": ["memory.list", "knowledge.search"],
            "plugin_keys": ["vp3.allowed"],
        }
        scoped = client.put(f"/api/v1/control/apps/{app_id}/scope", json=scope)
        assert scoped.status_code == 200, scoped.text

        response = client.get("/api/v1/capability-registry", headers=headers)
        assert response.status_code == 200, response.text
        registry = response.json()
        assert registry["registry_version"] == "v0.33"
        assert registry["app"]["key"] == "registry-test"
        assert registry["app"]["scope"] == scope
        assert {item["key"] for item in registry["tools"]} == {"memory.list", "knowledge.search"}
        assert {item["key"] for item in registry["skills"]} == {"local.research"}
        assert registry["memory"]["restricted"] is True
        assert registry["knowledge"]["restricted"] is True
        assert "capability.registry" in registry["operations"]
        assert "agent.chat" in registry["operations"]
        assert "memory.write" not in registry["operations"]

        # Contacts are an optional subsystem. A paired app may hold contacts.read
        # even when the current installation has no contacts table; registry must
        # degrade safely rather than 500 or advertise an unavailable operation.
        assert registry["contacts"] == {
            "available": False,
            "readable": False,
            "visible_contacts": 0,
        }
        assert "contacts.search" not in registry["operations"]

        encoded = response.text.lower()
        for forbidden in ("api_key", "credential_suffix", "base_url", "source_path", '"path"', "instructions"):
            assert forbidden not in encoded, forbidden

print("HomeServer v0.33 scoped capability registry regression passed")
