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

with tempfile.TemporaryDirectory(prefix="homeserver-sdk-v230-") as data_dir, tempfile.TemporaryDirectory(prefix="homeserver-sdk-cli-v230-") as cli_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import homeserver_app_packages, homeserver_app_resources, homeserver_app_security, homeserver_apps
    from app.services.tasks import scheduler

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        catalog=client.get("/api/v1/control/homeserver-apps/permissions/catalog")
        assert catalog.status_code==200,catalog.text
        catalog_payload=catalog.json()
        assert catalog_payload["contract"]=="vp3.app.permission-catalog.v1"
        assert catalog_payload["default_decision"]=="denied"

        created=client.post("/api/v1/control/homeserver-apps",json={
            "app_key":"starter.notes",
            "name":"Starter Notes",
            "runtime":"php",
            "source_type":"user_created",
            "permissions":["notifications.write","network.external"],
        })
        assert created.status_code==200,created.text
        payload=created.json()
        assert payload["sdk"]["sdk_version"]=="1.2"
        project=Path(data_dir)/"apps"/"starter.notes"
        manifest=json.loads((project/"vp3-app.json").read_text(encoding="utf-8"))
        assert manifest["sdk_version"]=="1.2"
        assert manifest["release_channel"]=="stable"
        assert manifest["data_schema_version"]=="1"
        assert manifest["data_migration_reversible"] is True
        assert manifest["permissions"]==["network.external","notifications.write"]
        assert (project/"database"/"migrations"/"README.md").is_file()
        assert (project/"agent"/"context.json").is_file()
        assert manifest["agent_context"]=="agent/context.json"
        assert json.loads((project/"agent"/"context.json").read_text(encoding="utf-8"))["contract"]=="vp3.app.agent-context.v1"
        assert "app-managed" in (project/"database"/"migrations"/"README.md").read_text(encoding="utf-8")

        first=client.post("/api/v1/control/homeserver-apps/starter.notes/build-install")
        assert first.status_code==200,first.text
        first_release=first.json()["release"]
        assert first_release["version"]=="0.1.0"

        permissions=client.get("/api/v1/control/homeserver-apps/starter.notes/permissions").json()["permissions"]
        assert permissions["allowed_count"]==0
        assert permissions["denied_count"]==2
        assert permissions["default_for_new_permissions"]=="denied"

        saved=client.put(
            "/api/v1/control/homeserver-apps/starter.notes/data/file?path=notes/one.txt",
            files={"file":("one.txt",b"persistent user data","text/plain")},
        )
        assert saved.status_code==200,saved.text
        assert client.get("/api/v1/control/homeserver-apps/starter.notes/data/file?path=notes/one.txt").content==b"persistent user data"

        # User app schema transitions must preserve Section 7 recovery without
        # executing package SQL. This was the pre-Section-9 integration gap.
        manifest["version"]="0.2.0"
        manifest["data_schema_version"]="2"
        manifest["release_notes"]=["Schema v2 handled by app runtime."]
        (project/"vp3-app.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
        (project/"database"/"migrations"/"002-user.sql").write_text(
            "CREATE TABLE should_not_auto_execute(id INTEGER PRIMARY KEY);\n",encoding="utf-8"
        )
        second=client.post("/api/v1/control/homeserver-apps/starter.notes/build-install")
        assert second.status_code==200,second.text
        second_release=second.json()["release"]
        migration=second_release["data_migration"]
        assert migration["migration_required"] is True
        assert migration["migration_execution"]=="app_managed"
        assert migration["snapshot_id"].startswith("appsnap_")
        assert migration["migration_scripts"]==[]
        assert client.get("/api/v1/control/homeserver-apps/starter.notes/data/file?path=notes/one.txt").content==b"persistent user data"

        db_path=homeserver_app_resources.sqlite_path("starter.notes","app.db")
        if db_path.exists():
            import sqlite3
            with sqlite3.connect(db_path) as connection:
                tables={row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert "should_not_auto_execute" not in tables

        rollback=client.post("/api/v1/control/homeserver-apps/starter.notes/rollback")
        assert rollback.status_code==200,rollback.text
        rollback_payload=rollback.json()
        assert rollback_payload["release"]["version"]=="0.1.0"
        assert rollback_payload["data_restore"]["restored"] is True
        assert client.get("/api/v1/control/homeserver-apps/starter.notes/data/file?path=notes/one.txt").content==b"persistent user data"
        app_state=homeserver_apps.get("starter.notes")
        assert app_state["metadata"]["data_schema_version"]=="1"

        runtime_js=(project/"assets"/"vp3-sdk.js").read_text(encoding="utf-8")
        assert 'version:"1.2"' in runtime_js
        assert "async permissions()" in runtime_js
        assert "async resources()" in runtime_js
        assert "async runtime()" in runtime_js
        assert "async agentPolicy()" in runtime_js
        assert "async agentContext(" in runtime_js
        assert "async agentPrompt(" in runtime_js

        ui=(ROOT/"ui"/"homeserver-apps.js").read_text(encoding="utf-8")
        assert "/api/v1/control/homeserver-apps/permissions/catalog" in ui
        assert "data-hs-create-permission" in ui

    proc=subprocess.run(
        [
            sys.executable,str(ROOT/"sdk"/"homeserver-app"/"create_app.py"),
            "cli.starter","CLI Starter","--runtime","static","--output",cli_dir,
            "--permission","notifications.write",
        ],
        check=True,capture_output=True,text=True,
    )
    cli_project=Path(proc.stdout.strip())
    cli_manifest=json.loads((cli_project/"vp3-app.json").read_text(encoding="utf-8"))
    assert cli_manifest["sdk_version"]=="1.2"
    assert cli_manifest["permissions"]==["notifications.write"]
    assert cli_manifest["data_schema_version"]=="1"
    assert cli_manifest["release_channel"]=="stable"
    assert cli_manifest["agent_context"]=="agent/context.json"
    assert (cli_project/"agent"/"context.json").is_file()
    cli_runtime_js=(cli_project/"assets"/"vp3-sdk.js").read_text(encoding="utf-8")
    assert "async agentPolicy()" in cli_runtime_js
    assert "async agentContext(" in cli_runtime_js
    assert "async agentPrompt(" in cli_runtime_js
    assert (cli_project/"database"/"migrations"/"README.md").is_file()

print("HomeServer System Apps Section 9 user app SDK starter runtime integration: PASS")
