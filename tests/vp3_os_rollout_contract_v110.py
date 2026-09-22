from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

vp3_os = (ROOT / "app" / "services" / "vp3_os.py").read_text(encoding="utf-8")
rollout = (ROOT / "app" / "services" / "device_rollout.py").read_text(encoding="utf-8")
rollout_api = (ROOT / "app" / "device_rollout_api.py").read_text(encoding="utf-8")
runtime_control = (ROOT / "app" / "services" / "runtime_control.py").read_text(encoding="utf-8")
launcher = (ROOT / "desktop" / "launcher.py").read_text(encoding="utf-8")
update_runtime = (ROOT / "desktop" / "update_runtime.py").read_text(encoding="utf-8")
bridge = (ROOT / "app" / "bridge.py").read_text(encoding="utf-8")
main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
migration = (ROOT / "database" / "migrations" / "027_controlled_rollout.sql").read_text(encoding="utf-8")
system_html = (ROOT / "ui" / "system.html").read_text(encoding="utf-8")
system_js = (ROOT / "ui" / "system.js").read_text(encoding="utf-8")
index_html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
workflow = (ROOT / ".github" / "workflows" / "vp3-os-controlled-rollout-v110.yml").read_text(encoding="utf-8")
v100_workflow = (ROOT / ".github" / "workflows" / "vp3-os-production-release-v100.yml").read_text(encoding="utf-8")
homeserver_ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
policy = (ROOT / ".github" / "CI_POLICY.md").read_text(encoding="utf-8")
docs = (ROOT / "docs" / "VP3_OS_CONTROLLED_ROLLOUT_V110.md").read_text(encoding="utf-8")

assert 'VP3_OS_VERSION = "v1.' in vp3_os
assert '"controlled_rollout": "vp3_os_controlled_rollout_v110"' in vp3_os

for required in (
    'ROLLOUT_VERSION = "v1.1"',
    'PACKAGE_FORMAT = "vp3-os-release-v1"',
    'CHANNELS = {"stable", "beta", "dev"}',
    'RINGS = {"pilot", "staged", "broad"}',
    '"automatic_apply": False',
    '"remote_unattended_updates": False',
    '"pre_update_backup_required": True',
    '"sanitized_support_bundle": True',
    'status IN (\'staged\',\'approved\',\'applying\')',
    'Release package is older than the installed VP3 OS',
    'backups.create_backup(f"pre-update-{package[\'version\']}")',
    'request_runtime_command("apply_update")',
    '"conversations_included": False',
    '"recordings_included": False',
    '"knowledge_content_included": False',
    '"credentials_included": False',
    '"absolute_paths_included": False',
):
    assert required in rollout, required

for forbidden in (
    "requests.get(",
    "httpx.get(",
    "urllib.request.urlopen(",
):
    assert forbidden not in rollout, forbidden

for required in (
    '"/api/v1/control/vp3-os/rollout"',
    '"/api/v1/control/vp3-os/commissioning"',
    '"/api/v1/control/vp3-os/certifications"',
    '"/api/v1/control/vp3-os/updates/stage"',
    '"/api/v1/control/vp3-os/updates/{package_id}/approve"',
    '"/api/v1/control/vp3-os/updates/{package_id}/apply"',
    '"/api/v1/control/vp3-os/support-bundle"',
):
    assert required in rollout_api, required

assert '"apply_update"' in runtime_control

for required in (
    "WATCHDOG_FAILURE_WINDOW_SECONDS = 300",
    "WATCHDOG_STABLE_RESET_SECONDS = 60",
    "_register_watchdog_failure(",
    "self._watchdog_max_failures",
    'command not in {"restart", "shutdown", "apply_update"}',
    "spawn_pending_update()",
):
    assert required in launcher, required

for required in (
    'payload.get("format") != _PENDING_FORMAT',
    "_is_within(installer, root)",
    "_is_within(rollback, root)",
    '_record_launch_failure(payload, "installer_revalidation_failed")',
    "Invoke-RestMethod",
    "$response.ok -eq $true",
    "$response.recovery -ne $true",
    "Copy-Item -LiteralPath $RollbackExe -Destination $TargetExe -Force",
    "new_binary_health_check_failed",
):
    assert required in update_runtime, required

for required in (
    "CREATE TABLE IF NOT EXISTS vp3_rollout_settings",
    "release_channel TEXT NOT NULL DEFAULT 'stable'",
    "rollout_ring TEXT NOT NULL DEFAULT 'pilot'",
    "automatic_apply INTEGER NOT NULL DEFAULT 0",
    "watchdog_enabled INTEGER NOT NULL DEFAULT 1",
    "CREATE TABLE IF NOT EXISTS vp3_hardware_certifications",
    "CREATE TABLE IF NOT EXISTS vp3_rollout_packages",
    "CREATE TABLE IF NOT EXISTS vp3_rollout_events",
):
    assert required in migration, required

for required in (
    "device_rollout.reconcile_update_results()",
    "device_rollout.start()",
    "device_rollout.stop()",
):
    assert required in main, required

for required in (
    "device_rollout_router",
    '"vp3_os_device_rollout": device_rollout.public_capability()',
    '"vp3.os.v110"',
    '"vp3.os.hardware_certification.v1"',
    '"vp3.os.controlled_rollout.v1"',
    '"vp3.os.staged_updates.v1"',
    '"vp3.os.support_bundle.v1"',
):
    assert required in bridge, required

for required in (
    'id="rolloutSection"',
    'id="commissioningState"',
    'id="rolloutChannel"',
    'id="rolloutRing"',
    'id="rolloutWatchdog"',
    'id="certifyHardware"',
    'id="rolloutPackage"',
    'id="stageRolloutPackage"',
    'id="downloadSupportBundle"',
):
    assert required in system_html, required

for required in (
    "renderRollout",
    "refreshRollout",
    "data-rollout-approve",
    "data-rollout-apply",
    "data-rollout-discard",
    "/api/v1/control/vp3-os/support-bundle",
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
    "tests/vp3_os_release_api_v100.py",
    "tests/vp3_os_release_contract_v100.py",
    "tests/vp3_os_rollout_v110.py",
    "tests/vp3_os_rollout_api_v110.py",
    "tests/vp3_os_rollout_contract_v110.py",
    "tests/migrations.py",
    "tests/backup_security.py",
    "tests/system_reliability.py",
    "tests/installer_contract.py",
):
    assert test in workflow, test

assert "workflow_dispatch:" in v100_workflow
assert "pull_request:" not in v100_workflow
assert "push:" not in v100_workflow

for required in (
    "dist/RELEASE.json",
    "vp3-os-release-v1",
    "channel = 'stable'",
):
    assert required in homeserver_ci, required

assert "No new VP3 OS phase starts until" in policy

for required in (
    "stable",
    "pilot",
    "automatic update apply: disabled",
    "hardware certification",
    "SHA-256",
    "binary rollback",
    "private-data backup",
    "Runtime watchdog",
    "support bundle",
    "absolute HomeServer filesystem paths",
    "post-merge validation",
):
    assert required.lower() in docs.lower(), required

print("VP3 OS v1.1 controlled rollout contract passed")
