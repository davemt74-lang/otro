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
        assert health.json()["version"] == "0.4.0"

        capabilities = client.get("/api/v1/capabilities", headers={"Origin": "https://vp3.me"})
        assert capabilities.status_code == 200
        assert capabilities.json()["pairing_protocol"] == "claim-v1"
        assert capabilities.headers.get("access-control-allow-origin") == "https://vp3.me"

        allowed_preflight = client.options(
            "/api/v1/pairing/request",
            headers={
                "Origin": "https://vp3.me",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert allowed_preflight.status_code == 200
        assert allowed_preflight.headers.get("access-control-allow-origin") == "https://vp3.me"

        blocked_preflight = client.options(
            "/api/v1/pairing/request",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert blocked_preflight.status_code == 400
        assert blocked_preflight.headers.get("access-control-allow-origin") is None

        status = client.get("/api/v1/status")
        assert status.status_code == 200
        assert status.json()["schema_version"] == 3

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
            headers={"Origin": "https://vp3.me"},
        )
        assert pair.status_code == 200
        assert pair.headers.get("access-control-allow-origin") == "https://vp3.me"
        pair_json = pair.json()
        assert pair_json["protocol"] == "claim-v1"
        assert "not.real" not in pair_json["permissions"]
        code = pair_json["code"]
        request_id = pair_json["request_id"]
        claim_token = pair_json["claim_token"]

        pending = client.post(
            "/api/v1/pairing/status",
            json={"request_id": request_id, "claim_token": claim_token},
            headers={"Origin": "https://vp3.me"},
        )
        assert pending.status_code == 200
        assert pending.json()["status"] == "pending"
        assert pending.json()["ready"] is False

        wrong_claim = client.post(
            "/api/v1/pairing/status",
            json={"request_id": request_id, "claim_token": "x" * 48},
        )
        assert wrong_claim.status_code == 404

        preapproval_auth = client.get("/api/v1/me", headers={"Authorization": f"Bearer {claim_token}"})
        assert preapproval_auth.status_code == 401

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

        duplicate = client.post(
            "/api/v1/control/knowledge/import",
            files={"file": ("merchant-plan-copy.md", document_bytes, "text/markdown")},
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["duplicate"] is True
        assert duplicate.json()["id"] == document_id
        assert len(list(settings.knowledge_files_dir.glob("*"))) == 1

        owner_search = client.get("/api/v1/control/knowledge?q=merchant+partnerships")
        assert owner_search.status_code == 200
        assert owner_search.json()["items"][0]["id"] == document_id

        approval = client.post("/api/v1/pairing/approve", json={"code": code})
        assert approval.status_code == 200
        approval_json = approval.json()
        assert approval_json["delivery"] == "claim_token"
        assert "token" not in approval_json

        approved = client.post(
            "/api/v1/pairing/status",
            json={"request_id": request_id, "claim_token": claim_token},
            headers={"Origin": "https://vp3.me"},
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"
        assert approved.json()["ready"] is True

        me = client.get(
            "/api/v1/me",
            headers={"Authorization": f"Bearer {claim_token}", "Origin": "https://vp3.me"},
        )
        assert me.status_code == 200
        assert me.json()["app_key"] == "vp3-test"
        assert "memory.write" in me.json()["permissions"]
        assert me.headers.get("access-control-allow-origin") == "https://vp3.me"

        client_knowledge = client.get(
            "/api/v1/knowledge?q=merchant",
            headers={"Authorization": f"Bearer {claim_token}"},
        )
        assert client_knowledge.status_code == 200
        assert client_knowledge.json()["items"][0]["id"] == document_id

        memory_write = client.post(
            "/api/v1/memory",
            json={"memory_key": "architecture", "content": "HomeServer is application-neutral.", "importance": 0.9},
            headers={"Authorization": f"Bearer {claim_token}"},
        )
        assert memory_write.status_code == 200

        client_memory = client.get("/api/v1/memory", headers={"Authorization": f"Bearer {claim_token}"})
        assert client_memory.status_code == 200
        assert len(client_memory.json()["items"]) == 1

        repair = client.post(
            "/api/v1/pairing/request",
            json={"app_key": "vp3-test", "app_name": "VP3 Test", "permissions": ["knowledge.search"]},
        )
        assert repair.status_code == 200
        repair_json = repair.json()
        repair_approval = client.post("/api/v1/pairing/approve", json={"code": repair_json["code"]})
        assert repair_approval.status_code == 200
        new_token = repair_json["claim_token"]

        old_token_rejected = client.get("/api/v1/me", headers={"Authorization": f"Bearer {claim_token}"})
        assert old_token_rejected.status_code == 401

        repaired_me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {new_token}"})
        assert repaired_me.status_code == 200
        assert repaired_me.json()["permissions"] == ["knowledge.search"]

        write_revoked = client.post(
            "/api/v1/memory",
            json={"content": "This should be denied."},
            headers={"Authorization": f"Bearer {new_token}"},
        )
        assert write_revoked.status_code == 403

        apps = client.get("/api/v1/control/apps")
        assert apps.status_code == 200
        assert apps.json()["apps"][0]["app_key"] == "vp3-test"

        deleted = client.delete(f"/api/v1/control/knowledge/{document_id}")
        assert deleted.status_code == 200
        assert not list(settings.knowledge_files_dir.glob("*"))

print("HomeServer smoke test passed")
