from __future__ import annotations

import io
import json
import os
import stat
import sys
import tempfile
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-apps-v110-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import homeserver_app_packages, homeserver_apps  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    def zip_bytes(files:dict[str,bytes|str])->bytes:
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,"w",zipfile.ZIP_DEFLATED) as z:
            for name,value in files.items():
                z.writestr(name,value.encode() if isinstance(value,str) else value)
        return buf.getvalue()

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        created=client.post("/api/v1/control/homeserver-apps",json={
            "app_key":"garage.inventory","name":"Garage Inventory","source_type":"user_created","runtime":"static"
        })
        assert created.status_code==200,created.text
        project=Path(data_dir)/"apps"/"garage.inventory"
        assert (project/"vp3-app.json").is_file()

        built=homeserver_app_packages.build_project_package("garage.inventory")
        assert built["validation"]["valid"] is True
        assert built["validation"]["manifest"]["app_key"]=="garage.inventory"
        assert built["validation"]["manifest"]["sdk_version"]=="1.2"

        validate=client.post(
            "/api/v1/control/homeserver-apps/garage.inventory/package/validate",
            files={"file":("garage.vp3app",built["package"],"application/zip")},
        )
        assert validate.status_code==200,validate.text
        v=validate.json()["validation"]
        assert v["package_sha256"]==built["validation"]["package_sha256"]
        assert v["file_count"]>=5

        installed=client.post("/api/v1/control/homeserver-apps/garage.inventory/build-install")
        assert installed.status_code==200,installed.text
        first=installed.json()["release"]
        assert first["contract"]=="vp3.app.release.v1"
        assert first["active"] is True
        assert first["version"]=="0.1.0"

        runtime=client.get("/api/v1/control/homeserver-apps/garage.inventory/runtime")
        assert runtime.status_code==200,runtime.text
        runtime_payload=runtime.json()["runtime"]
        assert runtime_payload["active_release_id"]==first["release_id"]
        assert runtime_payload["lifecycle_state"]=="running"

        app_row=homeserver_apps.get("garage.inventory")
        assert app_row["installed_version"]=="0.1.0"
        assert app_row["metadata"]["sdk_version"]=="1.2"
        assert app_row["metadata"]["active_release_id"]==first["release_id"]
        assert len(app_row["metadata"]["package_sha256"])==64

        # Build a second valid release and verify atomic release lineage.
        manifest=json.loads((project/"vp3-app.json").read_text(encoding="utf-8"))
        manifest["version"]="0.2.0"
        (project/"vp3-app.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
        second=client.post("/api/v1/control/homeserver-apps/garage.inventory/build-install")
        assert second.status_code==200,second.text
        second_release=second.json()["release"]
        assert second_release["version"]=="0.2.0"
        assert second_release["previous_release_id"]==first["release_id"]
        assert second_release["release_id"]!=first["release_id"]

        # Identity mismatch fails before lifecycle mutation.
        bad_identity=dict(manifest)
        bad_identity["app_key"]="other.app"
        mismatch=zip_bytes({
            "vp3-app.json":json.dumps(bad_identity),
            "index.html":"ok",
        })
        before=homeserver_apps.get("garage.inventory")["lifecycle_state"]
        mismatch_response=client.post(
            "/api/v1/control/homeserver-apps/garage.inventory/package/install",
            files={"file":("wrong.vp3app",mismatch,"application/zip")},
        )
        assert mismatch_response.status_code==409,mismatch_response.text
        assert homeserver_apps.get("garage.inventory")["lifecycle_state"]==before

        # Unknown manifest fields fail closed.
        unknown=dict(manifest)
        unknown["shell_command"]="rm -rf /"
        unknown_package=zip_bytes({"vp3-app.json":json.dumps(unknown),"index.html":"ok"})
        response=client.post(
            "/api/v1/control/homeserver-apps/garage.inventory/package/validate",
            files={"file":("unknown.vp3app",unknown_package,"application/zip")},
        )
        assert response.status_code==400
        assert "unsupported fields" in response.text

        # Missing referenced entrypoint fails closed.
        missing=zip_bytes({"vp3-app.json":json.dumps(manifest)})
        response=client.post(
            "/api/v1/control/homeserver-apps/garage.inventory/package/validate",
            files={"file":("missing.vp3app",missing,"application/zip")},
        )
        assert response.status_code==400
        assert "missing file" in response.text

        # Traversal paths and symlinks are rejected.
        traversal=zip_bytes({"vp3-app.json":json.dumps(manifest),"index.html":"ok","../escape.txt":"blocked"})
        response=client.post(
            "/api/v1/control/homeserver-apps/garage.inventory/package/validate",
            files={"file":("traversal.vp3app",traversal,"application/zip")},
        )
        assert response.status_code==400

        symlink_buffer=io.BytesIO()
        with zipfile.ZipFile(symlink_buffer,"w",zipfile.ZIP_DEFLATED) as z:
            z.writestr("vp3-app.json",json.dumps(manifest))
            z.writestr("index.html","ok")
            info=zipfile.ZipInfo("link")
            info.create_system=3
            info.external_attr=(stat.S_IFLNK|0o777)<<16
            z.writestr(info,"index.html")
        response=client.post(
            "/api/v1/control/homeserver-apps/garage.inventory/package/validate",
            files={"file":("symlink.vp3app",symlink_buffer.getvalue(),"application/zip")},
        )
        assert response.status_code==400
        assert "symbolic" in response.text.lower()

        # Unsupported SDK versions and wrong runtime entrypoints fail.
        future=dict(manifest)
        future["sdk_version"]="99.0"
        future_package=zip_bytes({"vp3-app.json":json.dumps(future),"index.html":"ok"})
        response=client.post(
            "/api/v1/control/homeserver-apps/garage.inventory/package/validate",
            files={"file":("future.vp3app",future_package,"application/zip")},
        )
        assert response.status_code==400

        php=dict(manifest)
        php["runtime"]="php"
        php["entrypoint"]="index.html"
        php_package=zip_bytes({"vp3-app.json":json.dumps(php),"index.html":"ok"})
        response=client.post(
            "/api/v1/control/homeserver-apps/garage.inventory/package/validate",
            files={"file":("phpbad.vp3app",php_package,"application/zip")},
        )
        assert response.status_code==400

        cap=client.get("/api/v1/control/homeserver-apps/capability").json()["packages"]
        assert cap["contract"]=="vp3.app.package.v1"
        assert cap["manifest_validation"] is True
        assert cap["atomic_release_activation"] is True
        assert cap["symbolic_links"] is False
        assert cap["system_app_installer"]=="separate_vp3_managed_path"

        history=client.get("/api/v1/control/homeserver-apps/garage.inventory").json()["history"]
        assert any(item["event_type"]=="app.package.installed" for item in history)

print("HomeServer Apps V1 Section 2 package validation and runtime installation: PASS")
