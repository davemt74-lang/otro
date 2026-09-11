from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


with tempfile.TemporaryDirectory(prefix="homeserver-agent-workflow-supervision-schema-v057-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db, initialize_database  # noqa: E402

    initialize_database()
    with db() as connection:
        versions_before = [row["version"] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()]
        assert versions_before == list(range(1, 22))
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(agent_workflow_supervisions)").fetchall()}
        assert {
            "source_app_key", "conversation_id", "plan_id", "team_run_id", "rehydration_id",
            "starting_state_fingerprint", "final_rehydration_id", "final_state_fingerprint",
            "max_steps", "status", "step_count", "steps_json", "stop_boundary", "result_json",
            "created_at", "updated_at",
        }.issubset(columns)
        connection.execute("DROP TABLE agent_workflow_supervisions")

    # Simulate an existing v0.56 data directory opening under v0.57. The
    # idempotent feature schema must add the table without changing numbered
    # migration history or disturbing existing data.
    initialize_database()
    with db() as connection:
        versions_after = [row["version"] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()]
        assert versions_after == versions_before
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(agent_workflow_supervisions)").fetchall()}
        assert "rehydration_id" in columns
        assert "result_json" in columns
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='agent_workflow_supervisions'"
        ).fetchone()[0]
        assert "UNIQUE" in table_sql
        assert "max_steps BETWEEN 1 AND 2" in table_sql
        assert "step_count BETWEEN 0 AND 2" in table_sql

    # Re-running initialization is a no-op for the extension.
    initialize_database()
    with db() as connection:
        assert connection.execute("SELECT COUNT(*) FROM agent_workflow_supervisions").fetchone()[0] == 0
        assert [row["version"] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()] == versions_before

print("HomeServer v0.57 supervision feature-schema upgrade/idempotency regression passed")
