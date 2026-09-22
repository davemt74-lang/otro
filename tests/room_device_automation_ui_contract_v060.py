from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

index_html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
automation_js = (ROOT / "ui" / "room-device-automation.js").read_text(encoding="utf-8")
automation_css = (ROOT / "ui" / "room-device-automation.css").read_text(encoding="utf-8")
automation_py = (ROOT / "app" / "services" / "room_device_automation.py").read_text(encoding="utf-8")
api_py = (ROOT / "app" / "room_device_api.py").read_text(encoding="utf-8")
policy_py = (ROOT / "app" / "services" / "action_policy.py").read_text(encoding="utf-8")
tools_py = (ROOT / "app" / "services" / "tools.py").read_text(encoding="utf-8")
approvals_py = (ROOT / "app" / "services" / "approvals.py").read_text(encoding="utf-8")
file_wrapper_py = (ROOT / "app" / "services" / "local_file_actions_tools.py").read_text(encoding="utf-8")
scheduling_wrapper_py = (ROOT / "app" / "services" / "vp3_scheduling_tools.py").read_text(encoding="utf-8")
commerce_wrapper_py = (ROOT / "app" / "services" / "vp3_commerce_agent_tools.py").read_text(encoding="utf-8")

for required in (
    'data-view="automation"',
    'id="view-automation"',
    'id="automationDevices"',
    'id="automationProviders"',
    'id="automationSuggestions"',
    '/assets/room-device-automation.css',
    '/assets/room-device-automation.js',
):
    assert required in index_html, required

# UI may request a physical command, but must never call a direct execution
# endpoint or claim that a click immediately changes the device.
assert "/request" in automation_js
assert "/execute" not in automation_js
assert "Request on" in automation_js
assert "Request off" in automation_js
assert "Approval request created" in automation_js

for required in (
    ".automation-guardrail",
    ".automation-device",
    ".automation-command-bar",
    ".automation-suggestions",
):
    assert required in automation_css, required

# High-impact physical categories are discoverable but never controllable.
for blocked in ('"camera"', '"lock"', '"garage"', '"security"', '"appliance"'):
    assert blocked in automation_py
assert "BLOCKED_CONTROL_CATEGORIES" in automation_py
assert "SAFE_CONTROL_CATEGORIES" in automation_py

# The service itself validates a real reserved action request, so internal
# callers cannot bypass Approvals by inventing a request ID.
for required in (
    "_assert_approved_execution_context",
    'str(row["action_key"]) != "devices.command"',
    'str(row["status"]) != "executing"',
    "request_arguments != expected",
):
    assert required in automation_py, required

# Every v0.60 device command is approval-only; safe-automatic is not a valid
# policy mode for this write tool.
assert '"devices.command"' in policy_py
approval_section = policy_py.split("APPROVAL_ONLY_WRITE_TOOLS", 1)[1].split("}", 1)[0]
assert '"devices.command"' in approval_section

# The execution primitive requires an approval request ID.
assert "approval_request_id: str | None = None" in tools_py
assert "Physical device commands require an approved action request" in tools_py

# Owner Control Center creates a request instead of calling execute_command.
assert "create_device_command_request" in api_py
assert "execute_command(" not in api_py

# Approvals is the only release path into the command tool.
assert '"devices.command"' in approvals_py
assert 'approval_request_id=request["id"]' in approvals_py

# Installed tool decorators must preserve approval context when they delegate
# tools they do not own. Dropping this keyword would break the approved
# physical-action release path depending on wrapper install order.
for wrapper in (file_wrapper_py, scheduling_wrapper_py, commerce_wrapper_py):
    assert "approval_request_id" in wrapper
    assert "approval_request_id=approval_request_id" in wrapper

print("VP3 OS v0.60 room-device governance/UI contract passed")
