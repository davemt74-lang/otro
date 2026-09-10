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


with tempfile.TemporaryDirectory(prefix="homeserver-knowledge-scope-v037-") as temp_root:
    root = Path(temp_root)
    data_dir = root / "data"
    travel_dir = root / "travel"
    private_dir = root / "private"
    travel_dir.mkdir(parents=True)
    private_dir.mkdir(parents=True)
    (travel_dir / "trip.md").write_text(
        "LEGACY_SCOPE_TRAVEL_3703 approved travel material", encoding="utf-8"
    )
    (private_dir / "budget.md").write_text(
        "LEGACY_SCOPE_PRIVATE_3704 synthetic private material", encoding="utf-8"
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

        for key, name in (("travel", "Travel"), ("private", "Private")):
            response = client.post(
                "/api/v1/control/knowledge/collections",
                json={"collection_key": key, "name": name},
            )
            assert response.status_code == 200, response.text

        travel = client.post(
            "/api/v1/control/knowledge/sources",
            json={"path": str(travel_dir), "label": "Travel", "scan_interval_seconds": 60},
        )
        private = client.post(
            "/api/v1/control/knowledge/sources",
            json={"path": str(private_dir), "label": "Private", "scan_interval_seconds": 60},
        )
        assert travel.status_code == 200, travel.text
        assert private.status_code == 200, private.text
        assert client.put(
            f"/api/v1/control/knowledge/sources/{travel.json()['source']['id']}/collection",
            json={"collection_key": "travel"},
        ).status_code == 200
        assert client.put(
            f"/api/v1/control/knowledge/sources/{private.json()['source']['id']}/collection",
            json={"collection_key": "private"},
        ).status_code == 200

        note = client.post(
            "/api/v1/control/knowledge",
            json={
                "title": "Travel reference note",
                "kind": "note",
                "content": "KIND_SCOPE_NOTE_3705 travel note that should be kind-filterable",
            },
        )
        assert note.status_code == 200, note.text
        note_id = int(note.json()["id"])
        assert client.put(
            f"/api/v1/control/knowledge/{note_id}/collection",
            json={"collection_key": "travel"},
        ).status_code == 200

        pair = client.post(
            "/api/v1/pairing/request",
            json={
                "app_key": "scope-compat-v037",
                "app_name": "Scope Compatibility Reader",
                "permissions": ["knowledge.search"],
            },
        ).json()
        assert client.post(
            "/api/v1/pairing/approve", json={"code": pair["code"]}
        ).status_code == 200
        auth = {"Authorization": f"Bearer {pair['claim_token']}"}
        app_id = int(
            next(
                item for item in client.get("/api/v1/control/apps").json()["apps"]
                if item["app_key"] == "scope-compat-v037"
            )["id"]
        )

        # Restrict the wrapper to Travel. Both v0.37 and the historical paired
        # endpoint must enforce the same collection boundary.
        scope = client.put(
            f"/api/v1/control/apps/{app_id}/knowledge-collections",
            json={"collection_keys": ["travel"]},
        )
        assert scope.status_code == 200, scope.text

        modern_private = client.get(
            "/api/v1/knowledge/search-v037",
            params={"q": "LEGACY_SCOPE_PRIVATE_3704"},
            headers=auth,
        )
        legacy_private = client.get(
            "/api/v1/knowledge",
            params={"q": "LEGACY_SCOPE_PRIVATE_3704"},
            headers=auth,
        )
        assert modern_private.status_code == 200
        assert legacy_private.status_code == 200
        assert modern_private.json()["count"] == 0
        assert legacy_private.json()["count"] == 0

        legacy_travel = client.get(
            "/api/v1/knowledge",
            params={"q": "LEGACY_SCOPE_TRAVEL_3703"},
            headers=auth,
        )
        assert legacy_travel.status_code == 200
        assert legacy_travel.json()["count"] == 1
        legacy_text = json.dumps(legacy_travel.json(), ensure_ascii=False)
        assert str(root.resolve()) not in legacy_text
        assert "source_path" not in legacy_text
        assert "content" not in legacy_travel.json()["items"][0]
        assert legacy_travel.json()["items"][0]["citation"]["collection_key"] == "travel"

        # Existing knowledge_kinds scope remains an additional narrowing layer.
        kind_scope = client.put(
            f"/api/v1/control/apps/{app_id}/scope",
            json={
                "cloud_allowed": True,
                "memory_key_prefixes": [],
                "knowledge_kinds": ["watched_document"],
                "tool_names": [],
                "plugin_keys": [],
            },
        )
        assert kind_scope.status_code == 200, kind_scope.text

        note_modern = client.get(
            "/api/v1/knowledge/search-v037",
            params={"q": "KIND_SCOPE_NOTE_3705"},
            headers=auth,
        )
        note_legacy = client.get(
            "/api/v1/knowledge",
            params={"q": "KIND_SCOPE_NOTE_3705"},
            headers=auth,
        )
        watched_modern = client.get(
            "/api/v1/knowledge/search-v037",
            params={"q": "LEGACY_SCOPE_TRAVEL_3703"},
            headers=auth,
        )
        assert note_modern.status_code == 200 and note_modern.json()["count"] == 0
        assert note_legacy.status_code == 200 and note_legacy.json()["count"] == 0
        assert watched_modern.status_code == 200 and watched_modern.json()["count"] == 1

print("HomeServer knowledge scope compatibility v0.37 regression passed")
