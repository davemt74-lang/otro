from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-media-library-v350-") as data_dir, tempfile.TemporaryDirectory(prefix="vp3-media-library-src-") as media_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir
    source=Path(media_dir)
    photo=source/"Vacation"/"Sunset.jpg"
    photo.parent.mkdir(parents=True)
    original=b"source-photo-bytes"
    photo.write_bytes(original)

    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import homeserver_app_control, homeserver_app_manager
    from app.services.tasks import scheduler

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        for app_key in ("vp3.media-server","vp3.media-library"):
            response=client.post(f"/api/v1/control/homeserver-apps/catalog/prebuilt/{app_key}/install")
            assert response.status_code==200,response.text

        grant=client.put("/api/v1/control/homeserver-apps/vp3.media-server/permissions",json={
            "permission":"files.read","allowed":True
        })
        assert grant.status_code==200,grant.text
        mapped=client.post("/api/v1/control/homeserver-apps/media-server/mapped-roots",json={
            "path":str(source),"label":"Metadata Sources","source_kind":"local_folder"
        })
        assert mapped.status_code==200,mapped.text
        scan=client.post("/api/v1/control/homeserver-apps/media-server/scan")
        assert scan.status_code==200,scan.text

        library=client.get("/api/v1/control/homeserver-apps/media-server/library",params={"media_type":"image"}).json()
        assert library["count"]==1
        media_id=library["items"][0]["media_id"]

        initial=client.get(f"/api/v1/control/homeserver-apps/media-library/items/{media_id}")
        assert initial.status_code==200,initial.text
        assert initial.json()["item"]["canonical"]["title"]=="Sunset"
        assert initial.json()["item"]["metadata"]["tags"]==[]
        assert initial.json()["item"]["source_file_modified"] is False
        assert media_dir not in initial.text

        updated=client.put(f"/api/v1/control/homeserver-apps/media-library/items/{media_id}",json={
            "patch":{
                "title":"Arizona Sunset",
                "description":"Golden-hour desert light",
                "rating":5,
                "favorite":True,
                "tags":["Travel","Arizona","travel"],
                "custom_fields":{"camera":"VP3 Test Cam","keepers":True},
                "relations":[
                    {"type":"person","value":"person_dave","label":"Dave"},
                    {"type":"collection","value":"trip_2026","label":"Arizona 2026"}
                ]
            },
            "actor":"user:test",
            "source":"section21.acceptance",
            "reason":"Metadata enrichment"
        })
        assert updated.status_code==200,updated.text
        body=updated.json()
        history_id=body["history_id"]
        meta=body["item"]["metadata"]
        assert meta["title"]=="Arizona Sunset"
        assert meta["description"]=="Golden-hour desert light"
        assert meta["rating"]==5
        assert meta["favorite"] is True
        assert meta["tags"]==["travel","arizona"]
        assert meta["custom_fields"]["camera"]=="VP3 Test Cam"
        assert len(meta["relations"])==2
        assert body["source_file_modified"] is False
        assert photo.read_bytes()==original

        searched=client.get("/api/v1/control/homeserver-apps/media-library/search",params={
            "q":"desert","tag":"travel","favorite":"true","limit":50
        })
        assert searched.status_code==200,searched.text
        assert searched.json()["count"]==1
        assert searched.json()["items"][0]["media_id"]==media_id
        assert media_dir not in searched.text

        history=client.get(f"/api/v1/control/homeserver-apps/media-library/items/{media_id}/history")
        assert history.status_code==200,history.text
        assert history.json()["count"]==1
        row=history.json()["history"][0]
        assert row["history_id"]==history_id
        assert row["actor"]=="user:test"
        assert row["source"]=="section21.acceptance"
        assert row["reason"]=="Metadata enrichment"

        undone=client.post(f"/api/v1/control/homeserver-apps/media-library/history/{history_id}/undo")
        assert undone.status_code==200,undone.text
        restored=undone.json()["item"]["metadata"]
        assert restored["title"]==""
        assert restored["description"]==""
        assert restored["rating"] is None
        assert restored["favorite"] is False
        assert restored["tags"]==[]
        assert photo.read_bytes()==original

        duplicate_undo=client.post(f"/api/v1/control/homeserver-apps/media-library/history/{history_id}/undo")
        assert duplicate_undo.status_code==409,duplicate_undo.text

        compatibility=homeserver_app_control.compatibility("vp3.media-library")
        assert compatibility["compatible"] is True
        assert compatibility["manifest_contract"]=="vp3.app.agent-actions.v2"
        actions={row["key"]:row for row in homeserver_app_control.manifest("vp3.media-library")["actions"]}
        assert actions["library.item.update"]["risk"]=="write"
        assert actions["library.undo"]["requires_confirmation"] is True
        assert actions["library.search"]["risk"]=="read"

        generic=client.post("/api/v1/control/homeserver-apps/vp3.media-library/control/invoke",json={
            "action":"library.item.get","arguments":{"media_id":media_id}
        })
        assert generic.status_code==200,generic.text
        assert generic.json()["result"]["item"]["media_id"]==media_id

        brain=client.get("/api/v1/control/homeserver-apps/media-library/brain-context")
        assert brain.status_code==200,brain.text
        assert brain.json()["contract"]=="vp3.media-library.brain-context.v1"
        assert brain.json()["source_files_modified"] is False
        assert brain.json()["filesystem_paths_exposed"] is False
        assert media_dir not in brain.text

        manager=homeserver_app_manager.inventory()
        row=next(item for item in manager["items"] if item["app_key"]=="vp3.media-library")
        assert row["installed"] is True
        assert row["media_library"]["canonical_source"]=="vp3.media-server"
        assert row["agent_control"]["complete"] is True

        cap=client.get("/api/v1/control/homeserver-apps/media-library/capability").json()
        assert cap["shared_metadata_authority"] is True
        assert cap["metadata_history"] is True
        assert cap["undo"] is True
        assert cap["source_file_writes"] is False
        assert cap["source_file_deletes"] is False

print("HomeServer Section 21A Media Metadata Core: PASS")
