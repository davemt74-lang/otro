from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

tmp = tempfile.TemporaryDirectory(prefix="vp3-os-v090-orchestration-")
os.environ["HOMESERVER_DATA_DIR"] = tmp.name
os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

from app.database import db, initialize_database  # noqa: E402
from app.services import (  # noqa: E402
    ambient_orchestration,
    automation_intelligence,
    local_automation,
    room_device_automation,
)

initialize_database()

room_device_automation.upsert_room("office", "Office")
room_device_automation.upsert_room("living-room", "Living Room")
room_device_automation.upsert_provider(
    "test-provider",
    "Test Provider",
    "test",
    executable=False,
    status="connected",
)
desk = room_device_automation.upsert_device(
    "desk-light",
    "test-provider",
    "light.desk",
    "Desk Light",
    "light",
    room_key="office",
    controllable=True,
    state={"power": "off", "brightness": 40},
)
fan = room_device_automation.upsert_device(
    "office-fan",
    "test-provider",
    "fan.office",
    "Office Fan",
    "fan",
    room_key="office",
    controllable=True,
    state={"power": "off"},
)
lamp = room_device_automation.upsert_device(
    "living-lamp",
    "test-provider",
    "light.living",
    "Living Lamp",
    "light",
    room_key="living-room",
    controllable=True,
    state={"power": "off"},
)

local_automation.upsert_routine(
    "focus-routine",
    "Focus Routine",
    approval_mode="ask_every_time",
    steps=[
        {"device_key": "desk-light", "command": "on", "arguments": {}},
        {"device_key": "office-fan", "command": "on", "arguments": {}},
    ],
)
local_automation.upsert_routine(
    "lower-routine",
    "Lower Priority Routine",
    approval_mode="ask_every_time",
    steps=[
        {"device_key": "desk-light", "command": "off", "arguments": {}},
    ],
)
local_automation.upsert_routine(
    "meeting-routine",
    "Meeting Routine",
    approval_mode="ask_every_time",
    steps=[
        {
            "device_key": "desk-light",
            "command": "set_brightness",
            "arguments": {"brightness": 25},
        },
    ],
)
local_automation.upsert_routine(
    "evening-routine",
    "Evening Routine",
    approval_mode="ask_every_time",
    steps=[
        {"device_key": "living-lamp", "command": "on", "arguments": {}},
    ],
)
local_automation.upsert_routine(
    "suggest-only-routine",
    "Suggest Only",
    approval_mode="suggest_only",
    steps=[
        {"device_key": "living-lamp", "command": "off", "arguments": {}},
    ],
)

focus = ambient_orchestration.upsert_mode(
    "focus",
    "Focus",
    routine_key="focus-routine",
    priority=50,
    suggest_trigger={"presence": "present", "meeting": "inactive"},
)
lower = ambient_orchestration.upsert_mode(
    "quiet",
    "Quiet",
    routine_key="lower-routine",
    priority=40,
)
meeting = ambient_orchestration.upsert_mode(
    "meeting",
    "Meeting",
    routine_key="meeting-routine",
    priority=80,
)
evening = ambient_orchestration.upsert_mode(
    "evening",
    "Evening",
    routine_key="evening-routine",
    priority=30,
    suggest_trigger={"presence": "present"},
)

assert focus["room_keys"] == ["office"]
assert focus["device_keys"] == ["desk-light", "office-fan"]
assert meeting["priority"] == 80

try:
    ambient_orchestration.upsert_mode(
        "unsafe-mode",
        "Unsafe Mode",
        routine_key="suggest-only-routine",
    )
except ambient_orchestration.OrchestrationError as exc:
    assert exc.status_code == 409
    assert "ask_every_time" in str(exc)
else:
    raise AssertionError("Mode accepted a suggest_only routine")

automation_intelligence.record_context_event(
    "presence",
    "present",
    source_kind="ambient",
    metadata={"source": "presence_sensor"},
)

suggestions = ambient_orchestration.evaluate_mode_suggestions()
focus_suggestion = next(
    item for item in suggestions if item["mode_key"] == "focus"
)
evening_suggestion = next(
    item for item in suggestions if item["mode_key"] == "evening"
)
assert focus_suggestion["state"] == "suggested"
assert evening_suggestion["state"] == "suggested"

with db() as connection:
    assert connection.execute(
        "SELECT COUNT(*) FROM action_requests"
    ).fetchone()[0] == 0
    assert connection.execute(
        "SELECT COUNT(*) FROM automation_device_actions"
    ).fetchone()[0] == 0

simulation = ambient_orchestration.simulate_mode("focus")
assert simulation["device_count"] == 2
assert simulation["approval_requests_if_activated"] == 2
assert simulation["physical_actions_without_owner_approval"] == 0
assert simulation["conflicts"] == []

requested = ambient_orchestration.accept_suggestion(
    int(focus_suggestion["id"])
)
assert requested["state"] == "requested"
assert len(requested["request_ids"]) == 2

with db() as connection:
    requests = connection.execute(
        """
        SELECT id,status,action_key FROM action_requests
        WHERE id IN (?,?)
        ORDER BY id
        """,
        tuple(requested["request_ids"]),
    ).fetchall()
    assert len(requests) == 2
    assert all(row["status"] == "pending" for row in requests)
    assert all(row["action_key"] == "devices.command" for row in requests)
    assert connection.execute(
        "SELECT COUNT(*) FROM automation_device_actions"
    ).fetchone()[0] == 0
    connection.execute(
        """
        UPDATE action_requests
        SET status='executed',decided_at=CURRENT_TIMESTAMP,
            executed_at=CURRENT_TIMESTAMP
        WHERE id IN (?,?)
        """,
        tuple(requested["request_ids"]),
    )

active_focus = ambient_orchestration.refresh_session(
    int(requested["id"])
)
assert active_focus["state"] == "active"

lower_sim = ambient_orchestration.simulate_mode("quiet")
assert len(lower_sim["conflicts"]) == 1
assert lower_sim["can_supersede"] is False
try:
    ambient_orchestration.activate_mode(
        "quiet",
        supersede_conflicts=True,
    )
except ambient_orchestration.OrchestrationError as exc:
    assert exc.status_code == 409
    assert "priority" in str(exc).lower()
else:
    raise AssertionError("Lower-priority mode superseded active Focus mode")

high = ambient_orchestration.activate_mode(
    "meeting",
    reason="Owner explicitly selected Meeting mode.",
    supersede_conflicts=True,
)
assert high["state"] == "requested"
assert len(high["request_ids"]) == 1
assert ambient_orchestration.get_session(
    int(active_focus["id"]), refresh=False
)["state"] == "suspended"

with db() as connection:
    connection.execute(
        """
        UPDATE action_requests
        SET status='denied',decided_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (high["request_ids"][0],),
    )
failed_high = ambient_orchestration.refresh_session(int(high["id"]))
assert failed_high["state"] == "failed"

dismissed = ambient_orchestration.dismiss_suggestion(
    int(evening_suggestion["id"])
)
assert dismissed["state"] == "ended"

# Activate Evening directly, mark its governed request executed, then simulate
# a manual owner change to verify active mode suspension.
evening_requested = ambient_orchestration.activate_mode(
    "evening",
    reason="Owner selected Evening mode.",
)
with db() as connection:
    connection.execute(
        """
        UPDATE action_requests
        SET status='executed',decided_at=CURRENT_TIMESTAMP,
            executed_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (evening_requested["request_ids"][0],),
    )
evening_active = ambient_orchestration.refresh_session(
    int(evening_requested["id"])
)
assert evening_active["state"] == "active"

override_at = datetime.now(timezone.utc) + timedelta(seconds=1)
with db() as connection:
    connection.execute(
        """
        INSERT INTO automation_device_actions(
            device_id,source_app_key,command,arguments_meta_json,
            before_state_json,after_state_json,status,created_at,completed_at
        ) VALUES (?,?,?,?,?,?,'completed',?,?)
        """,
        (
            int(lamp["id"]),
            "owner",
            "off",
            '{"command":"off","argument_count":0}',
            '{"power":"on"}',
            '{"power":"off"}',
            override_at.isoformat(),
            override_at.isoformat(),
        ),
    )
manual_suspended = ambient_orchestration.refresh_session(
    int(evening_active["id"])
)
assert manual_suspended["state"] == "suspended"

actions_before_end = room_device_automation.list_actions(100)
ended = ambient_orchestration.end_session(
    int(manual_suspended["id"])
)
assert ended["state"] == "ended"
assert room_device_automation.list_actions(100) == actions_before_end

conflicts = ambient_orchestration.list_conflicts(20)
assert any(item["resolution"] == "blocked" for item in conflicts)
assert any(item["resolution"] == "superseded" for item in conflicts)

overview = ambient_orchestration.overview()
assert overview["governance"]["ambient_auto_activation"] is False
assert overview["governance"]["owner_activation_required"] is True
assert overview["governance"]["ending_mode_reverts_device_state"] is False

with db() as connection:
    cognitive_count = connection.execute(
        """
        SELECT COUNT(*) FROM cognitive_events
        WHERE event_type LIKE 'orchestration.mode_%'
          AND privacy_scope='private'
          AND memory_candidate=0
        """
    ).fetchone()[0]
    assert cognitive_count >= 4

tmp.cleanup()
print("VP3 OS v0.90 ambient orchestration runtime passed")
