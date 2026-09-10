from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-file-policy-v039-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services.tasks import scheduler as task_scheduler  # noqa: E402
    from app.services.knowledge_sources import scheduler as knowledge_scheduler  # noqa: E402

    with TestClient(app) as client:
        knowledge_scheduler.stop()
        task_scheduler.stop()
        assert client.post(
            "/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}
        ).status_code == 200

        request = client.post(
            "/api/v1/pairing/request",
            json={
                "app_key": "file-policy-v039",
                "app_name": "File Policy v0.39",
                "permissions": ["files.read", "files.write", "tools.execute"],
            },
        ).json()
        assert client.post(
            "/api/v1/pairing/approve", json={"code": request["code"]}
        ).status_code == 200
        headers = {"Authorization": f"Bearer {request['claim_token']}"}
        identity = client.get("/api/v1/me", headers=headers).json()
        app_id = int(identity["id"])

        # Governed local-file mutations are deliberately approval-only. The
        # generic safe-automatic write mode remains available to lower-impact
        # write tools, but cannot be selected for files.update/files.delete.
        for tool_key in ("files.update", "files.delete"):
            rejected = client.put(
                f"/api/v1/control/action-policies/{app_id}/{tool_key}",
                json={"policy_mode": "safe_automatic"},
            )
            assert rejected.status_code == 422, rejected.text

        memory_auto = client.put(
            f"/api/v1/control/action-policies/{app_id}/memory.write",
            json={"policy_mode": "safe_automatic"},
        )
        assert memory_auto.status_code == 200, memory_auto.text
        assert memory_auto.json()["policy"]["policy_mode"] == "safe_automatic"

        tools_response = client.get("/api/v1/tools", headers=headers)
        assert tools_response.status_code == 200, tools_response.text
        by_key = {item["key"]: item for item in tools_response.json()["items"]}
        for tool_key in ("files.update", "files.delete"):
            policy = by_key[tool_key]["execution_policy"]
            assert policy["policy_mode"] == "approval_required"
            assert "safe_automatic" not in policy["allowed_modes"]
            assert set(policy["allowed_modes"]) == {"approval_required", "sensitive_high_impact"}

        # Defense in depth: an obsolete or manually inserted safe-automatic row
        # must not re-enable automatic file mutation. Policy resolution ignores
        # that invalid per-tool override and falls back to approval-required.
        with db() as connection:
            connection.execute(
                """
                INSERT INTO app_tool_execution_policies(paired_app_id, tool_key, policy_mode)
                VALUES (?, 'files.update', 'safe_automatic')
                ON CONFLICT(paired_app_id, tool_key) DO UPDATE SET
                    policy_mode='safe_automatic', updated_at=CURRENT_TIMESTAMP
                """,
                (app_id,),
            )

        repaired = client.get("/api/v1/tools", headers=headers)
        assert repaired.status_code == 200
        repaired_by_key = {item["key"]: item for item in repaired.json()["items"]}
        repaired_policy = repaired_by_key["files.update"]["execution_policy"]
        assert repaired_policy["policy_mode"] == "approval_required"
        assert repaired_policy["inherited"] is True
        assert "safe_automatic" not in repaired_policy["allowed_modes"]

print("HomeServer governed file approval-only policy v0.39 regression passed")
