from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

service = (
    ROOT / "app" / "services" / "automation_intelligence.py"
).read_text(encoding="utf-8")
api = (
    ROOT / "app" / "automation_intelligence_api.py"
).read_text(encoding="utf-8")
local_automation = (
    ROOT / "app" / "services" / "local_automation.py"
).read_text(encoding="utf-8")
ambient = (
    ROOT / "app" / "services" / "ambient_agent.py"
).read_text(encoding="utf-8")
main_py = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
bridge_py = (ROOT / "app" / "bridge.py").read_text(encoding="utf-8")
migration = (
    ROOT / "database" / "migrations" / "025_ambient_intelligence.sql"
).read_text(encoding="utf-8")
docs = (
    ROOT / "docs" / "VP3_OS_AMBIENT_INTELLIGENCE_V080.md"
).read_text(encoding="utf-8")

for forbidden in (
    "execute_command(",
    "create_device_command_request(",
    "approve_request(",
    "run_routine(",
    "evaluate_rule(",
):
    assert forbidden not in service, forbidden

for required in (
    "a.source_app_key NOT LIKE 'automation:%'",
    "enabled=False",
    "materialize_proposal",
    "enable_materialized_proposal",
    "physical_actions_without_owner_approval",
    'privacy_scope="private"',
    "memory_candidate=False",
):
    assert required in service, required

# Learned objects are ordinary v0.70 objects with explicit owner enable
# controls; the intelligence layer never bypasses the rule engine.
assert "def set_routine_enabled" in local_automation
assert "def set_rule_enabled" in local_automation

for forbidden in (
    "execute_command(",
    "approve_request(",
    "create_device_command_request(",
):
    assert forbidden not in api, forbidden

for required in (
    "/materialize",
    "/enable",
    "/dismiss",
    "/simulate",
    "/scan",
):
    assert required in api, required

# Ambient integration is bounded to presence state, not passive microphone
# capture or transcript/browser content.
assert "automation_intelligence.record_context_event" in ambient
assert '"presence"' in ambient
assert "raw_audio" not in ambient

assert "automation_intelligence.start()" in main_py
assert "automation_intelligence.stop()" in main_py

for required in (
    '"vp3_os_automation_intelligence": automation_intelligence.public_capability()',
    '"vp3.os.v080"',
    '"vp3.os.automation_drafts.disabled_by_default"',
    '"vp3.os.automation_learning.local_only"',
):
    assert required in bridge_py, required

for table in (
    "automation_intelligence_settings",
    "automation_context_events",
    "automation_learning_patterns",
    "automation_proposals",
    "automation_proposal_feedback",
    "automation_simulations",
):
    assert table in migration, table

assert "cannot execute a physical device command" in docs
assert "explicitly excludes any action whose source begins" in docs

print("VP3 OS v0.80 intelligence governance contract passed")
