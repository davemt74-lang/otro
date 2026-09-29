from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-apps-v120-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import homeserver_app_packages, homeserver_app_resources, homeserver_app_security, homeserver_apps  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        created=client.post("/api/v1/control/homeserver-apps",json={
            "app_key":"secure.notes","name":"Secure Notes","source_type":"user_created","runtime":"static"
        })
        assert created.status_code==200,created.text
        project=Path(data_dir)/"apps"/"secure.notes"
        manifest_path=project/"vp3-app.json"
        manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["permissions"]=["contacts.read","network.external"]
        manifest_path.write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")

        installed=client.post("/api/v1/control/homeserver-apps/secure.notes/build-install")
        assert installed.status_code==200,installed.text

        permissions=client.get("/api/v1/control/homeserver-apps/secure.notes/permissions")
        assert permissions.status_code==200
        rows={row["permission"]:row["allowed"] for row in permissions.json()["permissions"]["permissions"]}
        assert rows=={"contacts.read":False,"network.external":False}

        approved=client.put("/api/v1/control/homeserver-apps/secure.notes/permissions",json={
            "permission":"contacts.read","allowed":True
        })
        assert approved.status_code==200,approved.text
        rows={row["permission"]:row["allowed"] for row in approved.json()["permissions"]["permissions"]}
        assert rows["contacts.read"] is True
        assert homeserver_app_security.permission_allowed("secure.notes","contacts.read") is True
        assert homeserver_app_security.permission_allowed("secure.notes","network.external") is False

        undeclared=client.put("/api/v1/control/homeserver-apps/secure.notes/permissions",json={
            "permission":"files.write","allowed":True
        })
        assert undeclared.status_code==409

        # Updating the manifest preserves existing approval, removes stale scope,
        # and defaults the newly requested permission to denied.
        manifest["version"]="0.2.0"
        manifest["permissions"]=["contacts.read","hardware.camera"]
        manifest_path.write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
        updated=client.post("/api/v1/control/homeserver-apps/secure.notes/build-install")
        assert updated.status_code==200,updated.text
        permission_state=client.get("/api/v1/control/homeserver-apps/secure.notes/permissions").json()["permissions"]
        rows={row["permission"]:row["allowed"] for row in permission_state["permissions"]}
        assert rows=={"contacts.read":True,"hardware.camera":False}
        assert permission_state["default_for_new_permissions"]=="denied"

        # Unknown package permissions fail closed at validation.
        bad=dict(manifest)
        bad["permissions"]=["root.everything"]
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,"w",zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("vp3-app.json",json.dumps(bad))
            archive.writestr("index.html","ok")
        rejected=client.post(
            "/api/v1/control/homeserver-apps/secure.notes/package/validate",
            files={"file":("bad.vp3app",buf.getvalue(),"application/zip")},
        )
        assert rejected.status_code==400,rejected.text
        assert "unsupported app permission" in rejected.text.lower()

        # Secrets are write-only through the owner API. Values are not returned,
        # logged in app metadata, or exposed in status.
        secret_value="sk-test-super-sensitive-value"
        saved=client.put("/api/v1/control/homeserver-apps/secure.notes/secrets/EXTERNAL_API_KEY",json={"value":secret_value})
        assert saved.status_code==200,saved.text
        assert secret_value not in saved.text
        secret_state=saved.json()["secrets"]
        assert secret_state["configured_keys"]==["EXTERNAL_API_KEY"]
        assert secret_state["values_exposed"] is False
        assert homeserver_app_security.get_secret("secure.notes","EXTERNAL_API_KEY")==secret_value
        app_json=json.dumps(homeserver_apps.get("secure.notes"),sort_keys=True)
        assert secret_value not in app_json

        status=client.get("/api/v1/control/homeserver-apps/secure.notes/secrets")
        assert status.status_code==200
        assert secret_value not in status.text

        invalid_key=client.put("/api/v1/control/homeserver-apps/secure.notes/secrets/bad-key",json={"value":"x"})
        assert invalid_key.status_code==400

        removed=client.delete("/api/v1/control/homeserver-apps/secure.notes/secrets/EXTERNAL_API_KEY")
        assert removed.status_code==200
        assert removed.json()["secrets"]["count"]==0
        assert homeserver_app_security.get_secret("secure.notes","EXTERNAL_API_KEY") is None

        # Every app gets isolated data and SQLite roots with bounded quotas.
        resources=client.get("/api/v1/control/homeserver-apps/secure.notes/resources")
        assert resources.status_code==200,resources.text
        rp=resources.json()["resources"]
        assert rp["filesystem_paths_exposed"] is False
        assert rp["caller_paths_accepted"] is False
        assert rp["storage_limit_bytes"]==homeserver_app_resources.DEFAULT_STORAGE_LIMIT
        assert rp["sqlite_limit_bytes"]==homeserver_app_resources.DEFAULT_SQLITE_LIMIT

        limited=client.put("/api/v1/control/homeserver-apps/secure.notes/resources",json={
            "storage_limit_bytes":homeserver_app_resources.MIN_STORAGE_LIMIT,
            "sqlite_limit_bytes":homeserver_app_resources.MIN_SQLITE_LIMIT,
        })
        assert limited.status_code==200,limited.text
        assert limited.json()["resources"]["storage_limit_bytes"]==homeserver_app_resources.MIN_STORAGE_LIMIT

        written=homeserver_app_resources.write_file("secure.notes","notes/one.txt",b"private app data")
        assert written["relative_path"]=="notes/one.txt"
        assert homeserver_app_resources.read_file("secure.notes","notes/one.txt")==b"private app data"

        for unsafe in ("../escape.txt","/absolute.txt","C:/windows.txt"):
            try:
                homeserver_app_resources.write_file("secure.notes",unsafe,b"blocked")
                raise AssertionError(f"unsafe path accepted: {unsafe}")
            except homeserver_app_resources.AppResourceError:
                pass
        assert not (Path(data_dir)/"escape.txt").exists()

        try:
            homeserver_app_resources.write_file(
                "secure.notes","too-large.bin",b"x"*(homeserver_app_resources.MIN_STORAGE_LIMIT+1)
            )
            raise AssertionError("storage quota was not enforced")
        except homeserver_app_resources.AppResourceError as exc:
            assert exc.status_code==413

        db_path=homeserver_app_resources.sqlite_path("secure.notes","app.db")
        db_path.write_bytes(b"SQLite fixture")
        assert Path(data_dir)/"app-data" in db_path.parents
        homeserver_app_resources.enforce_sqlite_quota("secure.notes")
        for bad_name in ("../other.db","C:/bad.db","not-sqlite.txt"):
            try:
                homeserver_app_resources.sqlite_path("secure.notes",bad_name)
                raise AssertionError(f"unsafe SQLite name accepted: {bad_name}")
            except homeserver_app_resources.AppResourceError:
                pass

        cap=client.get("/api/v1/control/homeserver-apps/capability").json()
        assert cap["security"]["owner_approval_required"] is True
        assert cap["security"]["permission_expansion_auto_approved"] is False
        assert cap["security"]["secret_values_exposed"] is False
        assert cap["resources"]["isolated_data_root"] is True
        assert cap["resources"]["isolated_sqlite_root"] is True

        history=client.get("/api/v1/control/homeserver-apps/secure.notes").json()["history"]
        actions={item["event_type"] for item in history}
        assert "app.permissions.synced" in actions
        assert "app.permission.updated" in actions
        assert "app.secret.updated" in actions
        assert "app.secret.removed" in actions
        assert "app.resources.updated" in actions

print("HomeServer Apps V1 Section 3 permissions secrets and resource isolation: PASS")
