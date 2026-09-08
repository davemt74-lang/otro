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


with tempfile.TemporaryDirectory(prefix="homeserver-app-scopes-v026-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import app_scopes, plugins  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200

        capabilities = client.get("/api/v1/capabilities").json()
        assert "app.scopes.v1" in capabilities["features"]

        # Shared private resources that two equally-permissioned wrappers must see differently.
        assert client.post("/api/v1/control/memory", json={"memory_key": "vp3:project", "content": "VP3-only memory", "importance": 0.9}).status_code == 200
        assert client.post("/api/v1/control/memory", json={"memory_key": "other:project", "content": "Other-wrapper memory", "importance": 0.9}).status_code == 200
        assert client.post("/api/v1/control/knowledge", json={"title": "VP3 note", "kind": "note", "content": "Scoped note knowledge"}).status_code == 200
        assert client.post("/api/v1/control/knowledge", json={"title": "Private document", "kind": "document", "content": "Scoped document knowledge"}).status_code == 200

        permissions = ["agent.chat", "memory.read", "memory.write", "knowledge.search", "contacts.read", "tools.execute", "plugins.read"]

        def pair(app_key: str, app_name: str) -> tuple[dict, dict]:
            request = client.post("/api/v1/pairing/request", json={"app_key": app_key, "app_name": app_name, "permissions": permissions}).json()
            approved = client.post("/api/v1/pairing/approve", json={"code": request["code"]})
            assert approved.status_code == 200, approved.text
            return request, {"Authorization": f"Bearer {request['claim_token']}"}

        vp3_pairing, vp3_auth = pair("vp3-scope-test", "VP3 Scope Test")
        other_pairing, other_auth = pair("other-scope-test", "Other Scope Test")

        apps = client.get("/api/v1/control/apps").json()["apps"]
        vp3_id = next(item["id"] for item in apps if item["app_key"] == "vp3-scope-test")
        other_id = next(item["id"] for item in apps if item["app_key"] == "other-scope-test")

        vp3_scope = {
            "cloud_allowed": False,
            "memory_key_prefixes": ["vp3:"],
            "knowledge_kinds": ["note"],
            "tool_names": ["memory.list", "knowledge.search"],
            "plugin_keys": ["vp3.private"],
        }
        other_scope = {
            "cloud_allowed": True,
            "memory_key_prefixes": ["other:"],
            "knowledge_kinds": ["document"],
            "tool_names": ["contacts.search"],
            "plugin_keys": ["other.private"],
        }
        assert client.put(f"/api/v1/control/apps/{vp3_id}/scope", json=vp3_scope).status_code == 200
        assert client.put(f"/api/v1/control/apps/{other_id}/scope", json=other_scope).status_code == 200

        vp3_me = client.get("/api/v1/me", headers=vp3_auth).json()
        other_me = client.get("/api/v1/me", headers=other_auth).json()
        assert vp3_me["scope"] == vp3_scope
        assert other_me["scope"] == other_scope

        vp3_memory = client.get("/api/v1/memory", headers=vp3_auth).json()["items"]
        other_memory = client.get("/api/v1/memory", headers=other_auth).json()["items"]
        assert [item["memory_key"] for item in vp3_memory] == ["vp3:project"]
        assert [item["memory_key"] for item in other_memory] == ["other:project"]

        vp3_knowledge = client.get("/api/v1/knowledge?q=scoped", headers=vp3_auth).json()["items"]
        other_knowledge = client.get("/api/v1/knowledge?q=scoped", headers=other_auth).json()["items"]
        assert vp3_knowledge and all(item["kind"] == "note" for item in vp3_knowledge)
        assert other_knowledge and all(item["kind"] == "document" for item in other_knowledge)

        vp3_tools = client.get("/api/v1/tools", headers=vp3_auth).json()["items"]
        other_tools = client.get("/api/v1/tools", headers=other_auth).json()["items"]
        assert {item["key"] for item in vp3_tools} == {"memory.list", "knowledge.search"}
        assert {item["key"] for item in other_tools} == {"contacts.search"}

        allowed_tool = client.post("/api/v1/tools/memory.list/execute", headers=vp3_auth, json={"arguments": {"limit": 20}})
        assert allowed_tool.status_code == 200, allowed_tool.text
        assert [item["memory_key"] for item in allowed_tool.json()["result"]["items"]] == ["vp3:project"]
        denied_tool = client.post("/api/v1/tools/contacts.search/execute", headers=vp3_auth, json={"arguments": {"query": "test"}})
        assert denied_tool.status_code == 403

        assert client.post("/api/v1/memory", headers=vp3_auth, json={"memory_key": "other:blocked", "content": "must not write"}).status_code == 403
        assert client.post("/api/v1/memory", headers=vp3_auth, json={"memory_key": "vp3:allowed", "content": "allowed write"}).status_code == 200

        # The legacy model tools cannot enforce sub-resource filters internally,
        # so scoped Memory/Knowledge permissions are withheld from the model
        # rather than risking access broader than the context bundle.
        vp3_model_permissions = app_scopes.scoped_tool_permissions(vp3_scope, set(permissions))
        other_model_permissions = app_scopes.scoped_tool_permissions(other_scope, set(permissions))
        assert "memory.read" not in vp3_model_permissions
        assert "memory.write" not in vp3_model_permissions
        assert "knowledge.search" not in vp3_model_permissions
        assert "contacts.read" not in vp3_model_permissions
        assert "contacts.read" in other_model_permissions
        assert app_scopes.PLUGIN_SCOPE_SENTINEL in vp3_model_permissions
        assert f"{app_scopes.PLUGIN_SCOPE_PREFIX}vp3.private" in vp3_model_permissions

        # Plugin model tools are filtered before they are exposed and the same
        # filter is reused by execution lookup.
        plugin_manifest = lambda key: {
            "plugin_key": key,
            "name": key,
            "version": "1.0",
            "tools": [{
                "key": "read",
                "name": "Read",
                "description": "Scoped test plugin",
                "mode": "read",
                "required_permissions": [],
                "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
                "handler_key": "read",
            }],
        }
        plugins.register_plugin(plugin_manifest("vp3.private"), trusted=True)
        plugins.register_plugin(plugin_manifest("other.private"), trusted=True)
        plugins.register_tool_handler("vp3.private", "read", lambda arguments, context: {"source": "vp3"})
        plugins.register_tool_handler("other.private", "read", lambda arguments, context: {"source": "other"})
        vp3_plugin_tools = plugins.available_model_tools(vp3_model_permissions)
        other_plugin_tools = plugins.available_model_tools(other_model_permissions)
        assert {item["plugin_key"] for item in vp3_plugin_tools} == {"vp3.private"}
        assert {item["plugin_key"] for item in other_plugin_tools} == {"other.private"}

        # A scope-level no-cloud rule wins over the wrapper's normal chat defaults.
        # With no local Ollama provider configured, the request must fail closed
        # instead of selecting an external/cloud provider.
        private_chat = client.post("/api/v1/chat", headers=vp3_auth, json={"message": "Scope should force local-only compute"})
        assert private_chat.status_code == 409, private_chat.text
        assert "local Ollama" in private_chat.text

        # Re-pairing rotates credentials and permissions but must not broaden the
        # owner's existing resource scope.
        replacement = client.post("/api/v1/pairing/request", json={"app_key": "vp3-scope-test", "app_name": "VP3 Scope Test Repaired", "permissions": permissions}).json()
        assert client.post("/api/v1/pairing/approve", json={"code": replacement["code"]}).status_code == 200
        replacement_auth = {"Authorization": f"Bearer {replacement['claim_token']}"}
        assert client.get("/api/v1/me", headers=replacement_auth).json()["scope"] == vp3_scope
        assert client.get("/api/v1/me", headers=vp3_auth).status_code == 401

        # Helpers stay fail-closed for narrowed scopes while empty lists remain
        # backward-compatible and unrestricted within coarse permissions.
        assert app_scopes.memory_key_allowed(vp3_scope, "vp3:any")
        assert not app_scopes.memory_key_allowed(vp3_scope, "other:any")
        assert app_scopes.knowledge_kind_allowed(vp3_scope, "note")
        assert not app_scopes.knowledge_kind_allowed(vp3_scope, "document")
        assert app_scopes.tool_allowed(vp3_scope, "memory.list")
        assert not app_scopes.tool_allowed(vp3_scope, "contacts.search")
        assert app_scopes.plugin_allowed(vp3_scope, "vp3.private")
        assert not app_scopes.plugin_allowed(vp3_scope, "other.private")
        assert app_scopes.tool_allowed(app_scopes.DEFAULT_SCOPE, "anything")
        assert app_scopes.get_scope_for_source("app:missing-wrapper") == app_scopes.LOCKED_SCOPE

        with db() as connection:
            assert connection.execute("SELECT COUNT(*) FROM app_capability_scopes").fetchone()[0] == 2
            stored = connection.execute("SELECT cloud_allowed FROM app_capability_scopes WHERE paired_app_id=?", (vp3_id,)).fetchone()
            assert stored is not None and stored["cloud_allowed"] == 0
            audit = connection.execute("SELECT metadata_json FROM activity_log WHERE action='app.scope' AND resource_key=? ORDER BY id DESC LIMIT 1", (str(vp3_id),)).fetchone()
            assert audit is not None
            assert "vp3:" not in audit["metadata_json"]

print("HomeServer v0.26 cross-wrapper capability scope regression passed")
