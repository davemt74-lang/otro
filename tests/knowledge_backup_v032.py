from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-knowledge-backup-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.config import settings  # noqa: E402
    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import app_scopes  # noqa: E402
    from app.services.knowledge_backups import MAX_CHUNK_BYTES  # noqa: E402
    from app.services import remote_bridge  # noqa: E402

    def pair(client: TestClient, app_key: str, permissions: list[str]) -> tuple[str, int]:
        request = client.post(
            "/api/v1/pairing/request",
            json={"app_key": app_key, "app_name": app_key, "permissions": permissions},
        )
        assert request.status_code == 200, request.text
        payload = request.json()
        approved = client.post("/api/v1/pairing/approve", json={"code": payload["code"]})
        assert approved.status_code == 200, approved.text
        with db() as connection:
            row = connection.execute("SELECT id FROM paired_apps WHERE app_key=?", (app_key,)).fetchone()
        assert row is not None
        return payload["claim_token"], int(row["id"])

    with TestClient(app) as client:
        assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200

        capabilities = client.get("/api/v1/capabilities").json()
        assert "knowledge.write" in capabilities["permissions"]
        assert "knowledge.external_backup.v1" in capabilities["features"]
        assert getattr(remote_bridge, "_knowledge_backup_v032_installed", False) is True

        token, app_id = pair(client, "vp3", ["knowledge.search", "knowledge.write"])
        other_token, _ = pair(client, "other-wrapper", ["knowledge.search", "knowledge.write"])
        readonly_token, _ = pair(client, "readonly-wrapper", ["knowledge.search"])
        headers = {"Authorization": f"Bearer {token}"}
        other_headers = {"Authorization": f"Bearer {other_token}"}
        readonly_headers = {"Authorization": f"Bearer {readonly_token}"}

        app_scopes.save_scope(
            app_id,
            {
                "cloud_allowed": True,
                "memory_key_prefixes": [],
                "knowledge_kinds": ["transcription"],
                "tool_names": [],
                "plugin_keys": [],
            },
        )

        source_key = "vp3-transcript:session-42"
        transcript = "Speaker 1: local-first transcript content.\nSpeaker 2: keep the recording private."
        created = client.post(
            "/api/v1/knowledge/external",
            headers=headers,
            json={
                "source_key": source_key,
                "title": "Private transcript 42",
                "kind": "transcription",
                "content": transcript,
                "metadata": {
                    "cloud_knowledge_id": 4201,
                    "session_id": 42,
                    "track_id": 9,
                    "recording_count": 1,
                    "source": "vp3-cloud-knowledge",
                    "ignored_secret": "DO_NOT_COPY_THIS_FIELD",
                },
            },
        )
        assert created.status_code == 200, created.text
        item_id = int(created.json()["id"])
        assert created.json()["created"] is True

        updated = client.post(
            "/api/v1/knowledge/external",
            headers=headers,
            json={
                "source_key": source_key,
                "title": "Private transcript 42 updated",
                "kind": "transcription",
                "content": transcript + "\nSpeaker 1: updated safely.",
                "metadata": {"cloud_knowledge_id": 4201, "session_id": 42},
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["created"] is False
        assert int(updated.json()["id"]) == item_id

        # App scope narrows both writes and reads; a new write permission never widens scope.
        denied_kind = client.post(
            "/api/v1/knowledge/external",
            headers=headers,
            json={
                "source_key": "vp3-note:outside-scope",
                "title": "Denied note",
                "kind": "note",
                "content": "This must not enter the scoped VP3 Knowledge view.",
            },
        )
        assert denied_kind.status_code == 403, denied_kind.text
        assert client.post(
            "/api/v1/knowledge/external",
            headers=readonly_headers,
            json={"source_key": "readonly:attempt-1", "title": "No write", "kind": "transcription", "content": "blocked"},
        ).status_code == 403

        # Another paired wrapper cannot resolve VP3's stable external source key.
        other_status = client.post(
            "/api/v1/knowledge/external/status",
            headers=other_headers,
            json={"source_key": source_key},
        )
        assert other_status.status_code == 200
        assert other_status.json() == {"exists": False, "assets": []}

        recording = (b"private-audio-frame-" * 9000) + b"tail"
        digest = hashlib.sha256(recording).hexdigest()
        begin = client.post(
            "/api/v1/knowledge/external/assets/begin",
            headers=headers,
            json={
                "source_key": source_key,
                "asset_key": "recording:primary-001",
                "original_name": "session-42.webm",
                "media_type": "audio/webm",
                "size_bytes": len(recording),
                "sha256": digest,
            },
        )
        assert begin.status_code == 200, begin.text
        upload_id = begin.json()["upload_id"]
        assert begin.json()["chunk_bytes"] == MAX_CHUNK_BYTES

        first = recording[:MAX_CHUNK_BYTES]
        first_chunk = client.post(
            "/api/v1/knowledge/external/assets/chunk",
            headers=headers,
            json={"upload_id": upload_id, "offset": 0, "data_base64": base64.b64encode(first).decode("ascii")},
        )
        assert first_chunk.status_code == 200, first_chunk.text
        offset = int(first_chunk.json()["received_bytes"])
        assert offset == len(first)

        # Offset disagreement is resumable and does not duplicate/corrupt bytes.
        resync = client.post(
            "/api/v1/knowledge/external/assets/chunk",
            headers=headers,
            json={"upload_id": upload_id, "offset": 0, "data_base64": base64.b64encode(b"wrong").decode("ascii")},
        )
        assert resync.status_code == 200, resync.text
        assert resync.json()["resync"] is True
        assert int(resync.json()["received_bytes"]) == offset

        # Upload IDs are scoped to the paired app that created them.
        foreign_chunk = client.post(
            "/api/v1/knowledge/external/assets/chunk",
            headers=other_headers,
            json={"upload_id": upload_id, "offset": offset, "data_base64": base64.b64encode(b"foreign").decode("ascii")},
        )
        assert foreign_chunk.status_code == 403, foreign_chunk.text

        while offset < len(recording):
            chunk = recording[offset : offset + MAX_CHUNK_BYTES]
            response = client.post(
                "/api/v1/knowledge/external/assets/chunk",
                headers=headers,
                json={"upload_id": upload_id, "offset": offset, "data_base64": base64.b64encode(chunk).decode("ascii")},
            )
            assert response.status_code == 200, response.text
            offset = int(response.json()["received_bytes"])
        assert offset == len(recording)

        committed = client.post(
            "/api/v1/knowledge/external/assets/commit",
            headers=headers,
            json={"upload_id": upload_id},
        )
        assert committed.status_code == 200, committed.text
        public_asset = committed.json()["asset"]
        assert public_asset["asset_key"] == "recording:primary-001"
        assert public_asset["sha256"] == digest
        assert "stored_name" not in public_asset

        status = client.post(
            "/api/v1/knowledge/external/status",
            headers=headers,
            json={"source_key": source_key},
        )
        assert status.status_code == 200, status.text
        assert status.json()["exists"] is True
        assert len(status.json()["assets"]) == 1
        assert "stored_name" not in json.dumps(status.json())

        repeated = client.post(
            "/api/v1/knowledge/external/assets/begin",
            headers=headers,
            json={
                "source_key": source_key,
                "asset_key": "recording:primary-001",
                "original_name": "session-42.webm",
                "media_type": "audio/webm",
                "size_bytes": len(recording),
                "sha256": digest,
            },
        )
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["already_present"] is True

        with db() as connection:
            row = connection.execute("SELECT metadata_json FROM knowledge_items WHERE id=?", (item_id,)).fetchone()
            assert row is not None
            metadata = json.loads(row["metadata_json"])
            assert metadata["external"] == {"app_key": "vp3", "source_key": source_key}
            assert "ignored_secret" not in json.dumps(metadata)
            stored_name = metadata["assets"][0]["stored_name"]
            activity = connection.execute(
                "SELECT metadata_json FROM activity_log WHERE action IN ('knowledge.external_upserted','knowledge.asset_backed_up') ORDER BY id"
            ).fetchall()
        asset_path = settings.knowledge_files_dir / stored_name
        assert asset_path.is_file()
        assert asset_path.read_bytes() == recording
        audit = json.dumps([json.loads(row["metadata_json"]) for row in activity], ensure_ascii=False)
        assert transcript not in audit
        assert base64.b64encode(recording[:64]).decode("ascii") not in audit

        deleted = client.delete(f"/api/v1/control/knowledge/{item_id}")
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["deleted"] is True
        assert not asset_path.exists(), "Deleting Knowledge must delete its private recording attachments"

print("HomeServer v0.32 Knowledge backup test passed")
