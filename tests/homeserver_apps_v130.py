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

with tempfile.TemporaryDirectory(prefix="homeserver-apps-v130-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import homeserver_app_resources, homeserver_app_runtime, homeserver_apps  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    with TestClient(app) as client:
        scheduler.stop()
        # Preview/data/runtime surfaces are owner-controlled.
        assert client.get("/api/v1/control/homeserver-apps/runtime.demo/preview/").status_code in {401,403}
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        created=client.post("/api/v1/control/homeserver-apps",json={
            "app_key":"runtime.demo","name":"Runtime Demo","source_type":"user_created","runtime":"static"
        })
        assert created.status_code==200,created.text
        project=Path(data_dir)/"apps"/"runtime.demo"
        jobs_path=project/"runtime"/"jobs.json"
        events_path=project/"runtime"/"events.json"
        jobs_path.write_text(json.dumps({
            "contract":"vp3.app.jobs.v1",
            "jobs":[{
                "job_id":"heartbeat",
                "enabled":True,
                "interval_seconds":60,
                "action":{"type":"event.emit","topic":"heartbeat","payload":{"ok":True}},
            }],
        },indent=2)+"\n",encoding="utf-8")
        events_path.write_text(json.dumps({
            "contract":"vp3.app.events.v1",
            "subscriptions":["system.online","profile.updated"],
        },indent=2)+"\n",encoding="utf-8")

        installed=client.post("/api/v1/control/homeserver-apps/runtime.demo/build-install")
        assert installed.status_code==200,installed.text
        first_release=installed.json()["release"]["release_id"]

        # The protected local route serves the installed app, not source files.
        preview=client.get("/api/v1/control/homeserver-apps/runtime.demo/preview/")
        assert preview.status_code==200,preview.text
        assert "Ready to build" in preview.text
        asset=client.get("/api/v1/control/homeserver-apps/runtime.demo/preview/assets/app.css")
        assert asset.status_code==200
        for reserved in ("vp3-app.json","settings.schema.json","runtime/jobs.json","agent/actions.json","database/migrations/.gitkeep"):
            response=client.get(f"/api/v1/control/homeserver-apps/runtime.demo/preview/{reserved}")
            assert response.status_code==403,(reserved,response.status_code,response.text)

        runtime=client.get("/api/v1/control/homeserver-apps/runtime.demo/runtime/services")
        assert runtime.status_code==200,runtime.text
        rp=runtime.json()["runtime"]
        assert rp["contract"]=="vp3.app.runtime-services.v1"
        assert rp["state"]=="running"
        assert rp["subscriptions"]==["system.online","profile.updated"]
        jobs=rp["jobs"]
        assert len(jobs)==1
        assert jobs[0]["job_id"]=="heartbeat"
        assert jobs[0]["action"]["type"]=="event.emit"

        # Sample data ships with the SDK but remains admin-controlled and
        # separate from production app data.
        sample_off=client.get("/api/v1/control/homeserver-apps/runtime.demo/sample-data")
        assert sample_off.status_code==200,sample_off.text
        sample_payload=sample_off.json()["sample_data"]
        assert sample_payload["available"] is True
        assert sample_payload["enabled"] is False
        assert sample_payload["items"]==[]
        enabled=client.put("/api/v1/control/homeserver-apps/admin/sample-data",json={"enabled":True})
        assert enabled.status_code==200,enabled.text
        assert enabled.json()["sample_data"]["enabled"] is True
        sample_on=client.get("/api/v1/control/homeserver-apps/runtime.demo/sample-data").json()["sample_data"]
        assert sample_on["enabled"] is True
        assert sample_on["item_count"]>=1
        assert sample_on["items"][0]["id"]=="welcome"
        disabled=client.put("/api/v1/control/homeserver-apps/admin/sample-data",json={"enabled":False})
        assert disabled.status_code==200
        assert client.get("/api/v1/control/homeserver-apps/runtime.demo/sample-data").json()["sample_data"]["items"]==[]

        # Event stream is durable and scoped to this app.
        emitted=client.post("/api/v1/control/homeserver-apps/runtime.demo/runtime/events",json={
            "topic":"note.created","payload":{"id":7}
        })
        assert emitted.status_code==200,emitted.text
        event_id=emitted.json()["event"]["id"]
        listed=client.get("/api/v1/control/homeserver-apps/runtime.demo/runtime/events")
        assert listed.status_code==200
        assert any(item["id"]==event_id and item["payload"]=={"id":7} for item in listed.json()["events"])

        ran=client.post("/api/v1/control/homeserver-apps/runtime.demo/runtime/jobs/heartbeat/run")
        assert ran.status_code==200,ran.text
        assert ran.json()["run"]["status"]=="succeeded"
        heartbeat=client.get("/api/v1/control/homeserver-apps/runtime.demo/runtime/events?topic=heartbeat").json()["events"]
        assert heartbeat and heartbeat[0]["payload"]=={"ok":True}

        # Due-job scheduler executes registered interval jobs.
        connection=homeserver_app_runtime._connect("runtime.demo")
        try:
            connection.execute("UPDATE vp3_app_jobs SET next_run_at=0 WHERE job_id='heartbeat'")
            connection.commit()
        finally:
            connection.close()
        assert homeserver_app_runtime.run_due_jobs()>=1
        heartbeat2=client.get("/api/v1/control/homeserver-apps/runtime.demo/runtime/events?topic=heartbeat").json()["events"]
        assert len(heartbeat2)>=2

        # SDK data API remains inside this app's quota-governed isolated root.
        write=client.put(
            "/api/v1/control/homeserver-apps/runtime.demo/data/file?path=state/demo.txt",
            files={"file":("demo.txt",b"runtime-state","text/plain")},
        )
        assert write.status_code==200,write.text
        read=client.get("/api/v1/control/homeserver-apps/runtime.demo/data/file?path=state/demo.txt")
        assert read.status_code==200
        assert read.content==b"runtime-state"

        other=client.post("/api/v1/control/homeserver-apps",json={
            "app_key":"runtime.other","name":"Other Runtime","source_type":"user_created","runtime":"static"
        })
        assert other.status_code==200
        assert homeserver_app_resources.write_file("runtime.other","state/demo.txt",b"other")["size_bytes"]==5
        assert homeserver_app_resources.read_file("runtime.demo","state/demo.txt")==b"runtime-state"
        assert homeserver_app_resources.read_file("runtime.other","state/demo.txt")==b"other"

        deleted=client.delete("/api/v1/control/homeserver-apps/runtime.demo/data/file?path=state/demo.txt")
        assert deleted.status_code==200
        assert deleted.json()["deleted"] is True

        # Invalid runtime jobs fail before release/lifecycle activation.
        manifest_path=project/"vp3-app.json"
        manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["version"]="0.2.0"
        manifest_path.write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
        jobs_path.write_text(json.dumps({
            "contract":"vp3.app.jobs.v1",
            "jobs":[{
                "job_id":"unsafe",
                "enabled":True,
                "interval_seconds":60,
                "action":{"type":"shell","command":"whoami"},
            }],
        }),encoding="utf-8")
        rejected=client.post("/api/v1/control/homeserver-apps/runtime.demo/build-install")
        assert rejected.status_code==400,rejected.text
        current=homeserver_apps.get("runtime.demo")
        assert current["lifecycle_state"]=="running"
        status=client.get("/api/v1/control/homeserver-apps/runtime.demo/runtime").json()["runtime"]
        assert status["active_release_id"]==first_release
        assert status["installed_version"]=="0.1.0"

        # Missing runtime contract file also fails package validation.
        jobs_path.unlink()
        validation=client.post("/api/v1/control/homeserver-apps/runtime.demo/build-install")
        assert validation.status_code==400

        cap=client.get("/api/v1/control/homeserver-apps/capability").json()["runtime_services"]
        assert cap["local_owner_preview"] is True
        assert cap["durable_app_events"] is True
        assert cap["interval_jobs"] is True
        assert cap["arbitrary_shell_commands"] is False
        assert cap["php_open_basedir"] is True

        history=client.get("/api/v1/control/homeserver-apps/runtime.demo").json()["history"]
        assert any(item["event_type"]=="app.runtime.synced" for item in history)

    homeserver_app_runtime.stop()

print("HomeServer Apps V1 Section 4 runtime routes data events and jobs: PASS")
