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

with tempfile.TemporaryDirectory(prefix="homeserver-apps-v160-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import homeserver_app_releases, homeserver_apps  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        created=client.post("/api/v1/control/homeserver-apps",json={
            "app_key":"release.demo","name":"Release Demo","runtime":"static","source_type":"user_created"
        })
        assert created.status_code==200,created.text
        project=Path(data_dir)/"apps"/"release.demo"
        manifest_path=project/"vp3-app.json"

        first=client.post("/api/v1/control/homeserver-apps/release.demo/build-install")
        assert first.status_code==200,first.text
        first_release=first.json()["release"]
        assert first_release["version"]=="0.1.0"

        manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["version"]="0.2.0"
        manifest_path.write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
        (project/"index.html").write_text("<!doctype html><title>Release 0.2</title><h1>Release 0.2</h1>",encoding="utf-8")

        second=client.post("/api/v1/control/homeserver-apps/release.demo/build-install")
        assert second.status_code==200,second.text
        second_release=second.json()["release"]
        assert second_release["version"]=="0.2.0"
        assert second_release["previous_release_id"]==first_release["release_id"]

        history=client.get("/api/v1/control/homeserver-apps/release.demo/releases")
        assert history.status_code==200,history.text
        hp=history.json()
        assert hp["contract"]=="vp3.app.release-management.v1"
        assert hp["count"]==2
        assert hp["active_release_id"]==second_release["release_id"]
        assert hp["previous_release_id"]==first_release["release_id"]
        assert next(r for r in hp["releases"] if r["release_id"]==second_release["release_id"])["active"] is True
        assert next(r for r in hp["releases"] if r["release_id"]==first_release["release_id"])["previous"] is True

        rollback=client.post("/api/v1/control/homeserver-apps/release.demo/rollback")
        assert rollback.status_code==200,rollback.text
        rb=rollback.json()
        assert rb["changed"] is True
        assert rb["release"]["release_id"]==first_release["release_id"]
        assert homeserver_apps.get("release.demo")["installed_version"]=="0.1.0"
        preview=client.get("/api/v1/control/homeserver-apps/release.demo/preview/")
        assert preview.status_code==200
        assert "Release 0.2" not in preview.text

        promote=client.post(
            f"/api/v1/control/homeserver-apps/release.demo/releases/{second_release['release_id']}/promote"
        )
        assert promote.status_code==200,promote.text
        assert promote.json()["release"]["release_id"]==second_release["release_id"]
        assert homeserver_apps.get("release.demo")["installed_version"]=="0.2.0"
        preview=client.get("/api/v1/control/homeserver-apps/release.demo/preview/")
        assert "Release 0.2" in preview.text

        degraded=client.post("/api/v1/control/homeserver-apps/release.demo/lifecycle",json={
            "state":"degraded","metadata":{"reason":"test"}
        })
        assert degraded.status_code==200,degraded.text
        recovered=client.post("/api/v1/control/homeserver-apps/release.demo/recover")
        assert recovered.status_code==200,recovered.text
        assert recovered.json()["release"]["release_id"]==first_release["release_id"]
        assert homeserver_apps.get("release.demo")["lifecycle_state"]=="running"

        already=client.post(
            f"/api/v1/control/homeserver-apps/release.demo/releases/{first_release['release_id']}/promote"
        )
        assert already.status_code==200
        assert already.json()["changed"] is False
        assert already.json()["reason"]=="already_active"

        # Retention never removes active or previous releases.
        pruned=homeserver_app_releases.prune("release.demo",keep=2)
        assert first_release["release_id"] not in pruned["removed"]
        assert second_release["release_id"] not in pruned["removed"]

        # VP3 system apps keep their release authority with VP3.
        prebuilt=client.post("/api/v1/control/homeserver-apps/catalog/prebuilt/vp3.notes/install")
        assert prebuilt.status_code==200,prebuilt.text
        blocked=client.post("/api/v1/control/homeserver-apps/vp3.notes/rollback")
        assert blocked.status_code==409
        sys_history=client.get("/api/v1/control/homeserver-apps/vp3.notes/releases")
        assert sys_history.status_code==200
        sys_release=sys_history.json()["active_release_id"]
        blocked_promote=client.post(
            f"/api/v1/control/homeserver-apps/vp3.notes/releases/{sys_release}/promote"
        )
        assert blocked_promote.status_code==409

        cap=client.get("/api/v1/control/homeserver-apps/capability").json()["releases"]
        assert cap["release_history"] is True
        assert cap["manual_promotion"] is True
        assert cap["rollback"] is True
        assert cap["failed_release_recovery"] is True
        assert cap["protected_system_app_release_control"] is True

        events=client.get("/api/v1/control/homeserver-apps/release.demo").json()["history"]
        names={event["event_type"] for event in events}
        assert "app.release.promoted" in names
        assert "app.release.rolled_back" in names
        assert "app.release.recovered" in names

    ui=(ROOT/"ui"/"homeserver-apps.js").read_text(encoding="utf-8")
    assert "/releases" in ui
    assert "data-hs-app-rollback" in ui
    assert "data-hs-app-recover" in ui
    assert "data-hs-app-promote" in ui
    assert "Releases" in ui

print("HomeServer Apps V1 Section 7 updates rollback and recovery: PASS")
