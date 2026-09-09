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


with tempfile.TemporaryDirectory(prefix="homeserver-approval-federation-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import approvals  # noqa: E402

    def pair(client: TestClient, app_key: str, permissions: list[str]) -> str:
        request = client.post(
            "/api/v1/pairing/request",
            json={"app_key": app_key, "app_name": app_key, "permissions": permissions},
        )
        assert request.status_code == 200, request.text
        payload = request.json()
        approved = client.post("/api/v1/pairing/approve", json={"code": payload["code"]})
        assert approved.status_code == 200, approved.text
        return payload["claim_token"]

    with TestClient(app) as client:
        assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200

        capabilities = client.get("/api/v1/capabilities").json()
        assert "approvals.review" in capabilities["permissions"]
        assert "approvals.federation.v1" in capabilities["features"]

        perms = ["approvals.review", "memory.write", "tools.execute"]
        vp3_token = pair(client, "vp3", perms)
        other_token = pair(client, "other-wrapper", perms)
        readonly_token = pair(client, "readonly-wrapper", ["memory.write", "tools.execute"])

        vp3_private = "VP3_FEDERATION_PRIVATE_42091"
        other_private = "OTHER_WRAPPER_PRIVATE_93017"
        vp3_request = approvals.create_memory_write_request(
            "app:vp3",
            {"memory_key": "vp3-federated", "content": vp3_private, "importance": 0.7},
        )["result"]["request_id"]
        other_request = approvals.create_memory_write_request(
            "app:other-wrapper",
            {"memory_key": "other-federated", "content": other_private, "importance": 0.6},
        )["result"]["request_id"]

        vp3_headers = {"Authorization": f"Bearer {vp3_token}"}
        other_headers = {"Authorization": f"Bearer {other_token}"}
        readonly_headers = {"Authorization": f"Bearer {readonly_token}"}

        listed = client.get("/api/v1/action-requests?status=pending", headers=vp3_headers)
        assert listed.status_code == 200, listed.text
        items = listed.json()["items"]
        assert [item["id"] for item in items] == [vp3_request]
        assert "arguments" not in items[0]
        assert "arguments_meta" not in items[0]
        assert vp3_private not in json.dumps(items)
        assert other_private not in json.dumps(items)

        assert client.get(f"/api/v1/action-requests/{other_request}", headers=vp3_headers).status_code == 404
        assert client.post(f"/api/v1/action-requests/{other_request}/approve", headers=vp3_headers).status_code == 404
        assert client.post(f"/api/v1/action-requests/{other_request}/deny", headers=vp3_headers).status_code == 404
        assert client.get("/api/v1/action-requests", headers=readonly_headers).status_code == 403

        denied = client.post(f"/api/v1/action-requests/{vp3_request}/deny", headers=vp3_headers)
        assert denied.status_code == 200, denied.text
        assert denied.json()["request"]["status"] == "denied"
        assert "arguments" not in denied.json()["request"]
        assert client.post(f"/api/v1/action-requests/{vp3_request}/deny", headers=vp3_headers).status_code == 409

        executable_private = "VP3_EXECUTED_PRIVATE_58123"
        executable_request = approvals.create_memory_write_request(
            "app:vp3",
            {"memory_key": "vp3-executed", "content": executable_private, "importance": 0.8},
        )["result"]["request_id"]
        approved = client.post(f"/api/v1/action-requests/{executable_request}/approve", headers=vp3_headers)
        assert approved.status_code == 200, approved.text
        assert approved.json()["request"]["status"] == "executed"

        owner_memories = client.get("/api/v1/control/memory").json()["items"]
        assert any(item["content"] == executable_private for item in owner_memories)
        assert not any(item["content"] == vp3_private for item in owner_memories)

        other_list = client.get("/api/v1/action-requests?status=pending", headers=other_headers).json()["items"]
        assert [item["id"] for item in other_list] == [other_request]

        owner_pending = client.get("/api/v1/control/action-requests?status=pending").json()["items"]
        assert any(item["id"] == other_request and item["arguments"]["content"] == other_private for item in owner_pending)

        with db() as connection:
            reviews = connection.execute(
                "SELECT actor_type, actor_key, resource_key, metadata_json FROM activity_log WHERE action='action.federated_review' ORDER BY id"
            ).fetchall()
        assert len(reviews) == 2
        assert all(row["actor_type"] == "app" and row["actor_key"] == "app:vp3" for row in reviews)
        audit_json = json.dumps([dict(row) for row in reviews], ensure_ascii=False)
        assert vp3_private not in audit_json
        assert executable_private not in audit_json
        assert {json.loads(row["metadata_json"])["decision"] for row in reviews} == {"approve", "deny"}

print("HomeServer v0.31 approval federation test passed")
