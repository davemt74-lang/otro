from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


with tempfile.TemporaryDirectory(prefix="homeserver-agent-workflow-automation-schema-v058-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db, initialize_database  # noqa: E402

    initialize_database()
    with db() as connection:
        versions_before = [int(row["version"]) for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")]
        assert versions_before.count(22) == 1
        automation_sql = str(connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='agent_workflow_automations'"
        ).fetchone()["sql"])
        runs_sql = str(connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='agent_workflow_automation_runs'"
        ).fetchone()["sql"])
        assert "trigger_type IN ('once','interval','activity')" in automation_sql
        assert "max_steps BETWEEN 1 AND 2" in automation_sql
        assert "UNIQUE (source_app_key, conversation_id, plan_id, trigger_type, trigger_config_json)" in automation_sql
        assert "UNIQUE (automation_id, trigger_key)" in runs_sql
        assert "REFERENCES agent_workflow_supervisions(id) ON DELETE SET NULL" in runs_sql

    initialize_database()
    with db() as connection:
        versions_after = [int(row["version"]) for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")]
        assert versions_after == versions_before
        assert versions_after.count(22) == 1
        indexes = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_agent_workflow_automation%'"
            ).fetchall()
        }
        assert "idx_agent_workflow_automations_due" in indexes
        assert "idx_agent_workflow_automations_activity" in indexes
        assert "idx_agent_workflow_automation_runs_automation" in indexes

print("HomeServer v0.58 workflow automation schema/idempotency test passed")