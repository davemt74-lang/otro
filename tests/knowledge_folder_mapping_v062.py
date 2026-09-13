from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-knowledge-v062-") as temp_root:
    root = Path(temp_root)
    data_dir = root / "data"
    meetings_dir = root / "meeting-notes"
    meetings_dir.mkdir(parents=True)
    source_file = meetings_dir / "weekly.txt"
    source_file.write_text(
        "V062_FOLDER_SENTINEL synthetic weekly meeting notes for mapped-folder regression.",
        encoding="utf-8",
    )

    os.environ["HOMESERVER_DATA_DIR"] = str(data_dir)

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services.knowledge_sources import scheduler  # noqa: E402

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post(
            "/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}
        ).status_code == 200

        for key, name in (("meetings", "Meetings"), ("private", "Private")):
            created = client.post(
                "/api/v1/control/knowledge/collections",
                json={"collection_key": key, "name": name, "description": f"Synthetic {name}"},
            )
            assert created.status_code == 200, created.text

        pair = client.post(
            "/api/v1/pairing/request",
            json={
                "app_key": "vp3-cloud-v062",
                "app_name": "VP3 Cloud v0.62",
                "permissions": ["knowledge.search", "knowledge.write"],
            },
        ).json()
        approved = client.post("/api/v1/pairing/approve", json={"code": pair["code"]})
        assert approved.status_code == 200, approved.text
        auth = {"Authorization": f"Bearer {pair['claim_token']}"}

        collections = client.get("/api/v1/knowledge/collections-v062", headers=auth)
        assert collections.status_code == 200, collections.text
        collection_payload = collections.json()
        assert {item["collection_key"] for item in collection_payload["items"]} >= {
            "general",
            "meetings",
            "private",
        }
        assert collection_payload["scope"]["restricted"] is False

        with patch(
            "app.services.knowledge_folder_mapping.pick_local_folder",
            return_value=meetings_dir,
        ) as picker:
            mapped = client.post(
                "/api/v1/knowledge/folder-mappings-v062",
                json={
                    "collection_key": "meetings",
                    "label": "Meeting notes",
                    "recursive": True,
                    "scan_interval_seconds": 60,
                },
                headers=auth,
            )
        assert mapped.status_code == 200, mapped.text
        picker.assert_called_once()
        mapped_payload = mapped.json()
        assert mapped_payload["created"] is True
        assert mapped_payload["cancelled"] is False
        assert mapped_payload["mapping"]["collection_key"] == "meetings"
        assert mapped_payload["mapping"]["label"] == "Meeting notes"
        assert mapped_payload["mapping"]["indexed_files"] == 1
        assert mapped_payload["privacy"] == {"local_path_exposed": False}
        mapping_id = mapped_payload["mapping"]["mapping_id"]

        serialized_mapping = json.dumps(mapped_payload, ensure_ascii=False)
        assert str(root.resolve()) not in serialized_mapping
        assert str(meetings_dir.resolve()) not in serialized_mapping
        assert "source_path" not in serialized_mapping
        assert "path\"" not in serialized_mapping

        listed = client.get("/api/v1/knowledge/folder-mappings-v062", headers=auth)
        assert listed.status_code == 200, listed.text
        listed_payload = listed.json()
        assert listed_payload["privacy"]["local_paths_exposed"] is False
        assert listed_payload["items"][0]["mapping_id"] == mapping_id
        assert str(meetings_dir.resolve()) not in json.dumps(listed_payload, ensure_ascii=False)

        indexed = client.get(
            "/api/v1/knowledge/search-v037",
            params={"q": "V062_FOLDER_SENTINEL"},
            headers=auth,
        )
        assert indexed.status_code == 200, indexed.text
        assert indexed.json()["count"] == 1
        assert indexed.json()["items"][0]["citation"]["collection_key"] == "meetings"

        summary = client.post(
            "/api/v1/knowledge/items-v062",
            json={
                "collection_key": "meetings",
                "title": "Weekly AI summary",
                "kind": "summary",
                "content": "V062_SUMMARY_SENTINEL action items and decisions from the synthetic transcript.",
            },
            headers=auth,
        )
        assert summary.status_code == 200, summary.text
        summary_payload = summary.json()
        assert summary_payload["created"] is True
        assert summary_payload["item"]["collection_key"] == "meetings"
        assert summary_payload["item"]["kind"] == "summary"
        assert "content" not in summary_payload["item"]

        summary_search = client.get(
            "/api/v1/knowledge/search-v037",
            params={"q": "V062_SUMMARY_SENTINEL"},
            headers=auth,
        )
        assert summary_search.status_code == 200
        assert summary_search.json()["count"] == 1
        assert summary_search.json()["items"][0]["citation"]["collection_key"] == "meetings"

        apps = client.get("/api/v1/control/apps").json()["apps"]
        app_id = int(next(item for item in apps if item["app_key"] == "vp3-cloud-v062")["id"])
        restricted = client.put(
            f"/api/v1/control/apps/{app_id}/knowledge-collections",
            json={"collection_keys": ["meetings"]},
        )
        assert restricted.status_code == 200, restricted.text

        restricted_collections = client.get("/api/v1/knowledge/collections-v062", headers=auth).json()
        assert [item["collection_key"] for item in restricted_collections["items"]] == ["meetings"]
        assert restricted_collections["scope"] == {"restricted": True, "collections": ["meetings"]}

        with patch("app.services.knowledge_folder_mapping.pick_local_folder") as denied_picker:
            denied_map = client.post(
                "/api/v1/knowledge/folder-mappings-v062",
                json={"collection_key": "private"},
                headers=auth,
            )
        assert denied_map.status_code == 403
        denied_picker.assert_not_called()

        denied_write = client.post(
            "/api/v1/knowledge/items-v062",
            json={
                "collection_key": "private",
                "title": "Should not save",
                "kind": "summary",
                "content": "OUT_OF_SCOPE_V062",
            },
            headers=auth,
        )
        assert denied_write.status_code == 403

        scope_update = client.put(
            f"/api/v1/control/apps/{app_id}/scope",
            json={
                "cloud_allowed": True,
                "memory_key_prefixes": [],
                "knowledge_kinds": ["summary"],
                "tool_names": [],
                "plugin_keys": [],
            },
        )
        assert scope_update.status_code == 200, scope_update.text
        denied_kind = client.post(
            "/api/v1/knowledge/items-v062",
            json={
                "collection_key": "meetings",
                "title": "Wrong kind",
                "kind": "note",
                "content": "KIND_SCOPE_V062",
            },
            headers=auth,
        )
        assert denied_kind.status_code == 403

        with patch(
            "app.services.knowledge_folder_mapping.pick_local_folder",
            return_value=meetings_dir,
        ):
            remapped = client.post(
                "/api/v1/knowledge/folder-mappings-v062",
                json={"collection_key": "meetings", "label": "Meeting notes renamed"},
                headers=auth,
            )
        assert remapped.status_code == 200, remapped.text
        assert remapped.json()["created"] is False
        assert remapped.json()["mapping"]["mapping_id"] == mapping_id
        assert remapped.json()["mapping"]["label"] == "Meeting notes renamed"

        with patch(
            "app.services.knowledge_folder_mapping.pick_local_folder",
            return_value=None,
        ):
            cancelled = client.post(
                "/api/v1/knowledge/folder-mappings-v062",
                json={"collection_key": "meetings"},
                headers=auth,
            )
        assert cancelled.status_code == 200
        assert cancelled.json()["cancelled"] is True
        assert cancelled.json()["created"] is False

        removed = client.delete(
            f"/api/v1/knowledge/folder-mappings-v062/{mapping_id}", headers=auth
        )
        assert removed.status_code == 200, removed.text
        removed_payload = removed.json()
        assert removed_payload["deleted"] is True
        assert removed_payload["privacy"] == {"local_path_exposed": False}
        assert str(meetings_dir.resolve()) not in json.dumps(removed_payload, ensure_ascii=False)

        after_remove = client.get("/api/v1/knowledge/folder-mappings-v062", headers=auth)
        assert after_remove.status_code == 200
        assert after_remove.json()["items"] == []
        folder_search = client.get(
            "/api/v1/knowledge/search-v037",
            params={"q": "V062_FOLDER_SENTINEL"},
            headers=auth,
        )
        assert folder_search.status_code == 200
        assert folder_search.json()["count"] == 0
        retained_summary = client.get(
            "/api/v1/knowledge/search-v037",
            params={"q": "V062_SUMMARY_SENTINEL"},
            headers=auth,
        )
        assert retained_summary.status_code == 200
        assert retained_summary.json()["count"] == 1

        read_only_pair = client.post(
            "/api/v1/pairing/request",
            json={
                "app_key": "vp3-cloud-v062-readonly",
                "app_name": "VP3 Cloud v0.62 read only",
                "permissions": ["knowledge.search"],
            },
        ).json()
        assert client.post(
            "/api/v1/pairing/approve", json={"code": read_only_pair["code"]}
        ).status_code == 200
        read_only_auth = {"Authorization": f"Bearer {read_only_pair['claim_token']}"}
        write_denied = client.post(
            "/api/v1/knowledge/items-v062",
            json={
                "collection_key": "general",
                "title": "Denied",
                "kind": "summary",
                "content": "READ_ONLY_DENIED_V062",
            },
            headers=read_only_auth,
        )
        assert write_denied.status_code == 403

print("HomeServer native knowledge folder mapping v0.62 regression passed")
