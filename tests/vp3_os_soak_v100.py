from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

tmp = tempfile.TemporaryDirectory(prefix="vp3-os-v100-soak-")
os.environ["HOMESERVER_DATA_DIR"] = tmp.name
os.environ["VP3_OS_HARDWARE_PROFILE"] = "custom"
os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

from app.database import db, initialize_database  # noqa: E402
from app.services import ambient_orchestration, local_automation, room_device_automation  # noqa: E402

initialize_database()
room_device_automation.upsert_room("soak-room", "Soak Room")
room_device_automation.upsert_provider(
    "soak-provider",
    "Soak Provider",
    "test",
    executable=False,
    status="connected",
)
room_device_automation.upsert_device(
    "soak-light",
    "soak-provider",
    "light.soak",
    "Soak Light",
    "light",
    room_key="soak-room",
    controllable=True,
    state={"power": "off"},
)
local_automation.upsert_routine(
    "soak-routine",
    "Soak Routine",
    approval_mode="ask_every_time",
    steps=[
        {"device_key": "soak-light", "command": "on", "arguments": {}},
    ],
)
ambient_orchestration.upsert_mode(
    "soak-mode",
    "Soak Mode",
    routine_key="soak-routine",
    priority=50,
)

CYCLES = 50
for cycle in range(CYCLES):
    requested = ambient_orchestration.activate_mode(
        "soak-mode",
        reason=f"bounded soak cycle {cycle + 1}",
    )
    assert requested["state"] == "requested"
    assert len(requested["request_ids"]) == 1

    with db() as connection:
        connection.execute(
            """
            UPDATE action_requests
            SET status='executed',
                decided_at=CURRENT_TIMESTAMP,
                executed_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (requested["request_ids"][0],),
        )

    active = ambient_orchestration.refresh_session(int(requested["id"]))
    assert active["state"] == "active"
    ended = ambient_orchestration.end_session(int(active["id"]))
    assert ended["state"] == "ended"

    if (cycle + 1) % 10 == 0:
        initialize_database()

with db() as connection:
    assert connection.execute(
        "SELECT COUNT(*) FROM orchestration_mode_sessions"
    ).fetchone()[0] == CYCLES
    assert connection.execute(
        "SELECT COUNT(*) FROM action_requests"
    ).fetchone()[0] == CYCLES
    assert connection.execute(
        "SELECT COUNT(*) FROM automation_device_actions"
    ).fetchone()[0] == 0
    assert connection.execute(
        """
        SELECT COUNT(*) FROM orchestration_mode_sessions
        WHERE state IN ('suggested','requested','active','suspended')
        """
    ).fetchone()[0] == 0

connection = sqlite3.connect(Path(tmp.name) / "homeserver.db")
try:
    assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
finally:
    connection.close()

tmp.cleanup()
print("VP3 OS v1.0 bounded orchestration soak passed")
