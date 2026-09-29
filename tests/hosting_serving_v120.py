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


def package(runtime: str="static") -> bytes:
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,"w",zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("vp3-hosting.json",json.dumps({
            "contract":"vp3.hosting.package.v1",
            "version":"1.2.0",
            "runtime":runtime,
            "entrypoint":"public/index.php" if runtime=="php" else "public/index.html",
        }))
        if runtime=="php":
            archive.writestr("public/index.php","<?php echo 'real php'; ?>")
        else:
            archive.writestr("public/index.html","<h1>VP3 Hosted</h1>")
            archive.writestr("public/app.css","body{font-family:sans-serif}")
    return buffer.getvalue()


with tempfile.TemporaryDirectory(prefix="hosting-v120-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import hosting_deployment, hosting_runtime, hosting_serving

    with TestClient(app) as client:
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200
        site=hosting_runtime.create_site("Serving Test",requested_hostname="serve.vp3.me",runtime_kind="static")
        site_id=site["site_id"]

        before=hosting_serving.runtime_health(site_id)
        assert before["deployment_ready"] is False
        assert before["local_serving_ready"] is False
        assert before["public_routing"] is False

        hosting_deployment.deploy_package(site_id,package(),request_key="serve-1")
        health=hosting_serving.runtime_health(site_id)
        assert health["deployment_ready"] is True
        assert health["local_serving_ready"] is True
        assert health["sqlite"]["healthy"] is True

        response=client.get(f"/api/v1/control/hosting/sites/{site_id}/preview/")
        assert response.status_code==200
        assert "VP3 Hosted" in response.text
        assert response.headers["cache-control"]=="no-store"

        css=client.get(f"/api/v1/control/hosting/sites/{site_id}/preview/app.css")
        assert css.status_code==200
        assert "font-family" in css.text
        assert css.headers["x-content-type-options"]=="nosniff"
        static_post=client.post(f"/api/v1/control/hosting/sites/{site_id}/preview/app.css",content=b"x")
        assert static_post.status_code==405

        traversal=client.get(f"/api/v1/control/hosting/sites/{site_id}/preview/%2E%2E/manifest.json")
        assert traversal.status_code in {400,404}

        hosting_runtime.set_state(site_id,"suspended")
        blocked=client.get(f"/api/v1/control/hosting/sites/{site_id}/preview/")
        assert blocked.status_code==503

        php_site=hosting_runtime.create_site("PHP Serving",requested_hostname="php.vp3.me",runtime_kind="php")
        php_id=php_site["site_id"]
        hosting_deployment.deploy_package(php_id,package("php"),request_key="php-serve-1")

        captured={}
        original_path=hosting_serving.php_cgi_path
        original_run=hosting_serving.subprocess.run
        os.environ["CPANEL_API_TOKEN"]="must-not-leak"

        class Completed:
            returncode=0
            stdout=b"Status: 201 Created\r\nContent-Type: text/plain\r\nSet-Cookie: app_session=ok; Path=/\r\n\r\nphp-ok"
            stderr=b""

        def fake_run(args,**kwargs):
            captured["args"]=args
            captured["env"]=dict(kwargs["env"])
            captured["input"]=kwargs["input"]
            captured["timeout"]=kwargs["timeout"]
            return Completed()

        hosting_serving.php_cgi_path=lambda: "php-cgi-test"
        hosting_serving.subprocess.run=fake_run
        try:
            client.cookies.set("site_session","abc123")
            php=client.get(f"/api/v1/control/hosting/sites/{php_id}/preview/")
            assert php.status_code==201
            assert php.text=="php-ok"
            assert "app_session=ok" in php.headers.get("set-cookie","")
            assert captured["timeout"]==hosting_serving.PHP_TIMEOUT_SECONDS
            assert captured["env"]["VP3_SITE_ID"]==php_id
            assert captured["env"]["VP3_SQLITE_PATH"].endswith("database"+os.sep+"site.sqlite")
            assert captured["env"]["VP3_STORAGE_DIR"].endswith("storage")
            assert "CPANEL_API_TOKEN" not in captured["env"]
            assert "homeserver_owner" not in captured["env"].get("HTTP_COOKIE","")
            assert "site_session=abc123" in captured["env"].get("HTTP_COOKIE","")
        finally:
            hosting_serving.php_cgi_path=original_path
            hosting_serving.subprocess.run=original_run
            os.environ.pop("CPANEL_API_TOKEN",None)

    capability=hosting_serving.public_capability()
    assert capability["loopback_serving"] is True
    assert capability["secret_environment_inheritance"] is False
    assert capability["public_routing"] is False

print("HomeServer Hosting v1.20 Section 3: PASS")
