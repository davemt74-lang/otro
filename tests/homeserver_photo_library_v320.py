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

with tempfile.TemporaryDirectory(prefix="homeserver-photos-v320-") as data_dir, tempfile.TemporaryDirectory(prefix="vp3-mapped-photos-") as photo_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    source=Path(photo_dir)
    trip=source/"Trips"/"Sedona 2026"
    trip.mkdir(parents=True)
    p1=trip/"IMG_0001.jpg"
    p2=trip/"IMG_0002.jpg"
    p3=trip/"Screenshot 2026-09-30.png"
    p1.write_bytes(b"\xff\xd8\xff"+b"a"*100)
    p2.write_bytes(b"\xff\xd8\xff"+b"b"*100)
    p3.write_bytes(b"\x89PNG\r\n\x1a\n"+b"c"*95)

    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import (
        homeserver_app_control,
        homeserver_app_manager,
        homeserver_media_server,
        homeserver_photo_library,
        hosting_cloud_control,
        hosting_serving,
    )
    from app.services.tasks import scheduler

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        catalog=client.get("/api/v1/control/homeserver-apps/catalog/prebuilt")
        assert catalog.status_code==200,catalog.text
        by_key={row["key"]:row for row in catalog.json()["packages"]}
        assert "vp3.photo-library" in by_key
        assert by_key["vp3.photo-library"]["product_type"]=="vp3_optional_app"

        media_install=client.post("/api/v1/control/homeserver-apps/catalog/prebuilt/vp3.media-server/install")
        assert media_install.status_code==200,media_install.text
        photo_install=client.post("/api/v1/control/homeserver-apps/catalog/prebuilt/vp3.photo-library/install")
        assert photo_install.status_code==200,photo_install.text
        assert photo_install.json()["app"]["installed_version"]=="1.0.0"

        grant=client.put("/api/v1/control/homeserver-apps/vp3.media-server/permissions",json={
            "permission":"files.read","allowed":True
        })
        assert grant.status_code==200,grant.text

        mapped=client.post("/api/v1/control/homeserver-apps/media-server/mapped-roots",json={
            "path":str(source),
            "label":"Photo Archive",
            "computer_name":"Studio-PC",
            "source_hint":"D:\\Photos",
            "source_kind":"computer_folder",
        })
        assert mapped.status_code==200,mapped.text
        assert "D:\\Photos" not in mapped.text
        root=mapped.json()["root"]
        assert root["source_hint_configured"] is True
        assert "source_hint" not in root

        scan=client.post("/api/v1/control/homeserver-apps/media-server/scan")
        assert scan.status_code==200,scan.text
        assert scan.json()["types"]["image"]==3

        synced=client.post("/api/v1/control/homeserver-apps/photo-library/sync")
        assert synced.status_code==200,synced.text
        assert synced.json()["photos"]==3
        assert synced.json()["folder_albums"]==1

        status=client.get("/api/v1/control/homeserver-apps/photo-library/status")
        assert status.status_code==200,status.text
        assert status.json()["photos"]==3
        assert status.json()["mapped_sources"]==1
        assert status.json()["source_media_owned_by_photo_library"] is False
        assert status.json()["face_recognition_enabled"] is False

        photos=client.get("/api/v1/control/homeserver-apps/photo-library/photos")
        assert photos.status_code==200,photos.text
        body=photos.json()
        assert body["total"]==3
        assert {row["folder_album"] for row in body["photos"]}=={"Sedona 2026"}
        assert str(source) not in photos.text
        first=body["photos"][0]["media_id"]

        folders=client.get("/api/v1/control/homeserver-apps/photo-library/folders").json()
        assert folders["count"]==1
        assert folders["folders"][0]["folder_album"]=="Sedona 2026"

        fav=client.put(f"/api/v1/control/homeserver-apps/photo-library/favorites/{first}",json={"enabled":True})
        assert fav.status_code==200,fav.text
        tagged=client.put(f"/api/v1/control/homeserver-apps/photo-library/photos/{first}/tags",json={"tags":["Travel","Arizona","travel"]})
        assert tagged.status_code==200,tagged.text
        assert tagged.json()["tags"]==["travel","arizona"]
        tag_search=client.get("/api/v1/control/homeserver-apps/photo-library/photos",params={"tag":"travel"})
        assert tag_search.status_code==200
        assert tag_search.json()["total"]==1

        album=client.post("/api/v1/control/homeserver-apps/photo-library/albums",json={"name":"Best of Sedona"})
        assert album.status_code==200,album.text
        album_id=album.json()["album"]["album_id"]
        added=client.post(f"/api/v1/control/homeserver-apps/photo-library/albums/{album_id}/photos",json={"media_id":first})
        assert added.status_code==200,added.text
        assert len(added.json()["photos"])==1

        person=client.post("/api/v1/control/homeserver-apps/photo-library/people",json={"name":"Dave"})
        assert person.status_code==200,person.text
        person_id=person.json()["person_id"]
        assigned=client.put(f"/api/v1/control/homeserver-apps/photo-library/people/{person_id}/photos",json={"media_id":first,"enabled":True})
        assert assigned.status_code==200,assigned.text
        assert assigned.json()["recognition_enabled"] is False
        people=client.get("/api/v1/control/homeserver-apps/photo-library/people").json()
        assert people["people"][0]["photos"]==1
        assert people["people"][0]["recognition_enabled"] is False

        smart=client.get("/api/v1/control/homeserver-apps/photo-library/smart-albums").json()
        smart_map={row["key"]:row for row in smart["smart_albums"]}
        assert smart_map["favorites"]["photos"]==1
        assert smart_map["screenshots"]["photos"]==1

        dupes=client.get("/api/v1/control/homeserver-apps/photo-library/duplicates")
        assert dupes.status_code==200,dupes.text
        assert dupes.json()["content_hash_verified"] is False
        assert any(group["copies"]>=2 for group in dupes.json()["groups"])

        slide=client.get("/api/v1/control/homeserver-apps/photo-library/slideshow")
        assert slide.status_code==200,slide.text
        assert slide.json()["count"]==3

        # Universal Agent control is the canonical app-control path.
        compatibility=homeserver_app_control.compatibility("vp3.photo-library")
        assert compatibility["compatible"] is True
        assert compatibility["manifest_contract"]=="vp3.app.agent-actions.v2"
        manifest=homeserver_app_control.manifest("vp3.photo-library")
        actions={row["key"]:row for row in manifest["actions"]}
        assert actions["photos.search"]["risk"]=="read"
        assert actions["photos.album.delete"]["requires_confirmation"] is True

        generic=client.post("/api/v1/control/homeserver-apps/vp3.photo-library/control/invoke",json={
            "action":"photos.search","arguments":{"query":"IMG_0001"}
        })
        assert generic.status_code==200,generic.text
        assert generic.json()["result"]["total"]==1

        blocked=client.post("/api/v1/control/homeserver-apps/vp3.photo-library/control/invoke",json={
            "action":"photos.album.delete","arguments":{"album_id":album_id},"confirmed":False
        })
        assert blocked.status_code==409,blocked.text
        confirmed=client.post("/api/v1/control/homeserver-apps/vp3.photo-library/control/invoke",json={
            "action":"photos.album.delete","arguments":{"album_id":album_id},"confirmed":True
        })
        assert confirmed.status_code==200,confirmed.text
        assert confirmed.json()["result"]["source_files_deleted"] is False
        assert p1.is_file() and p2.is_file() and p3.is_file()

        # Private hosted Photo Library reuses Media Server access keys and image-only tickets.
        remote=client.post("/api/v1/control/homeserver-apps/media-server/remote/enable")
        assert remote.status_code==200,remote.text
        access_key=remote.json()["access_key"]
        original_get_site=hosting_serving.hosting_runtime.get_site
        original_binding=hosting_cloud_control.binding_for_site
        try:
            hosting_serving.hosting_runtime.get_site=lambda _site_id: {"state":"active","runtime_kind":"static"}
            hosting_cloud_control.binding_for_site=lambda _site_id: {"target_app_key":"vp3.photo-library"}
            try:
                hosting_serving.serve("site_photos","__vp3_photos__/status",request_headers={})
                raise AssertionError("Hosted Photo Library allowed anonymous access")
            except hosting_serving.ServingError as exc:
                assert exc.status_code==401

            hosted_status=hosting_serving.serve(
                "site_photos","__vp3_photos__/status",
                request_headers={"Authorization":"Bearer "+access_key},
            )
            assert hosted_status.status_code==200
            hosted_ticket=hosting_serving.serve(
                "site_photos",f"__vp3_photos__/stream-ticket/{first}",
                request_headers={"Authorization":"Bearer "+access_key},
            )
            ticket_payload=json.loads(hosted_ticket.body.decode("utf-8"))
            assert ticket_payload["stream_url"].startswith("/__vp3_photos__/stream/")
            hosted_stream=hosting_serving.serve(
                "site_photos",f"__vp3_photos__/stream/{first}",
                query_string="ticket="+ticket_payload["ticket"],
                request_headers={},
            )
            assert hosted_stream.status_code==200
        finally:
            hosting_serving.hosting_runtime.get_site=original_get_site
            hosting_cloud_control.binding_for_site=original_binding

        # Generation reconciliation scales beyond SQLite bind-variable limits and
        # removes stale organization references without touching source files.
        original_image_records=homeserver_media_server.image_source_records
        try:
            homeserver_media_server.image_source_records=lambda limit=100000: [
                {
                    "media_id":f"photo_bulk_{i}",
                    "root_id":root["root_id"],
                    "relative_path":f"Bulk/Album/{i:04d}.jpg",
                    "file_name":f"{i:04d}.jpg",
                    "title":f"{i:04d}",
                    "mime_type":"image/jpeg",
                    "extension":".jpg",
                    "size_bytes":100+i,
                    "mtime_ns":i+1,
                    "created_at":"2026-09-30 00:00:00",
                    "updated_at":"2026-09-30 00:00:00",
                }
                for i in range(1500)
            ]
            assert homeserver_photo_library.sync()["photos"]==1500
            homeserver_media_server.image_source_records=lambda limit=100000: []
            assert homeserver_photo_library.sync()["photos"]==0
            assert homeserver_photo_library.photos()["total"]==0
        finally:
            homeserver_media_server.image_source_records=original_image_records
            homeserver_photo_library.sync()

        manager=homeserver_app_manager.inventory()
        row=next(item for item in manager["items"] if item["app_key"]=="vp3.photo-library")
        assert row["installed"] is True
        assert row["photo_library"]["photos"]==3
        assert row["agent_control"]["complete"] is True

        cap=client.get("/api/v1/control/homeserver-apps/photo-library/capability").json()
        assert cap["private_hosted_viewing"] is True
        assert cap["people_placeholders"] is True
        assert cap["face_recognition_enabled"] is False
        assert cap["source_media_owned_by_photo_library"] is False

print("HomeServer Section 18 Photo Library: PASS")
