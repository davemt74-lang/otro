from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-media-library-artwork-v353-") as data_dir, tempfile.TemporaryDirectory(prefix="vp3-artwork-src-") as source_dir, tempfile.TemporaryDirectory(prefix="vp3-artwork-tools-") as tools_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir
    os.environ["HOMESERVER_MEDIA_TOOLS_DIR"]=tools_dir

    source=Path(source_dir)
    video=source/"Trip Video.mp4"
    image_a=source/"Cover A.jpg"
    image_b=source/"Cover B.jpg"
    video.write_bytes(b"video-source")
    image_a.write_bytes(b"image-a-source")
    image_b.write_bytes(b"image-b-source")
    originals={p.name:p.read_bytes() for p in (video,image_a,image_b)}

    tools_root=Path(tools_dir)
    ffmpeg=tools_root/("ffmpeg.exe" if os.name=="nt" else "ffmpeg")
    ffprobe=tools_root/("ffprobe.exe" if os.name=="nt" else "ffprobe")
    ffmpeg.write_bytes(b"managed-ffmpeg")
    ffprobe.write_bytes(b"managed-ffprobe")
    (tools_root/"manifest.json").write_text(json.dumps({
        "contract":"vp3.homeserver.media-tools.v1",
        "version":"9.0.2",
        "source":"https://www.gyan.dev/ffmpeg/builds/",
        "ffmpeg_sha256":hashlib.sha256(ffmpeg.read_bytes()).hexdigest(),
        "ffprobe_sha256":hashlib.sha256(ffprobe.read_bytes()).hexdigest(),
    }),encoding="utf-8")

    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import (
        homeserver_app_control,
        homeserver_media_library,
        homeserver_media_processor,
        homeserver_media_tools,
    )
    from app.services.tasks import scheduler

    original_version=homeserver_media_tools._version
    original_probe=homeserver_media_processor._probe_duration
    original_run=homeserver_media_processor._run_ffmpeg
    original_worker=homeserver_media_processor._ensure_worker

    homeserver_media_tools._version=lambda path: "ffmpeg version 9.0.2" if "probe" not in path.name else "ffprobe version 9.0.2"
    homeserver_media_tools.invalidate_cache()

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        for key in ("vp3.media-server","vp3.media-processor","vp3.media-library"):
            r=client.post(f"/api/v1/control/homeserver-apps/catalog/prebuilt/{key}/install")
            assert r.status_code==200,r.text

        assert client.put("/api/v1/control/homeserver-apps/vp3.media-server/permissions",json={
            "permission":"files.read","allowed":True
        }).status_code==200

        assert client.post("/api/v1/control/homeserver-apps/media-server/mapped-roots",json={
            "path":str(source),"label":"Artwork Sources","source_kind":"local_folder"
        }).status_code==200
        scan=client.post("/api/v1/control/homeserver-apps/media-server/scan")
        assert scan.status_code==200,scan.text

        library=client.get("/api/v1/control/homeserver-apps/media-server/library",params={"limit":50}).json()
        ids={row["name"]:row["media_id"] for row in library["items"]}
        video_id=ids["Trip Video.mp4"]
        image_a_id=ids["Cover A.jpg"]
        image_b_id=ids["Cover B.jpg"]

        collection=client.post("/api/v1/control/homeserver-apps/media-library/collections",json={
            "name":"Artwork Collection","collection_type":"manual"
        })
        assert collection.status_code==200,collection.text
        collection_id=collection.json()["collection"]["collection_id"]
        for pos,mid in enumerate((image_a_id,image_b_id),start=1):
            r=client.post(f"/api/v1/control/homeserver-apps/media-library/collections/{collection_id}/items",json={
                "media_id":mid,"position":pos
            })
            assert r.status_code==200,r.text

        # Deterministic processor execution while preserving the canonical job/derivative lifecycle.
        homeserver_media_processor._ensure_worker=lambda:None
        homeserver_media_processor._probe_duration=lambda _path:5.0
        def fake_run(job_id,cmd,duration):
            target=Path(cmd[-1])
            target.write_bytes(b"artwork-"+job_id.encode())
            homeserver_media_processor._update_progress(job_id,0.6,2,duration)
        homeserver_media_processor._run_ffmpeg=fake_run

        poster=client.post("/api/v1/control/homeserver-apps/media-library/artwork",json={
            "target_type":"media","target_id":video_id,"role":"poster"
        })
        assert poster.status_code==200,poster.text
        assert poster.json()["processor"]=="vp3.media-processor"
        assert poster.json()["processor_operation"]=="thumbnail"
        assert poster.json()["derivative_owned_by_processor"] is True
        assert source_dir not in poster.text
        homeserver_media_processor.process_next()

        poster_state=client.get(f"/api/v1/control/homeserver-apps/media-library/artwork/media/{video_id}")
        assert poster_state.status_code==200,poster_state.text
        poster_row=next(row for row in poster_state.json()["artwork"] if row["role"]=="poster")
        assert poster_row["status"]=="ready"
        assert poster_row["derivative_id"].startswith("deriv_")
        assert poster_row["filesystem_path_exposed"] is False

        cover=client.post("/api/v1/control/homeserver-apps/media-library/artwork",json={
            "target_type":"collection","target_id":collection_id,"role":"cover",
            "source_media_ids":[image_a_id]
        })
        assert cover.status_code==200,cover.text
        assert cover.json()["processor_operation"]=="image.convert"
        homeserver_media_processor.process_next()

        sheet=client.post("/api/v1/control/homeserver-apps/media-library/artwork",json={
            "target_type":"collection","target_id":collection_id,"role":"contact_sheet",
            "source_media_ids":[image_a_id,image_b_id],"preset":"2x2"
        })
        assert sheet.status_code==200,sheet.text
        assert sheet.json()["processor_operation"]=="contact_sheet"
        homeserver_media_processor.process_next()

        collection_art=client.get(f"/api/v1/control/homeserver-apps/media-library/artwork/collection/{collection_id}")
        assert collection_art.status_code==200,collection_art.text
        roles={row["role"]:row for row in collection_art.json()["artwork"]}
        assert {"cover","contact_sheet"}.issubset(roles)
        assert roles["cover"]["status"]=="ready"
        assert roles["contact_sheet"]["status"]=="ready"
        assert roles["contact_sheet"]["derivative_id"].startswith("deriv_")
        assert source_dir not in collection_art.text

        derivatives=client.get("/api/v1/control/homeserver-apps/media-processor/derivatives",params={"limit":50})
        assert derivatives.status_code==200,derivatives.text
        kinds={row["kind"] for row in derivatives.json()["derivatives"]}
        assert "thumbnail" in kinds
        assert "image" in kinds
        assert "contact_sheet" in kinds
        assert "relative_path" not in derivatives.text

        status=client.get("/api/v1/control/homeserver-apps/media-library/status").json()
        assert status["artwork_assignments"]==3
        brain=client.get("/api/v1/control/homeserver-apps/media-library/brain-context").json()
        assert brain["summary"]["artwork_assignments"]==3
        assert brain["summary"]["artwork_ready"]==3

        cap=client.get("/api/v1/control/homeserver-apps/media-library/capability").json()
        assert cap["processor_backed_artwork"] is True
        assert cap["contact_sheets"] is True
        assert cap["derivative_owned_by_processor"] is True
        assert set(cap["artwork_roles"])=={"thumbnail","poster","album_art","cover","contact_sheet"}

        processor_cap=client.get("/api/v1/control/homeserver-apps/media-processor/capability").json()
        assert processor_cap["contact_sheet_generation"] is True

        actions={row["key"]:row for row in homeserver_app_control.manifest("vp3.media-library")["actions"]}
        assert actions["library.artwork.generate"]["risk"]=="consequential"
        assert actions["library.artwork.generate"]["requires_confirmation"] is True
        assert actions["library.artwork"]["risk"]=="read"
        assert actions["library.artwork.remove"]["risk"]=="destructive"
        assert actions["library.artwork.remove"]["requires_confirmation"] is True

        blocked=client.post("/api/v1/control/homeserver-apps/vp3.media-library/control/invoke",json={
            "action":"library.artwork.remove",
            "arguments":{"target_type":"media","target_id":video_id,"role":"poster"},
            "confirmed":False
        })
        assert blocked.status_code==409,blocked.text
        removed=client.post("/api/v1/control/homeserver-apps/vp3.media-library/control/invoke",json={
            "action":"library.artwork.remove",
            "arguments":{"target_type":"media","target_id":video_id,"role":"poster"},
            "confirmed":True
        })
        assert removed.status_code==200,removed.text
        assert removed.json()["result"]["processor_derivative_deleted"] is False
        assert removed.json()["result"]["source_files_deleted"] is False

        # Semantic removal does not remove the processor derivative or any original.
        assert client.get(f"/api/v1/control/homeserver-apps/media-processor/derivatives/{poster_row['derivative_id']}/file").status_code==200
        for p in (video,image_a,image_b):
            assert p.read_bytes()==originals[p.name]

    homeserver_media_processor._ensure_worker=original_worker
    homeserver_media_processor._probe_duration=original_probe
    homeserver_media_processor._run_ffmpeg=original_run
    homeserver_media_tools._version=original_version
    homeserver_media_tools.invalidate_cache()
    os.environ.pop("HOMESERVER_MEDIA_TOOLS_DIR",None)

print("HomeServer Section 21D Artwork Posters & Thumbnails: PASS")
