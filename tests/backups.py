from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-backup-test-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.config import settings  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import backups  # noqa: E402

    archive_bytes = b""
    original_contact_id = None
    private_note = "SYNTHETIC_PRIVATE_CONTACT_NOTE_48152"
    private_memory = "SYNTHETIC_PRIVATE_MEMORY_71824"
    after_backup_memory = "SYNTHETIC_AFTER_BACKUP_MEMORY_99210"

    with TestClient(app) as client:
        assert client.get("/api/v1/control/backups").status_code == 401
        assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200

        contact = client.post(
            "/api/v1/control/contacts",
            json={
                "display_name": "Synthetic Contact Alpha",
                "organization": "Synthetic Org",
                "relationship": "test relationship",
                "notes": private_note,
            },
        )
        assert contact.status_code == 200
        original_contact_id = contact.json()["contact"]["id"]

        memory = client.post(
            "/api/v1/control/memory",
            json={"memory_key": "backup-test", "content": private_memory, "importance": 0.8},
        )
        assert memory.status_code == 200

        document_bytes = b"# Synthetic backup document\n\nPortable knowledge file sentinel 36177."
        imported = client.post(
            "/api/v1/control/knowledge/import",
            files={"file": ("synthetic-backup.md", document_bytes, "text/markdown")},
        )
        assert imported.status_code == 200
        imported_id = imported.json()["id"]
        assert any(settings.knowledge_files_dir.iterdir())

        created = client.post("/api/v1/control/backups/create")
        assert created.status_code == 200
        backup = created.json()["backup"]
        assert backup["schema_version"] == 10
        assert backup["file_count"] >= 2
        assert backup["reason"] == "manual"
        backup_file = settings.backups_dir / backup["name"]
        assert backup_file.is_file()

        listed = client.get("/api/v1/control/backups")
        assert listed.status_code == 200
        assert listed.json()["items"][0]["name"] == backup["name"]
        assert listed.json()["pending_restore"] is None

        downloaded = client.get(f"/api/v1/control/backups/download/{backup['name']}")
        assert downloaded.status_code == 200
        assert downloaded.headers["content-type"].startswith("application/zip")
        assert downloaded.headers["cache-control"] == "no-store"
        archive_bytes = downloaded.content
        assert archive_bytes

        with zipfile.ZipFile(io.BytesIO(archive_bytes), "r") as archive:
            names = set(archive.namelist())
            assert "manifest.json" in names
            assert "database/homeserver.db" in names
            assert any(name.startswith("knowledge/files/") for name in names)
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["format"] == backups.BACKUP_FORMAT
            assert manifest["schema_version"] == 10
            assert len(manifest["files"]) == backup["file_count"]

        activity_text = json.dumps(client.get("/api/v1/control/activity?limit=100").json(), ensure_ascii=False)
        assert private_note not in activity_text
        assert private_memory not in activity_text

        client.put(
            f"/api/v1/control/contacts/{original_contact_id}",
            json={
                "display_name": "Synthetic Contact Alpha",
                "organization": "Synthetic Org",
                "relationship": "changed after backup",
                "notes": "changed",
            },
        )
        client.post(
            "/api/v1/control/memory",
            json={"memory_key": "after-backup", "content": after_backup_memory, "importance": 0.4},
        )
        assert client.delete(f"/api/v1/control/knowledge/{imported_id}").status_code == 200

        corrupted_buffer = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(archive_bytes), "r") as source, zipfile.ZipFile(corrupted_buffer, "w") as target:
            for info in source.infolist():
                data = source.read(info.filename)
                if info.filename == "database/homeserver.db":
                    data += b"tamper"
                target.writestr(info.filename, data)
        corrupt_stage = client.post(
            "/api/v1/control/restore/stage",
            files={"file": ("corrupt.zip", corrupted_buffer.getvalue(), "application/zip")},
        )
        assert corrupt_stage.status_code == 422
        assert not settings.pending_restore_dir.exists()

        stage = client.post(
            "/api/v1/control/restore/stage",
            files={"file": ("valid-backup.zip", archive_bytes, "application/zip")},
        )
        assert stage.status_code == 200
        assert stage.json()["restart_required"] is True
        pending = client.get("/api/v1/control/backups").json()["pending_restore"]
        assert pending["valid"] is True
        assert pending["status"] == "pending_restart"

        staged_database = settings.pending_restore_dir / "database" / "homeserver.db"
        with staged_database.open("ab") as handle:
            handle.write(b"post-stage-tamper")
        try:
            backups.apply_pending_restore()
            raise AssertionError("Tampered staged restore should fail")
        except backups.BackupError:
            pass
        assert not settings.pending_restore_dir.exists()
        failed_result = backups.last_restore_result()
        assert failed_result is not None and failed_result["status"] == "failed"
        current_contact = client.get("/api/v1/control/contacts?q=Synthetic%20Contact").json()["items"][0]
        assert current_contact["relationship"] == "changed after backup"
        assert any(item["content"] == after_backup_memory for item in client.get("/api/v1/control/memory").json()["items"])

        stage_again = client.post(
            "/api/v1/control/restore/stage",
            files={"file": ("valid-backup.zip", archive_bytes, "application/zip")},
        )
        assert stage_again.status_code == 200
        assert settings.pending_restore_dir.is_dir()

    applied = backups.apply_pending_restore()
    assert applied is not None and applied["status"] == "applied"
    assert applied["pre_restore_backup"]
    assert not settings.pending_restore_dir.exists()

    with TestClient(app) as restored_client:
        assert restored_client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200
        restored_contacts = restored_client.get("/api/v1/control/contacts?q=Synthetic%20Contact")
        assert restored_contacts.status_code == 200
        restored_contact = restored_contacts.json()["items"][0]
        assert restored_contact["id"] == original_contact_id
        assert restored_contact["relationship"] == "test relationship"
        assert restored_contact["notes"] == private_note

        restored_memories = restored_client.get("/api/v1/control/memory").json()["items"]
        assert any(item["content"] == private_memory for item in restored_memories)
        assert not any(item["content"] == after_backup_memory for item in restored_memories)

        restored_knowledge = restored_client.get("/api/v1/control/knowledge?q=Portable%20knowledge").json()["items"]
        assert len(restored_knowledge) == 1
        assert restored_knowledge[0]["id"] == imported_id
        assert any(settings.knowledge_files_dir.iterdir())

        backup_listing = restored_client.get("/api/v1/control/backups").json()
        reasons = {item["reason"] for item in backup_listing["items"]}
        assert "manual" in reasons
        assert "pre-restore" in reasons
        assert backup_listing["last_restore"]["status"] == "applied"
        assert backup_listing["pending_restore"] is None

        manual_name = next(item["name"] for item in backup_listing["items"] if item["reason"] == "manual")
        deleted = restored_client.delete(f"/api/v1/control/backups/{manual_name}")
        assert deleted.status_code == 200
        assert all(item["name"] != manual_name for item in restored_client.get("/api/v1/control/backups").json()["items"])

print("HomeServer backup and restore test passed")
