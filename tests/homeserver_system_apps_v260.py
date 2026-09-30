from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-share-lifecycle-v260-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.database import db
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import homeserver_app_distribution, homeserver_app_sdk
    from app.services.tasks import scheduler

    def update_bundle(bundle:bytes,*,version:str,permissions:list[str],schema:str,publisher:str|None=None)->bytes:
        inspected=homeserver_app_distribution.inspect_bundle(bundle)
        with zipfile.ZipFile(io.BytesIO(inspected["package"]),"r") as src:
            files={name:src.read(name) for name in src.namelist() if not name.endswith("/")}
        manifest=json.loads(files["vp3-app.json"].decode("utf-8"))
        manifest["version"]=version
        manifest["permissions"]=permissions
        manifest["data_schema_version"]=schema
        manifest["release_notes"]=["Trusted private update"]
        files["vp3-app.json"]=(json.dumps(manifest,indent=2,sort_keys=True)+"\n").encode("utf-8")
        pkg_buf=io.BytesIO()
        with zipfile.ZipFile(pkg_buf,"w",zipfile.ZIP_DEFLATED) as out:
            for name,payload in files.items(): out.writestr(name,payload)
        package=pkg_buf.getvalue()
        descriptor=dict(inspected["descriptor"])
        descriptor["distribution_id"]="appdist_update_"+version.replace(".","")
        descriptor["version"]=version
        descriptor["permissions"]=permissions
        descriptor["data_schema_version"]=schema
        descriptor["package_sha256"]=hashlib.sha256(package).hexdigest()
        descriptor["publisher_fingerprint"]=publisher or descriptor["publisher_fingerprint"]
        descriptor["publisher_seal"]="portable-cloud-grant"
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,"w",zipfile.ZIP_DEFLATED) as out:
            out.writestr("vp3-distribution.json",json.dumps({"contract":"vp3.app.distribution-bundle.v1","descriptor":descriptor},indent=2,sort_keys=True)+"\n")
            out.writestr("app-package.zip",package)
        return buf.getvalue()

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200
        created=client.post("/api/v1/control/homeserver-apps",json={
            "app_key":"shared.notes","name":"Shared Notes","runtime":"static",
            "source_type":"user_created","permissions":["notifications.write"],
        })
        assert created.status_code==200,created.text

        initial=client.get("/api/v1/control/homeserver-apps/shared.notes/distribution/export")
        assert initial.status_code==200,initial.text
        initial_bundle=initial.content
        initial_hash=initial.headers["x-vp3-package-sha256"]
        initial_inspected=homeserver_app_distribution.inspect_bundle(initial_bundle)
        publisher=initial_inspected["descriptor"]["publisher_fingerprint"]

        # Simulate recipient HomeServer: source project/registry does not exist yet.
        with db() as connection:
            connection.execute("DELETE FROM homeserver_apps WHERE app_key='shared.notes'")
        homeserver_app_sdk.remove_project("shared.notes")

        installed=homeserver_app_distribution.install_bundle(
            initial_bundle,approved=True,expected_package_sha256=initial_hash,share_public_id="share_initial"
        )
        assert installed["installed"] is True
        provenance=homeserver_app_distribution.installed_provenance("shared.notes")
        assert provenance["installed_from_private_distribution"] is True
        assert provenance["publisher_fingerprint"]==publisher
        assert provenance["package_sha256"]==initial_hash
        assert provenance["last_share_public_id"]=="share_initial"

        # Same publisher, new permission + schema: review, never silent.
        update_bundle_bytes=update_bundle(
            initial_bundle,version="0.2.0",
            permissions=["notifications.write","network.external"],schema="2",
        )
        update_inspected=homeserver_app_distribution.inspect_bundle(update_bundle_bytes)
        update_hash=update_inspected["descriptor"]["package_sha256"]
        review=homeserver_app_distribution.preview_bundle(
            update_bundle_bytes,expected_package_sha256=update_hash
        )
        assert review["update"] is True
        assert review["publisher_continuity"] is True
        assert review["requires_explicit_approval"] is True
        assert review["automatic_update"] is False
        assert review["schema_change"]=={"from":"1","to":"2","changed":True}
        added={row["permission"] for row in review["permission_delta"]["added"]}
        assert "network.external" in added
        assert any(row["permission"]=="network.external" for row in review["permission_delta"]["high_risk_added"])

        # No approval = no update.
        try:
            homeserver_app_distribution.install_bundle(
                update_bundle_bytes,approved=False,expected_package_sha256=update_hash
            )
            raise AssertionError("unapproved update was installed")
        except homeserver_app_distribution.AppDistributionError as exc:
            assert exc.status_code==409
        assert homeserver_app_distribution.installed_provenance("shared.notes")["package_sha256"]==initial_hash

        updated=homeserver_app_distribution.install_bundle(
            update_bundle_bytes,approved=True,expected_package_sha256=update_hash,share_public_id="share_update"
        )
        assert updated["release"]["version"]=="0.2.0"
        assert updated["review"]["schema_change"]["changed"] is True
        provenance=homeserver_app_distribution.installed_provenance("shared.notes")
        assert provenance["package_sha256"]==update_hash
        assert provenance["last_share_public_id"]=="share_update"

        # New permission is declared but defaults denied after update.
        permissions=client.get("/api/v1/control/homeserver-apps/shared.notes/permissions").json()["permissions"]
        network=next(row for row in permissions["permissions"] if row["permission"]=="network.external")
        assert network["allowed"] is False

        # Publisher substitution is blocked even if package integrity is otherwise valid.
        hostile=update_bundle(
            update_bundle_bytes,version="0.3.0",
            permissions=["notifications.write","network.external"],schema="2",
            publisher="deadbeef"*4,
        )
        try:
            homeserver_app_distribution.preview_bundle(hostile)
            raise AssertionError("publisher substitution was accepted")
        except homeserver_app_distribution.AppDistributionError as exc:
            assert exc.status_code==409

        cap=client.get("/api/v1/control/homeserver-apps/capability").json()["distribution"]
        assert cap["installed_share_provenance"] is True
        assert cap["publisher_continuity_enforced"] is True
        assert cap["update_review"] is True
        assert cap["automatic_updates"] is False
        assert cap["revocation_uninstalls_app"] is False

print("HomeServer System Apps Section 12 trusted share update lifecycle: PASS")
