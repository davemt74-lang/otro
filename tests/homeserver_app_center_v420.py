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

with tempfile.TemporaryDirectory(prefix="homeserver-app-center-v420-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.database import db
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import homeserver_app_center, homeserver_app_prebuilt
    from app.services.tasks import scheduler as task_scheduler

    with TestClient(app) as client:
        task_scheduler.stop()
        assert client.get("/api/v1/control/homeserver-apps/app-center").status_code==401
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        initial=client.get("/api/v1/control/homeserver-apps/app-center")
        assert initial.status_code==200,initial.text
        payload=initial.json()
        assert payload["contract"]=="vp3.homeserver.app-center.v1"
        assert payload["public_app_store"] is False
        assert payload["first_party_catalog"] is True
        assert payload["count"]>=1
        assert payload["counts"]["discover"]>=1
        assert payload["categories"]==sorted(payload["categories"],key=str.lower)

        available=next(row for row in payload["items"] if row["available"] and not row["installed"])
        app_key=available["app_key"]
        assert available["readiness"]["operation"]=="install"
        assert available["readiness"]["ready"] is True
        assert len(available["readiness"]["package_sha256"])==64
        assert available["release_notes"] is not None
        assert "local" in available["deployment_modes"]

        search=client.get("/api/v1/control/homeserver-apps/app-center",params={"q":available["name"]})
        assert search.status_code==200
        assert any(row["app_key"]==app_key for row in search.json()["items"])

        by_category=client.get(
            "/api/v1/control/homeserver-apps/app-center",
            params={"category":available["category"]},
        )
        assert by_category.status_code==200
        assert by_category.json()["items"]
        assert all(row["category"]==available["category"] for row in by_category.json()["items"])

        discover=client.get("/api/v1/control/homeserver-apps/app-center",params={"view":"discover"})
        assert discover.status_code==200
        assert all(row["available"] and not row["installed"] for row in discover.json()["items"])
        assert client.get("/api/v1/control/homeserver-apps/app-center",params={"view":"not-real"}).status_code==400

        detail=client.get(f"/api/v1/control/homeserver-apps/app-center/{app_key}")
        assert detail.status_code==200,detail.text
        assert detail.json()["app"]["app_key"]==app_key

        # Canonical install path remains the existing prebuilt installer.
        installed=client.post(f"/api/v1/control/homeserver-apps/catalog/prebuilt/{app_key}/install")
        assert installed.status_code==200,installed.text
        after=client.get(f"/api/v1/control/homeserver-apps/app-center/{app_key}").json()["app"]
        assert after["installed"] is True
        assert after["readiness"]["operation"]=="none"
        assert after["update_available"] is False

        # Simulate an older installed registry version to exercise update planning
        # without inventing a second update engine.
        with db() as connection:
            connection.execute(
                "UPDATE homeserver_apps SET installed_version='0.0.1',lifecycle_state='running' WHERE app_key=?",
                (app_key,),
            )

        update_item=client.get(f"/api/v1/control/homeserver-apps/app-center/{app_key}").json()["app"]
        assert update_item["update_available"] is True
        assert update_item["readiness"]["operation"]=="update"
        assert update_item["readiness"]["ready"] is True

        update_plan=client.get("/api/v1/control/homeserver-apps/app-center/update-plan")
        assert update_plan.status_code==200,update_plan.text
        plan=update_plan.json()
        assert plan["contract"]=="vp3.homeserver.app-center.update-plan.v1"
        assert plan["batch_execution"] is False
        assert plan["execution_authority"]=="homeserver_prebuilt_installer"
        planned=next(row for row in plan["updates"] if row["app_key"]==app_key)
        assert planned["installed_version"]=="0.0.1"
        assert planned["available_version"]==update_item["available_version"]

        # Irreversible migrations are surfaced as warnings before the existing
        # installer is called; they are not hidden or automatically batch-applied.
        definition=homeserver_app_prebuilt.CATALOG[app_key]
        original_reversible=definition.get("data_migration_reversible",True)
        try:
            definition["data_migration_reversible"]=False
            warned=homeserver_app_center.item(app_key)
            assert warned["readiness"]["warnings"]
            assert warned["readiness"]["warnings"][0]["code"]=="irreversible_data_migration"
            assert warned["readiness"]["blocked"] is False
        finally:
            definition["data_migration_reversible"]=original_reversible

        updates=client.get("/api/v1/control/homeserver-apps/app-center",params={"view":"updates"}).json()["items"]
        assert updates
        assert all(row["update_available"] for row in updates)

        brain=client.get("/api/v1/control/homeserver-apps/app-center/brain-context")
        assert brain.status_code==200,brain.text
        brain_payload=brain.json()
        assert brain_payload["contract"]=="vp3.homeserver.app-center.brain-context.v1"
        assert brain_payload["governance"]["read_only_context"] is True
        assert brain_payload["governance"]["installs_execute_via_existing_prebuilt_installer"] is True
        assert brain_payload["governance"]["write_actions_require_owner_approval"] is True
        assert brain_payload["governance"]["homeserver_execution_authority"] is True
        assert brain_payload["governance"]["public_app_store"] is False
        brain_text=json.dumps(brain_payload,ensure_ascii=False)
        assert str(Path(data_dir)) not in brain_text
        assert "owner-bootstrap" not in brain_text
        assert "source_ref" not in brain_text

        capability=client.get("/api/v1/control/homeserver-apps/capability")
        assert capability.status_code==200
        cap=capability.json()["app_center"]
        assert cap["catalog_discovery"] is True
        assert cap["install_readiness"] is True
        assert cap["update_plan"] is True
        assert cap["agent_brain_context"] is True
        assert cap["batch_update_execution"] is False
        assert cap["public_app_store"] is False

        ui=(ROOT/"ui"/"homeserver-apps.js").read_text(encoding="utf-8")
        css=(ROOT/"ui"/"homeserver-apps.css").read_text(encoding="utf-8")
        assert "Install & Update Center" in ui
        assert 'id="hsAppSearch"' in ui
        assert 'id="hsAppCategory"' in ui
        assert 'data-hs-app-filter="discover"' in ui
        assert 'data-hs-app-filter="attention"' in ui
        assert "app-center/update-plan" in ui
        assert "readiness.blocked" in ui
        assert "confirm(message)" in ui
        assert ".hs-app-center-controls" in css
        assert ".hs-app-readiness.warning" in css

print("HomeServer Section 28 App Install Update Center & Catalog Experience: PASS")
