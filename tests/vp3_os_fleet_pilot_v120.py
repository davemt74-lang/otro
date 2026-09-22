from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

tmp = tempfile.TemporaryDirectory(prefix="vp3-os-v120-pilot-")
os.environ["HOMESERVER_DATA_DIR"] = tmp.name
os.environ["VP3_OS_HARDWARE_PROFILE"] = "custom"
os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

from app.database import db, initialize_database  # noqa: E402
from app.services import fleet_management  # noqa: E402

initialize_database()

with db() as connection:
    connection.execute(
        """
        INSERT INTO paired_apps(app_key,name,token_hash,status)
        VALUES ('vp3-fleet-control','VP3 Fleet Control','pilot-hash','active')
        """
    )
    app_id = int(connection.execute(
        "SELECT id FROM paired_apps WHERE app_key='vp3-fleet-control'"
    ).fetchone()["id"])
    for permission in ("fleet.read", "fleet.manage", "fleet.telemetry"):
        connection.execute(
            "INSERT INTO app_permissions(paired_app_id,permission,allowed) VALUES (?,?,1)",
            (app_id, permission),
        )

fleet_management.update_settings(
    enabled=True,
    controller_app_key="vp3-fleet-control",
    remote_diagnostics=True,
    remote_update_requests=True,
    remote_support_summary=True,
    stale_after_seconds=300,
    rollout_failure_threshold=2,
)

now = datetime.now(timezone.utc)
fixtures = [
    ("1", "Pilot Healthy", "node", "pilot", "ready", "passed", "ready", "ok", 0),
    ("2", "Pilot Rollback", "desk", "pilot", "ready", "passed", "ready", "ok", 0),
    ("3", "Staged Failed", "studio", "staged", "ready", "passed", "ready", "ok", 0),
    ("4", "Staged Degraded", "team-node", "staged", "degraded", "degraded", "stale", "low", 1),
    ("5", "Broad Offline", "pocket", "broad", "ready", "passed", "ready", "ok", 0),
]
for digit, label, profile, ring, state, cert, backup, storage, watchdog in fixtures:
    fleet_management.record_checkin(
        {
            "device_id": "hs-" + digit * 24,
            "label": label,
            "profile_key": profile,
            "os_version": "v1.1",
            "release_channel": "stable",
            "rollout_ring": ring,
            "commissioning_state": state,
            "certification_result": cert,
            "privacy_fault": False,
            "update_status": "idle",
            "backup_state": backup,
            "storage_state": storage,
            "watchdog_failures": watchdog,
            "reported_at": now.isoformat(),
        }
    )

with db() as connection:
    connection.execute(
        "UPDATE vp3_fleet_inventory SET last_seen_at=? WHERE device_id=?",
        (
            (now - timedelta(hours=1)).isoformat(),
            "hs-" + "5" * 24,
        ),
    )

inventory = fleet_management.list_inventory()
assert len(inventory) == 5
assert next(item for item in inventory if item["label"] == "Pilot Healthy")["health"] == "healthy"
degraded = next(item for item in inventory if item["label"] == "Staged Degraded")
assert degraded["health"] == "warning"
assert "commissioning_degraded" in degraded["issues"]
assert "certification_degraded" in degraded["issues"]
offline = next(item for item in inventory if item["label"] == "Broad Offline")
assert offline["health"] == "critical"
assert offline["online"] is False

pilot = fleet_management.create_rollout("v1.2", "stable", "pilot", 2)
staged = fleet_management.create_rollout("v1.2", "stable", "staged", 2)
broad = fleet_management.create_rollout("v1.2", "stable", "broad", 2)
assert pilot["eligible_devices"] == 2
assert staged["eligible_devices"] == 4
assert broad["eligible_devices"] == 5

broad = fleet_management.set_rollout_status(broad["id"], "active")
broad = fleet_management.record_rollout_outcome(
    broad["id"], "hs-" + "1" * 24, "healthy", "health_check_passed"
)
assert broad["status"] == "active"
broad = fleet_management.record_rollout_outcome(
    broad["id"], "hs-" + "4" * 24, "degraded", "hardware_degraded"
)
assert broad["status"] == "active"
broad = fleet_management.record_rollout_outcome(
    broad["id"], "hs-" + "5" * 24, "offline", "device_offline"
)
assert broad["status"] == "active"
broad = fleet_management.record_rollout_outcome(
    broad["id"], "hs-" + "3" * 24, "failed", "installer_failed"
)
assert broad["status"] == "active"
assert broad["counts"]["failures"] == 1
broad = fleet_management.record_rollout_outcome(
    broad["id"], "hs-" + "2" * 24, "rolled_back", "health_check_failed"
)
assert broad["status"] == "paused"
assert broad["pause_reason"] == "failure_threshold"
assert broad["counts"]["healthy"] == 1
assert broad["counts"]["degraded"] == 1
assert broad["counts"]["offline"] == 1
assert broad["counts"]["failed"] == 1
assert broad["counts"]["rolled_back"] == 1
assert broad["counts"]["failures"] == 2

alerts = fleet_management.fleet_alerts()
assert any(item["issue"] == "rollout_paused" and item["rollout_id"] == broad["id"] for item in alerts)
assert any(item["issue"] == "offline" and item["device_id"] == offline["device_id"] for item in alerts)
assert any(item["issue"] == "certification_degraded" and item["device_id"] == degraded["device_id"] for item in alerts)

events = fleet_management.list_events(200)
assert any(item["event_type"] == "fleet.rollout_auto_paused" for item in events)
assert any(item["event_type"] == "fleet.rollout_outcome" for item in events)

tmp.cleanup()
print("VP3 OS v1.2 five-device fleet pilot passed")
