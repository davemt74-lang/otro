from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-recovery-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.config import settings  # noqa: E402
    from app.database import db, initialize_database  # noqa: E402
    from app.recovery import build_recovery_app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import backups  # noqa: E402
    from app.services.restore_runtime import apply_pending_restore_for_startup  # noqa: E402
    from app.services.runtime_control import register_runtime_handler  # noqa: E402

    initialize_database()
    with db() as connection:
        connection.execute(
            "INSERT INTO agent_memory(memory_key, content, importance) VALUES ('recovery-test', 'known-good-state', 0.8)"
        )
    item = backups.create_backup("recovery-test")
    archive = Path(item["path"])
    archive_bytes = archive.read_bytes()

    for suffix in ("-wal", "-shm"):
        Path(str(settings.db_path) + suffix).unlink(missing_ok=True)
    corrupt_bytes = b"not-a-sqlite-database"
    settings.db_path.write_bytes(corrupt_bytes)

    app = build_recovery_app("DatabaseError: test recovery mode")
    commands: list[str] = []
    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["ok"] is False
        assert health.json()["recovery"] is True
        assert health.json()["version"] == "0.11.0"

        denied = client.post(
            "/api/v1/recovery/restore",
            files={"file": (archive.name, archive_bytes, "application/zip")},
        )
        assert denied.status_code == 401

        staged = client.post(
            "/api/v1/recovery/restore",
            files={"file": (archive.name, archive_bytes, "application/zip")},
            headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN},
        )
        assert staged.status_code == 200
        assert staged.json()["staged"] is True
        assert settings.pending_restore_dir.is_dir()

        register_runtime_handler(lambda command: commands.append(command) is None or True)
        try:
            restart = client.post(
                "/api/v1/recovery/restart",
                headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN},
            )
            assert restart.status_code == 200
            assert commands == ["restart"]
        finally:
            register_runtime_handler(None)

    restored = apply_pending_restore_for_startup()
    assert restored is not None and restored["status"] == "applied"
    quarantine = Path(restored["unreadable_live_snapshot"])
    assert quarantine.is_dir()
    assert (quarantine / "homeserver.db").read_bytes() == corrupt_bytes
    assert not settings.pending_restore_dir.exists()

    connection = sqlite3.connect(settings.db_path)
    try:
        row = connection.execute(
            "SELECT content FROM agent_memory WHERE memory_key='recovery-test' LIMIT 1"
        ).fetchone()
        assert row is not None and row[0] == "known-good-state"
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        connection.close()

print("HomeServer recovery-mode test passed")
