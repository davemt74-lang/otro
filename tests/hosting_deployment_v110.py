from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))


def package(version: str, body: str="hello", runtime: str="static") -> bytes:
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,"w",zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("vp3-hosting.json",json.dumps({
            "contract":"vp3.hosting.package.v1",
            "version":version,
            "runtime":runtime,
            "entrypoint":"public/index.html",
        }))
        archive.writestr("public/index.html",body)
    return buffer.getvalue()


with tempfile.TemporaryDirectory(prefix="hosting-v110-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir
    from app.database import db, initialize_database
    from app.services import hosting_deployment, hosting_runtime

    initialize_database()
    site=hosting_runtime.create_site("Deploy Test",requested_hostname="deploy.vp3.me",runtime_kind="static")

    first=hosting_deployment.deploy_package(site["site_id"],package("1.0.0","one"),request_key="deploy-1")
    assert first["active"] is True
    assert first["app_version"]=="1.0.0"
    assert hosting_runtime.get_site(site["site_id"])["state"]=="active"
    assert (hosting_deployment.active_public_root(site["site_id"])/"index.html").read_text()=="one"

    replay=hosting_deployment.deploy_package(site["site_id"],package("1.0.0","one"),request_key="deploy-1")
    assert replay["release_id"]==first["release_id"]
    assert len(hosting_deployment.list_releases(site["site_id"]))==1
    try:
        hosting_deployment.deploy_package(site["site_id"],package("1.0.1","different"),request_key="deploy-1")
        raise AssertionError("idempotency key conflict was accepted")
    except hosting_deployment.DeploymentError as exc:
        assert exc.status_code==409

    second=hosting_deployment.deploy_package(site["site_id"],package("2.0.0","two"),request_key="deploy-2")
    assert second["release_id"]!=first["release_id"]
    assert second["previous_release_id"]==first["release_id"]
    assert (hosting_deployment.active_public_root(site["site_id"])/"index.html").read_text()=="two"

    rolled=hosting_deployment.rollback(site["site_id"])
    assert rolled["release_id"]==first["release_id"]
    assert (hosting_deployment.active_public_root(site["site_id"])/"index.html").read_text()=="one"

    bad=io.BytesIO()
    with zipfile.ZipFile(bad,"w") as archive:
        archive.writestr("vp3-hosting.json",json.dumps({
            "contract":"vp3.hosting.package.v1","runtime":"static","entrypoint":"public/index.html"
        }))
        archive.writestr("../escape.txt","bad")
        archive.writestr("public/index.html","ok")
    try:
        hosting_deployment.deploy_package(site["site_id"],bad.getvalue())
        raise AssertionError("zip-slip package was accepted")
    except hosting_deployment.DeploymentError:
        pass
    assert not (Path(data_dir)/"escape.txt").exists()

    php_site=hosting_runtime.create_site("PHP Test",runtime_kind="php")
    try:
        hosting_deployment.deploy_package(php_site["site_id"],package("1","x","static"))
        raise AssertionError("runtime mismatch was accepted")
    except hosting_deployment.DeploymentError:
        pass

    capability=hosting_deployment.public_capability()
    assert capability["atomic_release_activation"] is True
    assert capability["rollback"] is True
    assert capability["caller_filesystem_paths"] is False
    assert capability["symbolic_links"] is False
    assert capability["public_serving"] is False

    with db() as connection:
        events=connection.execute(
            "SELECT event_type FROM hosting_runtime_events WHERE site_id=? ORDER BY id",
            (site["site_id"],),
        ).fetchall()
    names=[row["event_type"] for row in events]
    assert names.count("deployment.activated")==2
    assert "deployment.rolled_back" in names

print("HomeServer Hosting v1.10 Section 2: PASS")
