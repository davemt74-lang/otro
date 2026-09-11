from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


with tempfile.TemporaryDirectory(prefix="homeserver-agent-workflow-automation-v058-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db, initialize_database  # noqa: E402
    from app.services import agent_workflow_automation as automation  # noqa: E402
    from app.services import agent_workflow_supervision as supervision  # noqa: E402

    initialize_database()

    def create_plan(conversation_id: str, source: str = "owner") -> int:
        with db() as connection:
            primary = connection.execute("SELECT id, name FROM agents WHERE is_primary=1 LIMIT 1").fetchone()
            connection.execute(
                "INSERT INTO conversations(id, agent_id, source_app_key, title, status) VALUES (?, ?, ?, ?, 'active')",
                (conversation_id, int(primary["id"]), source, f"{conversation_id} automation workflow"),
            )
            cursor = connection.execute(
                """
                INSERT INTO agent_team_plans(
                    source_app_key, conversation_id, parent_agent_id, parent_agent_name,
                    objective, members_json, context_json, permission_snapshot_json,
                    cloud_used, status, decided_at
                ) VALUES (?, ?, ?, ?, 'Synthetic automation plan', '[]', '{}', '[]', 0, 'approved', CURRENT_TIMESTAMP)
                """,
                (source, conversation_id, int(primary["id"]), str(primary["name"])),
            )
            return int(cursor.lastrowid)

    def fake_rehydrate(source, conversation_id, plan_id, *, owner, permissions):
        with db() as connection:
            plan = connection.execute(
                "SELECT status FROM agent_team_plans WHERE id=? AND source_app_key=? AND conversation_id=?",
                (int(plan_id), source, conversation_id),
            ).fetchone()
            assert plan is not None
            fingerprint = hashlib.sha256(
                f"{source}:{conversation_id}:{plan_id}:{plan['status']}".encode("utf-8")
            ).hexdigest()
            connection.execute(
                """
                INSERT OR IGNORE INTO agent_workflow_rehydrations(
                    source_app_key, conversation_id, plan_id, state_fingerprint,
                    status, snapshot_json, drift_json
                ) VALUES (?, ?, ?, ?, 'ready', '{}', '{}')
                """,
                (source, conversation_id, int(plan_id), fingerprint),
            )
            checkpoint = connection.execute(
                """
                SELECT id FROM agent_workflow_rehydrations
                WHERE source_app_key=? AND conversation_id=? AND plan_id=? AND state_fingerprint=?
                """,
                (source, conversation_id, int(plan_id), fingerprint),
            ).fetchone()
        return {
            "version": "v0.56",
            "rehydration_id": int(checkpoint["id"]),
            "source_app_key": source,
            "conversation_id": conversation_id,
            "plan_id": int(plan_id),
            "state_fingerprint": fingerprint,
            "status": "ready",
            "safe_to_continue": True,
            "checkpoint": {
                "plan_status": str(plan["status"]),
                "workflow_status": "queued",
                "counts": {"members": 2, "queued": 2, "working": 0, "completed": 0, "failed": 0, "cancelled": 0},
            },
        }

    call_lock = threading.Lock()
    supervised_calls: list[tuple[str, str, int, int]] = []

    def fake_supervise(source, conversation_id, plan_id, rehydration_id, state_fingerprint, *, owner, current_permissions, max_steps):
        with call_lock:
            supervised_calls.append((source, conversation_id, int(plan_id), int(rehydration_id)))
            sequence = len(supervised_calls)
        return {
            "version": "v0.57",
            "supervision_id": None,
            "reused": False,
            "status": "stopped",
            "actions_executed": 1,
            "max_steps": int(max_steps),
            "steps": [{"sequence": 1, "action": "run"}],
            "stop_boundary": "step_budget_exhausted",
            "stop_label": f"Synthetic supervised step {sequence} completed.",
        }

    automation._rehydrate = fake_rehydrate
    supervision.continue_workflow = fake_supervise

    base = datetime(2026, 9, 11, 16, 0, tzinfo=timezone.utc)

    # Explicit one-time creation is idempotent, persists, and fires once.
    once_plan = create_plan("v058-once")
    once = automation.create_automation(
        "owner",
        "v058-once",
        once_plan,
        trigger_type="once",
        run_at=(base + timedelta(minutes=5)).isoformat(),
        owner=True,
        now=base,
    )
    duplicate = automation.create_automation(
        "owner",
        "v058-once",
        once_plan,
        trigger_type="once",
        run_at=(base + timedelta(minutes=5)).isoformat(),
        owner=True,
        now=base,
    )
    assert duplicate["automation_id"] == once["automation_id"]
    assert duplicate["reused"] is True
    first = automation.run_due_automations(now=base + timedelta(minutes=6))
    assert first == {"claimed": 1, "completed": 1, "stopped": 0, "conflicts": 0, "errors": 0}
    second = automation.run_due_automations(now=base + timedelta(minutes=6))
    assert second["claimed"] == 0
    assert len(supervised_calls) == 1
    once_after = automation.list_automations("owner", conversation_id="v058-once")[0]
    assert once_after["enabled"] is False
    assert once_after["last_status"] == "completed"
    assert len(automation.list_automation_runs("owner", once["automation_id"])) == 1

    # Interval triggers advance past missed slots instead of replaying a backlog.
    interval_plan = create_plan("v058-interval")
    interval = automation.create_automation(
        "owner",
        "v058-interval",
        interval_plan,
        trigger_type="interval",
        every_seconds=60,
        run_at=(base + timedelta(minutes=10)).isoformat(),
        max_steps=1,
        owner=True,
        now=base,
    )
    outcome = automation.run_due_automations(now=base + timedelta(minutes=12, seconds=30))
    assert outcome["claimed"] == 1
    interval_after = automation.list_automations("owner", conversation_id="v058-interval")[0]
    assert interval_after["enabled"] is True
    assert datetime.fromisoformat(interval_after["next_run_at"]) > base + timedelta(minutes=12, seconds=30)
    same_tick = automation.run_due_automations(now=base + timedelta(minutes=12, seconds=30))
    assert same_tick["claimed"] == 0
    next_tick = datetime.fromisoformat(interval_after["next_run_at"]) + timedelta(seconds=1)
    later = automation.run_due_automations(now=next_tick)
    assert later["claimed"] == 1
    assert len(automation.list_automation_runs("owner", interval["automation_id"])) == 2
    automation.set_automation_enabled("owner", interval["automation_id"], False, now=next_tick)

    # Activity triggers start at the creation watermark and consume one exact
    # matching event exactly once. Workflow-generated activity cannot be used as
    # an event trigger, preventing recursive automation loops.
    activity_plan = create_plan("v058-activity")
    activity = automation.create_automation(
        "owner",
        "v058-activity",
        activity_plan,
        trigger_type="activity",
        activity_action="task.reminded",
        resource_type="task",
        resource_key="42",
        owner=True,
        now=base,
    )
    with db() as connection:
        connection.execute(
            "INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json) VALUES ('system','test','task.reminded','task','41','{}')"
        )
        connection.execute(
            "INSERT INTO activity_log(actor_type, actor_key, action, resource_type, resource_key, metadata_json) VALUES ('system','test','task.reminded','task','42','{}')"
        )
    event_run = automation.run_due_automations(now=base + timedelta(minutes=20))
    assert event_run["claimed"] == 1
    assert len(automation.list_automation_runs("owner", activity["automation_id"])) == 1
    assert automation.run_due_automations(now=base + timedelta(minutes=20))["claimed"] == 0
    try:
        automation.create_automation(
            "owner",
            "v058-activity",
            activity_plan,
            trigger_type="activity",
            activity_action="agent.workflow.supervision.stopped",
            owner=True,
            now=base,
        )
        raise AssertionError("workflow-generated activity trigger should have been rejected")
    except automation.AgentWorkflowAutomationError as exc:
        assert exc.status_code == 422
    automation.set_automation_enabled("owner", activity["automation_id"], False, now=base)

    # Two competing scheduler calls cannot claim the same due trigger twice.
    race_plan = create_plan("v058-race")
    race = automation.create_automation(
        "owner",
        "v058-race",
        race_plan,
        trigger_type="once",
        run_at=(base + timedelta(minutes=30)).isoformat(),
        owner=True,
        now=base,
    )
    before_race_calls = len(supervised_calls)
    barrier = threading.Barrier(3)
    race_results: list[dict[str, int]] = []

    def race_worker():
        barrier.wait()
        race_results.append(automation.run_due_automations(now=base + timedelta(minutes=31)))

    threads = [threading.Thread(target=race_worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert sum(item["claimed"] for item in race_results) == 1
    assert len(supervised_calls) == before_race_calls + 1
    assert len(automation.list_automation_runs("owner", race["automation_id"])) == 1

    # Paired-app permission is re-read at trigger time. Revocation disables the
    # automation without entering v0.57.
    with db() as connection:
        cursor = connection.execute(
            "INSERT INTO paired_apps(app_key, name, token_hash, status) VALUES ('demo', 'Demo App', 'synthetic-token-hash', 'active')"
        )
        app_id = int(cursor.lastrowid)
        connection.execute(
            "INSERT INTO app_permissions(paired_app_id, permission, allowed) VALUES (?, 'agent.chat', 1)",
            (app_id,),
        )
    app_plan = create_plan("v058-app", "app:demo")
    app_auto = automation.create_automation(
        "app:demo",
        "v058-app",
        app_plan,
        trigger_type="once",
        run_at=(base + timedelta(minutes=40)).isoformat(),
        owner=False,
        current_permissions={"agent.chat"},
        now=base,
    )
    with db() as connection:
        connection.execute(
            "UPDATE app_permissions SET allowed=0 WHERE paired_app_id=? AND permission='agent.chat'",
            (app_id,),
        )
    before_revoke_calls = len(supervised_calls)
    revoked = automation.run_due_automations(now=base + timedelta(minutes=41))
    assert revoked["claimed"] == 1
    assert revoked["conflicts"] == 1
    assert len(supervised_calls) == before_revoke_calls
    app_after = automation.list_automations("app:demo", conversation_id="v058-app")[0]
    assert app_after["enabled"] is False
    assert app_after["last_status"] == "conflict"

    # Proposed plans remain an explicit approval boundary at creation time.
    proposed_plan = create_plan("v058-proposed")
    with db() as connection:
        connection.execute("UPDATE agent_team_plans SET status='proposed' WHERE id=?", (proposed_plan,))
    try:
        automation.create_automation(
            "owner",
            "v058-proposed",
            proposed_plan,
            trigger_type="once",
            run_at=(base + timedelta(hours=1)).isoformat(),
            owner=True,
            now=base,
        )
        raise AssertionError("proposed workflow should not be automatable")
    except automation.AgentWorkflowAutomationError as exc:
        assert exc.status_code == 409
        assert "approved" in str(exc).lower()

    with db() as connection:
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=22").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM agent_workflow_automation_runs").fetchone()[0] >= 6
        assert connection.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action='agent.workflow.automation.finished'"
        ).fetchone()[0] >= 6

print("HomeServer v0.58 Scheduled / Triggered Workflows regression passed")