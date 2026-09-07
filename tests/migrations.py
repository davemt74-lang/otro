from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-migration-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.config import settings  # noqa: E402
    from app.database import SCHEMA_PATH, db, initialize_database  # noqa: E402
    from app.services.knowledge import ensure_knowledge_index, list_knowledge  # noqa: E402

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(settings.db_path)
    connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    connection.execute("INSERT INTO schema_migrations(version) VALUES (1)")
    connection.execute(
        """
        INSERT INTO knowledge_items(title, kind, content, content_hash)
        VALUES ('Legacy knowledge', 'note', 'Legacy merchant context must survive database upgrades.', 'legacy')
        """
    )
    connection.commit()
    connection.close()

    initialize_database()
    ensure_knowledge_index()

    with db() as migrated:
        versions = [
            row["version"]
            for row in migrated.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]
        assert versions == [1, 2]
        assert migrated.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0] >= 1
        assert migrated.execute(
            "SELECT COUNT(*) FROM knowledge_chunks_fts WHERE knowledge_chunks_fts MATCH 'merchant'"
        ).fetchone()[0] >= 1

    results = list_knowledge("legacy merchant")
    assert len(results) == 1
    assert results[0]["title"] == "Legacy knowledge"

    initialize_database()
    with db() as migrated_again:
        versions_again = [
            row["version"]
            for row in migrated_again.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]
        assert versions_again == [1, 2]

print("HomeServer migration upgrade test passed")
