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


with tempfile.TemporaryDirectory(prefix="homeserver-connected-apps-v029-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    with TestClient(app) as client:
        scheduler.stop()
        owner = client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN})
        assert owner.status_code == 200, owner.text

        capabilities = client.get("/api/v1/capabilities").json()
        assert capabilities["pairing_protocol"] == "claim-v1"
        assert "owner.connected_apps.v1" in capabilities["features"]

        initial_permissions = ["agent.chat", "memory.read", "memory.write"]
        first = client.post(
            "/api/v1/pairing/request",
            json={"app_key": "vp3-control-test", "app_name": "VP3 Control Test", "permissions": initial_permissions},
        ).json()
        approved = client.post("/api/v1/pairing/approve", json={"code": first["code"]})
        assert approved.status_code == 200, approved.text
        old_auth = {"Authorization": f"Bearer {first['claim_token']}"}
        assert client.get("/api/v1/me", headers=old_auth).status_code == 200

        dashboard = client.get("/api/v1/control/connected-apps")
        assert dashboard.status_code == 200, dashboard.text
        payload = dashboard.json()
        app_row = next(item for item in payload["apps"] if item["app_key"] == "vp3-control-test")
        app_id = app_row["id"]
        assert payload["counts"]["active"] == 1
        assert app_row["allowed_permission_count"] == len(initial_permissions)
        assert "token" not in json.dumps(payload).lower()

        scope = {
            "cloud_allowed": False,
            "memory_key_prefixes": ["vp3-private:"],
            "knowledge_kinds": ["note"],
            "tool_names": ["memory.list"],
            "plugin_keys": ["vp3.private"],
        }
        scoped = client.put(f"/api/v1/control/apps/{app_id}/scope", json=scope)
        assert scoped.status_code == 200, scoped.text

        paused = client.patch(f"/api/v1/control/apps/{app_id}", json={"status": "paused"})
        assert paused.status_code == 200
        assert client.get("/api/v1/control/connected-apps").json()["apps"][0]["scope"] == scope
        active = client.patch(f"/api/v1/control/apps/{app_id}", json={"status": "active"})
        assert active.status_code == 200

        created = client.post(
            "/api/v1/memory",
            headers=old_auth,
            json={"memory_key": "vp3-private:test", "content": "PRIVATE-CONTENT-MUST-NOT-APPEAR", "importance": 0.8},
        )
        assert created.status_code == 200, created.text

        replacement_permissions = initial_permissions + ["knowledge.search"]
        replacement = client.post(
            "/api/v1/pairing/request",
            json={"app_key": "vp3-control-test", "app_name": "VP3 Control Test", "permissions": replacement_permissions},
        ).json()

        review = client.get("/api/v1/control/connected-apps").json()
        pending = next(item for item in review["pending"] if item["app_key"] == "vp3-control-test")
        assert pending["existing_app"] is True
        assert pending["requests_additional_capabilities"] is True
        assert pending["new_permissions"] == ["knowledge.search"]
        assert replacement["claim_token"] not in json.dumps(review)
        assert replacement["code"] not in json.dumps(review)

        repair = client.post(f"/api/v1/control/connected-apps/{app_id}/require-repair")
        assert repair.status_code == 200, repair.text
        assert repair.json()["status"] == "revoked"
        assert client.get("/api/v1/me", headers=old_auth).status_code == 401

        repaired = client.post("/api/v1/pairing/approve", json={"code": replacement["code"]})
        assert repaired.status_code == 200, repaired.text
        new_auth = {"Authorization": f"Bearer {replacement['claim_token']}"}
        me = client.get("/api/v1/me", headers=new_auth)
        assert me.status_code == 200, me.text
        assert me.json()["scope"] == scope
        assert "knowledge.search" in me.json()["permissions"]
        assert client.get("/api/v1/me", headers=old_auth).status_code == 401

        activity = client.get(f"/api/v1/control/connected-apps/{app_id}/activity?limit=100")
        assert activity.status_code == 200, activity.text
        activity_text = json.dumps(activity.json())
        assert "app.repair_required" in activity_text
        assert "PRIVATE-CONTENT-MUST-NOT-APPEAR" not in activity_text
        assert "vp3-private:" not in activity_text
        assert replacement["claim_token"] not in activity_text
        assert "metadata" not in activity_text
        assert "resource_key" not in activity_text

        final = client.get("/api/v1/control/connected-apps").json()
        final_app = next(item for item in final["apps"] if item["app_key"] == "vp3-control-test")
        assert final_app["status"] == "active"
        assert final_app["scope"] == scope
        assert final_app["scope_summary"]["cloud_allowed"] is False
        assert final_app["scope_summary"]["memory_prefix_count"] == 1
        assert final_app["scope_summary"]["knowledge_kind_count"] == 1
        assert final_app["scope_summary"]["tool_count"] == 1
        assert final_app["scope_summary"]["plugin_count"] == 1

ui_script = (ROOT_DIR / "ui" / "connected-apps-v029.js").read_text(encoding="utf-8")
ui_css = (ROOT_DIR / "ui" / "connected-apps-v029.css").read_text(encoding="utf-8")
doc = (ROOT_DIR / "docs" / "wrapper-onboarding-v029.md").read_text(encoding="utf-8")
assert "/api/v1/control/connected-apps" in ui_script
assert "require-repair" in ui_script
assert "confirm(" in ui_script
assert "data-v029-permission" in ui_script
assert "data-app-permission" not in ui_script
assert "token_hash" not in ui_script
assert "PRIVATE-CONTENT" not in ui_script
assert "@media" in ui_css
assert "claim-v1" in doc
assert "app_key" in doc and "app_name" in doc
assert "New HomeServer capabilities are not automatically granted" in doc

print("HomeServer v0.29 Connected Apps and wrapper control regression passed")
