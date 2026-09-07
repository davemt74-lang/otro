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
        versions = [row["version"] for row in migrated.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()]
        assert versions == list(range(1, 12))
        pairing_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(pairing_requests)").fetchall()}
        assert {"request_id", "claim_hash"}.issubset(pairing_columns)
        agent_run_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(agent_runs)").fetchall()}
        assert "tool_call_count" in agent_run_columns
        agent_policy_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(agent_tool_policy)").fetchall()}
        assert "allow_write_proposals" in agent_policy_columns
        contact_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(contacts)").fetchall()}
        assert {"display_name", "organization", "email", "phone", "relationship", "notes"}.issubset(contact_columns)
        system_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(system_settings)").fetchall()}
        assert {"setting_key", "value_json", "updated_at"}.issubset(system_columns)
        task_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(tasks)").fetchall()}
        assert {
            "title", "description", "status", "priority", "due_at", "remind_at",
            "recurrence", "recurrence_interval", "contact_id", "source_app_key",
            "created_by_type", "last_reminded_at", "completed_at", "cancelled_at"
        }.issubset(task_columns)
        notification_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(notifications)").fetchall()}
        assert {"task_id", "dismissed_at"}.issubset(notification_columns)
        system_settings = {
            row["setting_key"]: row["value_json"]
            for row in migrated.execute("SELECT setting_key, value_json FROM system_settings").fetchall()
        }
        assert system_settings["first_run_complete"] == "false"
        assert system_settings["first_run_prompted"] == "false"
        bridge = migrated.execute("SELECT enabled, broker_url FROM remote_bridge_settings WHERE id=1").fetchone()
        assert bridge is not None
        assert bridge["enabled"] == 0
        assert bridge["broker_url"] == ""
        assert migrated.execute("SELECT COUNT(*) FROM remote_bridge_events").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0] >= 1
        assert migrated.execute("SELECT COUNT(*) FROM knowledge_chunks_fts WHERE knowledge_chunks_fts MATCH 'merchant'").fetchone()[0] >= 1
        provider = migrated.execute("SELECT provider_key, enabled FROM model_providers WHERE provider_key='ollama'").fetchone()
        assert provider is not None and provider["enabled"] == 0
        assert migrated.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] == 0
        policies = migrated.execute("SELECT tool_key, enabled FROM tool_policies ORDER BY tool_key").fetchall()
        assert [(row["tool_key"], row["enabled"]) for row in policies] == [
            ("contacts.search", 1),
            ("knowledge.search", 1),
            ("memory.list", 1),
            ("memory.write", 1),
            ("notifications.list", 1),
            ("tasks.create", 1),
            ("tasks.list", 1),
        ]
        assert migrated.execute("SELECT COUNT(*) FROM tool_runs").fetchone()[0] == 0
        agent_policy = migrated.execute(
            "SELECT enabled, max_calls, allow_write_proposals FROM agent_tool_policy WHERE id=1"
        ).fetchone()
        assert agent_policy is not None
        assert agent_policy["enabled"] == 0
        assert agent_policy["max_calls"] == 3
        assert agent_policy["allow_write_proposals"] == 0
        assert migrated.execute("SELECT COUNT(*) FROM action_requests").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM contacts").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0

    results = list_knowledge("legacy merchant")
    assert len(results) == 1
    assert results[0]["title"] == "Legacy knowledge"

    initialize_database()
    with db() as migrated_again:
        versions_again = [row["version"] for row in migrated_again.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()]
        assert versions_again == list(range(1, 12))
        assert migrated_again.execute("SELECT COUNT(*) FROM model_providers WHERE provider_key='ollama'").fetchone()[0] == 1
        assert migrated_again.execute("SELECT COUNT(*) FROM tool_policies").fetchone()[0] == 7
        assert migrated_again.execute("SELECT COUNT(*) FROM agent_tool_policy").fetchone()[0] == 1
        assert migrated_again.execute("SELECT COUNT(*) FROM action_requests").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM contacts").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM system_settings").fetchone()[0] == 2
        assert migrated_again.execute("SELECT COUNT(*) FROM remote_bridge_settings").fetchone()[0] == 1
        assert migrated_again.execute("SELECT COUNT(*) FROM remote_bridge_events").fetchone()[0] == 0

print("HomeServer migration upgrade test passed")
