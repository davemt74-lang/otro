from __future__ import annotations

import io
import os
import sys
import tempfile
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-backup-security-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import initialize_database  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import backups  # noqa: E402
    from app.services.pairing import DEFAULT_PERMISSIONS  # noqa: E402

    initialize_database()
    assert all("backup" not in permission for permission in DEFAULT_PERMISSIONS)

    with TestClient(app) as client:
        pairing = client.post(
            "/api/v1/pairing/request",
            json={"app_key": "backup-boundary", "app_name": "Backup Boundary", "permissions": ["agent.chat"]},
        ).json()
        assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200
        assert client.post("/api/v1/pairing/approve", json={"code": pairing["code"]}).status_code == 200
        token = pairing["claim_token"]
        client.cookies.clear()
        assert client.get(
            "/api/v1/control/backups",
            headers={"Authorization": f"Bearer {token}"},
        ).status_code == 401

    backup = backups.create_backup("security-test")
    original = Path(backup["path"]).read_bytes()

    def archive_with_extra(name: str) -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(original), "r") as source, zipfile.ZipFile(output, "w") as target:
            for info in source.infolist():
                target.writestr(info.filename, source.read(info.filename))
            target.writestr(name, b"malicious")
        return output.getvalue()

    unsafe_names = [
        "../outside.txt",
        "knowledge/files/NUL.txt",
        "DATABASE/HOMESERVER.DB",
        "knowledge/files/stream:payload.txt",
        "knowledge/files/trailing. ",
    ]
    for unsafe in unsafe_names:
        try:
            backups.stage_restore(io.BytesIO(archive_with_extra(unsafe)), "unsafe.zip")
            raise AssertionError(f"Unsafe backup path was accepted: {unsafe}")
        except backups.BackupError:
            pass
        assert not backups.settings.pending_restore_dir.exists()

print("HomeServer backup security test passed")
