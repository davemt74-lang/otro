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
index_html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
automation_js = (
    ROOT / "ui" / "room-device-automation.js"
).read_text(encoding="utf-8")
automation_css = (
    ROOT / "ui" / "room-device-automation.css"
).read_text(encoding="utf-8")
local_api = (
    ROOT / "app" / "local_automation_api.py"
).read_text(encoding="utf-8")

for forbidden in (
    "execute_command(",
    "create_device_command_request(",
    "approve_request(",
    "run_routine(",
    "evaluate_rule(",
    "upsert_routine(",
    "upsert_rule(",
):
    assert forbidden not in service, forbidden

for required in (
    "a.source_app_key NOT LIKE 'automation:%'",
    "materialize_proposal",
    "enable_materialized_proposal",
    "physical_actions_without_owner_approval",
    'privacy_scope="private"',
    "memory_candidate=False",
    "create_disabled_draft_pair",
):
    assert required in service, required

# Learned objects are ordinary v0.70 objects with explicit owner enable
# controls; the intelligence layer never bypasses the rule engine.
assert "def set_routine_enabled" in local_automation
assert "def set_rule_enabled" in local_automation
assert "def create_disabled_draft_pair" in local_automation
assert "refusing to overwrite it" in local_automation
assert "create_disabled_draft_pair" in service
assert "VALUES (?,?,?,0,'ask_every_time')" in local_automation
assert "VALUES (?,?,?,0,?,?,?,?,?,?)" in local_automation
assert '"rule_enabled": False' in local_automation
assert '"routine_enabled": False' in local_automation

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
assert "\\n" not in bridge_py

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

for required in (
    'VP3 OS v0.80',
    'id="automationIntelligenceSettingsForm"',
    'id="automationIntelligenceScan"',
    'id="automationIntelligenceProposals"',
    'id="automationPatternCount"',
):
    assert required in index_html, required

for required in (
    "/automation/intelligence/scan",
    "Create disabled draft",
    "Enable reviewed draft",
    "Unapproved physical actions",
    "data-intelligence-dismiss",
    "data-rule-enabled",
    "data-routine-enabled",
):
    assert required in automation_js, required

# The browser UI never gets a direct physical execution endpoint.
assert "/execute" not in automation_js
assert "automation-intelligence-card" in automation_css

for required in (
    "/routines/{routine_key}/enabled",
    "/rules/{rule_key}/enabled",
    "set_routine_enabled",
    "set_rule_enabled",
):
    assert required in local_api, required

print("VP3 OS v0.80 intelligence governance contract passed")
