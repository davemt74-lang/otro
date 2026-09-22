from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

tmp = tempfile.TemporaryDirectory(prefix="vp3-os-v120-fleet-api-")
os.environ["HOMESERVER_DATA_DIR"] = tmp.name
os.environ["VP3_OS_HARDWARE_PROFILE"] = "custom"
os.environ["VP3_OS_HARDWARE_ADAPTER"] = "disabled"

from app.runtime import app  # noqa: E402
from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
from app.services.tasks import scheduler  # noqa: E402


def pair(client: TestClient, app_key: str) -> str:
    request = client.post(
        "/api/v1/pairing/request",
        json={
            "app_key": app_key,
            "app_name": app_key,
            "permissions": ["fleet.read", "fleet.manage", "fleet.telemetry"],
        },
    )
    assert request.status_code == 200, request.text
    payload = request.json()
    approved = client.post(
        "/api/v1/pairing/approve",
        json={"code": payload["code"]},
    )
    assert approved.status_code == 200, approved.text
    return payload["claim_token"]


with TestClient(app) as client:
    scheduler.stop()

    assert client.get("/api/v1/control/vp3-os/fleet").status_code == 401
    assert client.get("/api/v1/fleet/device/status").status_code == 401

    owner = client.post(
        "/__owner/session",
        headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN},
    )
    assert owner.status_code == 200

    controller_token = pair(client, "vp3-fleet-control")
    other_token = pair(client, "other-fleet-app")

    headers = {"Authorization": f"Bearer {controller_token}"}
    other_headers = {"Authorization": f"Bearer {other_token}"}

    before = client.get("/api/v1/fleet/device/status", headers=headers)
    assert before.status_code == 403

    configured = client.put(
        "/api/v1/control/vp3-os/fleet/settings",
        json={
            "enabled": True,
            "controller_app_key": "vp3-fleet-control",
            "device_label": "API Fleet Node",
            "remote_diagnostics": True,
            "remote_update_requests": True,
            "remote_support_summary": True,
            "telemetry_interval_seconds": 120,
            "stale_after_seconds": 300,
            "rollout_failure_threshold": 2,
        },
    )
    assert configured.status_code == 200, configured.text
    assert configured.json()["settings"]["enabled"] is True

    status = client.get("/api/v1/fleet/device/status", headers=headers)
    assert status.status_code == 200, status.text
    status_payload = status.json()
    assert status_payload["os_version"] == "v1.2"
    assert status_payload["controller_app"] == "vp3-fleet-control"
    assert status_payload["privacy"]["credentials_included"] is False
    assert status_payload["privacy"]["filesystem_paths_included"] is False

    assert client.get("/api/v1/fleet/device/status", headers=other_headers).status_code == 403

    diagnostics = client.post("/api/v1/fleet/device/diagnostics", headers=headers)
    assert diagnostics.status_code == 200, diagnostics.text
    assert diagnostics.json()["policy"]["automatic_update_apply"] is False
    assert diagnostics.json()["policy"]["physical_action_authority"] is False

    support = client.post("/api/v1/fleet/device/support-summary", headers=headers)
    assert support.status_code == 200, support.text
    assert support.json()["support_bundle_upload"] is False
    assert support.json()["owner_support_bundle_available_locally"] is True

    checkin_payload = {
        "format": "vp3-fleet-device-v1",
        "device_id": "hs-" + "a" * 24,
        "label": "Remote API Node",
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
        "reported_at": datetime.now(timezone.utc).isoformat(),
        "privacy": {
            "conversations_included": False,
            "recordings_included": False,
            "memory_content_included": False,
            "knowledge_content_included": False,
            "credentials_included": False,
            "filesystem_paths_included": False,
            "network_addresses_included": False,
        },
    }
    checkin = client.post("/api/v1/fleet/check-ins", json=checkin_payload, headers=headers)
    assert checkin.status_code == 200, checkin.text
    assert checkin.json()["device"]["device_id"] == checkin_payload["device_id"]

    malicious = dict(checkin_payload)
    malicious["private_content"] = "should be rejected"
    rejected = client.post("/api/v1/fleet/check-ins", json=malicious, headers=headers)
    assert rejected.status_code == 422

    inventory = client.get("/api/v1/fleet/inventory", headers=headers)
    assert inventory.status_code == 200, inventory.text
    assert len(inventory.json()["items"]) == 1
    assert inventory.json()["items"][0]["health"] == "healthy"

    rollout = client.post(
        "/api/v1/control/vp3-os/fleet/rollouts",
        json={
            "release_version": "v1.2",
            "channel": "stable",
            "rollout_ring": "pilot",
            "failure_threshold": 1,
        },
    )
    assert rollout.status_code == 200, rollout.text
    rollout_id = int(rollout.json()["id"])
    started = client.post(
        f"/api/v1/control/vp3-os/fleet/rollouts/{rollout_id}/status",
        json={"status": "active", "reason": ""},
    )
    assert started.status_code == 200

    outcome = client.post(
        f"/api/v1/fleet/rollouts/{rollout_id}/outcomes",
        json={
            "device_id": checkin_payload["device_id"],
            "outcome": "failed",
            "detail_code": "installer_failed",
        },
        headers=headers,
    )
    assert outcome.status_code == 200, outcome.text
    assert outcome.json()["rollout"]["status"] == "paused"
    assert outcome.json()["rollout"]["pause_reason"] == "failure_threshold"

    update_request = client.post(
        "/api/v1/fleet/device/update-requests",
        json={
            "request_key": "api-fleet-update-001",
            "package_sha256": "b" * 64,
            "release_version": "v1.2",
        },
        headers=headers,
    )
    assert update_request.status_code == 200, update_request.text
    assert update_request.json()["status"] == "unavailable"

    capabilities = client.get("/api/v1/capabilities")
    assert capabilities.status_code == 200
    caps = capabilities.json()
    assert caps["vp3_os"]["os_version"] == "v1.2"
    assert caps["vp3_os_fleet_management"]["version"] == "v1.2"
    assert caps["vp3_os_fleet_management"]["remote_update_apply"] is False
    assert "fleet.read" in caps["permissions"]
    assert "fleet.manage" in caps["permissions"]
    assert "fleet.telemetry" in caps["permissions"]
    assert "vp3.os.v120" in caps["features"]

    decommission = client.post("/api/v1/control/vp3-os/fleet/decommission")
    assert decommission.status_code == 200
    assert decommission.json()["private_data_deleted"] is False
    assert client.get("/api/v1/fleet/device/status", headers=headers).status_code == 403

tmp.cleanup()
print("VP3 OS v1.2 fleet API passed")
