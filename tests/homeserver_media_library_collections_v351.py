from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-media-library-collections-v351-") as data_dir, tempfile.TemporaryDirectory(prefix="vp3-media-collections-src-") as media_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir
    source=Path(media_dir)
    photo=source/"Trip"/"Sunset.jpg"
    audio=source/"Trip"/"Road Song.mp3"
    photo.parent.mkdir(parents=True)
    photo_bytes=b"photo-source"
    audio_bytes=b"audio-source"
    photo.write_bytes(photo_bytes)
    audio.write_bytes(audio_bytes)

    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import homeserver_app_control
    from app.services.tasks import scheduler

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200
        for key in ("vp3.media-server","vp3.media-library"):
            r=client.post(f"/api/v1/control/homeserver-apps/catalog/prebuilt/{key}/install")
            assert r.status_code==200,r.text

        assert client.put("/api/v1/control/homeserver-apps/vp3.media-server/permissions",json={
            "permission":"files.read","allowed":True
        }).status_code==200
        assert client.post("/api/v1/control/homeserver-apps/media-server/mapped-roots",json={
            "path":str(source),"label":"Collection Sources","source_kind":"local_folder"
        }).status_code==200
        scan=client.post("/api/v1/control/homeserver-apps/media-server/scan")
        assert scan.status_code==200,scan.text
        library=client.get("/api/v1/control/homeserver-apps/media-server/library",params={"limit":50}).json()
        by_type={row["media_type"]:row["media_id"] for row in library["items"]}
        assert {"image","audio"}.issubset(by_type)
        image_id=by_type["image"]
        audio_id=by_type["audio"]

        # Shared metadata drives smart collection membership.
        for media_id,patch in (
            (image_id,{"tags":["trip","arizona"],"favorite":True,"rating":5}),
            (audio_id,{"tags":["trip","road"],"favorite":True,"rating":4}),
        ):
            r=client.put(f"/api/v1/control/homeserver-apps/media-library/items/{media_id}",json={
                "patch":patch,"source":"section21b.acceptance"
            })
            assert r.status_code==200,r.text

        manual=client.post("/api/v1/control/homeserver-apps/media-library/collections",json={
            "name":"Arizona Road Trip",
            "description":"Cross-media trip collection",
            "collection_type":"manual"
        })
        assert manual.status_code==200,manual.text
        manual_id=manual.json()["collection"]["collection_id"]
        for position,media_id in enumerate((image_id,audio_id),start=1):
            added=client.post(f"/api/v1/control/homeserver-apps/media-library/collections/{manual_id}/items",json={
                "media_id":media_id,"position":position
            })
            assert added.status_code==200,added.text

        manual_get=client.get(f"/api/v1/control/homeserver-apps/media-library/collections/{manual_id}")
        assert manual_get.status_code==200,manual_get.text
        body=manual_get.json()
        assert body["count"]==2
        assert body["cross_media"] is True
        assert {row["canonical"]["media_type"] for row in body["items"]}=={"image","audio"}
        assert media_dir not in manual_get.text

        smart=client.post("/api/v1/control/homeserver-apps/media-library/collections",json={
            "name":"Favorite Trip Media",
            "collection_type":"smart",
            "rules":{"tag":"trip","favorite":True,"rating_min":4}
        })
        assert smart.status_code==200,smart.text
        smart_id=smart.json()["collection"]["collection_id"]
        smart_get=client.get(f"/api/v1/control/homeserver-apps/media-library/collections/{smart_id}")
        assert smart_get.status_code==200,smart_get.text
        assert smart_get.json()["count"]==2

        smart_list=client.get("/api/v1/control/homeserver-apps/media-library/collections/smart")
        assert smart_list.status_code==200,smart_list.text
        assert smart_list.json()["count"]==1
        assert smart_list.json()["collections"][0]["collection_id"]==smart_id

        # Manual membership cannot mutate smart collection rules.
        rejected=client.post(f"/api/v1/control/homeserver-apps/media-library/collections/{smart_id}/items",json={
            "media_id":image_id
        })
        assert rejected.status_code==409,rejected.text

        collections=client.get("/api/v1/control/homeserver-apps/media-library/collections")
        assert collections.status_code==200,collections.text
        assert collections.json()["count"]==2
        counts={row["name"]:row["count"] for row in collections.json()["collections"]}
        assert counts["Arizona Road Trip"]==2
        assert counts["Favorite Trip Media"]==2

        brain=client.get("/api/v1/control/homeserver-apps/media-library/brain-context")
        assert brain.status_code==200,brain.text
        assert brain.json()["summary"]["collections"]==2
        assert brain.json()["summary"]["smart_collections"]==1
        assert media_dir not in brain.text

        actions={row["key"]:row for row in homeserver_app_control.manifest("vp3.media-library")["actions"]}
        assert actions["library.collection.create"]["risk"]=="write"
        assert actions["library.collection.delete"]["risk"]=="destructive"
        assert actions["library.collection.delete"]["requires_confirmation"] is True

        blocked=client.post("/api/v1/control/homeserver-apps/vp3.media-library/control/invoke",json={
            "action":"library.collection.delete","arguments":{"collection_id":manual_id},"confirmed":False
        })
        assert blocked.status_code==409,blocked.text
        deleted=client.post("/api/v1/control/homeserver-apps/vp3.media-library/control/invoke",json={
            "action":"library.collection.delete","arguments":{"collection_id":manual_id},"confirmed":True
        })
        assert deleted.status_code==200,deleted.text
        assert deleted.json()["result"]["media_files_deleted"] is False
        assert deleted.json()["result"]["metadata_deleted"] is False

        assert photo.read_bytes()==photo_bytes
        assert audio.read_bytes()==audio_bytes

        cap=client.get("/api/v1/control/homeserver-apps/media-library/capability").json()
        assert cap["manual_collections"] is True
        assert cap["smart_collections"] is True
        assert cap["saved_filters"] is True
        assert cap["cross_media_collections"] is True

print("HomeServer Section 21B Collections & Smart Libraries: PASS")
