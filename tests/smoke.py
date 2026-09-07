from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-smoke-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.config import settings  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402

    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["version"] == "0.3.0"

        status = client.get("/api/v1/status")
        assert status.status_code == 200
        assert status.json()["schema_version"] == 2

        root = client.get("/")
        assert root.status_code == 200
        assert "HomeServer" in root.text

        unauthorized = client.get("/api/v1/control/overview")
        assert unauthorized.status_code == 401

        pair = client.post(
            "/api/v1/pairing/request",
            json={
                "app_key": "vp3-test",
                "app_name": "VP3 Test",
                "permissions": ["agent.chat", "knowledge.search", "memory.read", "memory.write", "not.real"],
            },
        )
        assert pair.status_code == 200
        code = pair.json()["code"]
        assert "not.real" not in pair.json()["permissions"]

        self_approval = client.post("/api/v1/pairing/approve", json={"code": code})
        assert self_approval.status_code == 401

        bad_bootstrap = client.post("/__owner/session", headers={"X-HomeServer-Owner": "wrong-token"})
        assert bad_bootstrap.status_code == 401

        bootstrap = client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN})
        assert bootstrap.status_code == 200
        assert client.cookies.get("homeserver_owner")

        overview = client.get("/api/v1/control/overview")
        assert overview.status_code == 200
        assert overview.json()["agent"]["name"] == "HomeServer Agent"

        update = client.put(
            "/api/v1/control/agent",
            json={"name": "Private Agent", "model": "local", "instructions": "Keep private data local."},
        )
        assert update.status_code == 200

        knowledge = client.post(
            "/api/v1/control/knowledge",
            json={"title": "VP3", "kind": "note", "content": "VP3 is an authorized HomeServer client."},
        )
        assert knowledge.status_code == 200
        assert knowledge.json()["chunk_count"] == 1

        document_bytes = (
            b"# Merchant plan\n\nNorth Mountain merchant partnerships use private HomeServer knowledge. "
            b"The document should be searchable through SQLite full-text search."
        )
        imported = client.post(
            "/api/v1/control/knowledge/import",
            files={"file": ("merchant-plan.md", document_bytes, "text/markdown")},
        )
        assert imported.status_code == 200
        imported_json = imported.json()
        assert imported_json["created"] is True
        assert imported_json["duplicate"] is False
        document_id = imported_json["id"]
        assert imported_json["chunk_count"] >= 1

        duplicate = client.post(
            "/api/v1/control/knowledge/import",
            files={"file": ("merchant-plan-copy.md", document_bytes, "text/markdown")},
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["duplicate"] is True
        assert duplicate.json()["id"] == document_id

        stored_files = list(settings.knowledge_files_dir.glob("*"))
        assert len(stored_files) == 1

        owner_search = client.get("/api/v1/control/knowledge?q=merchant+partnerships")
        assert owner_search.status_code == 200
        assert owner_search.json()["items"][0]["id"] == document_id
        assert "merchant" in owner_search.json()["items"][0]["snippet"].lower()

        reindex = client.post("/api/v1/control/knowledge/reindex")
        assert reindex.status_code == 200
        assert reindex.json()["items"] == 2
        assert reindex.json()["chunks"] >= 2

        memory = client.post(
            "/api/v1/control/memory",
            json={"memory_key": "architecture", "content": "HomeServer is application-neutral.", "importance": 0.9},
        )
        assert memory.status_code == 200

        approval = client.post("/api/v1/pairing/approve", json={"code": code})
        assert approval.status_code == 200
        token = approval.json()["token"]

        me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["app_key"] == "vp3-test"

        client_knowledge = client.get(
            "/api/v1/knowledge?q=merchant",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert client_knowledge.status_code == 200
        assert client_knowledge.json()["items"][0]["id"] == document_id

        client_memory = client.get("/api/v1/memory", headers={"Authorization": f"Bearer {token}"})
        assert client_memory.status_code == 200
        assert len(client_memory.json()["items"]) == 1

        apps = client.get("/api/v1/control/apps")
        assert apps.status_code == 200
        assert apps.json()["apps"][0]["app_key"] == "vp3-test"

        deleted = client.delete(f"/api/v1/control/knowledge/{document_id}")
        assert deleted.status_code == 200
        assert not list(settings.knowledge_files_dir.glob("*"))

        deleted_search = client.get("/api/v1/control/knowledge?q=merchant")
        assert deleted_search.status_code == 200
        assert deleted_search.json()["items"] == []

print("HomeServer smoke test passed")
