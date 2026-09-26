from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


with tempfile.TemporaryDirectory(prefix="homeserver-v244-files-") as temp_root:
    root = Path(temp_root)
    data_dir = root / "data"
    files_dir = root / "continuity-files"
    files_dir.mkdir(parents=True)
    plan_path = files_dir / "plan.txt"
    delete_path = files_dir / "delete.txt"
    plan_path.write_text("SECTION5_ORIGINAL_PLAN", encoding="utf-8")
    delete_path.write_text("SECTION5_DELETE_ME", encoding="utf-8")
    os.environ["HOMESERVER_DATA_DIR"] = str(data_dir)

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import federated_data, file_continuity, shared_agent_context  # noqa: E402
    from app.services.knowledge_sources import scheduler  # noqa: E402
    from app.services.tasks import scheduler as task_scheduler  # noqa: E402

    with TestClient(app) as client:
        scheduler.stop()
        task_scheduler.stop()

        assert client.post(
            "/__owner/session",
            headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN},
        ).status_code == 200

        with db() as connection:
            versions = [
                row["version"]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
            ]
        assert versions == list(range(1, 39))
        assert "files" in federated_data.DATASETS

        created = client.post(
            "/api/v1/control/knowledge/collections",
            json={"collection_key": "continuity", "name": "Continuity"},
        )
        assert created.status_code == 200, created.text

        source = client.post(
            "/api/v1/control/knowledge/sources",
            json={
                "path": str(files_dir),
                "label": "Section 5 local files",
                "scan_interval_seconds": 60,
            },
        )
        assert source.status_code == 200, source.text
        source_id = int(source.json()["source"]["id"])
        assigned = client.put(
            f"/api/v1/control/knowledge/sources/{source_id}/collection",
            json={"collection_key": "continuity"},
        )
        assert assigned.status_code == 200, assigned.text

        pair = client.post(
            "/api/v1/pairing/request",
            json={
                "app_key": "file-continuity-v244",
                "app_name": "File Continuity",
                "permissions": [
                    "agent.chat",
                    "files.read",
                    "files.write",
                    "tools.execute",
                ],
            },
        ).json()
        assert client.post(
            "/api/v1/pairing/approve",
            json={"code": pair["code"]},
        ).status_code == 200
        headers = {"Authorization": f"Bearer {pair['claim_token']}"}

        app_row = next(
            item
            for item in client.get("/api/v1/control/apps").json()["apps"]
            if item["app_key"] == "file-continuity-v244"
        )
        app_id = int(app_row["id"])
        assert client.put(
            f"/api/v1/control/apps/{app_id}/knowledge-collections",
            json={"collection_keys": ["continuity"]},
        ).status_code == 200

        listing = client.get("/api/v1/files", headers=headers)
        assert listing.status_code == 200, listing.text
        items = listing.json()["items"]
        assert {item["name"] for item in items} == {"plan.txt", "delete.txt"}
        plan = next(item for item in items if item["name"] == "plan.txt")
        delete_item = next(item for item in items if item["name"] == "delete.txt")

        for item in (plan, delete_item):
            assert item["authority_source"] == "homeserver"
            assert item["authority_key"].startswith("local_file:")
            assert item["canonical_id"].startswith("fd24_")
            assert len(item["canonical_id"]) == 45
            assert len(item["record_revision"]) == 64
            assert item["federation_version"] == "2.4"
            assert item["mirror_only"] is False
            assert item["mutation_route"] == "homeserver_owner_approval"
            assert str(files_dir.resolve()) not in json.dumps(item, ensure_ascii=False)

        old_canonical = plan["canonical_id"]
        old_revision = plan["record_revision"]
        old_ref = plan["ref"]
        updated_text = "SECTION5_UPDATED_PLAN\nCanonical file continuity."

        proposal_payload = {
            "canonical_id": old_canonical,
            "mutation_id": "file-update-v244-001",
            "expected_revision": old_revision,
            "content": updated_text,
        }
        proposed = client.post(
            "/api/v1/tools/files.update/execute",
            headers=headers,
            json={"arguments": proposal_payload},
        )
        assert proposed.status_code == 200, proposed.text
        proposed_json = proposed.json()
        assert proposed_json["approval_required"] is True
        request_id = str(proposed_json["result"]["request_id"])

        with db() as connection:
            queued = connection.execute(
                """
                SELECT arguments_json,arguments_meta_json
                FROM action_requests WHERE id=? LIMIT 1
                """,
                (request_id,),
            ).fetchone()
        assert queued is not None
        queued_arguments = json.loads(queued["arguments_json"])
        assert queued_arguments["content"] == updated_text
        assert queued_arguments["canonical_id"] == old_canonical
        assert queued_arguments["mutation_id"] == proposal_payload["mutation_id"]
        assert queued_arguments["expected_revision"] == old_revision
        assert updated_text not in queued["arguments_meta_json"]
        assert old_canonical not in queued["arguments_meta_json"]
        assert old_ref not in queued["arguments_meta_json"]
        assert '"canonical_id_present":true' in queued["arguments_meta_json"]
        assert '"expected_revision_present":true' in queued["arguments_meta_json"]

        approved = client.post(
            f"/api/v1/control/action-requests/{request_id}/approve"
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["request"]["status"] == "executed"
        assert plan_path.read_text(encoding="utf-8") == updated_text

        after_items = client.get("/api/v1/files", headers=headers).json()["items"]
        after = next(item for item in after_items if item["name"] == "plan.txt")
        assert after["canonical_id"] == old_canonical
        assert after["record_revision"] != old_revision
        assert after["ref"] != old_ref

        replay = file_continuity.update_federated_file(
            proposal_payload,
            source_app_key="app:file-continuity-v244",
        )
        assert replay["idempotent_replay"] is True
        assert replay["file"]["canonical_id"] == old_canonical

        try:
            file_continuity.update_federated_file(
                {
                    "canonical_id": old_canonical,
                    "mutation_id": "file-update-v244-stale",
                    "expected_revision": old_revision,
                    "content": "STALE_SHOULD_NOT_WRITE",
                },
                source_app_key="app:file-continuity-v244",
            )
            raise AssertionError("stale file revision should fail")
        except file_continuity.FileContinuityError as exc:
            assert exc.status_code == 409
        assert plan_path.read_text(encoding="utf-8") == updated_text

        cloud_canonical = federated_data.canonical_id(
            "vp3_cloud",
            "files",
            "knowledge_file:999",
        )
        try:
            file_continuity.delete_federated_file(
                {
                    "canonical_id": cloud_canonical,
                    "mutation_id": "file-cloud-v244-001",
                    "expected_revision": "0" * 64,
                },
                source_app_key="app:file-continuity-v244",
            )
            raise AssertionError("Cloud-authoritative file must not mutate on HomeServer")
        except file_continuity.FileContinuityError as exc:
            assert exc.status_code in {404, 409}

        delete_payload = {
            "canonical_id": delete_item["canonical_id"],
            "mutation_id": "file-delete-v244-001",
            "expected_revision": delete_item["record_revision"],
        }
        delete_proposal = client.post(
            "/api/v1/tools/files.delete/execute",
            headers=headers,
            json={"arguments": delete_payload},
        )
        assert delete_proposal.status_code == 200, delete_proposal.text
        delete_request = str(delete_proposal.json()["result"]["request_id"])
        deleted = client.post(
            f"/api/v1/control/action-requests/{delete_request}/approve"
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["request"]["status"] == "executed"
        assert not delete_path.exists()

        with db() as connection:
            tombstone = connection.execute(
                """
                SELECT tombstoned
                FROM federated_record_links
                WHERE canonical_id=? AND authority_source='homeserver'
                  AND dataset='files' AND observed_source='homeserver'
                ORDER BY last_seen_at DESC LIMIT 1
                """,
                (delete_item["canonical_id"],),
            ).fetchone()
        assert tombstone is not None and int(tombstone["tombstoned"]) == 1

        snapshot = shared_agent_context.local_snapshot("plan")
        assert "files" in snapshot["datasets"]
        shared_files = snapshot["datasets"]["files"]
        assert shared_files
        encoded = json.dumps(shared_files, ensure_ascii=False)
        assert str(files_dir.resolve()) not in encoded
        assert "relative_path" not in encoded
        assert old_canonical in encoded
        assert "SECTION5_UPDATED_PLAN" not in encoded
        assert after["ref"] in encoded

        registry = client.get("/api/v1/capabilities").json()
        continuity = registry["file_document_continuity"]
        assert continuity["dataset"] == "files"
        assert continuity["canonical_identity"] is True
        assert continuity["local_owner_approval_only"] is True
        assert continuity["optimistic_concurrency"] is True
        assert continuity["idempotent_mutations"] is True
        assert continuity["absolute_paths_exposed"] is False

print("HomeServer v2.4 Section 5 Files & Document continuity: PASS")
