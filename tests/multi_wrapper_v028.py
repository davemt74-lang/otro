from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

CONNECTOR = ROOT_DIR / "connectors" / "microgifter" / "HomeServerRemoteClient.php"
connector_text = CONNECTOR.read_text(encoding="utf-8")
assert "final class MicrogifterHomeServerRemoteClient" in connector_text
assert "'app_key'=>'microgifter'" in connector_text
assert "'app_name'=>'Microgifter'" in connector_text
assert "function appScope()" in connector_text
assert "'tools.list'" in connector_text
for forbidden in ["file_put_contents", "session_start", "setcookie", "$_SESSION", "error_log("]:
    assert forbidden not in connector_text, f"connector must not persist/log credentials via {forbidden}"
assert "CURLOPT_FOLLOWLOCATION=>false" in connector_text
assert "HomeServer relay must use HTTPS except on loopback" in connector_text

with tempfile.TemporaryDirectory(prefix="homeserver-multi-wrapper-v028-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import app_scopes  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200

        capabilities = client.get("/api/v1/capabilities").json()
        assert "app.scopes.v1" in capabilities["features"]

        # One private HomeServer, two wrappers, deliberately overlapping coarse
        # permissions, and separate owner-controlled resource boundaries.
        assert client.post(
            "/api/v1/control/memory",
            json={"memory_key": "vp3:project", "content": "VP3 private project context", "importance": 0.9},
        ).status_code == 200
        assert client.post(
            "/api/v1/control/memory",
            json={"memory_key": "microgifter:campaign", "content": "Microgifter private campaign context", "importance": 0.9},
        ).status_code == 200
        assert client.post(
            "/api/v1/control/knowledge",
            json={"title": "VP3 private note", "kind": "note", "content": "Shared test phrase VP3 knowledge"},
        ).status_code == 200
        assert client.post(
            "/api/v1/control/knowledge",
            json={"title": "Microgifter merchant document", "kind": "document", "content": "Shared test phrase merchant knowledge"},
        ).status_code == 200

        permissions = [
            "agent.chat",
            "memory.read",
            "memory.write",
            "knowledge.search",
            "contacts.read",
            "tools.execute",
            "plugins.read",
        ]

        def pair(app_key: str, app_name: str) -> tuple[dict, dict]:
            request = client.post(
                "/api/v1/pairing/request",
                json={"app_key": app_key, "app_name": app_name, "permissions": permissions},
            ).json()
            approved = client.post("/api/v1/pairing/approve", json={"code": request["code"]})
            assert approved.status_code == 200, approved.text
            return request, {"Authorization": f"Bearer {request['claim_token']}"}

        vp3_pairing, vp3_auth = pair("vp3", "VP3")
        microgifter_pairing, microgifter_auth = pair("microgifter", "Microgifter")

        apps = client.get("/api/v1/control/apps").json()["apps"]
        vp3_id = next(item["id"] for item in apps if item["app_key"] == "vp3")
        microgifter_id = next(item["id"] for item in apps if item["app_key"] == "microgifter")
        assert vp3_id != microgifter_id

        vp3_scope = {
            "cloud_allowed": False,
            "memory_key_prefixes": ["vp3:"],
            "knowledge_kinds": ["note"],
            "tool_names": ["memory.list", "knowledge.search"],
            "plugin_keys": ["vp3.private"],
        }
        microgifter_scope = {
            "cloud_allowed": True,
            "memory_key_prefixes": ["microgifter:"],
            "knowledge_kinds": ["document"],
            "tool_names": ["contacts.search"],
            "plugin_keys": ["microgifter.private"],
        }
        assert client.put(f"/api/v1/control/apps/{vp3_id}/scope", json=vp3_scope).status_code == 200
        assert client.put(f"/api/v1/control/apps/{microgifter_id}/scope", json=microgifter_scope).status_code == 200

        vp3_me = client.get("/api/v1/me", headers=vp3_auth).json()
        microgifter_me = client.get("/api/v1/me", headers=microgifter_auth).json()
        assert vp3_me["app_key"] == "vp3"
        assert microgifter_me["app_key"] == "microgifter"
        assert vp3_me["scope"] == vp3_scope
        assert microgifter_me["scope"] == microgifter_scope

        # The authenticated wrapper handshake reports each caller's own scope.
        vp3_tools_payload = client.get("/api/v1/tools", headers=vp3_auth).json()
        microgifter_tools_payload = client.get("/api/v1/tools", headers=microgifter_auth).json()
        assert vp3_tools_payload["app"] == "vp3"
        assert microgifter_tools_payload["app"] == "microgifter"
        assert vp3_tools_payload["app_scope"] == vp3_scope
        assert microgifter_tools_payload["app_scope"] == microgifter_scope
        assert {item["key"] for item in vp3_tools_payload["items"]} == {"memory.list", "knowledge.search"}
        assert {item["key"] for item in microgifter_tools_payload["items"]} == {"contacts.search"}

        vp3_memory = client.get("/api/v1/memory", headers=vp3_auth).json()["items"]
        microgifter_memory = client.get("/api/v1/memory", headers=microgifter_auth).json()["items"]
        assert [item["memory_key"] for item in vp3_memory] == ["vp3:project"]
        assert [item["memory_key"] for item in microgifter_memory] == ["microgifter:campaign"]

        vp3_knowledge = client.get("/api/v1/knowledge?q=Shared", headers=vp3_auth).json()["items"]
        microgifter_knowledge = client.get("/api/v1/knowledge?q=Shared", headers=microgifter_auth).json()["items"]
        assert vp3_knowledge and all(item["kind"] == "note" for item in vp3_knowledge)
        assert microgifter_knowledge and all(item["kind"] == "document" for item in microgifter_knowledge)

        # Both wrappers have memory.write at the coarse permission layer, but
        # neither may cross the other wrapper's Memory namespace.
        assert client.post(
            "/api/v1/memory",
            headers=vp3_auth,
            json={"memory_key": "microgifter:blocked", "content": "must not cross"},
        ).status_code == 403
        assert client.post(
            "/api/v1/memory",
            headers=microgifter_auth,
            json={"memory_key": "vp3:blocked", "content": "must not cross"},
        ).status_code == 403
        assert client.post(
            "/api/v1/memory",
            headers=vp3_auth,
            json={"memory_key": "vp3:allowed", "content": "VP3 allowed"},
        ).status_code == 200
        assert client.post(
            "/api/v1/memory",
            headers=microgifter_auth,
            json={"memory_key": "microgifter:allowed", "content": "Microgifter allowed"},
        ).status_code == 200

        # Scope helpers resolve independently from authenticated app identity.
        assert app_scopes.get_scope_for_source("app:vp3") == vp3_scope
        assert app_scopes.get_scope_for_source("app:microgifter") == microgifter_scope
        assert app_scopes.get_scope_for_source("app:vp3")["cloud_allowed"] is False
        assert app_scopes.get_scope_for_source("app:microgifter")["cloud_allowed"] is True

        # Re-pairing Microgifter rotates only its credential and preserves its
        # scope. VP3 remains independently paired and scoped.
        replacement = client.post(
            "/api/v1/pairing/request",
            json={"app_key": "microgifter", "app_name": "Microgifter", "permissions": permissions},
        ).json()
        assert client.post("/api/v1/pairing/approve", json={"code": replacement["code"]}).status_code == 200
        replacement_auth = {"Authorization": f"Bearer {replacement['claim_token']}"}
        assert client.get("/api/v1/me", headers=replacement_auth).json()["scope"] == microgifter_scope
        assert client.get("/api/v1/me", headers=microgifter_auth).status_code == 401
        assert client.get("/api/v1/me", headers=vp3_auth).json()["scope"] == vp3_scope

        with db() as connection:
            rows = connection.execute(
                "SELECT p.app_key, s.cloud_allowed, s.memory_key_prefixes, s.tool_names "
                "FROM paired_apps p JOIN app_capability_scopes s ON s.paired_app_id=p.id "
                "WHERE p.app_key IN ('vp3','microgifter') ORDER BY p.app_key"
            ).fetchall()
            assert len(rows) == 2
            stored = {row["app_key"]: row for row in rows}
            assert stored["vp3"]["cloud_allowed"] == 0
            assert stored["microgifter"]["cloud_allowed"] == 1

            # Scope audit metadata records counts/flags, not private scope values.
            audits = connection.execute(
                "SELECT metadata_json FROM activity_log WHERE action='app.scope' ORDER BY id DESC LIMIT 2"
            ).fetchall()
            assert len(audits) == 2
            audit_text = "\n".join(row["metadata_json"] for row in audits)
            for private_value in ["vp3:", "microgifter:", "vp3.private", "microgifter.private"]:
                assert private_value not in audit_text

print("HomeServer v0.28 VP3 + Microgifter multi-wrapper isolation proof passed")
