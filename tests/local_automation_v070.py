from __future__ import annotations

import tempfile
from pathlib import Path

from app import config
from app.database import initialize_database
from app.services import approvals, local_automation, room_device_automation

tmp = tempfile.TemporaryDirectory()
config.settings.data_dir = Path(tmp.name)
config.settings.db_path = Path(tmp.name) / "homeserver.db"
initialize_database()

room_device_automation.upsert_room("office", "Office")
room_device_automation.upsert_provider(
    "test-provider",
    "Test Provider",
    "test",
    executable=True,
    status="connected",
)
room_device_automation.upsert_device(
    "desk-light",
    "test-provider",
    "light.desk",
    "Desk Light",
    "light",
    room_key="office",
    controllable=True,
    state={"power": "off", "brightness": 20},
)

routine = local_automation.upsert_routine(
    "workday-start",
    "Workday Start",
    approval_mode="ask_every_time",
    steps=[
        {"device_key": "desk-light", "command": "on", "arguments": {}},
        {"device_key": "desk-light", "command": "set_brightness", "arguments": {"brightness": 60}},
    ],
)
assert routine["approval_mode"] == "ask_every_time"
assert len(routine["steps"]) == 2

manual = local_automation.upsert_rule(
    "manual-workday",
    "Manual Workday",
    routine_key="workday-start",
    trigger_kind="manual",
    cooldown_seconds=0,
)
manual_result = local_automation.evaluate_rule("manual-workday", force_manual=True)
assert manual_result["fired"] is True
assert manual_result["status"] == "requested"
assert len(manual_result["request_ids"]) == 2

pending = approvals.list_requests("pending", 10)
device_requests = [item for item in pending if item["action_key"] == "devices.command"]
assert len(device_requests) == 2
assert all(item["source_app_key"] == "automation:local-rule" for item in device_requests)

# No rule path executes a driver. Requests stay pending until local owner approval.
assert room_device_automation.list_actions(10) == []

suggest_routine = local_automation.upsert_routine(
    "suggest-lamp",
    "Suggest Lamp",
    approval_mode="suggest_only",
    steps=[{"device_key": "desk-light", "command": "off", "arguments": {}}],
)
suggest_result = local_automation.run_routine("suggest-lamp", source_kind="test")
assert suggest_result["status"] == "suggested"
assert len(suggest_result["suggestion_ids"]) == 1
assert len(room_device_automation.list_suggestions("suggested", 10)) == 1

state_rule = local_automation.upsert_rule(
    "when-brightness-20",
    "When Brightness 20",
    routine_key="suggest-lamp",
    trigger_kind="device_state",
    trigger={"device_key": "desk-light", "field": "brightness", "operator": "eq", "value": 20},
    cooldown_seconds=0,
)
first = local_automation.evaluate_rule("when-brightness-20")
second = local_automation.evaluate_rule("when-brightness-20")
assert first["fired"] is True
assert second["fired"] is False
assert second["reason"] == "trigger_not_met"

rule = local_automation.get_rule("when-brightness-20")
assert rule["last_condition"] is True

settings = local_automation.update_settings(
    enabled=False,
    poll_seconds=20,
    max_actions_per_run=8,
    max_rule_fires_per_minute=10,
)
assert settings["enabled"] is False
assert local_automation.evaluate_due_rules() == []

for execution in local_automation.list_executions(20):
    assert execution["status"] in {"suggested", "requested", "skipped", "failed"}

print("VP3 OS v0.70 local automation runtime passed")
