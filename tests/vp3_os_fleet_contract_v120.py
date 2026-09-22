from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

vp3_os = (ROOT / "app" / "services" / "vp3_os.py").read_text(encoding="utf-8")
fleet = (ROOT / "app" / "services" / "fleet_management.py").read_text(encoding="utf-8")
fleet_api = (ROOT / "app" / "fleet_api.py").read_text(encoding="utf-8")
pairing = (ROOT / "app" / "services" / "pairing.py").read_text(encoding="utf-8")
remote_bridge = (ROOT / "app" / "services" / "remote_bridge.py").read_text(encoding="utf-8")
bridge = (ROOT / "app" / "bridge.py").read_text(encoding="utf-8")
readiness = (ROOT / "app" / "services" / "release_readiness.py").read_text(encoding="utf-8")
migration = (ROOT / "database" / "migrations" / "028_fleet_management.sql").read_text(encoding="utf-8")
system_html = (ROOT / "ui" / "system.html").read_text(encoding="utf-8")
system_js = (ROOT / "ui" / "system.js").read_text(encoding="utf-8")
index_html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
workflow = (ROOT / ".github" / "workflows" / "vp3-os-fleet-management-v120.yml").read_text(encoding="utf-8")
v110_workflow = (ROOT / ".github" / "workflows" / "vp3-os-controlled-rollout-v110.yml").read_text(encoding="utf-8")
homeserver_ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
policy = (ROOT / ".github" / "CI_POLICY.md").read_text(encoding="utf-8")
docs = (ROOT / "docs" / "VP3_OS_FLEET_MANAGEMENT_V120.md").read_text(encoding="utf-8")

assert 'VP3_OS_VERSION = "v1.' in vp3_os
assert '"fleet_management": "vp3_os_fleet_management_v120"' in vp3_os

for required in (
    'FLEET_VERSION = "v1.2"',
    'DEVICE_ID_RE = re.compile(r"^hs-[0-9a-f]{24}$")',
    '"remote_physical_action_authority": False',
    '"remote_update_download": False',
    '"remote_update_approval": False',
    '"remote_update_apply": False',
    '"remote_private_data_access": False',
    '"owner_staged_release_required": True',
    '"owner_update_approval_required": True',
    '"v11_updater_reused": True',
    '"rollout_auto_pause_on_failures": True',
    'required_permissions = {"fleet.read", "fleet.manage", "fleet.telemetry"}',
    "device_rollout.approve_package",
    'status = "pending_owner" if matching is not None else "unavailable"',
    "counts[\"failures\"] >= int(rollout[\"failure_threshold\"])",
):
    assert required in fleet, required

for forbidden in (
    "execute_command(",
    "create_device_command_request(",
    "request_apply(",
    "httpx.get(",
    "requests.get(",
    "urllib.request.urlopen(",
):
    assert forbidden not in fleet, forbidden

for permission in ("fleet.read", "fleet.manage", "fleet.telemetry"):
    assert f'"{permission}"' in pairing

for required in (
    '"/api/v1/control/vp3-os/fleet"',
    '"/api/v1/control/vp3-os/fleet/settings"',
    '"/api/v1/control/vp3-os/fleet/decommission"',
    '"/api/v1/fleet/device/status"',
    '"/api/v1/fleet/device/diagnostics"',
    '"/api/v1/fleet/device/support-summary"',
    '"/api/v1/fleet/device/update-requests"',
    '"/api/v1/fleet/check-ins"',
    '"/api/v1/fleet/rollouts/{rollout_id}/outcomes"',
):
    assert required in fleet_api, required

for required in (
    '"fleet.device.status"',
    '"fleet.device.diagnostics"',
    '"fleet.device.support_summary"',
    '"fleet.device.update_request"',
    '"fleet.checkin"',
    '"fleet.inventory"',
    '"fleet.rollouts"',
    '"fleet.rollout.outcome"',
):
    assert required in remote_bridge, required

for forbidden in (
    '"fleet.device.apply"',
    '"fleet.device.approve"',
    '"fleet.physical.execute"',
):
    assert forbidden not in remote_bridge, forbidden

for required in (
    "CREATE TABLE IF NOT EXISTS vp3_fleet_settings",
    "enabled INTEGER NOT NULL DEFAULT 0",
    "remote_diagnostics INTEGER NOT NULL DEFAULT 0",
    "remote_update_requests INTEGER NOT NULL DEFAULT 0",
    "remote_support_summary INTEGER NOT NULL DEFAULT 0",
    "rollout_failure_threshold INTEGER NOT NULL DEFAULT 2",
    "CREATE TABLE IF NOT EXISTS vp3_fleet_inventory",
    "CREATE TABLE IF NOT EXISTS vp3_fleet_rollouts",
    "CREATE TABLE IF NOT EXISTS vp3_fleet_rollout_outcomes",
    "CREATE TABLE IF NOT EXISTS vp3_fleet_update_requests",
    "CREATE TABLE IF NOT EXISTS vp3_fleet_events",
):
    assert required in migration, required

assert "required_schema = max(MIN_SCHEMA_VERSION, supported)" in readiness

for required in (
    "fleet_router",
    '"vp3_os_fleet_management": fleet_management.public_capability()',
    '"vp3.os.v120"',
    '"vp3.os.fleet_management.v1"',
    '"vp3.os.fleet_health.v1"',
    '"vp3.os.fleet_rollouts.v1"',
    '"vp3.os.fleet_update_requests.v1"',
):
    assert required in bridge, required

for required in (
    'id="fleetSection"',
    'id="fleetControllerApp"',
    'id="fleetEnabled"',
    'id="fleetDiagnostics"',
    'id="fleetSupportSummary"',
    'id="fleetUpdateRequests"',
    'id="fleetInventory"',
    'id="fleetRollouts"',
    'id="fleetUpdateRequestsList"',
    'id="fleetAlerts"',
):
    assert required in system_html, required

for required in (
    "renderFleet",
    "refreshFleet",
    "data-fleet-remove",
    "data-fleet-rollout-status",
    "data-fleet-request-approve",
    "data-fleet-request-dismiss",
    "/api/v1/control/vp3-os/fleet/decommission",
):
    assert required in system_js, required

assert "VP3 OS v1." in index_html

assert "workflow_dispatch:" in workflow
assert "pull_request:" not in workflow
assert "push:" not in workflow
assert "ubuntu-latest" in workflow
assert "windows-latest" in workflow
for test in (
    "tests/vp3_os_release_v100.py",
    "tests/vp3_os_rollout_v110.py",
    "tests/vp3_os_fleet_v120.py",
    "tests/vp3_os_fleet_api_v120.py",
    "tests/vp3_os_fleet_pilot_v120.py",
    "tests/vp3_os_fleet_contract_v120.py",
    "tests/remote_bridge.py",
    "tests/migrations.py",
    "tests/backup_security.py",
    "tests/system_reliability.py",
    "tests/installer_contract.py",
):
    assert test in workflow, test

assert "workflow_dispatch:" in v110_workflow
assert "pull_request:" not in v110_workflow
assert "push:" not in v110_workflow

for required in (
    "dist/RELEASE.json",
    "vp3-os-release-v1",
    "channel = 'stable'",
):
    assert required in homeserver_ci, required

assert "No new VP3 OS phase starts until" in policy

for required in (
    "local-first fleet layer",
    "fleet access is disabled by default",
    "fleet.read",
    "fleet.manage",
    "fleet.telemetry",
    "Fleet decommissioning",
    "explicitly excludes conversations",
    "automatically pauses",
    "already exist in the local v1.1 staged-package registry",
    "Applying the update remains a separate explicit local v1.1 action",
    "five devices",
    "post-merge",
):
    assert required.lower() in docs.lower(), required

print("VP3 OS v1.2 fleet management contract passed")
