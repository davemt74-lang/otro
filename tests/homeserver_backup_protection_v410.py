from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-backup-protection-v410-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.config import settings
    from app.database import db
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import (
        backups,
        backup_protection,
        homeserver_app_prebuilt,
        homeserver_app_resources,
        members,
    )
    from app.services.tasks import scheduler as task_scheduler

    original_file=b"SECTION27_ORIGINAL_APP_FILE_91827"
    changed_file=b"SECTION27_CHANGED_APP_FILE_47211"
    sqlite_original="SECTION27_SQLITE_ORIGINAL_36129"
    sqlite_changed="SECTION27_SQLITE_CHANGED_80451"

    with TestClient(app) as client:
        task_scheduler.stop()
        assert client.get("/api/v1/control/backups").status_code==401
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        installed=homeserver_app_prebuilt.install("vp3.notes")
        assert installed["app"]["app_key"]=="vp3.notes"
        homeserver_app_resources.write_file("vp3.notes","section27/data.txt",original_file)
        app_db=homeserver_app_resources.sqlite_path("vp3.notes","app.db")
        con=sqlite3.connect(app_db)
        con.execute("CREATE TABLE IF NOT EXISTS section27_state (value TEXT NOT NULL)")
        con.execute("DELETE FROM section27_state")
        con.execute("INSERT INTO section27_state(value) VALUES (?)",(sqlite_original,))
        con.commit()

        # Keep a WAL-open write path while backup runs; SQLite backup must still be coherent.
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("UPDATE section27_state SET value=?",(sqlite_original,))
        con.commit()

        member=client.post("/api/v1/control/members",json={
            "username":"backup-user",
            "display_name":"Backup User",
            "password":"Backup-user-pass-2026",
            "role":"member",
        })
        assert member.status_code==200,member.text
        member_id=member.json()["member"]["member_id"]

        try:
            client.cookies.delete("homeserver_owner")
        except KeyError:
            pass
        login=client.post("/api/v1/member/session",json={
            "username":"backup-user","password":"Backup-user-pass-2026"
        })
        assert login.status_code==200,login.text
        assert client.get("/api/v1/member/me").status_code==200
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        # Deliberate excluded state must never enter the archive.
        settings.owner_secret_path.parent.mkdir(parents=True,exist_ok=True)
        settings.owner_secret_path.write_text("SECTION27_OWNER_SECRET_55139",encoding="utf-8")
        settings.runtime_dir.mkdir(parents=True,exist_ok=True)
        (settings.runtime_dir/"section27-runtime.txt").write_text("SECTION27_RUNTIME_PRIVATE_18342",encoding="utf-8")
        app_recovery=settings.data_dir/"app-recovery"/"section27-test"
        app_recovery.mkdir(parents=True,exist_ok=True)
        (app_recovery/"private.txt").write_text("SECTION27_RECOVERY_PRIVATE_72711",encoding="utf-8")

        policy=client.put("/api/v1/control/backups/policy",json={
            "include_app_data":True,
            "retain_manual":2,
            "retain_automatic":2,
            "retain_pre_restore":2,
        })
        assert policy.status_code==200,policy.text
        assert policy.json()["include_app_data"] is True

        created=client.post("/api/v1/control/backups/create")
        assert created.status_code==200,created.text
        backup=created.json()["backup"]
        assert backup["format_version"]==2
        assert backup["coverage"]["app_data"] is True
        assert backup["app_count"]>=1
        assert backup["app_data_files"]>=2
        archive_path=settings.backups_dir/backup["name"]
        assert archive_path.is_file()
        archive_bytes=archive_path.read_bytes()

        with zipfile.ZipFile(io.BytesIO(archive_bytes),"r") as archive:
            names=set(archive.namelist())
            manifest=json.loads(archive.read("manifest.json"))
            assert manifest["format_version"]==2
            assert manifest["coverage"]["app_data"] is True
            assert any(name.startswith("app-data/") and name.endswith("/section27/data.txt") for name in names)
            assert any(name.startswith("app-data/") and name.endswith("/sqlite/app.db") for name in names)
            assert not any(name.startswith("security/") for name in names)
            assert not any(name.startswith("runtime/") for name in names)
            assert not any(name.startswith("backups/") for name in names)
            assert not any(name.startswith("restore/") for name in names)
            assert not any(name.startswith("app-recovery/") for name in names)
            serialized=json.dumps(manifest)
            assert "SECTION27_OWNER_SECRET_55139" not in serialized
            assert "SECTION27_RUNTIME_PRIVATE_18342" not in serialized
            assert "SECTION27_RECOVERY_PRIVATE_72711" not in serialized

            app_db_name=next(name for name in names if name.startswith("app-data/") and name.endswith("/sqlite/app.db"))
            extracted_db=Path(data_dir)/"section27-extracted.db"
            extracted_db.write_bytes(archive.read(app_db_name))
            snap=sqlite3.connect(extracted_db)
            assert snap.execute("PRAGMA quick_check").fetchone()[0]=="ok"
            assert snap.execute("SELECT value FROM section27_state").fetchone()[0]==sqlite_original
            snap.close()

        # Health reports intentional exclusions and v2 protection.
        listing=client.get("/api/v1/control/backups")
        assert listing.status_code==200,listing.text
        health=listing.json()["health"]
        assert health["backup_format_current"]==2
        assert health["coverage"]["app_data"] is True
        assert health["coverage"]["security_secrets"] is False
        assert health["atomic_app_data_restore"] is True
        assert health["sqlite_consistent_app_snapshots"] is True

        # Legacy v1 archive derived from the same coherent database remains stageable.
        legacy_buffer=io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(archive_bytes),"r") as source, zipfile.ZipFile(legacy_buffer,"w",zipfile.ZIP_DEFLATED) as target:
            legacy_manifest=json.loads(source.read("manifest.json"))
            legacy_manifest["format_version"]=1
            legacy_manifest.pop("coverage",None)
            legacy_manifest.pop("apps",None)
            legacy_manifest.pop("app_data_bytes",None)
            legacy_manifest.pop("app_data_files",None)
            legacy_manifest["files"]=[
                item for item in legacy_manifest["files"]
                if not str(item["path"]).startswith("app-data/")
            ]
            target.writestr("manifest.json",json.dumps(legacy_manifest,sort_keys=True).encode())
            for info in source.infolist():
                if info.filename=="manifest.json" or info.filename.startswith("app-data/"):
                    continue
                target.writestr(info,source.read(info.filename))
        legacy_stage=client.post(
            "/api/v1/control/restore/stage",
            files={"file":("legacy-v1.zip",legacy_buffer.getvalue(),"application/zip")},
        )
        assert legacy_stage.status_code==200,legacy_stage.text
        legacy_restore=legacy_stage.json()["restore"]
        assert legacy_restore["format_version"]==1
        assert legacy_restore["app_data"]["included"] is False
        assert legacy_restore["app_data"]["legacy_backup"] is True
        assert client.delete("/api/v1/control/restore/pending").status_code==200

        # Change live app state, then stage the real v2 archive.
        homeserver_app_resources.write_file("vp3.notes","section27/data.txt",changed_file)
        con.execute("UPDATE section27_state SET value=?",(sqlite_changed,))
        con.commit()
        con.close()
        assert homeserver_app_resources.read_file("vp3.notes","section27/data.txt")==changed_file
        verify=sqlite3.connect(app_db)
        assert verify.execute("SELECT value FROM section27_state").fetchone()[0]==sqlite_changed
        verify.close()

        stage=client.post(
            "/api/v1/control/restore/stage",
            files={"file":("section27-v2.zip",archive_bytes,"application/zip")},
        )
        assert stage.status_code==200,stage.text
        staged=stage.json()["restore"]
        assert staged["format_version"]==2
        assert staged["app_data"]["included"] is True
        assert staged["app_data"]["files"]>=2

        # Corrupt staged app data after validation: apply must fail and leave live app data unchanged.
        staged_app_file=next(
            path for path in settings.pending_restore_dir.rglob("data.txt")
            if "app-data" in path.parts
        )
        staged_app_file.write_bytes(b"tampered-after-stage")
        try:
            backups.apply_pending_restore()
            raise AssertionError("Tampered staged app data restore should fail")
        except backups.BackupError:
            pass
        assert homeserver_app_resources.read_file("vp3.notes","section27/data.txt")==changed_file
        verify=sqlite3.connect(app_db)
        assert verify.execute("SELECT value FROM section27_state").fetchone()[0]==sqlite_changed
        verify.close()

        # Stage the intact v2 archive again.
        stage_again=client.post(
            "/api/v1/control/restore/stage",
            files={"file":("section27-v2.zip",archive_bytes,"application/zip")},
        )
        assert stage_again.status_code==200,stage_again.text
        assert settings.pending_restore_dir.is_dir()

    applied=backups.apply_pending_restore()
    assert applied is not None
    assert applied["status"]=="applied"
    assert applied["format_version"]==2
    assert applied["app_data_restored"] is True
    assert applied["member_sessions_invalidated"]>=1
    assert applied["pre_restore_backup"]

    with TestClient(app) as restored:
        assert restored.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        # Full v2 restore reverts app file and SQLite state.
        assert homeserver_app_resources.read_file("vp3.notes","section27/data.txt")==original_file
        restored_db=sqlite3.connect(homeserver_app_resources.sqlite_path("vp3.notes","app.db"))
        assert restored_db.execute("SELECT value FROM section27_state").fetchone()[0]==sqlite_original
        restored_db.close()

        # Database restore preserved member, but active member sessions were deliberately revoked.
        with db() as connection:
            assert connection.execute("SELECT member_id FROM homeserver_members WHERE member_id=?",(member_id,)).fetchone()
            assert connection.execute("SELECT COUNT(*) FROM homeserver_member_sessions").fetchone()[0]==0

        last=restored.get("/api/v1/control/backups").json()["last_restore"]
        assert last["status"]=="applied"
        assert last["app_data_restored"] is True
        assert last["member_sessions_invalidated"]>=1

        cap=restored.get("/api/v1/control/backups/capability")
        assert cap.status_code==200,cap.text
        capability=cap.json()
        assert capability["backup_format_v2"] is True
        assert capability["legacy_v1_restore"] is True
        assert capability["app_data_included"] is True
        assert capability["security_secrets_excluded"] is True

        # Retention policy prunes older manual backups while protecting the current create.
        updated=restored.put("/api/v1/control/backups/policy",json={"retain_manual":1})
        assert updated.status_code==200
        first=restored.post("/api/v1/control/backups/create")
        assert first.status_code==200,first.text
        second=restored.post("/api/v1/control/backups/create")
        assert second.status_code==200,second.text
        manual_items=[
            item for item in restored.get("/api/v1/control/backups").json()["items"]
            if item.get("reason")=="manual" and not item.get("invalid")
        ]
        assert len(manual_items)==1
        assert manual_items[0]["name"]==second.json()["backup"]["name"]

        ui=(ROOT/"ui"/"app.js").read_text(encoding="utf-8")
        css=(ROOT/"ui"/"backups.css").read_text(encoding="utf-8")
        assert "backupHealth" in ui
        assert "backupPolicyForm" in ui
        assert "legacy v1" in ui
        assert "app files" in ui
        assert ".backup-health" in css

print("HomeServer Section 27 Backup Restore & App Data Protection: PASS")
