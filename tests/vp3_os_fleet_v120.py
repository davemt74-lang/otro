from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

tmp = tempfile.TemporaryDirectory(prefix="vp3-os-v120-fleet-")
os.environ["HOMESERVER_DATA_DIR"] = tmp.name
os.environ["VP3_OS_HARDWARE_PROFILE"] = "custom"
os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

from app.database import db, initialize_database  # noqa: E402
from app.services import device_rollout, fleet_management, vp3_os  # noqa: E402
from app.services.owner_secret import load_or_create_owner_secret  # noqa: E402

initialize_database()
load_or_create_owner_secret()

assert vp3_os.VP3_OS_VERSION.startswith("v1.")
initial = fleet_management.get_settings()
assert initial["enabled"] is False
assert initial["controller_app_key"] is None
assert initial["remote_diagnostics"] is False
assert initial["remote_update_requests"] is False
assert initial["remote_support_summary"] is False
assert initial["telemetry_interval_seconds"] == 300
assert initial["stale_after_seconds"] == 900
assert initial["rollout_failure_threshold"] == 2

with db() as connection:
    connection.execute(
        """
        INSERT INTO paired_apps(app_key,name,token_hash,status)
        VALUES ('vp3-fleet-control','VP3 Fleet Control','synthetic-hash','active')
        """
    )
    app_id = int(
        connection.execute(
            "SELECT id FROM paired_apps WHERE app_key='vp3-fleet-control'"
        ).fetchone()["id"]
    )
    for permission in ("fleet.read", "fleet.manage", "fleet.telemetry", "agent.chat"):
        connection.execute(
            """
            INSERT INTO app_permissions(paired_app_id,permission,allowed)
            VALUES (?,?,1)
            """,
            (app_id, permission),
        )

fleet_management.update_settings(
    enabled=True,
    controller_app_key="vp3-fleet-control",
    device_label="Dave's VP3 Node",
    remote_diagnostics=True,
    remote_update_requests=True,
    remote_support_summary=True,
    telemetry_interval_seconds=120,
    stale_after_seconds=300,
    rollout_failure_threshold=2,
)
configured = fleet_management.get_settings()
assert configured["enabled"] is True
assert configured["controller_app_key"] == "vp3-fleet-control"
assert configured["remote_diagnostics"] is True

snapshot = fleet_management.local_device_snapshot()
encoded = json.dumps(snapshot, sort_keys=True)
assert snapshot["format"] == "vp3-fleet-device-v1"
assert str(snapshot["os_version"]).startswith("v1.")
assert snapshot["device_id"].startswith("hs-")
assert snapshot["privacy"]["conversations_included"] is False
assert snapshot["privacy"]["memory_content_included"] is False
assert snapshot["privacy"]["filesystem_paths_included"] is False
assert str(Path(tmp.name).resolve()) not in encoded

with db() as connection:
    connection.execute(
        """
        INSERT INTO agent_memory(memory_key,content,importance)
        VALUES ('fleet-private','SUPER-SECRET-FLEET-MEMORY',1.0)
        """
    )
assert "SUPER-SECRET-FLEET-MEMORY" not in json.dumps(
    fleet_management.remote_diagnostics_summary(),
    sort_keys=True,
)
assert "SUPER-SECRET-FLEET-MEMORY" not in json.dumps(
    fleet_management.remote_support_summary(),
    sort_keys=True,
)

now = datetime.now(timezone.utc).isoformat()
devices = [
    {
        "format": "vp3-fleet-device-v1",
        "device_id": "hs-" + "1" * 24,
        "label": "Pilot Node",
        "profile_key": "node",
        "os_version": "v1.1",
        "release_channel": "stable",
        "rollout_ring": "pilot",
        "commissioning_state": "ready",
        "certification_result": "passed",
        "privacy_fault": False,
        "update_status": "idle",
        "backup_state": "ready",
        "storage_state": "ok",
        "watchdog_failures": 0,
        "reported_at": now,
    },
    {
        "format": "vp3-fleet-device-v1",
        "device_id": "hs-" + "2" * 24,
        "label": "Staged Desk",
        "profile_key": "desk",
        "os_version": "v1.1",
        "release_channel": "stable",
        "rollout_ring": "staged",
        "commissioning_state": "degraded",
        "certification_result": "degraded",
        "privacy_fault": False,
        "update_status": "idle",
        "backup_state": "stale",
        "storage_state": "low",
        "watchdog_failures": 1,
        "reported_at": now,
    },
    {
        "format": "vp3-fleet-device-v1",
        "device_id": "hs-" + "3" * 24,
        "label": "Broad Studio",
        "profile_key": "studio",
        "os_version": "v1.1",
        "release_channel": "stable",
        "rollout_ring": "broad",
        "commissioning_state": "ready",
        "certification_result": "passed",
        "privacy_fault": False,
        "update_status": "idle",
        "backup_state": "ready",
        "storage_state": "ok",
        "watchdog_failures": 0,
        "reported_at": now,
    },
]
for item in devices:
    recorded = fleet_management.record_checkin(item)
    assert recorded["device_id"] == item["device_id"]

inventory = fleet_management.list_inventory()
assert len(inventory) == 3
pilot = next(item for item in inventory if item["rollout_ring"] == "pilot")
staged_device = next(item for item in inventory if item["rollout_ring"] == "staged")
assert pilot["health"] == "healthy"
assert staged_device["health"] == "warning"
assert "backup_stale" in staged_device["issues"]
assert "storage_low" in staged_device["issues"]
assert "watchdog_recovery" in staged_device["issues"]

with db() as connection:
    connection.execute(
        """
        UPDATE vp3_fleet_inventory
        SET last_seen_at=?
        WHERE device_id=?
        """,
        (
            (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
            "hs-" + "3" * 24,
        ),
    )
offline = fleet_management.get_inventory_device("hs-" + "3" * 24)
assert offline["online"] is False
assert "offline" in offline["issues"]
assert any(
    alert["device_id"] == offline["device_id"] and alert["issue"] == "offline"
    for alert in fleet_management.fleet_alerts()
)

rollout = fleet_management.create_rollout("v1.2", "stable", "broad", 2)
assert rollout["status"] == "planned"
assert rollout["eligible_devices"] == 3
rollout = fleet_management.set_rollout_status(rollout["id"], "active")
assert rollout["status"] == "active"

rollout = fleet_management.record_rollout_outcome(
    rollout["id"],
    "hs-" + "1" * 24,
    "healthy",
    "health_check_passed",
)
assert rollout["status"] == "active"
rollout = fleet_management.record_rollout_outcome(
    rollout["id"],
    "hs-" + "2" * 24,
    "failed",
    "installer_failed",
)
assert rollout["counts"]["failures"] == 1
rollout = fleet_management.record_rollout_outcome(
    rollout["id"],
    "hs-" + "3" * 24,
    "rolled_back",
    "health_check_failed",
)
assert rollout["status"] == "paused"
assert rollout["pause_reason"] == "failure_threshold"
assert rollout["counts"]["failures"] == 2
assert any(
    event["event_type"] == "fleet.rollout_auto_paused"
    for event in fleet_management.list_events(100)
)


def release_zip() -> bytes:
    exe = b"vp3-v120-fleet-exe"
    installer = b"vp3-v120-fleet-installer"
    exe_hash = hashlib.sha256(exe).hexdigest()
    installer_hash = hashlib.sha256(installer).hexdigest()
    manifest = {
        "format": "vp3-os-release-v1",
        "version": vp3_os.VP3_OS_VERSION,
        "channel": "stable",
        "minimum_schema_version": 29,
        "files": {
            "HomeServer.exe": exe_hash,
            "HomeServerSetup.exe": installer_hash,
        },
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("RELEASE.json", json.dumps(manifest, sort_keys=True))
        archive.writestr("HomeServer.exe", exe)
        archive.writestr("HomeServerSetup.exe", installer)
        archive.writestr(
            "SHA256SUMS.txt",
            f"{exe_hash}  HomeServer.exe\n{installer_hash}  HomeServerSetup.exe\n",
        )
    return stream.getvalue()


staged = device_rollout.stage_package(io.BytesIO(release_zip()), f"VP3-OS-{vp3_os.VP3_OS_VERSION}-fleet-test.zip")
request = fleet_management.request_update(
    "vp3-fleet-control",
    "fleet-request-v120-001",
    staged["package_sha256"],
    vp3_os.VP3_OS_VERSION,
)
assert request["status"] == "pending_owner"
assert device_rollout.get_package(staged["id"])["status"] == "staged"

approved = fleet_management.approve_update_request(request["id"])
assert approved["request"]["status"] == "approved"
assert approved["package"]["status"] == "approved"
assert approved["apply_automatic"] is False
assert approved["package"]["rollback_backup_name"]
assert device_rollout.get_package(staged["id"])["status"] == "approved"

try:
    fleet_management.request_update(
        "wrong-controller",
        "fleet-request-v120-002",
        staged["package_sha256"],
        vp3_os.VP3_OS_VERSION,
    )
except fleet_management.FleetError as exc:
    assert exc.status_code == 403
else:
    raise AssertionError("Non-controller app created a fleet update request")

result = fleet_management.remove_inventory_device("hs-" + "3" * 24)
assert result["removed"] is True
assert result["private_data_deleted"] is False
assert len(fleet_management.list_inventory()) == 2

fleet_management.decommission_local()
decommissioned = fleet_management.get_settings()
assert decommissioned["enabled"] is False
assert decommissioned["controller_app_key"] is None
assert decommissioned["remote_diagnostics"] is False
with db() as connection:
    permissions = {
        row["permission"]: int(row["allowed"])
        for row in connection.execute(
            "SELECT permission,allowed FROM app_permissions WHERE paired_app_id=?",
            (app_id,),
        ).fetchall()
    }
assert permissions["fleet.read"] == 0
assert permissions["fleet.manage"] == 0
assert permissions["fleet.telemetry"] == 0
assert permissions["agent.chat"] == 1

tmp.cleanup()
print("VP3 OS v1.2 fleet management runtime passed")
