from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-knowledge-sources-") as temp_root:
    root = Path(temp_root)
    data_dir = root / "data"
    source_dir = root / "project"
    source_dir.mkdir(parents=True)
    nested = source_dir / "notes"
    nested.mkdir()
    excluded = source_dir / "node_modules"
    excluded.mkdir()

    alpha = source_dir / "alpha.md"
    beta = nested / "beta.txt"
    alpha.write_text("ALPHA_SOURCE_SENTINEL_1001 original local knowledge", encoding="utf-8")
    beta.write_text("BETA_SOURCE_SENTINEL_2002 nested local knowledge", encoding="utf-8")
    (excluded / "hidden.md").write_text("EXCLUDED_SOURCE_SENTINEL_9999", encoding="utf-8")

    os.environ["HOMESERVER_DATA_DIR"] = str(data_dir)

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services.knowledge_sources import scheduler  # noqa: E402

    with TestClient(app) as client:
        scheduler.stop()
        assert client.get("/api/v1/control/knowledge/sources").status_code == 401
        assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200

        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["version"] == "0.18.0"
        status = client.get("/api/v1/status")
        assert status.status_code == 200
        assert status.json()["schema_version"] >= 20

        internal = client.post(
            "/api/v1/control/knowledge/sources",
            json={"path": str(data_dir), "label": "Must reject"},
        )
        assert internal.status_code == 422

        created = client.post(
            "/api/v1/control/knowledge/sources",
            json={
                "path": str(source_dir),
                "label": "Synthetic project",
                "recursive": True,
                "scan_interval_seconds": 60,
                "excludes": ["*.tmp"],
            },
        )
        assert created.status_code == 200, created.text
        created_json = created.json()
        source_id = created_json["source"]["id"]
        assert created_json["scan"]["indexed"] == 2
        assert created_json["scan"]["errors"] == 0
        assert created_json["source"]["indexed_files"] == 2
        assert created_json["source"]["error_files"] == 0

        sources = client.get("/api/v1/control/knowledge/sources")
        assert sources.status_code == 200
        source_json = next(item for item in sources.json()["items"] if item["id"] == source_id)
        assert source_json["label"] == "Synthetic project"
        assert source_json["enabled"] is True
        assert source_json["recursive"] is True
        assert source_json["status"] == "ready"
        assert Path(source_json["path"]) == source_dir.resolve()
        assert "node_modules" in source_json["excludes"]
        assert ".md" in sources.json()["supported_extensions"]

        tracked = client.get(f"/api/v1/control/knowledge/sources/{source_id}/files").json()["items"]
        assert {item["relative_path"] for item in tracked} == {"alpha.md", "notes/beta.txt"}

        alpha_search = client.get("/api/v1/control/knowledge?q=ALPHA_SOURCE_SENTINEL_1001")
        assert alpha_search.status_code == 200
        assert len(alpha_search.json()["items"]) == 1
        alpha_item = alpha_search.json()["items"][0]
        assert alpha_item["kind"] == "watched_document"
        assert alpha_item["source_path"] is None

        beta_search = client.get("/api/v1/control/knowledge?q=BETA_SOURCE_SENTINEL_2002")
        assert len(beta_search.json()["items"]) == 1
        beta_item_id = beta_search.json()["items"][0]["id"]
        assert beta_search.json()["items"][0]["source_path"] is None
        assert not client.get("/api/v1/control/knowledge?q=EXCLUDED_SOURCE_SENTINEL_9999").json()["items"]

        pair = client.post(
            "/api/v1/pairing/request",
            json={
                "app_key": "knowledge-source-reader",
                "app_name": "Knowledge Source Reader",
                "permissions": ["knowledge.search"],
            },
        ).json()
        assert client.post("/api/v1/pairing/approve", json={"code": pair["code"]}).status_code == 200
        paired = client.get(
            "/api/v1/knowledge?q=BETA_SOURCE_SENTINEL_2002",
            headers={"Authorization": f"Bearer {pair['claim_token']}"},
        )
        assert paired.status_code == 200
        paired_text = json.dumps(paired.json(), ensure_ascii=False)
        assert str(source_dir.resolve()) not in paired_text
        assert str(root.resolve()) not in paired_text
        paired_item = paired.json()["items"][0]
        assert "source_path" not in paired_item
        assert paired_item["citation"]["source_type"] == "watched_folder"
        assert paired_item["citation"]["relative_path"] == "notes/beta.txt"

        unchanged = client.post(f"/api/v1/control/knowledge/sources/{source_id}/scan")
        assert unchanged.status_code == 200
        assert unchanged.json()["scan"]["indexed"] == 0
        assert unchanged.json()["scan"]["updated"] == 0
        assert unchanged.json()["scan"]["skipped"] == 2

        alpha.write_text("ALPHA_SOURCE_SENTINEL_3003 updated local knowledge with new text", encoding="utf-8")
        stat = alpha.stat()
        os.utime(alpha, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
        changed = client.post(f"/api/v1/control/knowledge/sources/{source_id}/scan")
        assert changed.status_code == 200
        assert changed.json()["scan"]["updated"] == 1
        assert len(client.get("/api/v1/control/knowledge?q=ALPHA_SOURCE_SENTINEL_3003").json()["items"]) == 1
        assert not client.get("/api/v1/control/knowledge?q=ALPHA_SOURCE_SENTINEL_1001").json()["items"]

        gamma = source_dir / "gamma.txt"
        beta.rename(gamma)
        moved = client.post(f"/api/v1/control/knowledge/sources/{source_id}/scan")
        assert moved.status_code == 200
        assert moved.json()["scan"]["moved"] == 1
        assert moved.json()["scan"]["removed"] == 0
        beta_after = client.get("/api/v1/control/knowledge?q=BETA_SOURCE_SENTINEL_2002").json()["items"]
        assert len(beta_after) == 1
        assert beta_after[0]["id"] == beta_item_id
        moved_files = client.get(f"/api/v1/control/knowledge/sources/{source_id}/files").json()["items"]
        assert "gamma.txt" in {item["relative_path"] for item in moved_files}
        assert "notes/beta.txt" not in {item["relative_path"] for item in moved_files}

        manual_delete = client.delete(f"/api/v1/control/knowledge/{beta_item_id}")
        assert manual_delete.status_code == 200
        repaired = client.post(f"/api/v1/control/knowledge/sources/{source_id}/scan")
        assert repaired.status_code == 200
        repaired_beta = client.get("/api/v1/control/knowledge?q=BETA_SOURCE_SENTINEL_2002").json()["items"]
        assert len(repaired_beta) == 1
        assert repaired_beta[0]["id"] != beta_item_id

        alpha.unlink()
        removed = client.post(f"/api/v1/control/knowledge/sources/{source_id}/scan")
        assert removed.status_code == 200
        assert removed.json()["scan"]["removed"] == 1
        assert not client.get("/api/v1/control/knowledge?q=ALPHA_SOURCE_SENTINEL_3003").json()["items"]

        paused = client.patch(
            f"/api/v1/control/knowledge/sources/{source_id}",
            json={"enabled": False},
        )
        assert paused.status_code == 200
        assert paused.json()["source"]["enabled"] is False
        assert paused.json()["source"]["status"] == "paused"

        resumed = client.patch(
            f"/api/v1/control/knowledge/sources/{source_id}",
            json={"enabled": True},
        )
        assert resumed.status_code == 200
        assert resumed.json()["source"]["enabled"] is True

        source_dir.rename(root / "project-offline")
        unavailable = client.post(f"/api/v1/control/knowledge/sources/{source_id}/scan")
        assert unavailable.status_code == 422
        source_after_error = next(
            item for item in client.get("/api/v1/control/knowledge/sources").json()["items"]
            if item["id"] == source_id
        )
        assert source_after_error["status"] == "error"
        assert source_after_error["indexed_files"] == 1
        assert len(client.get("/api/v1/control/knowledge?q=BETA_SOURCE_SENTINEL_2002").json()["items"]) == 1

        offline_dir = root / "project-offline"
        offline_dir.rename(source_dir)
        recovered = client.post(f"/api/v1/control/knowledge/sources/{source_id}/scan")
        assert recovered.status_code == 200
        assert recovered.json()["source"]["status"] == "ready"

        deleted = client.delete(f"/api/v1/control/knowledge/sources/{source_id}")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True
        assert gamma.exists(), "Removing a source must never delete the original local file"
        assert not client.get("/api/v1/control/knowledge?q=BETA_SOURCE_SENTINEL_2002").json()["items"]
        assert not client.get("/api/v1/control/knowledge/sources").json()["items"]

print("HomeServer watched knowledge source regression passed")
