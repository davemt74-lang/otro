from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import settings  # noqa: E402
from app.database import db, initialize_database  # noqa: E402
from app.services import backups  # noqa: E402

BEFORE = "PACKAGED_RESTORE_BEFORE_48193"
AFTER = "PACKAGED_RESTORE_AFTER_71942"


def setup() -> None:
    initialize_database()
    with db() as connection:
        connection.execute("DELETE FROM agent_memory WHERE memory_key='packaged-restore'")
        connection.execute(
            "INSERT INTO agent_memory(memory_key, content, importance) VALUES ('packaged-restore', ?, 0.8)",
            (BEFORE,),
        )
    backup = backups.create_backup("packaged-restore-test")
    with db() as connection:
        connection.execute(
            "UPDATE agent_memory SET content=?, updated_at=CURRENT_TIMESTAMP WHERE memory_key='packaged-restore'",
            (AFTER,),
        )
    with Path(backup["path"]).open("rb") as source:
        staged = backups.stage_restore(source, backup["name"])
    assert staged["status"] == "pending_restart"
    assert settings.pending_restore_dir.is_dir()
    print(f"Packaged restore staged from {backup['name']}")


def verify() -> None:
    connection = sqlite3.connect(settings.db_path)
    try:
        row = connection.execute(
            "SELECT content FROM agent_memory WHERE memory_key='packaged-restore' LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row[0] == BEFORE
        assert row[0] != AFTER
    finally:
        connection.close()

    result = backups.last_restore_result()
    assert result is not None
    assert result["status"] == "applied"
    assert result["pre_restore_backup"]
    assert (settings.backups_dir / result["pre_restore_backup"]).is_file()
    assert not settings.pending_restore_dir.exists()
    print("Packaged HomeServer restore startup test passed")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "setup":
        setup()
    elif mode == "verify":
        verify()
    else:
        raise SystemExit("usage: packaged_restore.py setup|verify")
