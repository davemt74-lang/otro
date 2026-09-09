from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-tasks-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db  # noqa: E402
    from app.runtime import app  # noqa: E402
    from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
    from app.services import agent_tools  # noqa: E402
    from app.services.tasks import run_due_reminders, scheduler  # noqa: E402

    with TestClient(app) as client:
        assert scheduler._thread is not None and scheduler._thread.is_alive()
        scheduler.stop()
        assert client.get("/api/v1/health").json()["version"] == "0.18.0"
        assert client.get("/api/v1/status").json()["schema_version"] == 18
        assert client.get("/api/v1/control/tasks").status_code == 401
        assert client.get("/api/v1/control/task-notifications").status_code == 401
        assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200

        now = datetime.now(timezone.utc).replace(microsecond=0)
        reminder_time = now - timedelta(minutes=2)
        due_time = now + timedelta(days=2)
        owner_task = client.post(
            "/api/v1/control/tasks",
            json={
                "title": "Synthetic recurring check",
                "description": "Synthetic reminder content for scheduler regression.",
                "priority": "high",
                "due_at": due_time.isoformat(),
                "remind_at": reminder_time.isoformat(),
                "recurrence": "daily",
                "recurrence_interval": 1,
            },
        )
        assert owner_task.status_code == 200
        owner_task_json = owner_task.json()["task"]
        owner_task_id = owner_task_json["id"]
        assert owner_task_json["created_by_type"] == "owner"

        first_fire = run_due_reminders(now=now)
        assert first_fire == {"fired": 1}
        notifications = client.get("/api/v1/control/task-notifications").json()["items"]
        task_notifications = [item for item in notifications if item["task_id"] == owner_task_id]
        assert len(task_notifications) == 1
        notification = task_notifications[0]
        assert notification["title"] == "Reminder: Synthetic recurring check"
        assert notification["level"] == "warning"

        refreshed = client.get("/api/v1/control/tasks").json()["items"]
        task_after = next(item for item in refreshed if item["id"] == owner_task_id)
        assert task_after["last_reminded_at"] is not None
        next_reminder = datetime.fromisoformat(task_after["remind_at"])
        assert next_reminder > now

        second_fire = run_due_reminders(now=now)
        assert second_fire == {"fired": 0}
        notifications_again = client.get("/api/v1/control/task-notifications").json()["items"]
        assert len([item for item in notifications_again if item["task_id"] == owner_task_id]) == 1

        marked = client.patch(
            f"/api/v1/control/notifications/{notification['id']}",
            json={"read": True, "dismissed": True},
        )
        assert marked.status_code == 200
        assert marked.json()["notification"]["read_at"] is not None
        assert marked.json()["notification"]["dismissed_at"] is not None

        reader_pair = client.post(
            "/api/v1/pairing/request",
            json={
                "app_key": "task-reader",
                "app_name": "Synthetic Task Reader",
                "permissions": ["tasks.read", "notifications.read", "tools.execute"],
            },
        ).json()
        assert client.post("/api/v1/pairing/approve", json={"code": reader_pair["code"]}).status_code == 200
        reader_token = reader_pair["claim_token"]
        reader_headers = {"Authorization": f"Bearer {reader_token}"}

        reader_tasks = client.get("/api/v1/tasks", headers=reader_headers)
        assert reader_tasks.status_code == 200
        assert any(item["id"] == owner_task_id for item in reader_tasks.json()["items"])
        assert client.post(
            "/api/v1/tasks",
            headers=reader_headers,
            json={"title": "Must not be created"},
        ).status_code == 403
        reader_notifications = client.get("/api/v1/notifications", headers=reader_headers)
        assert reader_notifications.status_code == 200

        reader_tools = client.get("/api/v1/tools", headers=reader_headers).json()["items"]
        tools_by_key = {item["key"]: item for item in reader_tools}
        assert tools_by_key["tasks.list"]["available"] is True
        assert tools_by_key["notifications.list"]["available"] is True
        assert tools_by_key["tasks.create"]["available"] is False
        assert tools_by_key["tasks.create"]["missing_permissions"] == ["tasks.write"]
        assert client.post(
            "/api/v1/tools/tasks.list/execute",
            headers=reader_headers,
            json={"arguments": {"query": "Synthetic recurring", "limit": 10}},
        ).status_code == 200
        assert client.post(
            "/api/v1/tools/notifications.list/execute",
            headers=reader_headers,
            json={"arguments": {"unread_only": False, "limit": 10}},
        ).status_code == 200

        writer_pair = client.post(
            "/api/v1/pairing/request",
            json={
                "app_key": "task-writer",
                "app_name": "Synthetic Task Writer",
                "permissions": ["tasks.write"],
            },
        ).json()
        assert client.post("/api/v1/pairing/approve", json={"code": writer_pair["code"]}).status_code == 200
        writer_token = writer_pair["claim_token"]
        writer_headers = {"Authorization": f"Bearer {writer_token}"}
        app_created = client.post(
            "/api/v1/tasks",
            headers=writer_headers,
            json={"title": "Synthetic app-created task", "priority": "normal"},
        )
        assert app_created.status_code == 200
        assert app_created.json()["task"]["source_app_key"] == "task-writer"
        assert app_created.json()["task"]["created_by_type"] == "app"
        assert client.get("/api/v1/tasks", headers=writer_headers).status_code == 403

        assert client.put(
            "/api/v1/control/agent-tools",
            json={"enabled": True, "max_calls": 2, "allow_write_proposals": True},
        ).status_code == 200

        proposal_pair = client.post(
            "/api/v1/pairing/request",
            json={
                "app_key": "task-agent",
                "app_name": "Synthetic Task Agent",
                "permissions": ["tasks.write", "tools.execute"],
            },
        ).json()
        assert client.post("/api/v1/pairing/approve", json={"code": proposal_pair["code"]}).status_code == 200
        proposal_token = proposal_pair["claim_token"]
        proposal_headers = {"Authorization": f"Bearer {proposal_token}"}

        model_schemas = agent_tools.model_tool_schemas(
            {"tasks.write", "tools.execute"},
            owner=False,
            allow_write_proposals=True,
        )
        model_names = {item["function"]["name"] for item in model_schemas}
        assert "homeserver_task_create_request" in model_names
        assert "tasks.create" not in model_names

        private_title = "TASK_PRIVATE_TITLE_746201"
        private_description = "TASK_PRIVATE_DESCRIPTION_395118"
        task_count_before = len(client.get("/api/v1/control/tasks").json()["items"])
        proposal = agent_tools.execute_model_tool(
            "app:task-agent",
            "homeserver_task_create_request",
            {
                "title": private_title,
                "description": private_description,
                "priority": "urgent",
                "due_at": (now + timedelta(days=1)).isoformat(),
                "remind_at": (now + timedelta(hours=3)).isoformat(),
                "recurrence": "none",
            },
            {"tasks.write", "tools.execute"},
            owner=False,
        )
        request_id = proposal["result"]["request_id"]
        assert len(client.get("/api/v1/control/tasks").json()["items"]) == task_count_before

        pending = client.get("/api/v1/control/action-requests?status=pending").json()["items"]
        task_request = next(item for item in pending if item["id"] == request_id)
        assert task_request["action_key"] == "tasks.create"
        assert task_request["arguments"]["title"] == private_title
        assert task_request["arguments"]["description"] == private_description

        app_status = client.get(f"/api/v1/action-requests/{request_id}", headers=proposal_headers)
        assert app_status.status_code == 200
        assert app_status.json()["request"]["status"] == "pending"
        assert "arguments" not in app_status.json()["request"]
        assert private_title not in json.dumps(app_status.json(), ensure_ascii=False)
        assert private_description not in json.dumps(app_status.json(), ensure_ascii=False)

        approved = client.post(f"/api/v1/control/action-requests/{request_id}/approve")
        assert approved.status_code == 200
        assert approved.json()["request"]["status"] == "executed"
        assert client.post(f"/api/v1/control/action-requests/{request_id}/approve").status_code == 409
        tasks_after_approval = client.get("/api/v1/control/tasks").json()["items"]
        assert len(tasks_after_approval) == task_count_before + 1
        approved_task = next(item for item in tasks_after_approval if item["title"] == private_title)
        assert approved_task["priority"] == "urgent"
        assert approved_task["source_app_key"] == "task-agent"
        assert approved_task["created_by_type"] == "agent"

        tool_runs = client.get("/api/v1/control/tool-runs?limit=200").json()["items"]
        activity = client.get("/api/v1/control/activity?limit=200").json()["items"]
        safe_audit = json.dumps({"tool_runs": tool_runs, "activity": activity}, ensure_ascii=False)
        assert private_title not in safe_audit
        assert private_description not in safe_audit
        assert any(item["tool_key"] == "tasks.create.request" for item in tool_runs)
        assert any(item["tool_key"] == "tasks.create" and item["status"] == "completed" for item in tool_runs)

        assert client.put("/api/v1/control/tools/tasks.create", json={"enabled": False}).status_code == 200
        blocked_schemas = agent_tools.model_tool_schemas(
            {"tasks.write", "tools.execute"},
            owner=False,
            allow_write_proposals=True,
        )
        assert "homeserver_task_create_request" not in {item["function"]["name"] for item in blocked_schemas}

    # TestClient shutdown stops router lifespans, but on Windows an SQLite handle
    # can remain momentarily visible to the filesystem. Stop the scheduler again
    # after the ASGI portal closes and wait until the database can be renamed.
    scheduler.stop()
    assert scheduler._thread is None or not scheduler._thread.is_alive()
    db_path = Path(data_dir) / "homeserver.db"
    probe_path = Path(data_dir) / "homeserver.cleanup-probe.db"
    deadline = time.monotonic() + 5.0
    while db_path.exists():
        try:
            db_path.replace(probe_path)
            probe_path.replace(db_path)
            break
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)

print("HomeServer tasks, reminders and notifications test passed")
