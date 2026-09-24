from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

vp3_os = (ROOT / "app" / "services" / "vp3_os.py").read_text(encoding="utf-8")
adapter = (ROOT / "app" / "services" / "hardware_adapters.py").read_text(encoding="utf-8")
experience = (ROOT / "app" / "services" / "hardware_experience.py").read_text(encoding="utf-8")
experience_api = (ROOT / "app" / "hardware_experience_api.py").read_text(encoding="utf-8")
agent = (ROOT / "app" / "services" / "physical_agent.py").read_text(encoding="utf-8")
meeting = (ROOT / "app" / "services" / "physical_meeting.py").read_text(encoding="utf-8")
fleet = (ROOT / "app" / "services" / "fleet_management.py").read_text(encoding="utf-8")
fleet_api = (ROOT / "app" / "fleet_api.py").read_text(encoding="utf-8")
main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
bridge = (ROOT / "app" / "bridge.py").read_text(encoding="utf-8")
migration = (ROOT / "database" / "migrations" / "029_hardware_experience.sql").read_text(encoding="utf-8")
system_html = (ROOT / "ui" / "system.html").read_text(encoding="utf-8")
system_js = (ROOT / "ui" / "system.js").read_text(encoding="utf-8")
index_html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
workflow = (ROOT / ".github" / "workflows" / "vp3-os-hardware-experience-v130.yml").read_text(encoding="utf-8")
v120_workflow = (ROOT / ".github" / "workflows" / "vp3-os-fleet-management-v120.yml").read_text(encoding="utf-8")
homeserver_ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
policy = (ROOT / ".github" / "CI_POLICY.md").read_text(encoding="utf-8")
docs = (ROOT / "docs" / "VP3_OS_HARDWARE_EXPERIENCE_V130.md").read_text(encoding="utf-8")

assert 'VP3_OS_VERSION = "v1.3"' in vp3_os
assert '"hardware_experience": "vp3_os_hardware_experience_v130"' in vp3_os
assert '"control_dial"' in vp3_os

for required in (
    'HARDWARE_EXPERIENCE_VERSION = "v1.3"',
    '"vp3_node"',
    '"vp3_desk"',
    '"vp3_studio"',
    '"vp3_team_node"',
    '"vp3_pocket"',
    '"personal_voice"',
    '"desk_companion"',
    '"creator_console"',
    '"shared_room"',
    '"portable_companion"',
    '"normalized_event_bus": True',
    '"unified_visual_state": True',
    '"persistent_preferences": True',
    '"experience_certification": True',
    '"physical_action_authority": False',
    'device_rollout.list_packages',
    'physical_agent.status()',
    'physical_meeting.status()',
    'ambient_agent.status()',
    '"delegated_to_physical_runtimes"',
    '"volume_up"',
    '"volume_down"',
):
    assert required in experience, required

for forbidden in (
    "execute_command(",
    "create_device_command_request(",
    "room_device_automation.execute",
    "approve_request(",
    "request_apply(",
):
    assert forbidden not in experience, forbidden

for required in (
    '"/api/v1/control/vp3-os/hardware-experience"',
    '"/api/v1/control/vp3-os/hardware-experience/settings"',
    '"/api/v1/control/vp3-os/hardware-experience/certifications"',
    '"/api/v1/control/vp3-os/hardware-experience/events"',
    '"/api/v1/control/vp3-os/hardware-experience/cards"',
):
    assert required in experience_api, required

assert "hardware_experience.button_policy()" in agent
assert "hardware_experience.button_policy()" in meeting
assert '"meeting_toggle"' in meeting
assert '"push_to_talk"' in agent

assert '"control_dial"' in adapter
assert "Control-dial action is invalid." in adapter

for required in (
    "hardware_experience_version",
    "experience_profile",
    "hardware_experience.HARDWARE_EXPERIENCE_VERSION",
    'hardware_experience.profile_experience()["experience"]',
):
    assert required in fleet, required

assert "hardware_experience_version" in fleet_api
assert "experience_profile" in fleet_api

for required in (
    "ALTER TABLE vp3_fleet_inventory",
    "ADD COLUMN hardware_experience_version",
    "ADD COLUMN experience_profile",
    "CREATE TABLE IF NOT EXISTS vp3_hardware_experience_settings",
    "CREATE TABLE IF NOT EXISTS vp3_hardware_experience_events",
    "CREATE TABLE IF NOT EXISTS vp3_hardware_experience_cards",
    "CREATE TABLE IF NOT EXISTS vp3_hardware_experience_certifications",
    "brightness_percent INTEGER NOT NULL DEFAULT 70",
    "volume_percent INTEGER NOT NULL DEFAULT 65",
    "agent_button_action TEXT NOT NULL DEFAULT 'push_to_talk'",
    "hold_action TEXT NOT NULL DEFAULT 'cancel'",
):
    assert required in migration, required

assert "hardware_experience.start()" in main
assert "hardware_experience.stop()" in main

for required in (
    "hardware_experience_router",
    '"vp3_os_hardware_experience": hardware_experience.public_capability()',
    '"vp3.os.v130"',
    '"vp3.os.hardware_experience.v1"',
    '"vp3.os.hardware_event_bus.v1"',
    '"vp3.os.display_cards.v1"',
    '"vp3.os.experience_certification.v1"',
):
    assert required in bridge, required

for required in (
    'id="hardwareExperienceSection"',
    'id="hardwareExperienceState"',
    'id="experienceBrightness"',
    'id="experienceVolume"',
    'id="experienceLedIntensity"',
    'id="experienceWakeBehavior"',
    'id="experienceAgentButton"',
    'id="experienceHoldAction"',
    'id="certifyHardwareExperience"',
    'id="hardwareExperienceCards"',
    'id="hardwareExperienceEvents"',
):
    assert required in system_html, required

for required in (
    "renderHardwareExperience",
    "refreshHardwareExperience",
    "data-experience-dismiss-card",
    "/api/v1/control/vp3-os/hardware-experience/settings",
    "/api/v1/control/vp3-os/hardware-experience/certifications",
):
    assert required in system_js, required

assert "VP3 OS v1.3" in index_html

assert "pull_request:" in workflow
assert "push:" in workflow
assert "cancel-in-progress: true" in workflow
assert "ubuntu-latest" in workflow
assert "windows-latest" in workflow
for test in (
    "tests/vp3_os_v020.py",
    "tests/vp3_os_v030.py",
    "tests/physical_meeting_v040.py",
    "tests/ambient_agent_v050.py",
    "tests/vp3_os_release_v100.py",
    "tests/vp3_os_rollout_v110.py",
    "tests/vp3_os_fleet_v120.py",
    "tests/vp3_os_hardware_experience_v130.py",
    "tests/vp3_os_hardware_experience_api_v130.py",
    "tests/vp3_os_hardware_experience_contract_v130.py",
    "tests/migrations.py",
    "tests/backup_security.py",
    "tests/system_reliability.py",
    "tests/installer_contract.py",
):
    assert test in workflow, test

assert "workflow_dispatch:" in v120_workflow
assert "pull_request:" not in v120_workflow
assert "push:" not in v120_workflow

for required in (
    "dist/RELEASE.json",
    "vp3-os-release-v1",
    "version = '2.3'",
    "channel = 'stable'",
    "minimum_schema_version = 32",
):
    assert required in homeserver_ci, required

assert "No new VP3 OS phase starts until" in policy

for required in (
    "one vp3 os",
    "vp3 node",
    "vp3 desk",
    "vp3 studio",
    "vp3 team node",
    "vp3 pocket",
    "unified visual state",
    "physical event bus",
    "control dial",
    "display cards",
    "degraded behavior",
    "experience certification",
    "hardware_experience_version",
    "post-merge",
):
    assert required.lower() in docs.lower(), required

print("VP3 OS v1.3 hardware experience contract passed")
