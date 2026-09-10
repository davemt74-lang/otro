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


with tempfile.TemporaryDirectory(prefix="homeserver-knowledge-v037-") as temp_root:
    root = Path(temp_root)
    data_dir = root / "data"
    travel_dir = root / "travel"
    private_dir = root / "private-finance"
    travel_dir.mkdir(parents=True)
    private_dir.mkdir(parents=True)

    travel_file = travel_dir / "phoenix-trip.md"
    private_file = private_dir / "budget.txt"
    travel_file.write_text(
        "TRAVEL_COLLECTION_SENTINEL_3701 Phoenix itinerary with hiking and lodging notes.",
        encoding="utf-8",
    )
    private_file.write_text(
        "PRIVATE_COLLECTION_SENTINEL_3702 confidential synthetic budget notes.",
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

        status = client.get("/api/v1/status")
        assert status.status_code == 200
        assert status.json()["schema_version"] == 19

        collections = client.get("/api/v1/control/knowledge/collections")
        assert collections.status_code == 200, collections.text
        assert collections.json()["default_collection"] == "general"
        assert any(item["collection_key"] == "general" for item in collections.json()["items"])

        for key, name in (("travel", "Travel"), ("private", "Private")):
            created = client.post(
                "/api/v1/control/knowledge/collections",
                json={"collection_key": key, "name": name, "description": f"Synthetic {name} collection"},
            )
            assert created.status_code == 200, created.text
            assert created.json()["collection"]["collection_key"] == key

        travel_source = client.post(
            "/api/v1/control/knowledge/sources",
            json={"path": str(travel_dir), "label": "Travel files", "scan_interval_seconds": 60},
        )
        assert travel_source.status_code == 200, travel_source.text
        travel_source_id = int(travel_source.json()["source"]["id"])
        assert travel_source.json()["scan"]["indexed"] == 1

        private_source = client.post(
            "/api/v1/control/knowledge/sources",
            json={"path": str(private_dir), "label": "Private files", "scan_interval_seconds": 60},
        )
        assert private_source.status_code == 200, private_source.text
        private_source_id = int(private_source.json()["source"]["id"])
        assert private_source.json()["scan"]["indexed"] == 1

        assign_travel = client.put(
            f"/api/v1/control/knowledge/sources/{travel_source_id}/collection",
            json={"collection_key": "travel"},
        )
        assert assign_travel.status_code == 200, assign_travel.text
        assign_private = client.put(
            f"/api/v1/control/knowledge/sources/{private_source_id}/collection",
            json={"collection_key": "private"},
        )
        assert assign_private.status_code == 200, assign_private.text

        pair = client.post(
            "/api/v1/pairing/request",
            json={
                "app_key": "section-eight-reader",
                "app_name": "Section Eight Reader",
                "permissions": ["knowledge.search"],
            },
        ).json()
        approved = client.post("/api/v1/pairing/approve", json={"code": pair["code"]})
        assert approved.status_code == 200, approved.text
        token = pair["claim_token"]
        auth = {"Authorization": f"Bearer {token}"}

        unrestricted_travel = client.get(
            "/api/v1/knowledge/search-v037",
            params={"q": "TRAVEL_COLLECTION_SENTINEL_3701", "limit": 10},
            headers=auth,
        )
        assert unrestricted_travel.status_code == 200, unrestricted_travel.text
        travel_payload = unrestricted_travel.json()
        assert travel_payload["count"] == 1
        assert travel_payload["scope"]["restricted"] is False
        assert travel_payload["privacy"] == {
            "local_only_index": True,
            "absolute_paths_exposed": False,
            "full_documents_returned": False,
        }
        travel_item = travel_payload["items"][0]
        travel_citation = travel_item["citation"]
        assert travel_item["kind"] == "watched_document"
        assert travel_citation["collection_key"] == "travel"
        assert travel_citation["source_label"] == "Travel files"
        assert travel_citation["relative_path"] == "phoenix-trip.md"
        assert travel_citation["uri"].startswith("homeserver://knowledge/")
        assert travel_citation["id"].startswith("hs-knowledge:")
        assert "content" not in travel_item
        assert "source_path" not in travel_item
        serialized = json.dumps(travel_payload, ensure_ascii=False)
        assert str(root.resolve()) not in serialized
        assert str(travel_dir.resolve()) not in serialized
        original_citation_id = travel_citation["id"]
        original_item_id = travel_item["id"]

        unrestricted_private = client.get(
            "/api/v1/knowledge/search-v037",
            params={"q": "PRIVATE_COLLECTION_SENTINEL_3702"},
            headers=auth,
        )
        assert unrestricted_private.status_code == 200
        assert unrestricted_private.json()["count"] == 1
        assert unrestricted_private.json()["items"][0]["citation"]["collection_key"] == "private"

        apps = client.get("/api/v1/control/apps").json()["apps"]
        app_id = int(next(item for item in apps if item["app_key"] == "section-eight-reader")["id"])
        restricted = client.put(
            f"/api/v1/control/apps/{app_id}/knowledge-collections",
            json={"collection_keys": ["travel"]},
        )
        assert restricted.status_code == 200, restricted.text
        assert restricted.json()["scope"]["restricted"] is True
        assert restricted.json()["scope"]["collections"] == ["travel"]

        travel_visible = client.get(
            "/api/v1/knowledge/search-v037",
            params={"q": "TRAVEL_COLLECTION_SENTINEL_3701"},
            headers=auth,
        )
        assert travel_visible.status_code == 200
        assert travel_visible.json()["count"] == 1
        assert travel_visible.json()["scope"] == {"restricted": True, "collections": ["travel"]}

        private_hidden = client.get(
            "/api/v1/knowledge/search-v037",
            params={"q": "PRIVATE_COLLECTION_SENTINEL_3702"},
            headers=auth,
        )
        assert private_hidden.status_code == 200
        assert private_hidden.json()["count"] == 0

        unchanged = client.post(f"/api/v1/control/knowledge/sources/{travel_source_id}/scan")
        assert unchanged.status_code == 200
        assert unchanged.json()["scan"]["skipped"] == 1
        after_unchanged = client.get(
            "/api/v1/knowledge/search-v037",
            params={"q": "TRAVEL_COLLECTION_SENTINEL_3701"},
            headers=auth,
        ).json()["items"][0]
        assert after_unchanged["id"] == original_item_id
        assert after_unchanged["citation"]["id"] == original_citation_id

        travel_file.write_text(
            "TRAVEL_COLLECTION_SENTINEL_3701 Phoenix itinerary updated with a museum and day trip.",
            encoding="utf-8",
        )
        stat = travel_file.stat()
        os.utime(travel_file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
        changed = client.post(f"/api/v1/control/knowledge/sources/{travel_source_id}/scan")
        assert changed.status_code == 200
        assert changed.json()["scan"]["updated"] == 1
        after_change = client.get(
            "/api/v1/knowledge/search-v037",
            params={"q": "TRAVEL_COLLECTION_SENTINEL_3701"},
            headers=auth,
        ).json()["items"][0]
        assert after_change["id"] == original_item_id
        assert after_change["citation"]["id"] != original_citation_id
        assert after_change["citation"]["version"] != travel_citation["version"]

        restored = client.put(
            f"/api/v1/control/apps/{app_id}/knowledge-collections",
            json={"collection_keys": []},
        )
        assert restored.status_code == 200
        assert restored.json()["scope"]["restricted"] is False
        assert restored.json()["scope"]["collections"] == []
        private_restored = client.get(
            "/api/v1/knowledge/search-v037",
            params={"q": "PRIVATE_COLLECTION_SENTINEL_3702"},
            headers=auth,
        )
        assert private_restored.status_code == 200
        assert private_restored.json()["count"] == 1

        no_permission_pair = client.post(
            "/api/v1/pairing/request",
            json={"app_key": "no-knowledge", "app_name": "No Knowledge", "permissions": []},
        ).json()
        assert client.post(
            "/api/v1/pairing/approve", json={"code": no_permission_pair["code"]}
        ).status_code == 200
        denied = client.get(
            "/api/v1/knowledge/search-v037",
            params={"q": "TRAVEL_COLLECTION_SENTINEL_3701"},
            headers={"Authorization": f"Bearer {no_permission_pair['claim_token']}"},
        )
        assert denied.status_code == 403

        capability = client.get("/api/v1/capabilities").json()
        assert capability["knowledge"]["version"] == "v0.37"
        assert capability["knowledge"]["citation_safe_search"] is True
        assert "knowledge.collections.v1" in capability["features"]
        assert "knowledge.citations.v1" in capability["features"]
        assert "knowledge.search.scoped.v037" in capability["features"]

print("HomeServer knowledge collections v0.37 regression passed")
