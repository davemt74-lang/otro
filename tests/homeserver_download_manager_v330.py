from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-downloads-v330-") as data_dir, tempfile.TemporaryDirectory(prefix="vp3-download-dest-") as mapped_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import (
        homeserver_app_control,
        homeserver_app_manager,
        homeserver_app_resources,
        homeserver_app_runtime,
        homeserver_download_manager,
        hosting_cloud_control,
        hosting_serving,
    )
    from app.services.tasks import scheduler

    class FakeResponse:
        def __init__(self,chunks,status=200,headers=None,on_first_read=None):
            self._chunks=list(chunks)
            self.status=status
            self.headers=headers or {}
            self._on_first_read=on_first_read
            self._read_count=0
        def getcode(self):
            return self.status
        def read(self,_size=-1):
            self._read_count+=1
            if self._read_count==1 and self._on_first_read:
                self._on_first_read()
            return self._chunks.pop(0) if self._chunks else b""

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        install=client.post("/api/v1/control/homeserver-apps/catalog/prebuilt/vp3.download-manager/install")
        assert install.status_code==200,install.text
        assert install.json()["app"]["installed_version"]=="1.1.0"

        # Required network/file permissions are declared and default denied.
        perms=client.get("/api/v1/control/homeserver-apps/vp3.download-manager/permissions").json()["permissions"]
        by_perm={row["permission"]:row for row in perms["permissions"]}
        assert by_perm["network.external"]["allowed"] is False
        assert by_perm["files.write"]["allowed"] is False

        denied=client.post("/api/v1/control/homeserver-apps/download-manager/downloads",json={
            "url":"https://downloads.example.com/file.bin?token=private-secret"
        })
        assert denied.status_code==403,denied.text

        for permission in ("network.external","files.write"):
            grant=client.put("/api/v1/control/homeserver-apps/vp3.download-manager/permissions",json={
                "permission":permission,"allowed":True
            })
            assert grant.status_code==200,grant.text

        app_settings=client.get("/api/v1/control/homeserver-apps/vp3.download-manager/settings")
        assert app_settings.status_code==200,app_settings.text
        fields={row["key"]:row for row in app_settings.json()["schema"]["fields"]}
        assert fields["authorization_host"]["secret"] is False
        assert fields["authorization_header"]["secret"] is True
        scoped=client.put("/api/v1/control/homeserver-apps/vp3.download-manager/settings",json={
            "values":{"authorization_host":"secure.example.com","authorization_header":"Bearer top-secret"}
        })
        assert scoped.status_code==200,scoped.text
        assert "top-secret" not in scoped.text

        # Private/local-network SSRF targets fail closed before any request.
        blocked=client.post("/api/v1/control/homeserver-apps/download-manager/downloads",json={
            "url":"http://127.0.0.1/private"
        })
        assert blocked.status_code==403,blocked.text

        mapped=client.post("/api/v1/control/homeserver-apps/download-manager/destinations",json={
            "path":mapped_dir,"label":"Downloads Share","destination_kind":"mapped_folder"
        })
        assert mapped.status_code==200,mapped.text
        assert mapped.json()["destination"]["absolute_path_exposed"] is False
        assert mapped_dir not in mapped.text
        mapped_id=mapped.json()["destination"]["destination_id"]

        # Keep execution deterministic; workers are separately covered by restart recovery.
        original_ensure=homeserver_download_manager._ensure_worker
        original_open=homeserver_download_manager._open_url
        original_validate=homeserver_download_manager._validate_url
        original_open_for_job=homeserver_download_manager._open_for_job
        homeserver_download_manager._ensure_worker=lambda:None
        homeserver_download_manager._validate_url=lambda url: __import__("urllib.parse").parse.urlsplit(url)
        try:
            # Auth headers are host-scoped; another download host never receives them.
            captured=[]
            homeserver_download_manager._open_url=lambda url,headers,timeout=30: captured.append((url,dict(headers))) or FakeResponse([b""],200,{"Content-Length":"0"})
            homeserver_download_manager._open_for_job({"url":"https://other.example.com/file","etag":"","last_modified":""},0)
            assert "Authorization" not in captured[-1][1]
            homeserver_download_manager._open_for_job({"url":"https://secure.example.com/file","etag":"","last_modified":""},0)
            assert captured[-1][1].get("Authorization")=="Bearer top-secret"

            payload=b"abcdef"
            digest=hashlib.sha256(payload).hexdigest()

            created=client.post("/api/v1/control/homeserver-apps/download-manager/downloads",json={
                "url":"https://downloads.example.com/file.bin?token=private-secret",
                "filename":"file.bin",
                "checksum_algorithm":"sha256",
                "checksum_expected":digest,
                "destination_id":"app-storage",
                "priority":5,
            })
            assert created.status_code==200,created.text
            download_id=created.json()["download"]["download_id"]
            assert "downloads.example.com/file.bin" in created.json()["download"]["display_url"]
            assert "private-secret" not in created.text
            assert "token=" not in created.json()["download"]["display_url"]
            assert created.json()["download"]["source_url_exposed"] is False

            seen_headers=[]
            def first_open(_url,headers,timeout=30):
                seen_headers.append(dict(headers))
                return FakeResponse(
                    [b"abc"],
                    200,
                    {"Content-Length":"6","ETag":"v1"},
                    on_first_read=lambda:homeserver_download_manager.pause(download_id),
                )
            homeserver_download_manager._open_url=first_open
            paused=homeserver_download_manager.process_next()
            assert paused["download"]["status"]=="paused"
            assert paused["download"]["bytes_downloaded"]==3

            resumed=client.post(f"/api/v1/control/homeserver-apps/download-manager/downloads/{download_id}/resume")
            assert resumed.status_code==200,resumed.text

            def second_open(_url,headers,timeout=30):
                seen_headers.append(dict(headers))
                assert headers.get("Range")=="bytes=3-"
                assert headers.get("If-Range")=="v1"
                return FakeResponse([b"def"],206,{"Content-Length":"3","Content-Range":"bytes 3-5/6","ETag":"v1"})
            homeserver_download_manager._open_url=second_open
            completed=homeserver_download_manager.process_next()
            assert completed["download"]["status"]=="completed"
            assert completed["download"]["checksum_actual"]==digest
            assert completed["download"]["bytes_downloaded"]==6
            final_root=homeserver_app_resources.files_root("vp3.download-manager")/"downloads"
            assert (final_root/"file.bin").read_bytes()==payload

            # File-type safety rules are configurable and enforced before finalization.
            updated_settings=client.put("/api/v1/control/homeserver-apps/download-manager/settings",json={
                "values":{"blocked_extensions":["exe","ps1"]}
            })
            assert updated_settings.status_code==200,updated_settings.text
            blocked_job=homeserver_download_manager.enqueue(
                "https://downloads.example.com/tool.exe",filename="tool.exe",max_retries=0
            )
            homeserver_download_manager._open_url=lambda *_args,**_kwargs:FakeResponse(
                [b"binary"],200,{"Content-Length":"6"}
            )
            blocked_result=homeserver_download_manager.process_next()
            assert blocked_result["download"]["status"]=="failed"
            assert "blocked" in blocked_result["download"]["error"].lower()
            client.put("/api/v1/control/homeserver-apps/download-manager/settings",json={"values":{"blocked_extensions":[]}})

            # Truncated responses never finalize; retries use bounded backoff.
            short_job=homeserver_download_manager.enqueue(
                "https://downloads.example.com/short.bin",filename="short.bin",max_retries=1
            )
            short_id=short_job["download"]["download_id"]
            homeserver_download_manager._open_url=lambda *_args,**_kwargs:FakeResponse(
                [b"abc"],200,{"Content-Length":"6"}
            )
            short_result=homeserver_download_manager.process_next()
            assert short_result["download"]["status"]=="queued"
            assert "expected content length" in short_result["download"]["error"].lower()
            conn=homeserver_download_manager._connect()
            try:
                row=conn.execute("SELECT scheduled_at,retry_count FROM download_jobs WHERE download_id=?",(short_id,)).fetchone()
                assert int(row["retry_count"])==1
                assert int(row["scheduled_at"])>0
                conn.execute("UPDATE download_jobs SET status='cancelled' WHERE download_id=?",(short_id,))
                conn.commit()
            finally:
                conn.close()

            # Filename conflicts never overwrite prior downloads.
            second=homeserver_download_manager.enqueue(
                "https://downloads.example.com/file.bin",filename="file.bin"
            )
            second_id=second["download"]["download_id"]
            homeserver_download_manager._open_url=lambda *_args,**_kwargs:FakeResponse(
                [b"123"],200,{"Content-Length":"3"}
            )
            second_done=homeserver_download_manager.process_next()
            assert second_done["download"]["status"]=="completed"
            assert second_done["download"]["final_filename"]=="file (1).bin"
            assert (final_root/"file.bin").read_bytes()==payload

            # External destinations write only after explicit files.write grant.
            ext=homeserver_download_manager.enqueue(
                "https://downloads.example.com/share.zip",
                destination_id=mapped_id,
                filename="share.zip",
            )
            ext_id=ext["download"]["download_id"]
            homeserver_download_manager._open_url=lambda *_args,**_kwargs:FakeResponse(
                [b"zipdata"],200,{"Content-Length":"7"}
            )
            ext_done=homeserver_download_manager.process_next()
            assert ext_done["download"]["status"]=="completed"
            assert (Path(mapped_dir)/"share.zip").read_bytes()==b"zipdata"
            assert mapped_dir not in json.dumps(ext_done)

            # Cancellation preserves partial data for retry/resume; clear history
            # removes only temporary files, never completed user files.
            cancelled=homeserver_download_manager.enqueue(
                "https://downloads.example.com/cancel.bin",filename="cancel.bin"
            )
            cancel_id=cancelled["download"]["download_id"]
            temp=homeserver_download_manager._temp_path(final_root,cancel_id)
            temp.write_bytes(b"partial")
            client.delete(f"/api/v1/control/homeserver-apps/download-manager/downloads/{cancel_id}")
            cleared=client.delete("/api/v1/control/homeserver-apps/download-manager/history")
            assert cleared.status_code==200,cleared.text
            assert not temp.exists()
            assert (final_root/"file.bin").is_file()
            assert (Path(mapped_dir)/"share.zip").is_file()

            # Restart recovery returns interrupted jobs to the queue.
            restart=homeserver_download_manager.enqueue(
                "https://downloads.example.com/restart.bin",filename="restart.bin"
            )
            restart_id=restart["download"]["download_id"]
            conn=homeserver_download_manager._connect()
            try:
                conn.execute("UPDATE download_jobs SET status='downloading' WHERE download_id=?",(restart_id,))
                conn.commit()
            finally:
                conn.close()
            recovered=homeserver_download_manager.recover_interrupted()
            assert recovered["recovered"]==1
            assert homeserver_download_manager.get_download(restart_id)["download"]["status"]=="queued"

            # Agent Brain context is bounded and never includes raw source URLs or filesystem paths.
            brain=client.get("/api/v1/control/homeserver-apps/download-manager/brain-context")
            assert brain.status_code==200,brain.text
            assert brain.json()["contract"]=="vp3.download-manager.brain-context.v1"
            assert brain.json()["source_urls_exposed"] is False
            assert brain.json()["filesystem_paths_exposed"] is False
            assert mapped_dir not in brain.text

            events=homeserver_app_runtime.list_events("vp3.download-manager",limit=100)
            topics={row["topic"] for row in events}
            assert "downloads.queued" in topics
            assert "downloads.completed" in topics

            # Universal Agent control advertises governed write/read actions.
            compatibility=homeserver_app_control.compatibility("vp3.download-manager")
            assert compatibility["compatible"] is True
            assert compatibility["manifest_contract"]=="vp3.app.agent-actions.v2"
            manifest=homeserver_app_control.manifest("vp3.download-manager")
            actions={row["key"]:row for row in manifest["actions"]}
            assert actions["downloads.status"]["risk"]=="read"
            assert actions["downloads.enqueue"]["requires_confirmation"] is True
            assert actions["downloads.cancel"]["requires_confirmation"] is True
            generic=client.post("/api/v1/control/homeserver-apps/vp3.download-manager/control/invoke",json={
                "action":"downloads.brain-context","arguments":{"limit":4}
            })
            assert generic.status_code==200,generic.text
            assert generic.json()["result"]["source_urls_exposed"] is False

            # Private hosted access uses a Download Manager-owned access key.
            remote=client.post("/api/v1/control/homeserver-apps/download-manager/remote/enable")
            assert remote.status_code==200,remote.text
            access_key=remote.json()["access_key"]
            original_get_site=hosting_serving.hosting_runtime.get_site
            original_binding=hosting_cloud_control.binding_for_site
            try:
                hosting_serving.hosting_runtime.get_site=lambda _site_id:{"state":"active","runtime_kind":"static"}
                hosting_cloud_control.binding_for_site=lambda _site_id:{"target_app_key":"vp3.download-manager"}
                try:
                    hosting_serving.serve("site_dl","__vp3_downloads__/status",request_headers={})
                    raise AssertionError("Hosted Download Manager allowed anonymous access")
                except hosting_serving.ServingError as exc:
                    assert exc.status_code==401
                hosted=hosting_serving.serve(
                    "site_dl","__vp3_downloads__/brain-context",
                    request_headers={"Authorization":"Bearer "+access_key},
                )
                assert hosted.status_code==200
                hosted_payload=json.loads(hosted.body.decode("utf-8"))
                assert hosted_payload["source_urls_exposed"] is False
            finally:
                hosting_serving.hosting_runtime.get_site=original_get_site
                hosting_cloud_control.binding_for_site=original_binding

            manager=homeserver_app_manager.inventory()
            row=next(x for x in manager["items"] if x["app_key"]=="vp3.download-manager")
            assert row["installed"] is True
            assert row["download_manager"]["homeserver_execution_authority"] is True
            assert row["agent_control"]["complete"] is True

            cap=client.get("/api/v1/control/homeserver-apps/download-manager/capability").json()
            assert cap["http_https_only"] is True
            assert cap["private_network_downloads_blocked"] is True
            assert cap["restart_recovery"] is True
            assert cap["agent_brain_context"] is True
            assert cap["universal_agent_control"] is True
        finally:
            homeserver_download_manager._open_url=original_open
            homeserver_download_manager._open_for_job=original_open_for_job
            homeserver_download_manager._validate_url=original_validate
            homeserver_download_manager._ensure_worker=original_ensure
            homeserver_download_manager.stop_worker()

print("HomeServer Section 19 Download Manager: PASS")
