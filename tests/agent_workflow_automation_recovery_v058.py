from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


with tempfile.TemporaryDirectory(prefix="homeserver-agent-workflow-automation-recovery-v058-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db, initialize_database  # noqa: E402
    from app.services import agent_workflow_automation as automation  # noqa: E402
    from app.services import agent_workflow_automation_runtime as runtime  # noqa: E402
    from app.services import agent_workflow_supervision as supervision  # noqa: E402

    initialize_database()
    base = datetime(2026, 9, 11, 18, 0, tzinfo=timezone.utc)

    def plan(conversation_id: str) -> int:
        with db() as connection:
            primary = connection.execute("SELECT id, name FROM agents WHERE is_primary=1 LIMIT 1").fetchone()
            connection.execute(
                "INSERT INTO conversations(id, agent_id, source_app_key, title, status) VALUES (?, ?, 'owner', ?, 'active')",
                (conversation_id, int(primary["id"]), conversation_id),
            )
            cursor = connection.execute(
                """
                INSERT INTO agent_team_plans(
                    source_app_key, conversation_id, parent_agent_id, parent_agent_name,
                    objective, members_json, context_json, permission_snapshot_json,
                    cloud_used, status, decided_at
                ) VALUES ('owner', ?, ?, ?, 'Crash recovery', '[]', '{}', '[]', 0, 'approved', CURRENT_TIMESTAMP)
                """,
                (conversation_id, int(primary["id"]), str(primary["name"])),
            )
            return int(cursor.lastrowid)

    rehydrate_calls: list[tuple[str, int]] = []

    def fake_rehydrate(source, conversation_id, plan_id, *, owner, permissions):
        rehydrate_calls.append((conversation_id, int(plan_id)))
        fingerprint = hashlib.sha256(f"{source}:{conversation_id}:{plan_id}".encode()).hexdigest()
        with db() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO agent_workflow_rehydrations(
                    source_app_key, conversation_id, plan_id, state_fingerprint, status, snapshot_json, drift_json
                ) VALUES (?, ?, ?, ?, 'ready', '{}', '{}')
                """,
                (source, conversation_id, int(plan_id), fingerprint),
            )
            row = connection.execute(
                """
                SELECT id FROM agent_workflow_rehydrations
                WHERE source_app_key=? AND conversation_id=? AND plan_id=? AND state_fingerprint=?
                """,
                (source, conversation_id, int(plan_id), fingerprint),
            ).fetchone()
        return {
            "version": "v0.56",
            "rehydration_id": int(row["id"]),
            "conversation_id": conversation_id,
            "plan_id": int(plan_id),
            "state_fingerprint": fingerprint,
            "status": "ready",
            "safe_to_continue": True,
            "checkpoint": {
                "plan_status": "approved",
                "workflow_status": "queued",
                "counts": {"members": 2, "queued": 2, "failed": 0},
            },
        }

    automation._rehydrate = fake_rehydrate
    supervised: list[int] = []

    def completed_supervision(source, conversation_id, plan_id, rehydration_id, state_fingerprint, *, owner, current_permissions, max_steps):
        supervised.append(int(rehydration_id))
        return {
            "version": "v0.57",
            "supervision_id": None,
            "status": "stopped",
            "in_progress": False,
            "actions_executed": 1,
            "stop_boundary": "step_budget_exhausted",
            "stop_label": "Recovered exact checkpoint completed once.",
            "steps": [{"sequence": 1, "action": "run"}],
        }

    supervision.continue_workflow = completed_supervision

    # Crash after durable trigger claim but before a checkpoint is persisted.
    first_plan = plan("v058-recover-claimed")
    first = automation.create_automation(
        "owner",
        "v058-recover-claimed",
        first_plan,
        trigger_type="once",
        run_at=(base + timedelta(minutes=1)).isoformat(),
        owner=True,
        now=base,
    )
    raw = automation._claim_time(first["automation_id"], base + timedelta(minutes=2))
    assert raw is not None
    with db() as connection:
        run = connection.execute("SELECT status, rehydration_id FROM agent_workflow_automation_runs WHERE id=?", (raw["run_id"],)).fetchone()
        assert run["status"] == "claimed"
        assert run["rehydration_id"] is None
    before = len(supervised)
    recovered = runtime.run_due_automations(now=base + timedelta(minutes=2, seconds=1))
    assert recovered["recovered"] == 1
    assert recovered["claimed"] == 0
    assert recovered["completed"] == 1
    assert len(supervised) == before + 1
    with db() as connection:
        run = connection.execute("SELECT status, rehydration_id, state_fingerprint FROM agent_workflow_automation_runs WHERE id=?", (raw["run_id"],)).fetchone()
        assert run["status"] == "completed"
        assert run["rehydration_id"] is not None
        assert len(str(run["state_fingerprint"])) == 64
    assert runtime.run_due_automations(now=base + timedelta(minutes=2, seconds=2))["recovered"] == 0
    assert len(supervised) == before + 1

    # Crash after the exact checkpoint is persisted but before v0.57 returns.
    # Recovery must use that stored checkpoint and must not rehydrate to a newer
    # workflow state before asking v0.57 for its idempotent session result.
    second_plan = plan("v058-recover-checkpoint")
    second = automation.create_automation(
        "owner",
        "v058-recover-checkpoint",
        second_plan,
        trigger_type="once",
        run_at=(base + timedelta(minutes=10)).isoformat(),
        owner=True,
        now=base,
    )
    raw2 = automation._claim_time(second["automation_id"], base + timedelta(minutes=11))
    assert raw2 is not None
    reserved2 = runtime._reserve_run(raw2["run_id"], base + timedelta(minutes=11), allow_stale_running=False)
    assert reserved2 is not None
    checkpoint2 = fake_rehydrate("owner", "v058-recover-checkpoint", second_plan, owner=True, permissions=set())
    assert runtime._persist_checkpoint(raw2["run_id"], checkpoint2) is True
    with db() as connection:
        connection.execute(
            "UPDATE agent_workflow_automation_runs SET updated_at='2026-09-11 18:00:00' WHERE id=?",
            (raw2["run_id"],),
        )
    rehydrate_before = len(rehydrate_calls)
    supervised_before = len(supervised)
    recovered2 = runtime.run_due_automations(now=base + timedelta(minutes=13))
    assert recovered2["recovered"] == 1
    assert recovered2["completed"] == 1
    assert len(rehydrate_calls) == rehydrate_before
    assert len(supervised) == supervised_before + 1
    assert supervised[-1] == checkpoint2["rehydration_id"]

    # If v0.57 itself was left in-progress across a crash, v0.58 never starts a
    # different checkpoint. It stops, disables the recurring automation, and
    # requires review rather than risking a second execution path.
    third_plan = plan("v058-recover-in-progress")
    third = automation.create_automation(
        "owner",
        "v058-recover-in-progress",
        third_plan,
        trigger_type="interval",
        every_seconds=60,
        run_at=(base + timedelta(minutes=20)).isoformat(),
        owner=True,
        now=base,
    )
    raw3 = automation._claim_time(third["automation_id"], base + timedelta(minutes=21))
    assert raw3 is not None
    reserved3 = runtime._reserve_run(raw3["run_id"], base + timedelta(minutes=21), allow_stale_running=False)
    assert reserved3 is not None
    checkpoint3 = fake_rehydrate("owner", "v058-recover-in-progress", third_plan, owner=True, permissions=set())
    assert runtime._persist_checkpoint(raw3["run_id"], checkpoint3) is True
    with db() as connection:
        connection.execute(
            "UPDATE agent_workflow_automation_runs SET updated_at='2026-09-11 18:00:00' WHERE id=?",
            (raw3["run_id"],),
        )

    def in_progress_supervision(*args, **kwargs):
        return {
            "version": "v0.57",
            "supervision_id": None,
            "status": "running",
            "in_progress": True,
            "actions_executed": 0,
            "stop_boundary": "",
            "stop_label": "",
            "steps": [],
        }

    supervision.continue_workflow = in_progress_supervision
    conflict = runtime.run_due_automations(now=base + timedelta(minutes=23))
    assert conflict["recovered"] == 1
    assert conflict["conflicts"] == 1
    third_after = automation.list_automations("owner", conversation_id="v058-recover-in-progress")[0]
    assert third_after["enabled"] is False
    assert third_after["last_status"] == "conflict"
    assert "in progress" in third_after["last_error"].lower()

print("HomeServer v0.58 workflow automation crash-window recovery passed")