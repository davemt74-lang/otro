from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-media-player-v360-") as data_dir, tempfile.TemporaryDirectory(prefix="vp3-player-src-") as media_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir
    source=Path(media_dir)
    direct=source/"Movie.mp4"
    fallback=source/"Archive.mkv"
    direct.write_bytes(b"direct-video-bytes")
    fallback.write_bytes(b"fallback-video-bytes")
    originals={p.name:p.read_bytes() for p in source.iterdir()}

    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import homeserver_app_control, homeserver_media_player, homeserver_media_processor
    from app.services.tasks import scheduler

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        for app_key in ("vp3.media-server","vp3.media-processor","vp3.media-player"):
            response=client.post(f"/api/v1/control/homeserver-apps/catalog/prebuilt/{app_key}/install")
            assert response.status_code==200,response.text

        grant=client.put("/api/v1/control/homeserver-apps/vp3.media-server/permissions",json={
            "permission":"files.read","allowed":True
        })
        assert grant.status_code==200,grant.text

        mapped=client.post("/api/v1/control/homeserver-apps/media-server/mapped-roots",json={
            "path":str(source),"label":"Playback Sources","source_kind":"local_folder"
        })
        assert mapped.status_code==200,mapped.text
        scan=client.post("/api/v1/control/homeserver-apps/media-server/scan")
        assert scan.status_code==200,scan.text

        library=client.get("/api/v1/control/homeserver-apps/media-server/library",params={"media_type":"video","limit":20}).json()
        ids={row["name"]:row["media_id"] for row in library["items"]}
        assert {"Movie.mp4","Archive.mkv"}.issubset(ids)

        living=client.post("/api/v1/control/homeserver-apps/media-player/devices",json={
            "name":"Living Room TV","kind":"tv",
            "capabilities":{"direct_play":True,"video":["mp4","webm"],"max_height":2160},
            "device_id":"living-room"
        })
        assert living.status_code==200,living.text
        phone=client.post("/api/v1/control/homeserver-apps/media-player/devices",json={
            "name":"Phone","kind":"mobile","capabilities":{"direct_play":True},"device_id":"phone"
        })
        assert phone.status_code==200,phone.text

        created=client.post("/api/v1/control/homeserver-apps/media-player/sessions",json={
            "media_id":ids["Movie.mp4"],"device_id":"living-room","autoplay":True
        })
        assert created.status_code==200,created.text
        session=created.json()["session"]
        assert session["state"]=="playing"
        assert session["delivery_mode"]=="direct"
        assert session["stream_path"].endswith(ids["Movie.mp4"])
        assert session["filesystem_path_exposed"] is False

        seek=client.put(f"/api/v1/control/homeserver-apps/media-player/sessions/{session['session_id']}",json={
            "command":"seek","position_seconds":61.5,"duration_seconds":600
        })
        assert seek.status_code==200,seek.text
        assert seek.json()["session"]["position_seconds"]==61.5

        resume=client.get(f"/api/v1/control/homeserver-apps/media-server/item/{ids['Movie.mp4']}").json()["item"]["playback"]
        assert resume["position_seconds"]==61.5
        assert resume["duration_seconds"]==600
        assert resume["completed"] is False

        cont=client.get("/api/v1/control/homeserver-apps/media-player/continue-watching").json()
        assert any(row["media_id"]==ids["Movie.mp4"] for row in cont["items"])

        handed=client.post(f"/api/v1/control/homeserver-apps/media-player/sessions/{session['session_id']}/handoff",json={
            "device_id":"phone"
        })
        assert handed.status_code==200,handed.text
        assert handed.json()["session"]["device_id"]=="phone"
        assert handed.json()["session"]["position_seconds"]==61.5
        old=client.get(f"/api/v1/control/homeserver-apps/media-player/sessions/{session['session_id']}").json()
        assert old["session"]["state"]=="handed_off"

        original_enqueue=homeserver_media_processor.enqueue
        try:
            homeserver_media_processor.enqueue=lambda media_id,operation,preset="default",output_format="",priority=0,destination_id="app-storage": {
                "job":{"job_id":"job_playback_test","media_id":media_id,"operation":operation,"status":"queued"}
            }
            fallback_created=homeserver_media_player.create_session(ids["Archive.mkv"],"living-room",0,False)
        finally:
            homeserver_media_processor.enqueue=original_enqueue
        assert fallback_created["session"]["delivery_mode"]=="transcode"
        assert fallback_created["session"]["processor_job_id"]=="job_playback_test"
        assert fallback_created["source_file_modified"] is False
        assert fallback_created["source_file_deleted"] is False

        actions={row["key"]:row for row in homeserver_app_control.manifest("vp3.media-player")["actions"]}
        assert actions["player.play"]["risk"]=="write"
        assert actions["player.handoff"]["risk"]=="write"
        assert actions["player.brain-context"]["risk"]=="read"
        assert all(row["requires_confirmation"] is False for row in actions.values())

        generic=client.post("/api/v1/control/homeserver-apps/vp3.media-player/control/invoke",json={
            "action":"player.continue-watching","arguments":{"limit":10}
        })
        assert generic.status_code==200,generic.text
        assert generic.json()["result"]["count"]>=1

        brain=client.get("/api/v1/control/homeserver-apps/media-player/brain-context").json()
        assert brain["contract"]=="vp3.media-player.brain-context.v1"
        assert brain["governance"]["home_server_is_execution_authority"] is True
        assert brain["governance"]["source_files_never_modified_or_deleted"] is True
        assert brain["filesystem_paths_exposed"] is False
        assert media_dir not in str(brain)

        cap=client.get("/api/v1/control/homeserver-apps/media-player/capability").json()
        assert cap["direct_play"] is True
        assert cap["media_processor_transcode_fallback"] is True
        assert cap["cross_device_handoff"] is True
        assert cap["homeserver_execution_authority"] is True

        complete=client.put(f"/api/v1/control/homeserver-apps/media-player/sessions/{handed.json()['session']['session_id']}",json={
            "command":"complete","position_seconds":600,"duration_seconds":600
        })
        assert complete.status_code==200,complete.text
        assert complete.json()["session"]["state"]=="completed"
        cont2=client.get("/api/v1/control/homeserver-apps/media-player/continue-watching").json()
        assert all(row["media_id"]!=ids["Movie.mp4"] for row in cont2["items"])

        for path in source.iterdir():
            assert path.read_bytes()==originals[path.name]

print("HomeServer Section 22 Media Playback & Home Theater core: PASS")
