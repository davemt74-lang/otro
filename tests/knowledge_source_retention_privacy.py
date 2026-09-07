from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-source-retention-") as temp_root:
    root = Path(temp_root)
    data_dir = root / "data"
    source_dir = root / "private-project"
    source_dir.mkdir()
    secret_path = source_dir / "notes.md"
    secret_path.write_text("RETENTION_PRIVACY_SENTINEL_55127 local watched knowledge", encoding="utf-8")
    os.environ["HOMESERVER_DATA_DIR"] = str(data_dir)

    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services.knowledge_sources import scheduler  # noqa: E402
    from app.services.tasks import scheduler as task_scheduler  # noqa: E402

    with TestClient(app) as client:
        scheduler.stop()
        task_scheduler.stop()
        assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200

        created = client.post(
            "/api/v1/control/knowledge/sources",
            json={"path": str(source_dir), "label": "Private project", "scan_interval_seconds": 60},
        )
        assert created.status_code == 200, created.text
        source_id = created.json()["source"]["id"]

        pair = client.post(
            "/api/v1/pairing/request",
            json={"app_key": "retention-reader", "app_name": "Retention Reader", "permissions": ["knowledge.search"]},
        ).json()
        assert client.post("/api/v1/pairing/approve", json={"code": pair["code"]}).status_code == 200
        headers = {"Authorization": f"Bearer {pair['claim_token']}"}

        before = client.get("/api/v1/knowledge?q=RETENTION_PRIVACY_SENTINEL_55127", headers=headers)
        assert before.status_code == 200
        assert len(before.json()["items"]) == 1
        assert before.json()["items"][0]["source_path"] is None

        retained = client.delete(f"/api/v1/control/knowledge/sources/{source_id}?keep_indexed=true")
        assert retained.status_code == 200
        assert retained.json()["kept_indexed"] is True
        assert secret_path.exists()

        owner_rows = client.get("/api/v1/control/knowledge?q=RETENTION_PRIVACY_SENTINEL_55127").json()["items"]
        assert len(owner_rows) == 1
        assert owner_rows[0]["kind"] == "document"
        assert owner_rows[0]["source_path"] is None

        after = client.get("/api/v1/knowledge?q=RETENTION_PRIVACY_SENTINEL_55127", headers=headers)
        assert after.status_code == 200
        assert len(after.json()["items"]) == 1
        assert after.json()["items"][0]["source_path"] is None
        assert str(source_dir) not in after.text
        assert str(root) not in after.text

print("HomeServer retained Knowledge source path privacy test passed")
