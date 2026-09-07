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


with tempfile.TemporaryDirectory(prefix="homeserver-tools-security-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402

    with TestClient(app) as client:
        assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200

        pair = client.post(
            "/api/v1/pairing/request",
            json={
                "app_key": "tool-security",
                "app_name": "Tool Security Test",
                "permissions": ["knowledge.search", "memory.write", "tools.execute"],
            },
        )
        assert pair.status_code == 200
        pairing = pair.json()
        assert client.post("/api/v1/pairing/approve", json={"code": pairing["code"]}).status_code == 200
        token = pairing["claim_token"]
        headers = {"Authorization": f"Bearer {token}"}

        sensitive_query = "SENSITIVE_QUERY_SENTINEL_104729"
        search = client.post(
            "/api/v1/tools/knowledge.search/execute",
            json={"arguments": {"query": sensitive_query, "limit": 3}},
            headers=headers,
        )
        assert search.status_code == 200

        private_limit = "PRIVATE_LIMIT_SENTINEL_845211"
        bad_limit = client.post(
            "/api/v1/tools/knowledge.search/execute",
            json={"arguments": {"query": "safe", "limit": private_limit}},
            headers=headers,
        )
        assert bad_limit.status_code == 422

        private_key = "PRIVATE_MEMORY_KEY_SENTINEL_239418"
        private_body = "PRIVATE_MEMORY_BODY_SENTINEL_572940"
        write = client.post(
            "/api/v1/tools/memory.write/execute",
            json={"arguments": {"memory_key": private_key, "content": private_body, "importance": 0.6}},
            headers=headers,
        )
        assert write.status_code == 200

        private_importance = "PRIVATE_IMPORTANCE_SENTINEL_112358"
        bad_importance = client.post(
            "/api/v1/tools/memory.write/execute",
            json={"arguments": {"content": "safe content", "importance": private_importance}},
            headers=headers,
        )
        assert bad_importance.status_code == 422

        runs = client.get("/api/v1/control/tool-runs?limit=100")
        assert runs.status_code == 200
        audit_text = json.dumps(runs.json()["items"], ensure_ascii=False)
        for secret in (sensitive_query, private_limit, private_key, private_body, private_importance):
            assert secret not in audit_text
        assert "query_length" in audit_text
        assert "content_length" in audit_text
        assert "memory_key_length" in audit_text

print("HomeServer tool audit privacy test passed")
