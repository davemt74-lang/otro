from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-app-manager-v270-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import homeserver_app_manager, homeserver_app_prebuilt
    from app.services.tasks import scheduler

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        catalog=client.get("/api/v1/control/homeserver-apps/catalog/prebuilt")
        assert catalog.status_code==200,catalog.text
        packages=catalog.json()["packages"]
        keys={row["key"] for row in packages}
        expected={
            "vp3.notes","vp3.inventory","vp3.checklists","vp3.contacts","vp3.tasks",
            "vp3.calendar","vp3.files","vp3.media","vp3.home","vp3.tracky",
            "vp3.crm","vp3.campaigns","vp3.rewards","vp3.website",
        }
        assert expected.issubset(keys)
        assert catalog.json()["app_store"] is False

        manager=client.get("/api/v1/control/homeserver-apps/manager")
        assert manager.status_code==200,manager.text
        state=manager.json()
        assert state["contract"]=="vp3.app.manager.v1"
        assert state["first_party_library"] is True
        assert state["app_store"] is False
        assert state["counts"]["available"]>=len(expected)
        by_key={row["app_key"]:row for row in state["items"]}
        assert by_key["vp3.tracky"]["available"] is True
        assert by_key["vp3.tracky"]["installed"] is False
        assert by_key["vp3.tracky"]["actions"]["install"] is True
        assert by_key["vp3.website"]["category"]=="Hosting"

        # Install a system app through the canonical prebuilt/package runtime.
        install=client.post("/api/v1/control/homeserver-apps/catalog/prebuilt/vp3.tasks/install")
        assert install.status_code==200,install.text

        manager=client.get("/api/v1/control/homeserver-apps/manager").json()
        by_key={row["app_key"]:row for row in manager["items"]}
        tasks=by_key["vp3.tasks"]
        assert tasks["installed"] is True
        assert tasks["lifecycle_state"]=="running"
        assert tasks["actions"]["open"] is True
        assert tasks["permissions"]["declared_count"]==1
        assert tasks["permissions"]["allowed_count"]==0
        assert tasks["resources"]["storage_used_bytes"]>=0
        assert tasks["releases"]["active_release_id"]
        assert manager["counts"]["installed"]>=1
        assert manager["counts"]["running"]>=1

        # User-created apps appear in the same manager instead of another registry.
        created=client.post("/api/v1/control/homeserver-apps",json={
            "app_key":"my.dashboard","name":"My Dashboard","runtime":"static",
            "source_type":"user_created","permissions":[],
        })
        assert created.status_code==200,created.text
        manager=client.get("/api/v1/control/homeserver-apps/manager").json()
        by_key={row["app_key"]:row for row in manager["items"]}
        assert by_key["my.dashboard"]["app_class"]=="user"
        assert by_key["my.dashboard"]["available"] is False
        assert manager["counts"]["user"]>=1

        item=client.get("/api/v1/control/homeserver-apps/manager/vp3.tasks")
        assert item.status_code==200,item.text
        assert item.json()["app"]["app_key"]=="vp3.tasks"

        cap=client.get("/api/v1/control/homeserver-apps/capability").json()
        assert cap["manager"]["unified_inventory"] is True
        assert cap["manager"]["vp3_system_library"] is True
        assert cap["manager"]["canonical_permission_engine"] if "canonical_permission_engine" in cap["manager"] else True
        assert cap["prebuilt"]["package_count"]>=len(expected)
        assert cap["prebuilt"]["app_store"] is False

        direct=homeserver_app_manager.inventory()
        assert direct["canonical_registry"] is True
        assert direct["canonical_release_engine"] is True
        assert direct["canonical_permission_engine"] is True
        assert direct["canonical_hosting_engine"] is True
        assert direct["canonical_distribution_engine"] is True

print("HomeServer System Apps Section 13 Unified App Manager and System Apps Library: PASS")
