from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-update-center-v430-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.database import db
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import (
        homeserver_app_agent,
        homeserver_app_prebuilt,
        homeserver_app_update_center,
        tools,
    )
    from app.services.tasks import scheduler as task_scheduler

    with TestClient(app) as client:
        task_scheduler.stop()

        assert client.get("/api/v1/control/homeserver-apps/update-center").status_code==401
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        center=client.get("/api/v1/control/homeserver-apps/update-center")
        assert center.status_code==200,center.text
        state=center.json()
        assert state["contract"]=="vp3.app.update-center.v1"
        assert state["automatic_updates"] is False
        assert state["app_store"] is False
        assert state["counts"]["available"]>0

        review_response=client.get("/api/v1/control/homeserver-apps/update-center/vp3.notes")
        assert review_response.status_code==200,review_response.text
        review=review_response.json()["review"]
        assert review["recommended_action"]=="install"
        assert review["installed"] is False
        assert review["available_version"]
        assert review["integrity"]["trust"]=="embedded_vp3"
        assert len(review["integrity"]["package_sha256"])==64
        assert review["release_notes"]
        assert review["automatic_update"] is False
        assert review["owner_approval_required"] is True
        assert review["permissions"]["new_permissions_default_denied"] is True
        assert review["hosting"]["bindings_preserved_across_release"] is True
        assert review["agent_control"]["governed_write_actions"] is True

        # Reviewed installs are pinned to both version and package hash.
        stale_version=client.post(
            "/api/v1/control/homeserver-apps/update-center/vp3.notes/apply",
            json={"expected_version":"0.0.0","expected_sha256":review["integrity"]["package_sha256"]},
        )
        assert stale_version.status_code==409,stale_version.text
        stale_hash=client.post(
            "/api/v1/control/homeserver-apps/update-center/vp3.notes/apply",
            json={"expected_version":review["available_version"],"expected_sha256":"0"*64},
        )
        assert stale_hash.status_code==409,stale_hash.text

        applied=client.post(
            "/api/v1/control/homeserver-apps/update-center/vp3.notes/apply",
            json={
                "expected_version":review["available_version"],
                "expected_sha256":review["integrity"]["package_sha256"],
            },
        )
        assert applied.status_code==200,applied.text
        installed_review=applied.json()["review"]
        assert installed_review["installed"] is True
        assert installed_review["installed_version"]==review["available_version"]
        assert installed_review["recommended_action"] in {"current","current_with_rollback"}

        # Simulate an older installed registry state so the embedded current package is an update.
        with db() as connection:
            connection.execute(
                "UPDATE homeserver_apps SET installed_version='0.9.0' WHERE app_key='vp3.notes'"
            )

        update_review=client.get("/api/v1/control/homeserver-apps/update-center/vp3.notes")
        assert update_review.status_code==200,update_review.text
        update=update_review.json()["review"]
        assert update["recommended_action"]=="update"
        assert update["catalog_update_available"] is True
        assert update["installed_version"]=="0.9.0"
        assert update["available_version"]==review["available_version"]

        # Package metadata can be reviewed without exposing package contents.
        serialized=json.dumps(update,ensure_ascii=False)
        assert str(Path(data_dir)) not in serialized
        assert "package_sha256" in serialized
        assert "content/" not in serialized
        assert "automatic_update" in serialized

        reapplied=client.post(
            "/api/v1/control/homeserver-apps/update-center/vp3.notes/apply",
            json={
                "expected_version":update["available_version"],
                "expected_sha256":update["integrity"]["package_sha256"],
            },
        )
        assert reapplied.status_code==200,reapplied.text
        current=reapplied.json()["review"]
        assert current["installed_version"]==update["available_version"]
        assert current["rollback"]["available"] is True
        assert current["rollback"]["safe"] is True

        # Rollback remains the canonical release-engine path.
        rollback=client.post("/api/v1/control/homeserver-apps/vp3.notes/rollback")
        assert rollback.status_code==200,rollback.text

        # Agent Brain sees update/install/recovery attention but cannot auto-apply.
        brain=client.get("/api/v1/control/homeserver-apps/update-center/brain-context")
        assert brain.status_code==200,brain.text
        brain_payload=brain.json()
        assert brain_payload["contract"]=="vp3.app.update-center.brain-context.v1"
        assert brain_payload["governance"]["automatic_updates"] is False
        assert brain_payload["governance"]["owner_approval_required"] is True
        assert brain_payload["governance"]["prebuilt_apply_version_pinned"] is True
        assert brain_payload["governance"]["prebuilt_apply_sha256_pinned"] is True

        fragment=homeserver_app_update_center.agent_context_fragment("Are any apps out of date?",1500)
        assert "HomeServer App Update Center" in fragment
        assert "automatic_updates=false" in fragment
        assert "owner_approval_required=true" in fragment

        # Agent Chat gets read-only review tools; non-owner apps cannot use them.
        owner_tools={row["key"]:row for row in tools.list_tools(owner=True)}
        assert owner_tools["apps.update-center"]["available"] is True
        assert owner_tools["apps.update.review"]["available"] is True
        app_tools={row["key"]:row for row in tools.list_tools({"tools.execute","apps.read"},owner=False)}
        assert app_tools["apps.update-center"]["available"] is False
        assert app_tools["apps.update.review"]["available"] is False
        assert "owner.control" in app_tools["apps.update-center"]["missing_permissions"]

        center_tool=tools.execute_tool("owner","apps.update-center",{},set(),owner=True)
        assert center_tool["status"]=="completed"
        assert center_tool["result"]["automatic_updates"] is False
        review_tool=tools.execute_tool("owner","apps.update.review",{"app_key":"vp3.notes"},set(),owner=True)
        assert review_tool["status"]=="completed"
        assert review_tool["result"]["app_key"]=="vp3.notes"
        assert review_tool["result"]["owner_approval_required"] is True

        # Existing Agent write contract is still the execution authority.
        capability=homeserver_app_agent.public_capability()
        assert "apps.prebuilt.install" in capability["write_actions"]
        assert "apps.rollback" in capability["write_actions"]
        assert "apps.recover" in capability["write_actions"]
        assert capability["write_actions_require_owner_approval"] is True

        app_cap=client.get("/api/v1/control/homeserver-apps/capability")
        assert app_cap.status_code==200,app_cap.text
        update_cap=app_cap.json()["update_center"]
        assert update_cap["unified_update_center"] is True
        assert update_cap["version_pinned_apply"] is True
        assert update_cap["sha256_pinned_apply"] is True
        assert update_cap["automatic_updates"] is False
        assert update_cap["app_store"] is False
        assert update_cap["agent_brain_context"] is True
        assert update_cap["agent_chat_context"] is True

        ui=(ROOT/"ui"/"homeserver-apps.js").read_text(encoding="utf-8")
        css=(ROOT/"ui"/"homeserver-apps.css").read_text(encoding="utf-8")
        assert "UPDATE CENTER" in ui
        assert "Review & Install" in ui
        assert "Review Update" in ui
        assert "data-hs-update-apply" in ui
        assert "/update-center/" in ui
        assert "expected_version" in ui
        assert "expected_sha256" in ui
        assert "data-hs-prebuilt-install" not in ui
        assert ".hs-update-center" in css

print("HomeServer Section 29 App Install Update Center: PASS")
