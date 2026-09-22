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

tmp = tempfile.TemporaryDirectory(prefix="vp3-os-v080-intelligence-")
os.environ["HOMESERVER_DATA_DIR"] = tmp.name
os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

from app.database import db, initialize_database  # noqa: E402
from app.services import automation_intelligence, local_automation, room_device_automation  # noqa: E402

initialize_database()

room_device_automation.upsert_room("office", "Office")
room_device_automation.upsert_provider(
    "test-provider",
    "Test Provider",
    "test",
    executable=False,
    status="connected",
)
light = room_device_automation.upsert_device(
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

now = datetime.now(timezone.utc)
presence_times = []
with db() as connection:
    for days_ago in (1, 2, 3, 4):
        day = (now - timedelta(days=days_ago)).date()
        light_at = datetime(
            day.year, day.month, day.day, 8, 5, tzinfo=timezone.utc
        )
        fan_at = datetime(
            day.year, day.month, day.day, 8, 10, tzinfo=timezone.utc
        )
        connection.execute(
            """
            INSERT INTO automation_device_actions(
                device_id,source_app_key,command,arguments_meta_json,
                before_state_json,after_state_json,status,created_at,completed_at
            ) VALUES (?,?,?,?,?,?, 'completed', ?, ?)
            """,
            (
                int(light["id"]),
                "owner",
                "on",
                json.dumps(
                    {
                        "device_key": "desk-light",
                        "category": "light",
                        "room_key": "office",
                        "command": "on",
                        "argument_count": 0,
                    },
                    separators=(",", ":"),
                ),
                '{"power":"off"}',
                '{"power":"on"}',
                light_at.isoformat(),
                light_at.isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO automation_device_actions(
                device_id,source_app_key,command,arguments_meta_json,
                before_state_json,after_state_json,status,created_at,completed_at
            ) VALUES (?,?,?,?,?,?, 'completed', ?, ?)
            """,
            (
                int(fan["id"]),
                "owner",
                "on",
                json.dumps(
                    {
                        "device_key": "office-fan",
                        "category": "fan",
                        "room_key": "office",
                        "command": "on",
                        "argument_count": 0,
                    },
                    separators=(",", ":"),
                ),
                '{"power":"off"}',
                '{"power":"on"}',
                fan_at.isoformat(),
                fan_at.isoformat(),
            ),
        )
        presence_times.append(
            (light_at - timedelta(minutes=10)).isoformat()
        )

    # Repeated clicks on one day must not inflate a learned daily pattern.
    noisy_day = (now - timedelta(days=1)).date()
    for minute in (12, 18, 24):
        noisy_at = datetime(
            noisy_day.year,
            noisy_day.month,
            noisy_day.day,
            8,
            minute,
            tzinfo=timezone.utc,
        )
        connection.execute(
            """
            INSERT INTO automation_device_actions(
                device_id,source_app_key,command,arguments_meta_json,
                before_state_json,after_state_json,status,created_at,completed_at
            ) VALUES (?,?,?,?,?,?, 'completed', ?, ?)
            """,
            (
                int(light["id"]),
                "owner",
                "on",
                '{"command":"on","argument_count":0}',
                '{"power":"off"}',
                '{"power":"on"}',
                noisy_at.isoformat(),
                noisy_at.isoformat(),
            ),
        )

    # This action must not become learning evidence because it came from
    # the automation runtime itself.
    auto_at = now - timedelta(hours=2)
    connection.execute(
        """
        INSERT INTO automation_device_actions(
            device_id,source_app_key,command,arguments_meta_json,
            before_state_json,after_state_json,status,created_at,completed_at
        ) VALUES (?,?,?,?,?,?, 'completed', ?, ?)
        """,
        (
            int(light["id"]),
            "automation:local-rule",
            "off",
            '{"command":"off","argument_count":0}',
            '{"power":"on"}',
            '{"power":"off"}',
            auto_at.isoformat(),
            auto_at.isoformat(),
        ),
    )

for occurred_at in presence_times:
    automation_intelligence.record_context_event(
        "presence",
        "present",
        source_kind="ambient",
        metadata={
            "source": "presence_sensor",
            "raw_audio": "must-not-persist",
        },
        occurred_at=occurred_at,
    )

settings = automation_intelligence.get_settings()
assert settings["enabled"] is True
assert settings["min_occurrences"] == 4

scan = automation_intelligence.scan_patterns()
assert scan["enabled"] is True
assert scan["actions_considered"] == 11
assert scan["patterns_found"] >= 3
assert scan["proposals"]

proposals = automation_intelligence.list_proposals("proposed", 20)
desk_single = next(
    item
    for item in proposals
    if item["pattern_kind"] == "time_action"
    and item["draft_routine"]["steps"][0]["device_key"] == "desk-light"
)
assert desk_single["occurrence_count"] == 4
sequence = next(
    item for item in proposals if item["pattern_kind"] == "action_sequence"
)
assert sequence["occurrence_count"] == 4
assert len(sequence["draft_routine"]["steps"]) == 2
assert sequence["draft_routine"]["enabled"] is False
assert sequence["draft_rule"]["enabled"] is False
assert sequence["simulation"]["physical_actions_without_owner_approval"] == 0
assert sequence["simulation"]["approval_requests_if_enabled"] == 8
assert sequence["evidence"]["nearby_context"]

contexts = automation_intelligence.list_context_events(20)
assert len(contexts) == 4
assert all("raw_audio" not in item["metadata"] for item in contexts)
assert all(item["state"] == "present" for item in contexts)

simulation = automation_intelligence.simulate_proposal(int(sequence["id"]))
assert simulation["historical_occurrences"] == 4
assert simulation["physical_actions_without_owner_approval"] == 0

materialized = automation_intelligence.materialize_proposal(int(sequence["id"]))
assert materialized["status"] == "materialized"
routine = local_automation.get_routine(
    materialized["materialized_routine_key"]
)
rule = local_automation.get_rule(materialized["materialized_rule_key"])
assert routine["enabled"] is False
assert rule["enabled"] is False

with db() as connection:
    assert connection.execute(
        "SELECT COUNT(*) FROM action_requests"
    ).fetchone()[0] == 0
    assert connection.execute(
        "SELECT COUNT(*) FROM automation_device_actions"
    ).fetchone()[0] == 12

active = automation_intelligence.enable_materialized_proposal(
    int(sequence["id"])
)
assert active["status"] == "active"
assert local_automation.get_routine(
    active["materialized_routine_key"]
)["enabled"] is True
assert local_automation.get_rule(
    active["materialized_rule_key"]
)["enabled"] is True

with db() as connection:
    # Enabling a learned rule still does not execute anything or create an
    # approval request. v0.70 must evaluate later and v0.60 approval still wins.
    assert connection.execute(
        "SELECT COUNT(*) FROM action_requests"
    ).fetchone()[0] == 0
    assert connection.execute(
        "SELECT COUNT(*) FROM automation_device_actions"
    ).fetchone()[0] == 12

remaining = [
    item
    for item in automation_intelligence.list_proposals("proposed", 20)
    if int(item["id"]) != int(sequence["id"])
]
assert len(remaining) >= 2

collision = remaining[0]
collision_routine = collision["draft_routine"]
local_automation.upsert_routine(
    collision_routine["routine_key"],
    "Owner-created collision guard",
    enabled=False,
    approval_mode="ask_every_time",
    steps=collision_routine["steps"],
)
try:
    automation_intelligence.materialize_proposal(int(collision["id"]))
except automation_intelligence.AutomationIntelligenceError as exc:
    assert exc.status_code == 409
    assert "refusing to overwrite" in str(exc)
else:
    raise AssertionError("Learned draft overwrote an existing owner routine")

other = remaining[1]
suppressed = automation_intelligence.dismiss_proposal(
    int(other["id"]),
    note="Do not suggest this again yet.",
)
assert suppressed["status"] == "suppressed"
assert suppressed["suppression_until"]

rescan = automation_intelligence.scan_patterns()
assert all(
    int(item["id"]) != int(other["id"])
    for item in rescan["proposals"]
)

with db() as connection:
    feedback = connection.execute(
        """
        SELECT decision FROM automation_proposal_feedback
        WHERE proposal_id=? ORDER BY id
        """,
        (int(sequence["id"]),),
    ).fetchall()
    assert [row["decision"] for row in feedback] == [
        "materialized",
        "enabled",
    ]
    cognitive = connection.execute(
        """
        SELECT COUNT(*) FROM cognitive_events
        WHERE event_type='automation.opportunity'
          AND privacy_scope='private'
          AND memory_candidate=0
        """
    ).fetchone()[0]
    assert cognitive >= 1
    context_count = connection.execute(
        "SELECT COUNT(*) FROM automation_context_events"
    ).fetchone()[0]

automation_intelligence.update_settings(
    enabled=False,
    scan_interval_seconds=3600,
    lookback_days=21,
    min_occurrences=4,
    time_bucket_minutes=30,
    max_proposals_per_scan=12,
    suppression_days=30,
)
not_recorded = automation_intelligence.record_context_event(
    "presence",
    "absent",
    source_kind="ambient",
)
assert not_recorded["recorded"] is False
with db() as connection:
    assert connection.execute(
        "SELECT COUNT(*) FROM automation_context_events"
    ).fetchone()[0] == context_count
assert automation_intelligence.scan_patterns()["enabled"] is False

tmp.cleanup()
print("VP3 OS v0.80 automation intelligence runtime passed")
