from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-apps-v140-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import homeserver_apps  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    with TestClient(app) as client:
        scheduler.stop()
        assert client.get("/api/v1/control/homeserver-apps").status_code in {401,403}
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        created=client.post("/api/v1/control/homeserver-apps",json={
            "app_key":"manager.demo","name":"Manager Demo","runtime":"static","source_type":"user_created"
        })
        assert created.status_code==200,created.text
        app_row=created.json()["app"]
        assert app_row["lifecycle_state"]=="draft"
        assert (Path(data_dir)/"apps"/"manager.demo"/"vp3-app.json").is_file()

        installed=client.post("/api/v1/control/homeserver-apps/manager.demo/build-install")
        assert installed.status_code==200,installed.text
        assert homeserver_apps.get("manager.demo")["lifecycle_state"]=="running"

        stopped=client.post("/api/v1/control/homeserver-apps/manager.demo/lifecycle",json={
            "state":"stopped","metadata":{"reason":"ui_test"}
        })
        assert stopped.status_code==200,stopped.text
        assert stopped.json()["app"]["lifecycle_state"]=="stopped"

        resumed=client.post("/api/v1/control/homeserver-apps/manager.demo/resume")
        assert resumed.status_code==200,resumed.text
        assert resumed.json()["app"]["lifecycle_state"]=="running"

        archived=client.post("/api/v1/control/homeserver-apps/manager.demo/archive")
        assert archived.status_code==200,archived.text
        assert archived.json()["app"]["lifecycle_state"]=="archived"
        assert (Path(data_dir)/"apps"/"manager.demo").is_dir()

        restored=client.post("/api/v1/control/homeserver-apps/manager.demo/resume")
        assert restored.status_code==200,restored.text
        assert restored.json()["app"]["lifecycle_state"]=="draft"

        listing=client.get("/api/v1/control/homeserver-apps")
        assert listing.status_code==200
        assert any(item["app_key"]=="manager.demo" for item in listing.json()["apps"])

    index=(ROOT/"ui"/"index.html").read_text(encoding="utf-8")
    app_js=(ROOT/"ui"/"app.js").read_text(encoding="utf-8")
    manager_js=(ROOT/"ui"/"homeserver-apps.js").read_text(encoding="utf-8")
    manager_css=(ROOT/"ui"/"homeserver-apps.css").read_text(encoding="utf-8")

    assert "/assets/homeserver-apps.css" in index
    assert "/assets/homeserver-apps.js" in index
    assert "'homeserver-apps':'Apps'" in app_js
    assert "window.loadHomeServerApps" in app_js
    assert "'homeserver-apps'" in app_js
    assert "Installed Apps" in manager_js
    assert "Create from SDK" in manager_js
    assert "data-hs-app-archive" in manager_js
    assert "data-hs-app-permission" in manager_js
    assert "/api/v1/control/homeserver-apps/" in manager_js
    assert "hs-apps-grid" in manager_css

print("HomeServer Apps V1 Section 5 installed apps manager: PASS")
