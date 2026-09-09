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


with tempfile.TemporaryDirectory(prefix="homeserver-action-policy-v035-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402

    with TestClient(app) as client:
        owner = client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN})
        assert owner.status_code == 200

        def pair(app_key: str) -> tuple[int, dict[str, str]]:
            response = client.post(
                "/api/v1/pairing/request",
                json={
                    "app_key": app_key,
                    "app_name": f"{app_key} test",
                    "permissions": [
                        "approvals.review",
                        "knowledge.search",
                        "memory.write",
                        "tasks.write",
                        "tools.execute",
                    ],
                },
            )
            assert response.status_code == 200
            request = response.json()
            assert client.post("/api/v1/pairing/approve", json={"code": request["code"]}).status_code == 200
            headers = {"Authorization": f"Bearer {request['claim_token']}"}
            identity = client.get("/api/v1/me", headers=headers).json()
            return int(identity["id"]), headers

        app_id, headers = pair("policy-alpha")
        other_app_id, other_headers = pair("policy-beta")

        tools_response = client.get("/api/v1/tools", headers=headers)
        assert tools_response.status_code == 200
        by_key = {item["key"]: item for item in tools_response.json()["items"]}
        assert by_key["knowledge.search"]["execution_policy"]["policy_mode"] == "read_only"
        assert by_key["memory.write"]["execution_policy"]["policy_mode"] == "approval_required"
        assert by_key["memory.write"]["execution_policy"]["inherited"] is True

        secret_one = "POLICY_SECRET_PENDING_74192"
        proposed = client.post(
            "/api/v1/tools/memory.write/execute",
            json={"arguments": {"content": secret_one, "memory_key": "policy.pending", "importance": 0.7}},
            headers=headers,
        )
        assert proposed.status_code == 200
        proposed_json = proposed.json()
        assert proposed_json["approval_required"] is True
        assert proposed_json["execution_policy"]["policy_mode"] == "approval_required"
        request_id = proposed_json["result"]["request_id"]
        memory_before = client.get("/api/v1/control/memory").json()["items"]
        assert all(item.get("content") != secret_one for item in memory_before)

        approved = client.post(f"/api/v1/action-requests/{request_id}/approve", headers=headers)
        assert approved.status_code == 200
        assert approved.json()["request"]["status"] == "executed"
        memory_after = client.get("/api/v1/control/memory").json()["items"]
        assert any(item.get("content") == secret_one for item in memory_after)

        automatic_policy = client.put(
            f"/api/v1/control/action-policies/{app_id}/memory.write",
            json={"policy_mode": "safe_automatic"},
        )
        assert automatic_policy.status_code == 200
        assert automatic_policy.json()["policy"]["policy_mode"] == "safe_automatic"

        secret_two = "POLICY_SECRET_AUTO_85203"
        automatic = client.post(
            "/api/v1/tools/memory.write/execute",
            json={"arguments": {"content": secret_two, "memory_key": "policy.auto"}},
            headers=headers,
        )
        assert automatic.status_code == 200
        assert automatic.json()["execution_policy"]["policy_mode"] == "safe_automatic"
        assert automatic.json().get("approval_required") is not True
        memory_auto = client.get("/api/v1/control/memory").json()["items"]
        assert any(item.get("content") == secret_two for item in memory_auto)

        sensitive_policy = client.put(
            f"/api/v1/control/action-policies/{app_id}/memory.write",
            json={"policy_mode": "sensitive_high_impact"},
        )
        assert sensitive_policy.status_code == 200
        blocked = client.post(
            "/api/v1/tools/memory.write/execute",
            json={"arguments": {"content": "BLOCKED_SECRET_96314"}},
            headers=headers,
        )
        assert blocked.status_code == 403
        assert "sensitive/high-impact" in blocked.json()["detail"].lower()

        invalid_read_upgrade = client.put(
            f"/api/v1/control/action-policies/{app_id}/knowledge.search",
            json={"policy_mode": "safe_automatic"},
        )
        assert invalid_read_upgrade.status_code == 422

        other_policy = client.get("/api/v1/action-policy", headers=other_headers)
        assert other_policy.status_code == 200
        other_by_key = {item["tool_key"]: item for item in other_policy.json()["policies"]}
        assert other_by_key["memory.write"]["policy_mode"] == "approval_required"
        assert other_by_key["memory.write"]["inherited"] is True
        assert int(other_policy.json()["app"]["id"]) == other_app_id

        owner_policies = client.get("/api/v1/control/action-policies?audit_limit=100")
        assert owner_policies.status_code == 200
        policy_payload = owner_policies.json()
        assert policy_payload["version"] == "v0.35"
        assert len(policy_payload["apps"]) == 2
        decisions = {item["decision"] for item in policy_payload["audit"]}
        assert {"approval_requested", "allowed_automatic", "blocked"}.issubset(decisions)

        audit_text = json.dumps(policy_payload["audit"], ensure_ascii=False)
        for secret in (secret_one, secret_two, "BLOCKED_SECRET_96314"):
            assert secret not in audit_text

print("HomeServer action execution policy v0.35 test passed")
