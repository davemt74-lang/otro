from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-v243-task-calendar-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db, initialize_database  # noqa: E402
    from app.services import action_policy, approvals, federated_data, pairing, task_calendar_continuity as continuity, tools  # noqa: E402

    initialize_database()

    with db() as connection:
        versions = [row["version"] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()]
    assert versions == list(range(1, 39))
    assert "calendar" in federated_data.DATASETS

    migration = (ROOT / "database" / "migrations" / "035_governed_task_calendar_continuity.sql").read_text(encoding="utf-8")
    for action_key in ("tasks.update", "tasks.delete", "calendar.create", "calendar.update", "calendar.delete"):
        assert action_key in migration
    assert "app_permissions" not in migration

    request = pairing.create_pairing_request(
        "vp3", "VP3", ["tools.execute", "tasks.read", "tasks.write", "events.read", "events.write"]
    )
    approved = pairing.approve_pairing_request(request["request_id"])
    assert approved is not None

    tool_items = {
        item["key"]: item
        for item in tools.list_tools({"tools.execute", "tasks.read", "tasks.write", "events.read", "events.write"})
    }
    for key in ("tasks.list", "tasks.create", "tasks.update", "tasks.delete", "calendar.list", "calendar.create", "calendar.update", "calendar.delete"):
        assert key in tool_items
        assert tool_items[key]["available"] is True

    assert action_policy.default_mode("tasks.create") == action_policy.APPROVAL_REQUIRED
    assert action_policy.default_mode("tasks.update") == action_policy.APPROVAL_REQUIRED
    assert action_policy.default_mode("tasks.delete") == action_policy.APPROVAL_REQUIRED
    assert action_policy.SAFE_AUTOMATIC not in action_policy.allowed_modes("tasks.delete")
    assert action_policy.default_mode("calendar.create") == action_policy.APPROVAL_REQUIRED
    assert action_policy.SAFE_AUTOMATIC not in action_policy.allowed_modes("calendar.delete")

    create_request = approvals.create_task_create_request(
        "app:vp3",
        {
            "mutation_id": "task-create-001",
            "title": "Prepare Phoenix launch",
            "description": "Private task details must not appear in approval metadata.",
            "priority": "high",
            "due_at": "2026-10-01T17:00:00-07:00",
        },
        owner=False,
    )
    request_id = str(create_request["result"]["request_id"])
    with db() as connection:
        queued = connection.execute("SELECT arguments_json,arguments_meta_json FROM action_requests WHERE id=?", (request_id,)).fetchone()
    assert queued is not None
    assert "Prepare Phoenix launch" in queued["arguments_json"]
    assert "Private task details" in queued["arguments_json"]
    assert "Prepare Phoenix launch" not in queued["arguments_meta_json"]
    assert "Private task details" not in queued["arguments_meta_json"]
    assert '"title_length"' in queued["arguments_meta_json"]

    executed = approvals.approve_request(request_id)
    assert executed["status"] == "executed"
    tasks = continuity.list_federated_tasks(q="Phoenix")
    assert len(tasks) == 1
    task = tasks[0]
    assert task["authority_source"] == "homeserver"
    assert task["canonical_id"].startswith("fd24_")

    update_request = approvals.create_task_update_request(
        "app:vp3",
        {
            "canonical_id": task["canonical_id"],
            "mutation_id": "task-update-001",
            "expected_revision": task["record_revision"],
            "status": "completed",
        },
        owner=False,
    )
    approvals.approve_request(str(update_request["result"]["request_id"]))
    updated = continuity.get_federated_task_by_canonical(task["canonical_id"])
    assert updated is not None and updated["status"] == "completed"
    assert updated["record_revision"] != task["record_revision"]

    try:
        continuity.update_federated_task(
            {
                "canonical_id": task["canonical_id"],
                "mutation_id": "task-update-stale",
                "expected_revision": task["record_revision"],
                "priority": "urgent",
            },
            source_app_key="app:vp3",
        )
        raise AssertionError("stale task revision should fail")
    except continuity.TaskCalendarContinuityError as exc:
        assert exc.status_code == 409

    cloud_task = federated_data.canonical_id("vp3_cloud", "tasks", "task:999")
    try:
        continuity.normalize_task_update_arguments(
            {"canonical_id": cloud_task, "mutation_id": "task-cloud-001", "expected_revision": "0" * 64, "status": "completed"}
        )
        continuity.update_federated_task(
            {"canonical_id": cloud_task, "mutation_id": "task-cloud-001", "expected_revision": "0" * 64, "status": "completed"},
            source_app_key="app:vp3",
        )
        raise AssertionError("Cloud-authoritative task must not mutate on HomeServer")
    except continuity.TaskCalendarContinuityError as exc:
        assert exc.status_code in {404, 409}

    calendar_request = approvals.create_calendar_create_request(
        "app:vp3",
        {
            "mutation_id": "calendar-create-001",
            "title": "Studio session",
            "description": "Private calendar notes stay out of approval metadata.",
            "location": "Studio A",
            "start_at": "2026-10-03T18:00:00-07:00",
            "end_at": "2026-10-03T20:00:00-07:00",
            "timezone": "America/Phoenix",
        },
        owner=False,
    )
    calendar_request_id = str(calendar_request["result"]["request_id"])
    with db() as connection:
        queued = connection.execute("SELECT arguments_json,arguments_meta_json FROM action_requests WHERE id=?", (calendar_request_id,)).fetchone()
    assert "Studio session" in queued["arguments_json"]
    assert "Studio session" not in queued["arguments_meta_json"]
    assert "Private calendar notes" not in queued["arguments_meta_json"]
    approvals.approve_request(calendar_request_id)

    events = continuity.list_federated_calendar(from_at="2026-10-01T00:00:00Z", to_at="2026-10-10T00:00:00Z")
    assert len(events) == 1
    event = events[0]
    assert event["authority_source"] == "homeserver"

    update_calendar = approvals.create_calendar_update_request(
        "app:vp3",
        {
            "canonical_id": event["canonical_id"],
            "mutation_id": "calendar-update-001",
            "expected_revision": event["record_revision"],
            "location": "Studio B",
        },
        owner=False,
    )
    approvals.approve_request(str(update_calendar["result"]["request_id"]))
    updated_event = continuity.list_federated_calendar()[0]
    assert updated_event["location"] == "Studio B"

    replay_payload = {
        "mutation_id": "calendar-replay-001",
        "title": "Replay safe",
        "start_at": "2026-10-04T18:00:00Z",
        "end_at": "2026-10-04T19:00:00Z",
        "timezone": "UTC",
    }
    first = continuity.create_federated_calendar(replay_payload, source_app_key="app:vp3")
    second = continuity.create_federated_calendar(replay_payload, source_app_key="app:vp3")
    assert first["canonical_id"] == second["canonical_id"]

    delete_request = approvals.create_calendar_delete_request(
        "app:vp3",
        {
            "canonical_id": updated_event["canonical_id"],
            "mutation_id": "calendar-delete-001",
            "expected_revision": updated_event["record_revision"],
        },
        owner=False,
    )
    approvals.approve_request(str(delete_request["result"]["request_id"]))
    assert all(item["canonical_id"] != updated_event["canonical_id"] for item in continuity.list_federated_calendar())

    tasks_api = (ROOT / "app" / "tasks_api.py").read_text(encoding="utf-8")
    assert "create_task_create_request" in tasks_api
    assert "create_task_update_request" in tasks_api
    assert "client_task_delete" in tasks_api

print("HomeServer v2.4 Section 4 task/calendar continuity: PASS")
