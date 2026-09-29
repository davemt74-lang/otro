from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-apps-v150-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import homeserver_app_prebuilt, homeserver_apps  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    # Embedded first-party packages must be deterministic and require no remote source.
    notes_def=homeserver_app_prebuilt.CATALOG["vp3.notes"]
    package_a=homeserver_app_prebuilt._package(notes_def)
    package_b=homeserver_app_prebuilt._package(notes_def)
    assert package_a==package_b
    assert b"http://" not in package_a and b"https://" not in package_a

    with TestClient(app) as client:
        scheduler.stop()
        assert client.get("/api/v1/control/homeserver-apps/catalog/prebuilt").status_code in {401,403}
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        catalog=client.get("/api/v1/control/homeserver-apps/catalog/prebuilt")
        assert catalog.status_code==200,catalog.text
        payload=catalog.json()
        assert payload["contract"]=="vp3.app.prebuilt-catalog.v1"
        assert payload["source"]=="embedded_vp3"
        assert payload["app_store"] is False
        assert {p["key"] for p in payload["packages"]}=={"vp3.notes","vp3.inventory","vp3.checklists"}
        assert all(len(p["package_sha256"])==64 for p in payload["packages"])
        assert all(p["installed"] is False for p in payload["packages"])

        installed=client.post("/api/v1/control/homeserver-apps/catalog/prebuilt/vp3.notes/install")
        assert installed.status_code==200,installed.text
        result=installed.json()
        assert result["changed"] is True
        assert result["release"]["source_type"]=="vp3_system"

        notes=homeserver_apps.get("vp3.notes")
        assert notes["app_class"]=="system"
        assert notes["source_type"]=="vp3_system"
        assert notes["protected_system_app"] is True
        assert notes["lifecycle_state"]=="running"
        assert notes["installed_version"]=="1.0.0"
        assert notes["metadata"]["prebuilt_app"] is True
        assert notes["metadata"]["vp3_managed"] is True
        assert notes["metadata"]["prebuilt_catalog_key"]=="vp3.notes"

        # Existing user/system control surfaces cannot take over a protected VP3 app.
        direct=client.post(
            "/api/v1/control/homeserver-apps/vp3.notes/package/install",
            files={"file":("notes.vp3app",package_a,"application/zip")},
        )
        assert direct.status_code==409,direct.text
        archive=client.post("/api/v1/control/homeserver-apps/vp3.notes/archive")
        assert archive.status_code==409
        resume=client.post("/api/v1/control/homeserver-apps/vp3.notes/resume")
        assert resume.status_code==409

        preview=client.get("/api/v1/control/homeserver-apps/vp3.notes/preview/")
        assert preview.status_code==200,preview.text
        assert "VP3 Notes" in preview.text
        assert "VP3 PREBUILT APP" in preview.text

        # Sample data is integrated but remains globally off until explicitly enabled.
        sample=client.get("/api/v1/control/homeserver-apps/vp3.notes/sample-data")
        assert sample.status_code==200
        assert sample.json()["sample_data"]["enabled"] is False
        assert sample.json()["sample_data"]["items"]==[]
        enabled=client.put("/api/v1/control/homeserver-apps/admin/sample-data",json={"enabled":True})
        assert enabled.status_code==200
        sample=client.get("/api/v1/control/homeserver-apps/vp3.notes/sample-data").json()["sample_data"]
        assert sample["enabled"] is True
        assert sample["item_count"]==1
        assert sample["items"][0]["title"]=="Welcome to VP3 Notes"

        # Reinstalling the same catalog package is idempotent.
        current=client.post("/api/v1/control/homeserver-apps/catalog/prebuilt/vp3.notes/install")
        assert current.status_code==200,current.text
        assert current.json()["changed"] is False
        assert current.json()["reason"]=="already_current"

        # A VP3 catalog version bump uses the same atomic release lineage.
        old_version=notes_def["version"]
        try:
            first_release=result["release"]["release_id"]
            notes_def["version"]="1.0.1"
            upgraded=client.post("/api/v1/control/homeserver-apps/catalog/prebuilt/vp3.notes/install")
            assert upgraded.status_code==200,upgraded.text
            upgrade=upgraded.json()
            assert upgrade["changed"] is True
            assert upgrade["release"]["version"]=="1.0.1"
            assert upgrade["release"]["previous_release_id"]==first_release
        finally:
            notes_def["version"]=old_version

        for key in ("vp3.inventory","vp3.checklists"):
            response=client.post(f"/api/v1/control/homeserver-apps/catalog/prebuilt/{key}/install")
            assert response.status_code==200,response.text
            row=homeserver_apps.get(key)
            assert row["app_class"]=="system"
            assert row["protected_system_app"] is True
            assert row["lifecycle_state"]=="running"

        listing=client.get("/api/v1/control/homeserver-apps").json()
        system_keys={item["app_key"] for item in listing["apps"] if item["app_class"]=="system"}
        assert {"vp3.notes","vp3.inventory","vp3.checklists"}.issubset(system_keys)

        cap=client.get("/api/v1/control/homeserver-apps/capability").json()["prebuilt"]
        assert cap["embedded"] is True
        assert cap["first_party_only"] is True
        assert cap["external_downloads"] is False
        assert cap["app_store"] is False
        assert cap["protected_system_apps"] is True
        assert cap["package_count"]==3

        history=client.get("/api/v1/control/homeserver-apps/vp3.notes").json()["history"]
        prebuilt_events=[event for event in history if event["event_type"]=="app.prebuilt.installed"]
        assert prebuilt_events
        assert all(event["actor_type"]=="system" and event["actor_key"]=="vp3_prebuilt" for event in prebuilt_events)

    ui=(ROOT/"ui"/"homeserver-apps.js").read_text(encoding="utf-8")
    assert "VP3 PREBUILT" in ui
    assert "VP3 Apps" in ui
    assert "data-hs-prebuilt-install" in ui
    assert "/catalog/prebuilt" in ui
    assert "meta.prebuilt_app" in ui

print("HomeServer Apps V1 Section 6 VP3 prebuilt apps: PASS")
