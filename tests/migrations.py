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
    from app.database import SCHEMA_PATH, _apply_migration, connect, db, initialize_database, migration_files  # noqa: E402
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

    # Build an authentic schema-10 database first so migrations 11 through 23
    # are tested as upgrades rather than only as a fresh install.
    for version, path in migration_files():
        if version >= 11:
            break
        migration_connection = connect()
        try:
            _apply_migration(migration_connection, version, path)
        finally:
            migration_connection.close()

    with db() as prior:
        prior_versions = [row["version"] for row in prior.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()]
        assert prior_versions == list(range(1, 11))
        prior.execute(
            """
            INSERT INTO action_requests(
                id, action_key, source_app_key, actor_type, status,
                arguments_json, arguments_meta_json, expires_at
            ) VALUES (
                'legacy-memory-request', 'memory.write', 'app:legacy', 'app', 'pending',
                '{"content":"preserve-me"}', '{"content_length":11}', '2099-01-01T00:00:00+00:00'
            )
            """
        )

    initialize_database()
    ensure_knowledge_index()

    with db() as migrated:
        versions = [row["version"] for row in migrated.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()]
        assert versions == list(range(1, 39))
        for automation_table in (
            "automation_rooms",
            "automation_providers",
            "automation_devices",
            "automation_device_actions",
            "automation_suggestions",
            "automation_runtime_settings",
            "automation_routines",
            "automation_routine_steps",
            "automation_rules",
            "automation_rule_executions",
            "automation_intelligence_settings",
            "automation_context_events",
            "automation_learning_patterns",
            "automation_proposals",
            "automation_proposal_feedback",
            "automation_simulations",
            "orchestration_settings",
            "orchestration_modes",
            "orchestration_mode_sessions",
            "orchestration_mode_conflicts",
            "orchestration_mode_transitions",
            "vp3_rollout_settings",
            "vp3_hardware_certifications",
            "vp3_rollout_packages",
            "vp3_rollout_events",
            "vp3_fleet_settings",
            "vp3_fleet_inventory",
            "vp3_fleet_rollouts",
            "vp3_fleet_rollout_outcomes",
            "vp3_fleet_update_requests",
            "vp3_fleet_events",
            "vp3_hardware_experience_settings",
            "vp3_hardware_experience_events",
            "vp3_hardware_experience_cards",
            "vp3_hardware_experience_certifications",
        ):
            assert migrated.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (automation_table,),
            ).fetchone() is not None
        migrated.execute(
            """
            INSERT INTO action_requests(
                id, action_key, source_app_key, actor_type, status,
                arguments_json, arguments_meta_json, expires_at
            ) VALUES (
                'v060-device-request', 'devices.command', 'owner', 'owner', 'pending',
                '{"device_key":"migration-test","command":"on","arguments":{}}',
                '{"command":"on"}', '2099-01-01T00:00:00+00:00'
            )
            """
        )
        assert migrated.execute(
            "SELECT action_key FROM action_requests WHERE id='v060-device-request'"
        ).fetchone()["action_key"] == "devices.command"
        pairing_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(pairing_requests)").fetchall()}
        assert {"request_id", "claim_hash"}.issubset(pairing_columns)
        agent_run_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(agent_runs)").fetchall()}
        assert {"tool_call_count", "contact_count", "context_chars", "awareness_count"}.issubset(agent_run_columns)
        memory_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(agent_memory)").fetchall()}
        assert {
            "memory_type", "source_app_key", "source_event_id", "confidence",
            "reinforcement_count", "last_accessed_at", "expires_at", "entity_type", "entity_key"
        }.issubset(memory_columns)
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
        usage_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(inference_usage_events)").fetchall()}
        assert {
            "event_id", "source_app_key", "compute_source", "provider_key", "model",
            "prompt_tokens", "completion_tokens", "total_tokens", "billable_tokens",
            "balance_after_tokens", "created_at"
        }.issubset(usage_columns)
        source_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(knowledge_sources)").fetchall()}
        assert {
            "path", "label", "enabled", "recursive", "scan_interval_seconds", "exclude_json",
            "status", "last_scan_completed_at", "last_scan_error_count", "updated_at"
        }.issubset(source_columns)
        source_file_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(knowledge_source_files)").fetchall()}
        assert {
            "source_id", "relative_path", "knowledge_item_id", "content_hash", "size_bytes",
            "modified_ns", "status", "last_error", "last_seen_scan"
        }.issubset(source_file_columns)
        context_setting_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(conversation_context_settings)").fetchall()}
        assert {
            "conversation_id", "include_memory", "include_knowledge", "include_contacts",
            "include_awareness", "cloud_allowed", "max_context_chars", "updated_at"
        }.issubset(context_setting_columns)
        context_event_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(context_retrieval_events)").fetchall()}
        assert {
            "conversation_id", "source_app_key", "memory_count", "knowledge_count",
            "contact_count", "context_chars", "source_refs_json", "created_at"
        }.issubset(context_event_columns)
        cognitive_event_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(cognitive_events)").fetchall()}
        assert {
            "event_id", "source_app_key", "source_kind", "plugin_key", "event_type", "entity_type",
            "entity_key", "correlation_id", "conversation_id", "summary", "importance", "privacy_scope",
            "memory_candidate", "memory_type", "memory_key", "payload_json", "occurred_at", "created_at"
        }.issubset(cognitive_event_columns)
        cognition_job_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(cognition_jobs)").fetchall()}
        assert {"event_row_id", "job_type", "status", "attempts", "result_json", "error"}.issubset(cognition_job_columns)
        awareness_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(awareness_items)").fetchall()}
        assert {"fingerprint", "event_type", "summary", "importance", "occurrence_count", "source_apps_json", "last_event_id"}.issubset(awareness_columns)
        candidate_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(memory_candidates)").fetchall()}
        assert {"source_event_id", "source_awareness_id", "memory_type", "content", "confidence", "importance", "status"}.issubset(candidate_columns)
        plugin_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(plugins)").fetchall()}
        assert {"plugin_key", "name", "version", "status", "trusted", "manifest_json"}.issubset(plugin_columns)
        scope_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(app_capability_scopes)").fetchall()}
        assert {
            "paired_app_id", "cloud_allowed", "memory_key_prefixes", "knowledge_kinds",
            "tool_names", "plugin_keys", "created_at", "updated_at"
        }.issubset(scope_columns)
        collaboration_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(app_collaboration_grants)").fetchall()}
        assert {
            "consumer_app_id", "source_app_id", "memory_allowed", "knowledge_allowed",
            "enabled", "created_at", "updated_at"
        }.issubset(collaboration_columns)
        execution_policy_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(app_tool_execution_policies)").fetchall()}
        assert {"paired_app_id", "tool_key", "policy_mode", "created_at", "updated_at"}.issubset(execution_policy_columns)
        policy_decision_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(action_policy_decisions)").fetchall()}
        assert {
            "paired_app_id", "source_app_key", "tool_key", "policy_mode", "decision",
            "request_id", "reason", "metadata_json", "created_at"
        }.issubset(policy_decision_columns)
        rehydration_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(agent_workflow_rehydrations)").fetchall()}
        assert {
            "source_app_key", "conversation_id", "plan_id", "team_run_id", "state_fingerprint",
            "status", "snapshot_json", "drift_json", "created_at", "updated_at"
        }.issubset(rehydration_columns)
        assert migrated.execute("SELECT COUNT(*) FROM knowledge_sources").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM knowledge_source_files").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM conversation_context_settings").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM context_retrieval_events").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM cognitive_events").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM cognition_jobs").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM awareness_items").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM memory_candidates").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM plugins").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM app_capability_scopes").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM app_collaboration_grants").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM app_tool_execution_policies").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM action_policy_decisions").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM agent_workflow_rehydrations").fetchone()[0] == 0
        assert migrated.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='federated_file_mutations'"
        ).fetchone() is not None
        assert migrated.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='federated_memory_mutations'"
        ).fetchone() is not None
        assert migrated.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='federated_reconciliation_state'"
        ).fetchone() is not None
        assert migrated.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='federated_reconciliation_runs'"
        ).fetchone() is not None
        cursor = migrated.execute("SELECT cursor_value FROM cognition_cursors WHERE cursor_key='activity_log_id'").fetchone()
        assert cursor is not None and cursor["cursor_value"] == "0"
        source_delete_trigger = migrated.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='knowledge_source_item_before_delete'"
        ).fetchone()
        assert source_delete_trigger is not None
        assert "re-indexed on the next scan" in source_delete_trigger["sql"]

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
        provider_keys = [row["provider_key"] for row in migrated.execute("SELECT provider_key FROM model_providers ORDER BY provider_key").fetchall()]
        assert provider_keys == ["anthropic", "ollama", "openai", "openrouter"]
        inference_settings = migrated.execute("SELECT preferred_provider FROM inference_settings WHERE id=1").fetchone()
        assert inference_settings is not None and inference_settings["preferred_provider"] == "auto"
        assert migrated.execute("SELECT COUNT(*) FROM inference_usage_events").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] == 0
        policies = migrated.execute("SELECT tool_key, enabled FROM tool_policies ORDER BY tool_key").fetchall()
        assert [(row["tool_key"], row["enabled"]) for row in policies] == [
            ("calendar.create", 1),
            ("calendar.delete", 1),
            ("calendar.list", 1),
            ("calendar.update", 1),
            ("contacts.create", 1),
            ("contacts.delete", 1),
            ("contacts.search", 1),
            ("contacts.update", 1),
            ("devices.command", 1),
            ("devices.list", 1),
            ("files.delete", 1),
            ("files.update", 1),
            ("knowledge.create", 1),
            ("knowledge.delete", 1),
            ("knowledge.search", 1),
            ("knowledge.update", 1),
            ("memory.delete", 1),
            ("memory.list", 1),
            ("memory.update", 1),
            ("memory.write", 1),
            ("notifications.list", 1),
            ("tasks.create", 1),
            ("tasks.delete", 1),
            ("tasks.list", 1),
            ("tasks.update", 1),
        ]
        runtime_settings = migrated.execute(
            "SELECT enabled,poll_seconds,max_actions_per_run,max_rule_fires_per_minute FROM automation_runtime_settings WHERE id=1"
        ).fetchone()
        assert runtime_settings is not None
        assert runtime_settings["enabled"] == 1
        assert runtime_settings["poll_seconds"] == 15
        assert runtime_settings["max_actions_per_run"] == 12
        assert runtime_settings["max_rule_fires_per_minute"] == 20
        assert migrated.execute("SELECT COUNT(*) FROM automation_routines").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM automation_rules").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM automation_rule_executions").fetchone()[0] == 0
        intelligence_settings = migrated.execute(
            """
            SELECT enabled,scan_interval_seconds,lookback_days,min_occurrences,
                   time_bucket_minutes,max_proposals_per_scan,suppression_days
            FROM automation_intelligence_settings WHERE id=1
            """
        ).fetchone()
        assert intelligence_settings is not None
        assert intelligence_settings["enabled"] == 1
        assert intelligence_settings["scan_interval_seconds"] == 3600
        assert intelligence_settings["lookback_days"] == 21
        assert intelligence_settings["min_occurrences"] == 4
        assert intelligence_settings["time_bucket_minutes"] == 30
        assert intelligence_settings["max_proposals_per_scan"] == 12
        assert intelligence_settings["suppression_days"] == 30
        assert migrated.execute("SELECT COUNT(*) FROM automation_context_events").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM automation_learning_patterns").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM automation_proposals").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM automation_proposal_feedback").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM automation_simulations").fetchone()[0] == 0
        orchestration_settings = migrated.execute(
            """
            SELECT enabled,poll_seconds,suggestion_cooldown_seconds,
                   max_open_sessions
            FROM orchestration_settings WHERE id=1
            """
        ).fetchone()
        assert orchestration_settings is not None
        assert orchestration_settings["enabled"] == 1
        assert orchestration_settings["poll_seconds"] == 30
        assert orchestration_settings["suggestion_cooldown_seconds"] == 14400
        assert orchestration_settings["max_open_sessions"] == 12
        assert migrated.execute("SELECT COUNT(*) FROM orchestration_modes").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM orchestration_mode_sessions").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM orchestration_mode_conflicts").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM orchestration_mode_transitions").fetchone()[0] == 0
        rollout_settings = migrated.execute(
            """
            SELECT release_channel,rollout_ring,automatic_apply,
                   watchdog_enabled,max_failed_starts
            FROM vp3_rollout_settings WHERE id=1
            """
        ).fetchone()
        assert rollout_settings is not None
        assert rollout_settings["release_channel"] == "stable"
        assert rollout_settings["rollout_ring"] == "pilot"
        assert rollout_settings["automatic_apply"] == 0
        assert rollout_settings["watchdog_enabled"] == 1
        assert rollout_settings["max_failed_starts"] == 3
        assert migrated.execute("SELECT COUNT(*) FROM vp3_hardware_certifications").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM vp3_rollout_packages").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM vp3_rollout_events").fetchone()[0] == 0
        fleet_settings = migrated.execute(
            """
            SELECT enabled,controller_app_key,remote_diagnostics,
                   remote_update_requests,remote_support_summary,
                   telemetry_interval_seconds,stale_after_seconds,
                   rollout_failure_threshold
            FROM vp3_fleet_settings WHERE id=1
            """
        ).fetchone()
        assert fleet_settings is not None
        assert fleet_settings["enabled"] == 0
        assert fleet_settings["controller_app_key"] is None
        assert fleet_settings["remote_diagnostics"] == 0
        assert fleet_settings["remote_update_requests"] == 0
        assert fleet_settings["remote_support_summary"] == 0
        assert fleet_settings["telemetry_interval_seconds"] == 300
        assert fleet_settings["stale_after_seconds"] == 900
        assert fleet_settings["rollout_failure_threshold"] == 2
        assert migrated.execute("SELECT COUNT(*) FROM vp3_fleet_inventory").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM vp3_fleet_rollouts").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM vp3_fleet_rollout_outcomes").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM vp3_fleet_update_requests").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM vp3_fleet_events").fetchone()[0] == 0
        fleet_inventory_columns = {
            row["name"]
            for row in migrated.execute("PRAGMA table_info(vp3_fleet_inventory)").fetchall()
        }
        assert "hardware_experience_version" in fleet_inventory_columns
        assert "experience_profile" in fleet_inventory_columns
        experience_settings = migrated.execute(
            """
            SELECT enabled,brightness_percent,volume_percent,led_intensity_percent,
                   screen_timeout_seconds,wake_behavior,agent_button_action,
                   hold_action,display_detail,quiet_visuals
            FROM vp3_hardware_experience_settings WHERE id=1
            """
        ).fetchone()
        assert experience_settings is not None
        assert experience_settings["enabled"] == 1
        assert experience_settings["brightness_percent"] == 70
        assert experience_settings["volume_percent"] == 65
        assert experience_settings["led_intensity_percent"] == 70
        assert experience_settings["screen_timeout_seconds"] == 300
        assert experience_settings["wake_behavior"] == "presence"
        assert experience_settings["agent_button_action"] == "push_to_talk"
        assert experience_settings["hold_action"] == "cancel"
        assert experience_settings["display_detail"] == "standard"
        assert experience_settings["quiet_visuals"] == 0
        assert migrated.execute("SELECT COUNT(*) FROM vp3_hardware_experience_events").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM vp3_hardware_experience_cards").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM vp3_hardware_experience_certifications").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM tool_runs").fetchone()[0] == 0
        agent_policy = migrated.execute(
            "SELECT enabled, max_calls, allow_write_proposals FROM agent_tool_policy WHERE id=1"
        ).fetchone()
        assert agent_policy is not None
        assert agent_policy["enabled"] == 0
        assert agent_policy["max_calls"] == 3
        assert agent_policy["allow_write_proposals"] == 0

        legacy_request = migrated.execute(
            "SELECT action_key, source_app_key, status, arguments_json FROM action_requests WHERE id='legacy-memory-request'"
        ).fetchone()
        assert legacy_request is not None
        assert legacy_request["action_key"] == "memory.write"
        assert legacy_request["source_app_key"] == "app:legacy"
        assert legacy_request["status"] == "pending"
        assert "preserve-me" in legacy_request["arguments_json"]

        action_schema = migrated.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='action_requests'"
        ).fetchone()[0].replace(" ", "").replace("\n", "")
        for action_key in (
            "memory.write", "memory.update", "memory.delete",
            "tasks.create", "tasks.update", "tasks.delete", "files.update", "files.delete",
            "vp3.booking.create", "vp3.booking.reschedule", "vp3.booking.cancel",
            "contacts.create", "contacts.update", "contacts.delete",
            "knowledge.create", "knowledge.update", "knowledge.delete",
            "calendar.create", "calendar.update", "calendar.delete",
        ):
            assert f"'{action_key}'" in action_schema
        proposal_checks = (
            ("memory-update-check", "memory.update", '{"canonical_id":"fd24_0123456789abcdef0123456789abcdef01234567","mutation_id":"memory-update-check","expected_revision":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","content":"test"}', '{"content_length":4}'),
            ("memory-delete-check", "memory.delete", '{"canonical_id":"fd24_0123456789abcdef0123456789abcdef01234567","mutation_id":"memory-delete-check","expected_revision":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}', '{"content_length":0}'),
            ("task-proposal-check", "tasks.create", '{"title":"test"}', '{"title_length":4}'),
            ("file-update-proposal-check", "files.update", '{"ref":"hsf-1-0123456789abcdef","content":"test"}', '{"content_length":4}'),
            ("file-delete-proposal-check", "files.delete", '{"ref":"hsf-1-0123456789abcdef"}', '{"ref_length":22}'),
            ("vp3-booking-create-check", "vp3.booking.create", '{"kind":"personal","target_id":1,"start_at_utc":"2099-01-01T18:00:00Z","guest_name":"Test"}', '{"kind":"personal"}'),
            ("vp3-booking-reschedule-check", "vp3.booking.reschedule", '{"booking_id":1,"start_at_utc":"2099-01-02T18:00:00Z"}', '{"booking_id":1}'),
            ("vp3-booking-cancel-check", "vp3.booking.cancel", '{"kind":"personal","booking_id":1}', '{"booking_id":1}'),
            ("contacts-create-check", "contacts.create", '{"display_name":"Test Contact"}', '{"field_count":1}'),
            ("contacts-update-check", "contacts.update", '{"canonical_id":"fd24_0123456789abcdef0123456789abcdef01234567","display_name":"Updated"}', '{"field_count":1}'),
            ("contacts-delete-check", "contacts.delete", '{"canonical_id":"fd24_0123456789abcdef0123456789abcdef01234567"}', '{"field_count":0}'),
        )
        for request_id, action_key, arguments_json, arguments_meta_json in proposal_checks:
            migrated.execute(
                """
                INSERT INTO action_requests(
                    id, action_key, source_app_key, actor_type, status,
                    arguments_json, arguments_meta_json, expires_at
                ) VALUES (?, ?, 'app:migration-test', 'app', 'pending', ?, ?, '2099-01-01T00:00:00+00:00')
                """,
                (request_id, action_key, arguments_json, arguments_meta_json),
            )
            assert migrated.execute(
                "SELECT COUNT(*) FROM action_requests WHERE id=? AND action_key=?",
                (request_id, action_key),
            ).fetchone()[0] == 1
            migrated.execute("DELETE FROM action_requests WHERE id=?", (request_id,))

        remaining_requests = {
            row["id"]: row["action_key"]
            for row in migrated.execute(
                "SELECT id, action_key FROM action_requests ORDER BY id"
            ).fetchall()
        }
        assert remaining_requests == {
            "legacy-memory-request": "memory.write",
            "v060-device-request": "devices.command",
        }
        assert migrated.execute("SELECT COUNT(*) FROM contacts").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0

    results = list_knowledge("legacy merchant")
    assert len(results) == 1
    assert results[0]["title"] == "Legacy knowledge"

    initialize_database()
    with db() as migrated_again:
        versions_again = [row["version"] for row in migrated_again.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()]
        assert versions_again == list(range(1, 39))
        assert migrated_again.execute("SELECT COUNT(*) FROM model_providers WHERE provider_key='ollama'").fetchone()[0] == 1
        assert migrated_again.execute("SELECT COUNT(*) FROM model_providers").fetchone()[0] == 4
        assert migrated_again.execute("SELECT COUNT(*) FROM inference_settings").fetchone()[0] == 1
        assert migrated_again.execute("SELECT COUNT(*) FROM inference_usage_events").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM tool_policies").fetchone()[0] == 25
        assert migrated_again.execute("SELECT COUNT(*) FROM agent_tool_policy").fetchone()[0] == 1
        assert migrated_again.execute("SELECT COUNT(*) FROM automation_intelligence_settings").fetchone()[0] == 1
        assert migrated_again.execute("SELECT COUNT(*) FROM automation_context_events").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM automation_learning_patterns").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM automation_proposals").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM automation_proposal_feedback").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM automation_simulations").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM orchestration_settings").fetchone()[0] == 1
        assert migrated_again.execute("SELECT COUNT(*) FROM orchestration_modes").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM orchestration_mode_sessions").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM orchestration_mode_conflicts").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM orchestration_mode_transitions").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM vp3_rollout_settings").fetchone()[0] == 1
        assert migrated_again.execute("SELECT COUNT(*) FROM vp3_hardware_certifications").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM vp3_rollout_packages").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM vp3_rollout_events").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM vp3_fleet_settings").fetchone()[0] == 1
        assert migrated_again.execute("SELECT COUNT(*) FROM vp3_fleet_inventory").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM vp3_fleet_rollouts").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM vp3_fleet_rollout_outcomes").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM vp3_fleet_update_requests").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM vp3_fleet_events").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM vp3_hardware_experience_settings").fetchone()[0] == 1
        assert migrated_again.execute("SELECT COUNT(*) FROM vp3_hardware_experience_events").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM vp3_hardware_experience_cards").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM vp3_hardware_experience_certifications").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM action_requests").fetchone()[0] == 2
        assert migrated_again.execute(
            "SELECT COUNT(*) FROM action_requests WHERE id='legacy-memory-request' AND action_key='memory.write'"
        ).fetchone()[0] == 1
        assert migrated_again.execute(
            "SELECT COUNT(*) FROM action_requests WHERE id='v060-device-request' AND action_key='devices.command'"
        ).fetchone()[0] == 1
        assert migrated_again.execute("SELECT COUNT(*) FROM contacts").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM knowledge_sources").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM knowledge_source_files").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM conversation_context_settings").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM context_retrieval_events").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM cognitive_events").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM cognition_jobs").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM awareness_items").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM memory_candidates").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM plugins").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM plugin_event_subscriptions").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM app_capability_scopes").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM app_collaboration_grants").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM app_tool_execution_policies").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM action_policy_decisions").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM agent_workflow_rehydrations").fetchone()[0] == 0
        assert migrated_again.execute("SELECT COUNT(*) FROM cognition_cursors").fetchone()[0] == 1
        assert migrated_again.execute("SELECT COUNT(*) FROM system_settings").fetchone()[0] == 2
        assert migrated_again.execute("SELECT COUNT(*) FROM remote_bridge_settings").fetchone()[0] == 1
        assert migrated_again.execute("SELECT COUNT(*) FROM remote_bridge_events").fetchone()[0] == 0

print("HomeServer migration upgrade test passed")