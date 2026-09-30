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

with tempfile.TemporaryDirectory(prefix="homeserver-processor-v340-") as data_dir, tempfile.TemporaryDirectory(prefix="vp3-media-source-") as source_dir, tempfile.TemporaryDirectory(prefix="vp3-processor-output-") as output_dir, tempfile.TemporaryDirectory(prefix="vp3-media-tools-") as tools_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir
    os.environ["HOMESERVER_MEDIA_TOOLS_DIR"]=tools_dir

    source=Path(source_dir)
    video=source/"video.mp4"
    video.write_bytes(b"fake-video-source")
    image=source/"image.jpg"
    image.write_bytes(b"fake-image-source")

    tools_root=Path(tools_dir)
    ffmpeg_name="ffmpeg.exe" if os.name=="nt" else "ffmpeg"
    ffprobe_name="ffprobe.exe" if os.name=="nt" else "ffprobe"
    ffmpeg=tools_root/ffmpeg_name
    ffprobe=tools_root/ffprobe_name
    ffmpeg.write_bytes(b"managed-ffmpeg")
    ffprobe.write_bytes(b"managed-ffprobe")
    manifest={
        "contract":"vp3.homeserver.media-tools.v1",
        "version":"9.0.2",
        "source":"https://www.gyan.dev/ffmpeg/builds/",
        "ffmpeg_sha256":hashlib.sha256(ffmpeg.read_bytes()).hexdigest(),
        "ffprobe_sha256":hashlib.sha256(ffprobe.read_bytes()).hexdigest(),
    }
    (tools_root/"manifest.json").write_text(json.dumps(manifest),encoding="utf-8")

    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import (
        homeserver_app_control,
        homeserver_app_manager,
        homeserver_app_runtime,
        homeserver_apps,
        homeserver_download_manager,
        homeserver_media_processor,
        homeserver_media_server,
        homeserver_media_tools,
        homeserver_video_editor,
        hosting_cloud_control,
        hosting_serving,
    )
    from app.services.tasks import scheduler

    original_version=homeserver_media_tools._version
    original_require=homeserver_media_tools.require
    original_public=homeserver_media_tools.public_capability
    original_probe=homeserver_media_processor._probe_duration
    original_run_ffmpeg=homeserver_media_processor._run_ffmpeg
    original_worker=homeserver_media_processor._ensure_worker

    def fake_version(path:Path)->str:
        return "ffmpeg version 9.0.2" if "ffmpeg" in path.name and "probe" not in path.name else "ffprobe version 9.0.2"

    homeserver_media_tools._version=fake_version
    homeserver_media_tools.invalidate_cache()

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        # Managed runtime is hash-verified and never exposes its absolute install path.
        health=homeserver_media_tools.health(force=True)
        assert health["managed"] is True
        assert health["healthy"] is True
        assert health["hashes_verified"] is True
        assert health["manifest_version"]=="9.0.2"
        assert health["absolute_paths_exposed"] is False
        assert tools_dir not in json.dumps(health)

        for app_key in ("vp3.media-server","vp3.media-processor","vp3.download-manager","vp3.video-editor"):
            response=client.post(f"/api/v1/control/homeserver-apps/catalog/prebuilt/{app_key}/install")
            assert response.status_code==200,response.text

        assert homeserver_apps.get("vp3.media-server")["installed_version"]=="1.2.0"
        assert homeserver_apps.get("vp3.media-processor")["installed_version"]=="1.0.0"
        assert homeserver_apps.get("vp3.download-manager")["installed_version"]=="1.1.0"
        assert homeserver_apps.get("vp3.video-editor")["installed_version"]=="1.1.0"

        # Canonical Media Server source.
        grant_media=client.put("/api/v1/control/homeserver-apps/vp3.media-server/permissions",json={
            "permission":"files.read","allowed":True
        })
        assert grant_media.status_code==200,grant_media.text
        mapped=client.post("/api/v1/control/homeserver-apps/media-server/mapped-roots",json={
            "path":str(source),"label":"Processor Sources","source_kind":"local_folder"
        })
        assert mapped.status_code==200,mapped.text
        scan=client.post("/api/v1/control/homeserver-apps/media-server/scan")
        assert scan.status_code==200,scan.text
        library=client.get("/api/v1/control/homeserver-apps/media-server/library",params={"media_type":"video"}).json()
        assert library["count"]==1
        media_id=library["items"][0]["media_id"]

        # files.write is required only for external processor destinations.
        permissions=client.get("/api/v1/control/homeserver-apps/vp3.media-processor/permissions").json()["permissions"]["permissions"]
        by_perm={row["permission"]:row for row in permissions}
        assert by_perm["files.write"]["allowed"] is False
        denied=client.post("/api/v1/control/homeserver-apps/media-processor/destinations",json={
            "path":output_dir,"label":"Processed","destination_kind":"mapped_folder"
        })
        assert denied.status_code==403,denied.text

        grant_proc=client.put("/api/v1/control/homeserver-apps/vp3.media-processor/permissions",json={
            "permission":"files.write","allowed":True
        })
        assert grant_proc.status_code==200,grant_proc.text
        destination=client.post("/api/v1/control/homeserver-apps/media-processor/destinations",json={
            "path":output_dir,"label":"Processed","destination_kind":"mapped_folder"
        })
        assert destination.status_code==200,destination.text
        destination_id=destination.json()["destination"]["destination_id"]
        assert output_dir not in destination.text
        assert destination.json()["destination"]["absolute_path_exposed"] is False

        # Deterministic FFmpeg execution for CI; processor still exercises job,
        # quotas, atomic rename, derivative registry, events and public projections.
        homeserver_media_processor._ensure_worker=lambda:None
        homeserver_media_processor._probe_duration=lambda _path:10.0
        def fake_run(job_id,cmd,duration):
            target=Path(cmd[-1])
            target.write_bytes(b"processed-"+job_id.encode())
            homeserver_media_processor._update_progress(job_id,0.5,5,duration)
        homeserver_media_processor._run_ffmpeg=fake_run

        queued=client.post("/api/v1/control/homeserver-apps/media-processor/jobs",json={
            "media_id":media_id,"operation":"proxy","preset":"editor","output_format":"mp4"
        })
        assert queued.status_code==200,queued.text
        job_id=queued.json()["job"]["job_id"]
        assert "output_rel" not in queued.text
        done=homeserver_media_processor.process_next()
        assert done["job"]["status"]=="completed"
        assert done["job"]["progress"]==1
        assert done["job"]["output_ready"] is True
        assert done["job"]["filesystem_path_exposed"] is False

        derivatives=client.get("/api/v1/control/homeserver-apps/media-processor/derivatives",params={"media_id":media_id})
        assert derivatives.status_code==200,derivatives.text
        assert derivatives.json()["count"]==1
        derivative_id=derivatives.json()["derivatives"][0]["derivative_id"]
        assert "relative_path" not in derivatives.text
        derivative_file=client.get(f"/api/v1/control/homeserver-apps/media-processor/derivatives/{derivative_id}/file")
        assert derivative_file.status_code==200
        assert derivative_file.content.startswith(b"processed-")
        assert video.read_bytes()==b"fake-video-source"

        # External derivative destination.
        external=client.post("/api/v1/control/homeserver-apps/media-processor/jobs",json={
            "media_id":media_id,"operation":"video.convert","preset":"720p","output_format":"mp4",
            "destination_id":destination_id
        })
        assert external.status_code==200,external.text
        ext_done=homeserver_media_processor.process_next()
        assert ext_done["job"]["status"]=="completed"
        assert any(Path(output_dir).iterdir())
        assert output_dir not in json.dumps(ext_done)

        # Media Server governed process handoff.
        media_process=client.post(f"/api/v1/control/homeserver-apps/media-server/item/{media_id}/process",json={
            "operation":"thumbnail","output_format":"jpg"
        })
        assert media_process.status_code==200,media_process.text
        assert media_process.json()["job"]["media_id"]==media_id

        # Video Editor clip -> processor proxy handoff.
        project=client.post("/api/v1/control/homeserver-apps/video-editor/projects",json={"name":"Processor Project"}).json()
        project_id=project["project"]["project_id"]
        project_state=client.get(f"/api/v1/control/homeserver-apps/video-editor/projects/{project_id}").json()
        video_track=next(row for row in project_state["tracks"] if row["kind"]=="video")
        clip_state=client.post(f"/api/v1/control/homeserver-apps/video-editor/projects/{project_id}/clips",json={
            "track_id":video_track["track_id"],"media_id":media_id,"start_seconds":0
        }).json()
        clip_id=next(row["clip_id"] for row in clip_state["clips"] if row["media_id"]==media_id)
        proxy=client.post(f"/api/v1/control/homeserver-apps/video-editor/projects/{project_id}/clips/{clip_id}/proxy")
        assert proxy.status_code==200,proxy.text
        assert proxy.json()["processor"]=="vp3.media-processor"

        # Download Manager completed-file handoff requires the file to be inside
        # a canonical Media Server root. Create a completed job pointing at that root.
        conn=homeserver_download_manager._connect()
        try:
            conn.execute(
                "INSERT INTO download_destinations(destination_id,label,root_path,destination_kind,enabled) VALUES ('processor-test-root','Processor Source',?,'mapped_folder',1)",
                (str(source),),
            )
            conn.execute(
                """INSERT INTO download_jobs(download_id,url,display_url,destination_id,requested_filename,final_filename,status)
                   VALUES ('dl_processor_test','https://example.com/video.mp4','https://example.com/video.mp4','processor-test-root','video.mp4','video.mp4','completed')"""
            )
            conn.commit()
        finally:
            conn.close()
        handoff=client.post("/api/v1/control/homeserver-apps/download-manager/downloads/dl_processor_test/process",json={
            "operation":"proxy","preset":"editor","output_format":"mp4"
        })
        assert handoff.status_code==200,handoff.text
        assert handoff.json()["media_id"]==media_id
        assert handoff.json()["processor_job"]["operation"]=="proxy"

        # Universal Agent control + Agent Brain knowledge.
        compatibility=homeserver_app_control.compatibility("vp3.media-processor")
        assert compatibility["compatible"] is True
        assert compatibility["manifest_contract"]=="vp3.app.agent-actions.v2"
        actions={row["key"]:row for row in homeserver_app_control.manifest("vp3.media-processor")["actions"]}
        assert actions["processor.enqueue"]["requires_confirmation"] is True
        assert actions["processor.destination.add"]["requires_confirmation"] is True
        assert "media.process" in {row["key"] for row in homeserver_app_control.manifest("vp3.media-server")["actions"]}
        assert "downloads.processor.handoff" in {row["key"] for row in homeserver_app_control.manifest("vp3.download-manager")["actions"]}
        assert "video.clip.proxy" in {row["key"] for row in homeserver_app_control.manifest("vp3.video-editor")["actions"]}

        brain=client.get("/api/v1/control/homeserver-apps/media-processor/brain-context")
        assert brain.status_code==200,brain.text
        assert brain.json()["source_paths_exposed"] is False
        assert brain.json()["output_paths_exposed"] is False
        assert source_dir not in brain.text and output_dir not in brain.text

        generic=client.post("/api/v1/control/homeserver-apps/vp3.media-processor/control/invoke",json={
            "action":"processor.brain-context","arguments":{"limit":4}
        })
        assert generic.status_code==200,generic.text

        # Runtime event bus carries processor knowledge for other VP3 systems.
        events=homeserver_app_runtime.list_events("vp3.media-processor",limit=100)
        topics={row["topic"] for row in events}
        assert "processor.queued" in topics
        assert "processor.completed" in topics

        # Private hosted processor status + derivative access.
        remote=client.post("/api/v1/control/homeserver-apps/media-processor/remote/enable")
        assert remote.status_code==200,remote.text
        access_key=remote.json()["access_key"]
        original_site=hosting_serving.hosting_runtime.get_site
        original_binding=hosting_cloud_control.binding_for_site
        try:
            hosting_serving.hosting_runtime.get_site=lambda _site_id:{"state":"active","runtime_kind":"static"}
            hosting_cloud_control.binding_for_site=lambda _site_id:{"target_app_key":"vp3.media-processor"}
            try:
                hosting_serving.serve("site_proc","__vp3_processor__/status",request_headers={})
                raise AssertionError("Hosted Media Processor allowed anonymous access")
            except hosting_serving.ServingError as exc:
                assert exc.status_code==401
            hosted=hosting_serving.serve(
                "site_proc","__vp3_processor__/brain-context",
                request_headers={"Authorization":"Bearer "+access_key},
            )
            assert hosted.status_code==200
            hosted_file=hosting_serving.serve(
                "site_proc",f"__vp3_processor__/derivatives/{derivative_id}/file",
                request_headers={"Authorization":"Bearer "+access_key},
            )
            assert hosted_file.status_code==200
        finally:
            hosting_serving.hosting_runtime.get_site=original_site
            hosting_cloud_control.binding_for_site=original_binding

        manager=homeserver_app_manager.inventory()
        proc=next(row for row in manager["items"] if row["app_key"]=="vp3.media-processor")
        assert proc["installed"] is True
        assert proc["media_processor"]["homeserver_execution_authority"] is True
        assert proc["media_tools"]["managed"] is True
        assert proc["agent_control"]["complete"] is True

        assert homeserver_media_server.status()["processing_provider"]=="vp3.media-processor"
        assert homeserver_media_server.status()["transcoding"] is True

    homeserver_media_processor._ensure_worker=original_worker
    homeserver_media_processor._probe_duration=original_probe
    homeserver_media_processor._run_ffmpeg=original_run_ffmpeg
    homeserver_media_tools._version=original_version
    homeserver_media_tools.invalidate_cache()
    os.environ.pop("HOMESERVER_MEDIA_TOOLS_DIR",None)

print("HomeServer Section 20 Media Processor: PASS")
