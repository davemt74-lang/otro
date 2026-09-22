from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

service = (
    ROOT / "app" / "services" / "ambient_orchestration.py"
).read_text(encoding="utf-8")
api = (
    ROOT / "app" / "ambient_orchestration_api.py"
).read_text(encoding="utf-8")
main_py = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
bridge_py = (ROOT / "app" / "bridge.py").read_text(encoding="utf-8")
migration = (
    ROOT / "database" / "migrations" / "026_ambient_orchestration.sql"
).read_text(encoding="utf-8")
docs = (
    ROOT / "docs" / "VP3_OS_AMBIENT_ORCHESTRATION_V090.md"
).read_text(encoding="utf-8")
index_html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
automation_js = (
    ROOT / "ui" / "room-device-automation.js"
).read_text(encoding="utf-8")
automation_css = (
    ROOT / "ui" / "room-device-automation.css"
).read_text(encoding="utf-8")
local_automation = (
    ROOT / "app" / "services" / "local_automation.py"
).read_text(encoding="utf-8")

for forbidden in (
    "execute_command(",
    "approve_request(",
    "create_device_command_request(",
):
    assert forbidden not in service, forbidden

for required in (
    "local_automation.run_routine",
    "ambient_agent.status",
    "physical_meeting.status",
    "approvals.cancel_pending_requests",
    "physical_actions_without_owner_approval",
    "supersede_conflicts",
    "Manual device change suspended the Room Mode.",
    'privacy_scope="private"',
    "memory_candidate=False",
    "ambient_auto_activation",
    "orchestration.mode_active",
    "orchestration.mode_failed",
    "orchestration.mode_suspended",
):
    assert required in service, required

assert '"ambient_auto_activation": False' in service
assert '"direct_physical_execution": False' in service
assert '"device_commands_require_v060_owner_approval": True' in service

for forbidden in (
    "execute_command(",
    "approve_request(",
    "create_device_command_request(",
):
    assert forbidden not in api, forbidden

for required in (
    "/simulate",
    "/activate",
    "/accept",
    "/dismiss",
    "/suspend",
    "/end",
    "/refresh",
    "/conflicts",
):
    assert required in api, required

assert "ambient_orchestration.start()" in main_py
assert "ambient_orchestration.stop()" in main_py

for required in (
    '"vp3_os_ambient_orchestration": ambient_orchestration.public_capability()',
    '"vp3.os.v090"',
    '"vp3.os.room_modes.v1"',
    '"vp3.os.orchestration.owner_activation_required"',
    '"vp3.os.orchestration.conflict_detection"',
):
    assert required in bridge_py, required

for table in (
    "orchestration_settings",
    "orchestration_modes",
    "orchestration_mode_sessions",
    "orchestration_mode_conflicts",
    "orchestration_mode_transitions",
):
    assert table in migration, table

assert "idx_orchestration_one_open_session_per_mode" in migration
assert "WHERE state IN ('suggested','requested','active','suspended')" in migration

assert "Ambient context never activates a mode automatically." in docs
assert "strictly higher priority" in docs
assert "never silently reverses physical device state" in docs

for required in (
    'VP3 OS v0.90',
    'id="orchestrationModeForm"',
    'id="orchestrationSettingsForm"',
    'id="orchestrationEvaluate"',
    'id="orchestrationModes"',
    'id="orchestrationSessions"',
):
    assert required in index_html, required

for required in (
    "/api/v1/control/vp3-os/orchestration",
    "data-mode-simulate",
    "data-mode-activate",
    "data-mode-supersede",
    "data-mode-session-accept",
    "data-mode-session-suspend",
    "0 unapproved physical actions",
):
    assert required in automation_js, required

assert "execute_command(" not in automation_js
assert "orchestration-card" in automation_css
assert "def _assert_routine_not_bound_to_room_mode" in local_automation
assert "bound to Room Mode" in local_automation
assert "with _LOCK:" in service
assert "def _open_session_for_mode" in service
assert "Room Mode already has an open" in service

print("VP3 OS v0.90 orchestration governance contract passed")
