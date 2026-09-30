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

with tempfile.TemporaryDirectory(prefix="homeserver-distribution-v250-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import homeserver_app_distribution, homeserver_app_security
    from app.services.tasks import scheduler

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        created=client.post("/api/v1/control/homeserver-apps",json={
            "app_key":"private.notes",
            "name":"Private Notes",
            "runtime":"static",
            "source_type":"user_created",
            "permissions":["notifications.write"],
        })
        assert created.status_code==200,created.text
        built=client.post("/api/v1/control/homeserver-apps/private.notes/build-install")
        assert built.status_code==200,built.text

        data_write=client.put(
            "/api/v1/control/homeserver-apps/private.notes/data/file?path=private/data.txt",
            files={"file":("data.txt",b"must-not-export","text/plain")},
        )
        assert data_write.status_code==200,data_write.text
        secret=client.put(
            "/api/v1/control/homeserver-apps/private.notes/secrets/API_KEY",
            json={"value":"super-secret-value"},
        )
        assert secret.status_code==200,secret.text

        descriptor=client.get("/api/v1/control/homeserver-apps/private.notes/distribution")
        assert descriptor.status_code==200,descriptor.text
        d=descriptor.json()["distribution"]
        assert d["contract"]=="vp3.app.distribution.v1"
        assert d["includes_app_data"] is False
        assert d["includes_secrets"] is False
        assert d["ownership_transfer"] is False
        assert len(d["package_sha256"])==64
        assert d["permissions"]==["notifications.write"]

        exported=client.get("/api/v1/control/homeserver-apps/private.notes/distribution/export")
        assert exported.status_code==200,exported.text
        bundle=exported.content
        assert exported.headers["x-vp3-package-sha256"]==d["package_sha256"]

        with zipfile.ZipFile(io.BytesIO(bundle),"r") as z:
            assert set(z.namelist())=={"vp3-distribution.json","app-package.zip"}
            meta=json.loads(z.read("vp3-distribution.json").decode("utf-8"))
            package=z.read("app-package.zip")
            assert b"must-not-export" not in package
            assert b"super-secret-value" not in package
            assert meta["descriptor"]["package_sha256"]==d["package_sha256"]

        inspected=homeserver_app_distribution.inspect_bundle(bundle)
        assert inspected["integrity_verified"] is True
        assert inspected["publisher_seal"]["locally_verifiable"] is True
        assert inspected["publisher_seal"]["valid"] is True

        # A private-share grant is bound to the exact package SHA-256.
        try:
            homeserver_app_distribution.install_bundle(
                bundle,approved=True,expected_package_sha256="0"*64
            )
            raise AssertionError("mismatched private-share hash was accepted")
        except homeserver_app_distribution.AppDistributionError as exc:
            assert exc.status_code==409

        installed=homeserver_app_distribution.install_bundle(
            bundle,approved=True,expected_package_sha256=d["package_sha256"]
        )
        assert installed["installed"] is True
        assert installed["share_hash_match"] is True
        assert installed["release"]["package_sha256"]==d["package_sha256"]

        assert homeserver_app_security.get_secret("private.notes","API_KEY")=="super-secret-value"
        read=client.get("/api/v1/control/homeserver-apps/private.notes/data/file",params={"path":"private/data.txt"})
        assert read.status_code==200 and read.content==b"must-not-export"

        # Tampering the embedded package is detected before install.
        with zipfile.ZipFile(io.BytesIO(bundle),"r") as src:
            meta_raw=src.read("vp3-distribution.json")
            package_raw=bytearray(src.read("app-package.zip"))
        package_raw[-1]=(package_raw[-1]+1)%256
        tampered_buffer=io.BytesIO()
        with zipfile.ZipFile(tampered_buffer,"w",zipfile.ZIP_DEFLATED) as out:
            out.writestr("vp3-distribution.json",meta_raw)
            out.writestr("app-package.zip",bytes(package_raw))
        try:
            homeserver_app_distribution.inspect_bundle(tampered_buffer.getvalue())
            raise AssertionError("tampered bundle was accepted")
        except homeserver_app_distribution.AppDistributionError as exc:
            assert exc.status_code==409

        cap=client.get("/api/v1/control/homeserver-apps/capability").json()["distribution"]
        assert cap["export_bundle"] is True
        assert cap["app_data_exported"] is False
        assert cap["secrets_exported"] is False
        assert cap["package_hash_binding"] is True
        assert cap["marketplace"] is False

print("HomeServer System Apps Section 11 distribution and private sharing: PASS")
