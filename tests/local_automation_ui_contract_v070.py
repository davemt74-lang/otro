from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
service = (ROOT / "app" / "services" / "local_automation.py").read_text(encoding="utf-8")
api = (ROOT / "app" / "local_automation_api.py").read_text(encoding="utf-8")
migration = (ROOT / "database" / "migrations" / "024_local_automation_rules.sql").read_text(encoding="utf-8")
docs = (ROOT / "docs" / "VP3_OS_LOCAL_AUTOMATION_V070.md").read_text(encoding="utf-8")
index_html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
automation_js = (ROOT / "ui" / "room-device-automation.js").read_text(encoding="utf-8")
main_py = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
bridge_py = (ROOT / "app" / "bridge.py").read_text(encoding="utf-8")

for required in (
    "suggest_only",
    "ask_every_time",
    "create_device_command_request",
    "create_suggestion",
    "evaluate_due_rules",
    "cooldown_seconds",
    "max_rule_fires_per_minute",
):
    assert required in service, required

# v0.70 is not allowed to create an alternate physical execution path.
assert "execute_command(" not in service
assert "register_driver(" not in service
assert "approve_request(" not in service
assert "direct_physical_execution" in service
assert '"direct_physical_execution": False' in service

# Control Center APIs may create routines/rules and manual runs, but cannot
# release a physical request themselves.
assert "approve_request(" not in api
assert "execute_command(" not in api

for table in (
    "automation_runtime_settings",
    "automation_routines",
    "automation_routine_steps",
    "automation_rules",
    "automation_rule_executions",
):
    assert table in migration, table

assert "There is no automatic physical execution mode in v0.70." in docs

for required in (
    'id="automationRoutineForm"',
    'id="automationRuleForm"',
    'id="automationRuntimeForm"',
    'class="automation-version"',
    'VP3 OS v0.',
):
    assert required in index_html, required

for required in (
    "/automation/routines/",
    "/automation/rules/",
    "/automation/rules-runtime/settings",
    "approval request(s) created",
):
    assert required in automation_js, required

# Lifespan owns the deterministic local scheduler.
assert "local_automation.start()" in main_py
assert "local_automation.stop()" in main_py

# Public capability discovery advertises v0.70 and its approval boundary.
for required in (
    '"vp3_os_local_automation": local_automation.public_capability()',
    '"vp3.os.v070"',
    '"vp3.os.rules.require_owner_approval"',
):
    assert required in bridge_py, required

print("VP3 OS v0.70 local automation governance contract passed")
