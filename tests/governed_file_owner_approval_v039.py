from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-file-owner-approval-v039-") as temp_root:
    root = Path(temp_root)
    os.environ["HOMESERVER_DATA_DIR"] = str(root / "data")
    watched = root / "watched"
    watched.mkdir(parents=True)
    target = watched / "owner-only.txt"
    target.write_text("OWNER_APPROVAL_REQUIRED_3906", encoding="utf-8")

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services.knowledge_sources import scheduler  # noqa: E402
    from app.services.tasks import scheduler as task_scheduler  # noqa: E402

    with TestClient(app) as client:
        scheduler.stop()
        task_scheduler.stop()
        assert client.post(
            "/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}
        ).status_code == 200

        assert client.post(
            "/api/v1/control/knowledge/collections",
            json={"collection_key": "owner-test", "name": "Owner Test"},
        ).status_code == 200
        source = client.post(
            "/api/v1/control/knowledge/sources",
            json={"path": str(watched), "label": "Owner approval files", "scan_interval_seconds": 60},
        )
        assert source.status_code == 200, source.text
        source_id = int(source.json()["source"]["id"])
        assert client.put(
            f"/api/v1/control/knowledge/sources/{source_id}/collection",
            json={"collection_key": "owner-test"},
        ).status_code == 200

        pair = client.post(
            "/api/v1/pairing/request",
            json={
                "app_key": "self-review-file-v039",
                "app_name": "Self Review File App",
                "permissions": [
                    "approvals.review",
                    "files.read",
                    "files.write",
                    "tools.execute",
                ],
            },
        ).json()
        assert client.post(
            "/api/v1/pairing/approve", json={"code": pair["code"]}
        ).status_code == 200
        headers = {"Authorization": f"Bearer {pair['claim_token']}"}

        app_row = next(
            item for item in client.get("/api/v1/control/apps").json()["apps"]
            if item["app_key"] == "self-review-file-v039"
        )
        assert client.put(
            f"/api/v1/control/apps/{int(app_row['id'])}/knowledge-collections",
            json={"collection_keys": ["owner-test"]},
        ).status_code == 200

        listing = client.get("/api/v1/files", headers=headers)
        assert listing.status_code == 200, listing.text
        file_ref = listing.json()["items"][0]["ref"]
        proposal = client.post(
            "/api/v1/tools/files.delete/execute",
            json={"arguments": {"ref": file_ref}},
            headers=headers,
        )
        assert proposal.status_code == 200, proposal.text
        request_id = proposal.json()["result"]["request_id"]
        assert target.exists()

        # approvals.review does not grant a wrapper authority to self-approve a
        # governed file mutation. It may still deny/cancel its own request.
        self_approve = client.post(
            f"/api/v1/action-requests/{request_id}/approve", headers=headers
        )
        assert self_approve.status_code == 403, self_approve.text
        assert target.exists()
        pending = client.get(f"/api/v1/action-requests/{request_id}", headers=headers)
        assert pending.status_code == 200
        assert pending.json()["request"]["status"] == "pending"

        owner_approve = client.post(
            f"/api/v1/control/action-requests/{request_id}/approve"
        )
        assert owner_approve.status_code == 200, owner_approve.text
        assert owner_approve.json()["request"]["status"] == "executed"
        assert not target.exists()

print("HomeServer governed file local-owner approval regression passed")