from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-workspace-v240-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import homeserver_app_agent, homeserver_app_workspace
    from app.services.tasks import scheduler

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        created=client.post("/api/v1/control/homeserver-apps",json={
            "app_key":"workspace.demo",
            "name":"Workspace Demo",
            "runtime":"static",
            "source_type":"user_created",
            "permissions":["notifications.write"],
        })
        assert created.status_code==200,created.text

        status=client.get("/api/v1/control/homeserver-apps/workspace.demo/workspace")
        assert status.status_code==200,status.text
        workspace=status.json()
        assert workspace["contract"]=="vp3.app.development-workspace.v1"
        assert workspace["validation"]["valid"] is True
        assert workspace["permissions"]["declared_count"]==1
        assert workspace["permissions"]["allowed_count"]==0
        assert workspace["preview_url"].endswith("/workspace.demo/preview/")
        paths={row["path"] for row in workspace["project"]["files"]}
        assert "index.html" in paths and "vp3-app.json" in paths

        read=client.get("/api/v1/control/homeserver-apps/workspace.demo/workspace/file",params={"path":"index.html"})
        assert read.status_code==200,read.text
        original=read.json()["content"]
        assert "Workspace Demo" in original

        saved=client.put("/api/v1/control/homeserver-apps/workspace.demo/workspace/file",json={
            "path":"index.html",
            "content":original.replace("Ready to build.","Workspace ready."),
        })
        assert saved.status_code==200,saved.text
        assert saved.json()["validated"] is True

        created_file=client.put("/api/v1/control/homeserver-apps/workspace.demo/workspace/file",json={
            "path":"pages/about.html",
            "content":"<!doctype html><title>About</title><p>About this app.</p>",
        })
        assert created_file.status_code==200,created_file.text
        assert created_file.json()["created"] is True

        renamed=client.post("/api/v1/control/homeserver-apps/workspace.demo/workspace/rename",json={
            "path":"pages/about.html","new_path":"pages/info.html",
        })
        assert renamed.status_code==200,renamed.text

        deleted=client.delete("/api/v1/control/homeserver-apps/workspace.demo/workspace/file",params={"path":"pages/info.html"})
        assert deleted.status_code==200,deleted.text

        manifest_path=Path(data_dir)/"apps"/"workspace.demo"/"vp3-app.json"
        manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
        invalid=dict(manifest);invalid["app_key"]="different.app"
        blocked=client.put("/api/v1/control/homeserver-apps/workspace.demo/workspace/file",json={
            "path":"vp3-app.json","content":json.dumps(invalid),
        })
        assert blocked.status_code==409,blocked.text
        assert json.loads(manifest_path.read_text(encoding="utf-8"))["app_key"]=="workspace.demo"

        bad_delete=client.delete("/api/v1/control/homeserver-apps/workspace.demo/workspace/file",params={"path":"vp3-app.json"})
        assert bad_delete.status_code==409

        # Persistent data must never appear inside the editable project tree.
        data=client.put(
            "/api/v1/control/homeserver-apps/workspace.demo/data/file?path=private/note.txt",
            files={"file":("note.txt",b"persistent data","text/plain")},
        )
        assert data.status_code==200,data.text
        listing=client.get("/api/v1/control/homeserver-apps/workspace.demo/workspace/files").json()
        assert all(not row["path"].startswith("app-data") for row in listing["files"])

        validate=client.post("/api/v1/control/homeserver-apps/workspace.demo/workspace/validate")
        assert validate.status_code==200,validate.text
        assert validate.json()["valid"] is True
        assert validate.json()["permission_delta"]["requires_review"] is False

        build=client.post("/api/v1/control/homeserver-apps/workspace.demo/build-install")
        assert build.status_code==200,build.text
        assert build.json()["release"]["version"]=="0.1.0"

        preview=client.get("/api/v1/control/homeserver-apps/workspace.demo/preview/")
        assert preview.status_code==200,preview.text
        assert "Workspace ready." in preview.text

        assert "apps.workspace.status" in homeserver_app_agent.READ_ACTIONS
        assert "apps.workspace.file.read" in homeserver_app_agent.READ_ACTIONS
        assert "apps.workspace.file.write" in homeserver_app_agent.WRITE_ACTIONS
        normalized=homeserver_app_agent.normalize_action("apps.workspace.file.write",{
            "app_key":"workspace.demo","path":"assets/app.js","content":"console.log('agent edit');"
        })
        assert normalized["app_key"]=="workspace.demo"
        meta=homeserver_app_agent.safe_action_meta("apps.workspace.file.write",normalized)
        assert meta["content_bytes"]>0 and "content" not in meta
        executed=homeserver_app_agent.execute_action("apps.workspace.file.write",normalized)
        assert executed["workspace"]["validated"] is True

        cap=client.get("/api/v1/control/homeserver-apps/capability").json()
        assert cap["workspace"]["user_app_workspace"] is True
        assert cap["workspace"]["persistent_data_separate"] is True
        assert cap["workspace"]["permissions_use_section8_governance"] is True
        assert cap["workspace"]["data_uses_section7_recovery"] is True

print("HomeServer System Apps Section 10 development workspace and build lifecycle: PASS")
