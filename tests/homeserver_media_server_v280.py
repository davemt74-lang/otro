from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-media-server-v280-") as data_dir, tempfile.TemporaryDirectory(prefix="vp3-media-library-") as media_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    library=Path(media_dir)
    video=library/"Movie One.mp4"
    audio=library/"Song One.mp3"
    image=library/"Photo One.jpg"
    ignored=library/"notes.txt"
    video.write_bytes(b"0123456789abcdef")
    audio.write_bytes(b"ID3"+b"a"*64)
    image.write_bytes(b"\xff\xd8\xff"+b"b"*64)
    ignored.write_text("not media",encoding="utf-8")

    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import homeserver_media_server
    from app.services.tasks import scheduler

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        catalog=client.get("/api/v1/control/homeserver-apps/catalog/prebuilt")
        assert catalog.status_code==200,catalog.text
        media_pkg=next(row for row in catalog.json()["packages"] if row["key"]=="vp3.media-server")
        assert media_pkg["product_type"]=="vp3_optional_app"
        assert media_pkg["core_homeserver_feature"] is False
        assert "hosted_subdomain" in media_pkg["deployment_modes"]

        install=client.post("/api/v1/control/homeserver-apps/catalog/prebuilt/vp3.media-server/install")
        assert install.status_code==200,install.text
        assert install.json()["app"]["installed_version"]=="1.0.0"

        perms=client.get("/api/v1/control/homeserver-apps/vp3.media-server/permissions")
        assert perms.status_code==200,perms.text
        file_perm=next(row for row in perms.json()["permissions"]["permissions"] if row["permission"]=="files.read")
        assert file_perm["allowed"] is False

        blocked=client.post("/api/v1/control/homeserver-apps/media-server/roots",json={"path":str(library),"label":"Movies"})
        assert blocked.status_code==403,blocked.text

        grant_perm=client.put("/api/v1/control/homeserver-apps/vp3.media-server/permissions",json={"permission":"files.read","allowed":True})
        assert grant_perm.status_code==200,grant_perm.text

        grant=client.post("/api/v1/control/homeserver-apps/media-server/roots",json={"path":str(library),"label":"My Media"})
        assert grant.status_code==200,grant.text
        root=grant.json()["root"]
        assert root["absolute_path_exposed"] is False
        assert str(library) not in str(grant.json())

        scan=client.post("/api/v1/control/homeserver-apps/media-server/scan")
        assert scan.status_code==200,scan.text
        scan_body=scan.json()
        assert scan_body["scanned"]==3
        assert scan_body["library_count"]==3
        assert scan_body["types"]=={"video":1,"audio":1,"image":1}
        assert scan_body["transcoding"] is False

        listing=client.get("/api/v1/control/homeserver-apps/media-server/library")
        assert listing.status_code==200,listing.text
        body=listing.json()
        assert body["total"]==3
        assert str(library) not in str(body)
        ids={row["media_type"]:row["media_id"] for row in body["items"]}
        assert set(ids)=={"video","audio","image"}

        videos=client.get("/api/v1/control/homeserver-apps/media-server/library",params={"media_type":"video","q":"Movie"})
        assert videos.status_code==200
        assert videos.json()["count"]==1

        stream=client.get(
            f"/api/v1/control/homeserver-apps/media-server/stream/{ids['video']}",
            headers={"Range":"bytes=0-3"},
        )
        assert stream.status_code==206,stream.text
        assert stream.content==b"0123"
        assert stream.headers.get("accept-ranges")=="bytes"
        assert stream.headers.get("x-vp3-media-id")==ids["video"]

        playback=client.put(
            f"/api/v1/control/homeserver-apps/media-server/playback/{ids['video']}",
            json={"position_seconds":12.5,"duration_seconds":120,"completed":False},
        )
        assert playback.status_code==200,playback.text
        assert playback.json()["item"]["playback"]["position_seconds"]==12.5

        status=client.get("/api/v1/control/homeserver-apps/media-server/status")
        assert status.status_code==200,status.text
        state=status.json()
        assert state["library_count"]==3
        assert state["source_media_owned_by_app"] is False
        assert state["transcoding"] is False

        remote=client.post("/api/v1/control/homeserver-apps/media-server/remote/enable")
        assert remote.status_code==200,remote.text
        access_key=remote.json()["access_key"]
        assert homeserver_media_server.authenticate_remote(access_key) is True
        ticket=homeserver_media_server.stream_ticket(ids["video"],ttl_seconds=60)
        assert homeserver_media_server.authenticate_stream_ticket(ids["video"],ticket["ticket"]) is True
        assert access_key not in ticket["stream_url"]

        # File changed after scan: stale index blocks playback until owner rescans.
        time.sleep(0.001)
        video.write_bytes(b"changed-media-content")
        stale=client.get(f"/api/v1/control/homeserver-apps/media-server/stream/{ids['video']}")
        assert stale.status_code==409,stale.text
        rescanned=client.post("/api/v1/control/homeserver-apps/media-server/scan")
        assert rescanned.status_code==200,rescanned.text

        remove=client.delete(f"/api/v1/control/homeserver-apps/media-server/roots/{root['root_id']}")
        assert remove.status_code==200,remove.text
        assert remove.json()["media_files_deleted"] is False
        assert video.is_file() and audio.is_file() and image.is_file()

        manager=client.get("/api/v1/control/homeserver-apps/manager").json()
        media_row=next(row for row in manager["items"] if row["app_key"]=="vp3.media-server")
        assert media_row["installed"] is True
        assert media_row["media_server"]["source_media_owned_by_app"] is False
        assert media_row["actions"]["host"] is True

        capability=client.get("/api/v1/control/homeserver-apps/media-server/capability").json()
        assert capability["owner_granted_media_roots"] is True
        assert capability["short_lived_stream_tickets"] is True
        assert capability["source_media_deleted_on_uninstall"] is False
        assert capability["transcoding"] is False
        assert capability["dlna"] is False

print("HomeServer Section 14 VP3 Media Server: PASS")
