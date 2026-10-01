from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-apps-v100-") as data_dir, tempfile.TemporaryDirectory(prefix="homeserver-app-sdk-") as sdk_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import homeserver_apps  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    with TestClient(app) as client:
        scheduler.stop()
        assert client.get("/api/v1/control/homeserver-apps").status_code in {401,403}
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        cap=client.get("/api/v1/control/homeserver-apps/capability")
        assert cap.status_code==200,cap.text
        cp=cap.json()
        assert cp["contract"]=="vp3.homeserver-apps.registry.v1"
        assert cp["system_apps"] is True
        assert cp["user_apps"] is True
        assert cp["app_store"]=="future"
        assert "app_store" not in cp["user_app_sources"]

        created=client.post("/api/v1/control/homeserver-apps",json={
            "app_key":"garage.inventory",
            "name":"Garage Inventory",
            "source_type":"agent_builder",
            "metadata":{"sdk_version":"1.0"},
        })
        assert created.status_code==200,created.text
        created_payload=created.json()
        item=created_payload["app"]
        assert created_payload["sdk"]["sdk_version"]=="1.2"
        managed_project=Path(data_dir)/"apps"/"garage.inventory"
        assert (managed_project/"vp3-app.json").is_file()
        assert (managed_project/"assets"/"vp3-sdk.js").is_file()
        assert (managed_project/"agent"/"actions.json").is_file()
        assert item["app_class"]=="user"
        assert item["source_type"]=="agent_builder"
        assert item["lifecycle_state"]=="draft"
        assert item["protected_system_app"] is False

        duplicate=client.post("/api/v1/control/homeserver-apps",json={
            "app_key":"garage.inventory","name":"Duplicate"
        })
        assert duplicate.status_code==409

        bad=client.post("/api/v1/control/homeserver-apps",json={
            "app_key":"bad app","name":"Bad"
        })
        assert bad.status_code==400

        installing=client.post("/api/v1/control/homeserver-apps/garage.inventory/lifecycle",json={"state":"installing"})
        assert installing.status_code==200,installing.text
        installed=client.post("/api/v1/control/homeserver-apps/garage.inventory/lifecycle",json={"state":"installed"})
        assert installed.status_code==200,installed.text
        running=client.post("/api/v1/control/homeserver-apps/garage.inventory/lifecycle",json={"state":"running"})
        assert running.status_code==200,running.text

        invalid=client.post("/api/v1/control/homeserver-apps/garage.inventory/lifecycle",json={"state":"draft"})
        assert invalid.status_code==409

        detail=client.get("/api/v1/control/homeserver-apps/garage.inventory")
        assert detail.status_code==200
        history=detail.json()["history"]
        assert history[0]["event_type"]=="app.lifecycle.changed"
        assert any(event["event_type"]=="app.registered" for event in history)

        listing=client.get("/api/v1/control/homeserver-apps").json()
        assert listing["counts"]["user"]>=1

    # SDK must generate a working package skeleton rather than a blank project.
    proc=subprocess.run(
        [sys.executable,str(ROOT/"sdk"/"homeserver-app"/"create_app.py"),"garage.sdk","Garage SDK App","--output",sdk_dir],
        check=True,capture_output=True,text=True,
    )
    project=Path(proc.stdout.strip())
    manifest=json.loads((project/"vp3-app.json").read_text(encoding="utf-8"))
    assert manifest["contract"]=="vp3.app.package.v1"
    assert manifest["sdk_version"]=="1.2"
    assert manifest["routes"]=={"local":True,"private_remote":False,"public":False}
    assert (project/"index.html").is_file()
    assert (project/"assets"/"vp3-sdk.js").is_file()
    assert (project/"settings.schema.json").is_file()
    assert (project/"agent"/"actions.json").is_file()
    assert (project/"database"/"migrations"/"README.md").is_file()

    with __import__("sqlite3").connect(Path(data_dir)/"homeserver.db") as connection:
        version=connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        assert version==62

print("HomeServer Apps V1 Section 1 registry lifecycle and SDK foundation passed")
