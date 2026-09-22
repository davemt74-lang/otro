from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

tmp = tempfile.TemporaryDirectory(prefix="vp3-os-v100-release-")
os.environ["HOMESERVER_DATA_DIR"] = tmp.name
os.environ["VP3_OS_HARDWARE_PROFILE"] = "custom"
os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

from app.database import db, initialize_database  # noqa: E402
from app.services import (  # noqa: E402
    ambient_orchestration,
    backups,
    local_automation,
    release_readiness,
    room_device_automation,
    vp3_os,
)
from app.services.owner_secret import load_or_create_owner_secret  # noqa: E402

initialize_database()
load_or_create_owner_secret()

assert vp3_os.VP3_OS_VERSION == "v1.0"

initial = release_readiness.report()
assert initial["release"] == "v1.0"
assert initial["vp3_os_version"] == "v1.0"
assert initial["production_ready"] is True
assert initial["status"] in {"ready", "degraded"}
assert next(
    item for item in initial["checks"] if item["name"] == "database"
)["status"] == "ready"
assert next(
    item for item in initial["checks"] if item["name"] == "physical_action_governance"
)["status"] == "ready"

room_device_automation.upsert_room("release-office", "Release Office")
room_device_automation.upsert_provider(
    "release-provider",
    "Release Provider",
    "test",
    executable=False,
    status="connected",
)
room_device_automation.upsert_device(
    "release-light",
    "release-provider",
    "light.release",
    "Release Light",
    "light",
    room_key="release-office",
    controllable=True,
    state={"power": "off", "brightness": 50},
)
local_automation.upsert_routine(
    "release-routine",
    "Release Routine",
    approval_mode="ask_every_time",
    steps=[
        {
            "device_key": "release-light",
            "command": "on",
            "arguments": {},
        }
    ],
)
mode = ambient_orchestration.upsert_mode(
    "release-mode",
    "Release Mode",
    routine_key="release-routine",
    room_keys=["release-office"],
    priority=50,
)
assert mode["device_keys"] == ["release-light"]

simulation = ambient_orchestration.simulate_mode("release-mode")
assert simulation["approval_requests_if_activated"] == 1
assert simulation["physical_actions_without_owner_approval"] == 0

requested = ambient_orchestration.activate_mode(
    "release-mode",
    reason="v1.0 release journey",
)
assert requested["state"] == "requested"
assert len(requested["request_ids"]) == 1

with db() as connection:
    request = connection.execute(
        "SELECT status,action_key FROM action_requests WHERE id=?",
        (requested["request_ids"][0],),
    ).fetchone()
    assert request is not None
    assert request["status"] == "pending"
    assert request["action_key"] == "devices.command"
    assert connection.execute(
        "SELECT COUNT(*) FROM automation_device_actions"
    ).fetchone()[0] == 0

initialize_database()
persisted = ambient_orchestration.get_mode("release-mode")
assert persisted["routine_key"] == "release-routine"
persisted_session = ambient_orchestration.get_session(
    int(requested["id"]),
    refresh=False,
)
assert persisted_session["state"] == "requested"

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

with db() as connection:
    assert connection.execute(
        "SELECT COUNT(*) FROM automation_device_actions"
    ).fetchone()[0] == 0
    schema_version = connection.execute(
        "SELECT MAX(version) FROM schema_migrations"
    ).fetchone()[0]
    assert int(schema_version) >= 26

backup = backups.create_backup("vp3-os-v100-release")
assert Path(backup["path"]).is_file()

final = release_readiness.report()
assert final["production_ready"] is True
assert final["status"] == "ready", final
assert all(
    item["status"] == "ready" for item in final["checks"]
), final

connection = sqlite3.connect(Path(tmp.name) / "homeserver.db")
try:
    assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
finally:
    connection.close()

tmp.cleanup()
print("VP3 OS v1.0 production release journey passed")
