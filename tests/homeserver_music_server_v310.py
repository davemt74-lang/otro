from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-music-v310-") as data_dir, tempfile.TemporaryDirectory(prefix="vp3-mapped-music-") as music_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    source=Path(music_dir)
    album=source/"The Example Band"/"First Album"
    album.mkdir(parents=True)
    t1=album/"01 Opening Track.mp3"
    t2=album/"02 Second Track.flac"
    t1.write_bytes(b"ID3"+b"a"*80)
    t2.write_bytes(b"fLaC"+b"b"*96)

    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import homeserver_app_control, homeserver_app_manager, homeserver_media_server, homeserver_music_server
    from app.services.tasks import scheduler

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        catalog=client.get("/api/v1/control/homeserver-apps/catalog/prebuilt")
        assert catalog.status_code==200,catalog.text
        by_key={row["key"]:row for row in catalog.json()["packages"]}
        assert "vp3.media-server" in by_key
        assert "vp3.music-server" in by_key
        assert by_key["vp3.music-server"]["product_type"]=="vp3_optional_app"

        media_install=client.post("/api/v1/control/homeserver-apps/catalog/prebuilt/vp3.media-server/install")
        assert media_install.status_code==200,media_install.text
        music_install=client.post("/api/v1/control/homeserver-apps/catalog/prebuilt/vp3.music-server/install")
        assert music_install.status_code==200,music_install.text

        grant_perm=client.put("/api/v1/control/homeserver-apps/vp3.media-server/permissions",json={
            "permission":"files.read","allowed":True
        })
        assert grant_perm.status_code==200,grant_perm.text

        mapped=client.post("/api/v1/control/homeserver-apps/media-server/mapped-roots",json={
            "path":str(source),
            "label":"Dave Music",
            "computer_name":"Studio-PC",
            "source_hint":"D:\\Music",
            "source_kind":"computer_folder",
        })
        assert mapped.status_code==200,mapped.text
        root=mapped.json()["root"]
        assert root["source_kind"]=="computer_folder"
        assert root["computer_name"]=="Studio-PC"
        assert root["source_hint"]=="D:\\Music"
        assert root["connected"] is True
        assert mapped.json()["mapped_from_computer"] is True
        assert mapped.json()["source_files_copied"] is False
        assert str(source) not in mapped.text

        roots=client.get("/api/v1/control/homeserver-apps/media-server/roots")
        assert roots.status_code==200,roots.text
        assert roots.json()["mapped_sources"]==1
        assert str(source) not in roots.text

        checked=client.post(f"/api/v1/control/homeserver-apps/media-server/roots/{root['root_id']}/check")
        assert checked.status_code==200,checked.text
        assert checked.json()["root"]["connected"] is True

        scanned=client.post("/api/v1/control/homeserver-apps/media-server/scan")
        assert scanned.status_code==200,scanned.text
        assert scanned.json()["types"]["audio"]==2

        synced=client.post("/api/v1/control/homeserver-apps/music-server/sync")
        assert synced.status_code==200,synced.text
        assert synced.json()["tracks"]==2
        assert synced.json()["artists"]==1
        assert synced.json()["albums"]==1

        status=client.get("/api/v1/control/homeserver-apps/music-server/status")
        assert status.status_code==200,status.text
        state=status.json()
        assert state["tracks"]==2
        assert state["mapped_sources"]==1
        assert state["source_media_owned_by_music_server"] is False

        tracks=client.get("/api/v1/control/homeserver-apps/music-server/tracks")
        assert tracks.status_code==200,tracks.text
        body=tracks.json()
        assert body["total"]==2
        assert {row["artist"] for row in body["tracks"]}=={"The Example Band"}
        assert {row["album"] for row in body["tracks"]}=={"First Album"}
        assert {row["track_no"] for row in body["tracks"]}=={1,2}
        assert str(source) not in tracks.text
        first=body["tracks"][0]["media_id"]

        artists=client.get("/api/v1/control/homeserver-apps/music-server/artists").json()
        assert artists["artists"][0]["artist"]=="The Example Band"
        albums=client.get("/api/v1/control/homeserver-apps/music-server/albums",params={"artist":"The Example Band"}).json()
        assert albums["albums"][0]["album"]=="First Album"

        fav=client.put(f"/api/v1/control/homeserver-apps/music-server/favorites/{first}",json={"enabled":True})
        assert fav.status_code==200,fav.text
        assert client.get("/api/v1/control/homeserver-apps/music-server/favorites").json()["count"]==1

        pl=client.post("/api/v1/control/homeserver-apps/music-server/playlists",json={"name":"Favorites Mix"})
        assert pl.status_code==200,pl.text
        playlist_id=pl.json()["playlist"]["playlist_id"]
        added=client.post(f"/api/v1/control/homeserver-apps/music-server/playlists/{playlist_id}/tracks",json={"media_id":first})
        assert added.status_code==200,added.text
        assert len(added.json()["tracks"])==1

        queued=client.post("/api/v1/control/homeserver-apps/music-server/queue",json={"media_id":first})
        assert queued.status_code==200,queued.text
        playing=client.post("/api/v1/control/homeserver-apps/music-server/playback",json={
            "command":"play","media_id":first,"position_seconds":0
        })
        assert playing.status_code==200,playing.text
        assert playing.json()["playing"] is True
        assert playing.json()["current_media_id"]==first

        # Universal Section 16 control path is the canonical Agent control path.
        compatibility=homeserver_app_control.compatibility("vp3.music-server")
        assert compatibility["compatible"] is True
        manifest=homeserver_app_control.manifest("vp3.music-server")
        actions={row["key"]:row for row in manifest["actions"]}
        assert actions["music.search"]["risk"]=="read"
        assert actions["music.playlist.delete"]["requires_confirmation"] is True
        generic=client.post("/api/v1/control/homeserver-apps/vp3.music-server/control/invoke",json={
            "action":"music.search","arguments":{"query":"Opening"}
        })
        assert generic.status_code==200,generic.text
        assert generic.json()["result"]["total"]==1

        blocked=client.post("/api/v1/control/homeserver-apps/vp3.music-server/control/invoke",json={
            "action":"music.playlist.delete","arguments":{"playlist_id":playlist_id},"confirmed":False
        })
        assert blocked.status_code==409,blocked.text
        confirmed=client.post("/api/v1/control/homeserver-apps/vp3.music-server/control/invoke",json={
            "action":"music.playlist.delete","arguments":{"playlist_id":playlist_id},"confirmed":True
        })
        assert confirmed.status_code==200,confirmed.text
        assert confirmed.json()["result"]["source_files_deleted"] is False
        assert t1.is_file() and t2.is_file()

        manager=homeserver_app_manager.inventory()
        music_row=next(row for row in manager["items"] if row["app_key"]=="vp3.music-server")
        assert music_row["installed"] is True
        assert music_row["music_server"]["tracks"]==2
        assert music_row["agent_control"]["complete"] is True

        music_cap=client.get("/api/v1/control/homeserver-apps/music-server/capability").json()
        assert music_cap["mapped_computer_folders"] is True
        assert music_cap["universal_agent_control"] is True
        media_cap=client.get("/api/v1/control/homeserver-apps/media-server/capability").json()
        assert media_cap["mapped_computer_folders"] is True
        assert media_cap["mapped_source_health"] is True

print("HomeServer Section 17 Music Server + mapped computer folders: PASS")
