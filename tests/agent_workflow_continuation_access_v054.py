from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient


with tempfile.TemporaryDirectory(prefix="homeserver-agent-workflow-continuation-access-v054-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services.tasks import scheduler  # noqa: E402

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post(
            "/__owner/session",
            headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN},
        ).status_code == 200

        primary = next(
            item for item in client.get("/api/v1/control/agents").json()["items"] if item["is_primary"]
        )
        primary_id = int(primary["id"])
        secondary_response = client.post(
            "/api/v1/control/agents",
            json={
                "name": "v0.54 Scoped Specialist",
                "instructions": "Access-boundary regression specialist.",
                "model": "local-access-test-model",
            },
        )
        assert secondary_response.status_code == 200, secondary_response.text
        secondary_id = int(secondary_response.json()["agent"]["id"])

        archived_id = "v054-archived-owner"
        with db() as connection:
            connection.execute(
                """
                INSERT INTO conversations(id, agent_id, source_app_key, title, status)
                VALUES (?, ?, 'owner', 'Archived v0.54 conversation', 'archived')
                """,
                (archived_id, primary_id),
            )
        archived = client.get(
            f"/api/v1/control/agent-workflows/continuation?conversation_id={archived_id}"
        )
        assert archived.status_code == 404, archived.text

        app_key = "v054-access-wrapper"
        token = "v054-access-wrapper-token-with-sufficient-length"
        conversation_id = "v054-secondary-wrapper-conversation"
        with db() as connection:
            cursor = connection.execute(
                "INSERT INTO paired_apps(app_key, name, status, token_hash) VALUES (?, 'v0.54 Access Wrapper', 'active', ?)",
                (app_key, hashlib.sha256(token.encode("utf-8")).hexdigest()),
            )
            app_id = int(cursor.lastrowid)
            connection.execute(
                "INSERT INTO app_permissions(paired_app_id, permission, allowed) VALUES (?, 'agent.chat', 1)",
                (app_id,),
            )
            connection.execute(
                """
                INSERT INTO conversations(id, agent_id, source_app_key, title, status)
                VALUES (?, ?, ?, 'v0.54 Secondary Wrapper', 'active')
                """,
                (conversation_id, secondary_id, f"app:{app_key}"),
            )
        headers = {"Authorization": f"Bearer {token}"}
        endpoint = f"/api/v1/agent-workflows/continuation?conversation_id={conversation_id}"

        denied = client.get(endpoint, headers=headers)
        assert denied.status_code == 403, denied.text
        assert "Agent is not authorized" in denied.json()["detail"]

        with db() as connection:
            connection.execute(
                "INSERT INTO app_agent_grants(paired_app_id, agent_id, allowed) VALUES (?, ?, 1)",
                (app_id, secondary_id),
            )
        allowed = client.get(endpoint, headers=headers)
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["parent"]["id"] == secondary_id
        assert allowed.json()["current"] is None
        assert allowed.json()["read_only"] is True

        with db() as connection:
            connection.execute(
                "UPDATE app_agent_grants SET allowed=0, updated_at=CURRENT_TIMESTAMP WHERE paired_app_id=? AND agent_id=?",
                (app_id, secondary_id),
            )
        revoked = client.get(endpoint, headers=headers)
        assert revoked.status_code == 403, revoked.text
        assert "Agent is not authorized" in revoked.json()["detail"]

print("HomeServer v0.54 Workflow Continuation live access-boundary regression passed")
